"""Glue between one CallSession and the rest of the system (hub, bank, circle).

Everything that turns a risk level into a real-world effect lives here, so the
session stays pure and testable.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid

from riposte.analyze.resilient import ResilientAnalyzer
from riposte.analyze.rules import KeywordAnalyzer
from riposte.coach.playbook import card_for
from riposte.config import settings
from riposte.guard.bank import MockBank
from riposte.linkguard.guard import LinkGuard, lookalike_brand
from riposte.risk.policy import ActionType
from riposte.session import CallSession
from riposte.signals import Signal, SignalType as T, Source
from riposte.store.hub import Hub, IntelItem

log = logging.getLogger(__name__)
_ORDER = ["calm", "caution", "high", "critical"]


def normalize_number(n: str) -> str:
    """'+91 98301 77241', ' 919830177241' (a '+' lost in a URL), '09830177241' -> '+919830177241'."""
    d = re.sub(r"\D", "", n or "")
    if not d:
        return ""
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10 and d[0] in "6789":
        return "+91" + d
    if len(d) == 12 and d.startswith("91") and d[2] in "6789":
        return "+" + d
    return ("+" if (n or "").strip().startswith("+") or len(d) > 10 else "") + d


def is_1600(n: str) -> bool:
    d = re.sub(r"\D", "", n or "")
    return d.startswith("1600") or d.startswith("911600")


class CallController:
    def __init__(self, hub: Hub, bank: MockBank, circle_id: str, caller: str = "",
                 use_gemini: bool = True) -> None:
        self.hub, self.bank, self.circle_id = hub, bank, circle_id
        self.caller = normalize_number(caller)
        analyzers = [KeywordAnalyzer()]
        if use_gemini and (settings.gemini_api_key or settings.google_cloud_project):
            analyzers.append(ResilientAnalyzer([settings.analyzer_model, *settings.analyzer_fallbacks],
                                               timeout_s=settings.analyzer_timeout_s))
        self.session = CallSession(analyzers=analyzers, linkguard=LinkGuard(), window_seconds=settings.window_seconds)
        self.call_id = self.session.id
        self._pinged = False
        self._presence_member = ""
        self._acted: set[str] = set()
        self.session.subscribe(self._on_event)
        hub.calls[self.call_id] = {"call_id": self.call_id, "circle_id": circle_id, "caller": self.caller,
                                   "started_at": time.time(), "ended_at": None, "level": "calm",
                                   "peak": "calm", "score": 0, "reasons": [], "transcript": []}
        self.outbox: asyncio.Queue = asyncio.Queue(maxsize=1000)  # events for the listener socket

    async def start(self) -> None:
        self.session.start()
        c = self.hub.circle(self.circle_id)
        self.hub.publish(self.circle_id, {"type": "call_started", "call_id": self.call_id, "caller": self.caller})
        known = self.hub.contact_for(self.circle_id, self.caller) if self.caller else None
        self.hub.calls[self.call_id]["caller_name"] = known.name if known else ""
        await self._send({"type": "caller_info", "number": self.caller, "known": bool(known),
                          "name": known.name if known else ""})
        if self.caller and not known:
            await self.session.add_signal(Signal(type=T.CALLER_UNKNOWN, source=Source.DEVICE,
                                                 evidence=f"{self.caller} is not in your saved contacts"))

    async def stop(self) -> None:
        await self.session.close()
        info = self.hub.calls[self.call_id]
        info["ended_at"] = time.time()
        self.hub.publish(self.circle_id, {"type": "call_ended", "call_id": self.call_id, "peak": info["peak"]})

    # ---- event fan-out ------------------------------------------------------------
    async def _send(self, event: dict) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self.outbox.put_nowait(event)

    async def _on_event(self, e: dict) -> None:
        info = self.hub.calls[self.call_id]
        if e["type"] == "transcript":
            if e["final"]:
                info["transcript"].append(e["text"])
                info["transcript"] = info["transcript"][-200:]
            await self._send(e)
            return
        if e["type"] == "entity":
            ent = e["entity"]
            if ent["kind"] in ("upi", "account", "ifsc", "domain") or (
                    ent["kind"] == "phone" and not self.hub.contact_for(self.circle_id, normalize_number(ent["value"]))):
                note = ent.get("note", "")
                if ent["kind"] == "domain" and (brand := lookalike_brand(ent["value"])):
                    note = f"Fake look-alike of {brand.upper()}"
                item = IntelItem(circle_id=self.circle_id, call_id=self.call_id, kind=ent["kind"],
                                 value=ent["value"], valid=ent["valid"], note=note,
                                 source="decoy" if info.get("decoy") else "call")
                self.hub.intel.append(item)
                self.hub.publish(self.circle_id, {"type": "intel", "intel": item.model_dump()})
            await self._send(e)
            return
        if e["type"] != "risk":
            await self._send(e)
            return

        risk = e["risk"]
        types = {T(t) for t in risk["types"]}
        info.update(level=risk["level"], peak=risk["peak"], score=risk["score"],
                    reasons=risk["reasons"], combos=risk["combos"], claims=e["claims"])

        # caller-id rule: a 'bank' calling from a non-1600 number (TRAI 2025 direction)
        if T.CLAIMS_BANK in types and self.caller and not is_1600(self.caller) and T.CALLER_NOT_1600 not in types:
            asyncio.create_task(self.session.add_signal(Signal(
                type=T.CALLER_NOT_1600, source=Source.RULES,
                evidence=f"Claims to be a bank but is calling from {self.caller}, not a 1600-series number")))
        # presence ping when the caller claims to be family
        if T.CLAIMS_FAMILY in types and not self._pinged and self.hub.circle(self.circle_id).members:
            self._pinged = True
            asyncio.create_task(self._presence_ping(e["claims"].get("claimed_person_name", "")))

        card = card_for(types, e.get("coach_tip", ""))
        out = {**e, "card": card}
        await self._send(out)
        self.hub.publish(self.circle_id, {"type": "risk", "call_id": self.call_id, "level": risk["level"],
                                          "peak": risk["peak"], "score": risk["score"],
                                          "reasons": risk["reasons"][:6], "combos": risk["combos"],
                                          "card": card, "claims": e["claims"]})
        await self._apply_actions({a["type"] for a in e["actions"]}, risk["level"])

    async def _apply_actions(self, actions: set[str], level: str) -> None:
        if ActionType.BANK_RISK_CONTEXT.value in actions and f"ctx:{level}" not in self._acted:
            self._acted.add(f"ctx:{level}")
            self.bank.risk_context(self.circle_id, level)
        for a in (ActionType.ALERT_FAMILY_SILENT.value, ActionType.ALERT_FAMILY_APPROVAL.value):
            if a in actions and a not in self._acted:
                self._acted.add(a)
                info = self.hub.calls[self.call_id]
                self.hub.publish(self.circle_id, {
                    "type": "family_alert", "call_id": self.call_id, "level": level,
                    "urgent": a == ActionType.ALERT_FAMILY_APPROVAL.value,
                    "caller": self.caller, "claims": info.get("claims", {}),
                    "reasons": info.get("reasons", [])[:4]})

    async def _presence_ping(self, claimed_name: str) -> None:
        c = self.hub.circle(self.circle_id)
        targets = [m for m in c.members if not claimed_name or claimed_name.lower() in m.name.lower()] or c.members
        m = targets[0]
        ping_id = uuid.uuid4().hex[:8]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.hub.ping_waiters[ping_id] = fut
        self.hub.publish(self.circle_id, {"type": "presence_ping", "ping_id": ping_id, "member_id": m.id,
                                          "member": m.name, "call_id": self.call_id})
        await self._send({"type": "presence", "status": "checking", "member": m.name})
        try:
            on_call = await asyncio.wait_for(fut, timeout=8)
        except asyncio.TimeoutError:
            await self._send({"type": "presence", "status": "unverified", "member": m.name})
            return
        finally:
            self.hub.ping_waiters.pop(ping_id, None)
        await self.presence_result(m.name, bool(on_call))

    async def presence_result(self, member: str, on_call: bool) -> None:
        """Apply (or re-apply) what the family member's phone reports. Called on the ping answer and
        whenever the member's call state changes later in the call."""
        self._presence_member = member
        if on_call:
            await self.session.set_family_presence(True, f"{member}'s phone confirms they are on this call")
            self.hub.calls[self.call_id]["caller_name"] = f"{member} (verified by their phone)"
            await self._send({"type": "presence", "status": "confirmed", "member": member})
            await self._send({"type": "caller_info", "number": self.caller, "known": True,
                              "name": f"{member}, verified by their own phone"})
        else:
            await self.session.set_family_presence(False, f"{member}'s phone says they are NOT on a call with you")
            await self._send({"type": "presence", "status": "denied", "member": member})
