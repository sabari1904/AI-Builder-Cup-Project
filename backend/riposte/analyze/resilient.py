"""Resilience wrapper for LLM analyzers.

Found in testing: models return 503 "high demand" and SDK retries can stretch a
single call past 60 s. A live call cannot wait, so:
  - each attempt has a hard timeout,
  - models are tried in a fallback chain,
  - a model that fails is skipped for a cool-down period (circuit breaker),
  - if every model fails we return an empty Assessment (the keyword analyzer,
    which always runs in parallel, still produces signals).
"""
from __future__ import annotations

import asyncio
import logging
import time

from riposte.analyze.base import Assessment, Claims
from riposte.analyze.gemini import GeminiAnalyzer

log = logging.getLogger(__name__)


class ResilientAnalyzer:
    def __init__(self, models: list[str], timeout_s: float = 8.0, cooldown_s: float = 20.0) -> None:
        self.chain = [GeminiAnalyzer(model=m) for m in models]
        self.timeout_s = timeout_s
        self.cooldown_s = cooldown_s
        self._down_until: dict[str, float] = {}
        self.last_model = ""
        self.last_latency = 0.0

    async def assess(self, window: str, prior: Claims) -> Assessment:
        now = time.time()
        for a in self.chain:
            if self._down_until.get(a.model, 0) > now:
                continue
            t = time.time()
            try:
                r = await asyncio.wait_for(a.assess(window, prior), timeout=self.timeout_s)
                self.last_model, self.last_latency = a.model, time.time() - t
                return r
            except Exception as e:  # timeout, 503, 429, 400, malformed JSON...
                msg = str(e)
                log.warning("analyzer %s failed after %.1fs: %s", a.model, time.time() - t,
                            f"{type(e).__name__}: {msg[:140]}")
                # Trip the breaker only for overload/quota/config errors; one slow call is not an outage.
                if isinstance(e, asyncio.TimeoutError):
                    continue
                if "PerDay" in msg or "per day" in msg.lower():
                    self._down_until[a.model] = time.time() + 3600   # daily quota: don't hammer it
                elif any(c in msg for c in ("503", "504", "429", "400", "404")):
                    self._down_until[a.model] = time.time() + self.cooldown_s
        self.last_model = "none"
        return Assessment(claims=prior)
