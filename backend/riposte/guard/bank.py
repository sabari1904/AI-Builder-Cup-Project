"""BankAdapter interface + MockBank.

The money guard is deliberately *not* AI: it reads the call's risk level (a fact
produced by the rules engine) and applies fixed release rules.
"""
from __future__ import annotations

import time
from typing import Protocol

from riposte.risk.policy import RELEASE_RULES
from riposte.risk.engine import Level
from riposte.store.hub import Hub, IntelItem, Payment


class BankAdapter(Protocol):
    def pay(self, circle_id: str, amount: float, payee: str) -> Payment: ...
    def decide(self, payment_id: str, decision: str, by: str) -> Payment: ...
    def release_after_cooling(self, payment_id: str) -> Payment: ...
    def freeze(self, circle_id: str, frozen: bool, by: str) -> None: ...


class MockBank:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    def _emit(self, circle_id: str, kind: str, **data) -> None:
        self.hub.publish(circle_id, {"type": kind, **data})

    def pay(self, circle_id: str, amount: float, payee: str) -> Payment:
        c = self.hub.circle(circle_id)
        p = Payment(circle_id=circle_id, amount=amount, payee=payee)
        risky = self.hub.recent_risky_call(circle_id)
        live = self.hub.active_call(circle_id)
        p.call_id = (risky or live or {}).get("call_id")
        if c.frozen:
            p.status, p.reason = "frozen", "Account is frozen (kill switch is on)"
        elif any(payee.strip().lower() == i.value.lower() for i in self.hub.intel if i.circle_id == circle_id):
            p.status, p.reason = "blocked", "Payee was named by a scam caller on a recent call"
        elif risky:
            lvl = Level(risky["peak"])
            rule = RELEASE_RULES[lvl]
            p.status, p.level, p.call_id = "held", lvl.value, risky["call_id"]
            p.release_after = time.time() + rule["cooling_minutes"] * 60
            p.reason = (f"Payment started during or right after a {lvl.value}-risk call. "
                        f"Your bank is holding it for a {rule['cooling_minutes']}-minute cooling-off. "
                        "You can cancel it any time; your trusted contact has been alerted.")
        else:
            p.status, p.reason = "completed", "No risky call in progress"
        self.hub.payments[p.id] = p
        self._emit(circle_id, "payment", payment=p.model_dump())
        return p

    def decide(self, payment_id: str, decision: str, by: str) -> Payment:
        p = self.hub.payments[payment_id]
        if p.status != "held":
            return p
        if decision == "cancel":
            p.status, p.decided_by = "blocked", by
            p.reason = f"Cancelled by {by}. The money never left the account."
        self._emit(p.circle_id, "payment", payment=p.model_dump())
        return p

    def release_after_cooling(self, payment_id: str) -> Payment:
        p = self.hub.payments[payment_id]
        if p.status == "held" and p.release_after and time.time() >= p.release_after:
            p.status, p.reason, p.decided_by = "released", "Released after the cooling-off period", "user"
            self._emit(p.circle_id, "payment", payment=p.model_dump())
        return p

    def freeze(self, circle_id: str, frozen: bool, by: str) -> None:
        c = self.hub.circle(circle_id)
        c.frozen = frozen
        for p in self.hub.payments.values():
            if p.circle_id == circle_id and p.status == "held" and frozen:
                p.status, p.reason = "frozen", f"Kill switch pressed by {by}"
                self._emit(circle_id, "payment", payment=p.model_dump())
        self._emit(circle_id, "freeze", frozen=frozen, by=by)

    def risk_context(self, circle_id: str, level: str, hours: float = 24) -> None:
        c = self.hub.circle(circle_id)
        c.risk_context_until = max(c.risk_context_until, time.time() + hours * 3600)
        self._emit(circle_id, "bank_context", level=level, until=c.risk_context_until)

    def card_attempt(self, circle_id: str, card: str, merchant: str) -> dict:
        """A charge attempt on a card. If it is the planted decoy card, it's a tripwire."""
        c = self.hub.circle(circle_id)
        digits = card.replace(" ", "")
        if digits == c.honey.card_number.replace(" ", ""):
            item = IntelItem(circle_id=circle_id, call_id=None, kind="merchant", value=merchant,
                             source="tripwire", note="Decoy card used: this merchant is the scam's cash-out point")
            self.hub.intel.append(item)
            self._emit(circle_id, "tripwire", intel=item.model_dump())
            return {"declined": True, "tripwire": True, "intel": item.model_dump()}
        return {"declined": False, "tripwire": False}
