"""CallSession: the per-call orchestrator. Transport-agnostic.

Feed it transcript segments (from any Transcriber) and device/circle signals.
It runs analyzers, the link guard and the risk engine, and publishes events
to subscribers (WebSocket to the listener app, Firestore, bank adapter...).

Concurrency rule: at most one analysis in flight; while it runs, newer text
accumulates and the *latest* window is analysed next (no backlog, no stale work).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Awaitable, Callable

from riposte.analyze.base import Analyzer, Assessment, Claims, verify_findings
from riposte.intel.entities import Entity, extract
from riposte.linkguard.guard import LinkGuard
from riposte.risk.engine import RiskEngine, RiskState
from riposte.risk.policy import Action, Policy
from riposte.signals import Signal

log = logging.getLogger(__name__)
Listener = Callable[[dict], Awaitable[None]]


class CallSession:
    def __init__(self, analyzers: list[Analyzer], linkguard: LinkGuard | None = None,
                 policy: Policy | None = None, window_seconds: int = 90, call_id: str | None = None) -> None:
        self.id = call_id or uuid.uuid4().hex[:12]
        self.analyzers = analyzers
        self.linkguard = linkguard
        self.policy = policy or Policy()
        self.engine = RiskEngine()
        self.claims = Claims()
        self.window_seconds = window_seconds
        self.segments: deque[tuple[float, str]] = deque()
        self.entities: dict[str, Entity] = {}
        self.coach_tip = ""
        self._listeners: list[Listener] = []
        self._changed = asyncio.Event()
        self._version = 0
        self._closed = False
        self._worker: asyncio.Task | None = None
        self._last_actions: set[str] = set()

    # ---- wiring -------------------------------------------------------------
    def subscribe(self, fn: Listener) -> None:
        self._listeners.append(fn)

    async def _emit(self, event: dict) -> None:
        event = {"call_id": self.id, "at": time.time(), **event}
        for fn in list(self._listeners):
            try:
                await fn(event)
            except Exception as e:  # a broken subscriber must not break the call
                log.warning("listener failed: %s", e)

    def start(self) -> None:
        if not self._worker:
            self._worker = asyncio.create_task(self._loop())

    async def close(self) -> None:
        self._closed = True
        self._changed.set()
        if self._worker:
            await self._worker
        await self._emit({"type": "closed", "risk": self.engine.state.model_dump(mode="json")})

    # ---- inputs -------------------------------------------------------------
    async def add_transcript(self, text: str, final: bool = True) -> None:
        text = text.strip()
        if not text:
            return
        await self._emit({"type": "transcript", "text": text, "final": final})
        if not final:
            return
        self.segments.append((time.time(), text))
        # cheap, synchronous checks on every final segment
        if self.linkguard:
            asyncio.create_task(self._check_links(text))
        for e in extract(text):
            if e.value not in self.entities:
                self.entities[e.value] = e
                await self._emit({"type": "entity", "entity": e.model_dump()})
        self._bump()

    async def set_family_presence(self, confirmed: bool, evidence: str) -> RiskState:
        before = self.engine.state.level
        state = self.engine.verify_family(evidence) if confirmed else self.engine.deny_family(evidence)
        await self._publish(state, [], before, source="circle")
        return state

    async def add_signal(self, signal: Signal) -> RiskState:
        before = self.engine.state.level
        state = self.engine.update(signal)
        await self._publish(state, [signal], before)
        return state

    # ---- internals ----------------------------------------------------------
    def window(self) -> str:
        cutoff = time.time() - self.window_seconds
        while self.segments and self.segments[0][0] < cutoff and len(self.segments) > 4:
            self.segments.popleft()
        return "\n".join(t for _, t in self.segments)

    async def _check_links(self, text: str) -> None:
        try:
            for s in await self.linkguard.check_text(text):  # type: ignore[union-attr]
                await self.add_signal(s)
        except Exception as e:
            log.warning("linkguard error: %s", e)

    async def _loop(self) -> None:
        # One independent lane per analyzer: a fast analyzer never waits for a slow one.
        lanes = [asyncio.create_task(self._lane(a)) for a in self.analyzers]
        await asyncio.gather(*lanes)

    async def _lane(self, analyzer: Analyzer) -> None:
        seen_version = 0
        while not self._closed:
            if self._version == seen_version:
                await self._changed.wait()
                continue
            seen_version = self._version
            await self._run_analyzer(analyzer, self.window())

    def _bump(self) -> None:
        self._version += 1
        self._changed.set()
        self._changed = asyncio.Event()

    async def analyze_now(self) -> None:
        """Run every analyzer once on the current window (used by tests and replays)."""
        window = self.window()
        await asyncio.gather(*(self._run_analyzer(a, window) for a in self.analyzers))

    async def _run_analyzer(self, analyzer: Analyzer, window: str) -> None:
        if not window:
            return
        try:
            r = await analyzer.assess(window, self.claims)
        except Exception as e:
            log.warning("analyzer %s failed: %r", type(analyzer).__name__, e)
            return
        signals = verify_findings(r, window)
        if r.claims.summary or r.claims.claimed_identity:
            self.claims = r.claims
        if r.coach_tip:
            self.coach_tip = r.coach_tip
        before = self.engine.state.level
        state = self.engine.update_many(signals)
        # spoken links the model normalised (e.g. "icici cards dot services")
        if self.linkguard and r.claims.links_or_apps:
            asyncio.create_task(self._check_links(" ".join(r.claims.links_or_apps)))
        await self._publish(state, signals, before, source=type(analyzer).__name__)

    async def _publish(self, state: RiskState, new: list[Signal], before, source: str = "") -> None:
        actions: list[Action] = self.policy.actions(state)
        await self._emit({
            "type": "risk",
            "source": source,
            "risk": state.model_dump(mode="json"),
            "new_signals": [s.model_dump(mode="json") for s in new],
            "claims": self.claims.model_dump(),
            "coach_tip": self.coach_tip,
            "actions": [a.model_dump(mode="json") for a in actions],
            "escalated": state.level != before,
        })
