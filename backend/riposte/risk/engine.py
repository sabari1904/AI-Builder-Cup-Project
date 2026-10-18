"""Deterministic, explainable risk scoring.

Rules, not the LLM, make the decision. Every point in the score is traceable
to a Signal with evidence. Within one call the score never drops below the
highest level already reached (scammers calm down on purpose).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from riposte.signals import Signal, SignalType as T, Source


class Level(str, Enum):
    CALM = "calm"
    CAUTION = "caution"
    HIGH = "high"
    CRITICAL = "critical"


# Points per signal type. Requests and hard facts outweigh tone.
WEIGHTS: dict[T, int] = {
    T.CLAIMS_BANK: 2, T.CLAIMS_GOVT_POLICE: 3, T.CLAIMS_FAMILY: 1, T.CLAIMS_COMPANY: 1,
    T.ASKS_OTP: 6, T.ASKS_PIN_PASSWORD: 6, T.ASKS_CARD_DETAILS: 5, T.ASKS_INSTALL_APP: 5,
    T.ASKS_SCREEN_SHARE: 5, T.ASKS_OPEN_LINK: 3, T.ASKS_PAYMENT: 3, T.ASKS_MOVE_MONEY_SAFE: 7,
    T.URGENCY_FEAR: 2, T.SECRECY_ISOLATION: 3, T.REFUSES_VERIFICATION: 3, T.TOO_GOOD_TO_BE_TRUE: 2,
    T.CALLER_UNKNOWN: 1, T.CALLER_NOT_1600: 2,
    T.LINK_LOOKALIKE: 4, T.LINK_MALICIOUS: 6, T.DOMAIN_NEW: 2,
    T.PRESENCE_DENIED: 8, T.PRESENCE_CONFIRMED: 0,
    T.OTP_RECEIVED: 2, T.PAYMENT_STARTED: 2,
    T.USER_INITIATED_CONTEXT: 0,
}

# Combinations that are scams on their own, whatever the total.
CRITICAL_COMBOS: list[tuple[set[T], str]] = [
    ({T.CLAIMS_GOVT_POLICE, T.ASKS_MOVE_MONEY_SAFE}, "Police/agency asking to move money: 'digital arrest' pattern"),
    ({T.CLAIMS_GOVT_POLICE, T.ASKS_PAYMENT}, "Police/agency asking for payment"),
    ({T.CLAIMS_BANK, T.ASKS_OTP}, "A 'bank' asking for your OTP"),
    ({T.CLAIMS_BANK, T.ASKS_PIN_PASSWORD}, "A 'bank' asking for your PIN or password"),
    ({T.CLAIMS_BANK, T.ASKS_INSTALL_APP}, "A 'bank' asking you to install an app"),
    ({T.CLAIMS_BANK, T.LINK_LOOKALIKE}, "A 'bank' sending you to a fake website"),
    ({T.CLAIMS_FAMILY, T.PRESENCE_DENIED}, "Your family member's phone says they are NOT calling you"),
    ({T.ASKS_SCREEN_SHARE, T.OTP_RECEIVED}, "Screen sharing while an OTP arrives"),
    ({T.CLAIMS_FAMILY, T.ASKS_PAYMENT, T.SECRECY_ISOLATION}, "'Family' asking for money and secrecy"),
]

THRESHOLDS = [(10, Level.CRITICAL), (6, Level.HIGH), (3, Level.CAUTION), (0, Level.CALM)]
_ORDER = [Level.CALM, Level.CAUTION, Level.HIGH, Level.CRITICAL]

# Benign context may soften only the CAUTION band, and only before any request signal.
_REQUESTS = {T.ASKS_OTP, T.ASKS_PIN_PASSWORD, T.ASKS_CARD_DETAILS, T.ASKS_INSTALL_APP,
             T.ASKS_SCREEN_SHARE, T.ASKS_PAYMENT, T.ASKS_MOVE_MONEY_SAFE}


class Reason(BaseModel):
    type: T
    points: int
    evidence: str


class RiskState(BaseModel):
    score: int = 0
    level: Level = Level.CALM
    peak: Level = Level.CALM
    reasons: list[Reason] = Field(default_factory=list)
    combos: list[str] = Field(default_factory=list)
    types: list[T] = Field(default_factory=list)


def _level_for(score: int) -> Level:
    for cut, lvl in THRESHOLDS:
        if score >= cut:
            return lvl
    return Level.CALM


def _max(a: Level, b: Level) -> Level:
    return a if _ORDER.index(a) >= _ORDER.index(b) else b


class RiskEngine:
    """One engine per call. Feed it Signals, read RiskState."""

    MIN_CONFIDENCE = 0.5

    def __init__(self) -> None:
        self._seen: dict[str, Signal] = {}
        self._counted_types: set[T] = set()
        self.state = RiskState()

    def update(self, signal: Signal) -> RiskState:
        if signal.confidence < self.MIN_CONFIDENCE:
            return self.state
        k = signal.key()
        if k in self._seen:
            return self.state
        self._seen[k] = signal
        return self._recompute()

    def verify_family(self, evidence: str) -> RiskState:
        """The real family member's own device confirmed the call. That is stronger evidence than
        anything said on the call, so withdraw the 'not them' / 'unknown number' flags and let the
        level drop (the floor resets because the identity question is settled)."""
        self._seen = {k: s for k, s in self._seen.items()
                      if s.type not in (T.PRESENCE_DENIED, T.CALLER_UNKNOWN, T.PRESENCE_CONFIRMED)}
        confirm = Signal(type=T.PRESENCE_CONFIRMED, source=Source.CIRCLE, evidence=evidence)
        self._seen[confirm.key()] = confirm
        self.state = self.state.model_copy(update={"peak": Level.CALM})
        return self._recompute()

    def deny_family(self, evidence: str) -> RiskState:
        self._seen = {k: s for k, s in self._seen.items() if s.type != T.PRESENCE_CONFIRMED}
        return self.update(Signal(type=T.PRESENCE_DENIED, source=Source.CIRCLE, evidence=evidence))

    def update_many(self, signals: list[Signal]) -> RiskState:
        for s in signals:
            self.update(s)
        return self.state

    def _recompute(self) -> RiskState:
        # Each type counts once (repetition is not new evidence); keep the first evidence.
        first_by_type: dict[T, Signal] = {}
        for s in self._seen.values():
            first_by_type.setdefault(s.type, s)
        types = set(first_by_type)
        reasons = [Reason(type=t, points=WEIGHTS[t], evidence=s.evidence)
                   for t, s in first_by_type.items() if WEIGHTS[t] > 0]
        score = sum(r.points for r in reasons)

        if T.PRESENCE_CONFIRMED in types and T.CLAIMS_FAMILY in types and T.PRESENCE_DENIED not in types:
            # The real family member confirmed the call: family-claim pressure is not evidence of a scam.
            score = max(0, score - WEIGHTS[T.SECRECY_ISOLATION] - WEIGHTS[T.URGENCY_FEAR])

        level = _level_for(score)
        if T.USER_INITIATED_CONTEXT in types and not (types & _REQUESTS) and level == Level.CAUTION:
            level = Level.CALM

        combos = [why for need, why in CRITICAL_COMBOS if need <= types]
        if combos:
            level = Level.CRITICAL

        peak = _max(self.state.peak, level)
        # Floor: never show less than one level below the peak (and never below HIGH after CRITICAL).
        floor = _ORDER[max(0, _ORDER.index(peak) - 1)]
        if peak == Level.CRITICAL:
            floor = Level.HIGH if not combos else Level.CRITICAL
        level = _max(level, floor)

        self.state = RiskState(score=score, level=level, peak=peak, reasons=sorted(reasons, key=lambda r: -r.points),
                               combos=combos, types=sorted(types, key=lambda t: t.value))
        return self.state
