"""Maps a RiskState to concrete actions. Pure and table-driven so banks can tune it."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from riposte.risk.engine import Level, RiskState


class ActionType(str, Enum):
    SHOW_CARD = "show_card"                # silent on-screen coaching
    COVER_OTP = "cover_otp"                # OTP guard on the protected phone
    ARM_PAYMENT_HOLD = "arm_payment_hold"  # next payment is held
    ALERT_FAMILY_SILENT = "alert_family_silent"
    ALERT_FAMILY_APPROVAL = "alert_family_urgent"   # urgent alert to the trusted contact
    BANK_RISK_CONTEXT = "bank_risk_context"
    OFFER_STRIKE = "offer_strike"
    OFFER_KILL_SWITCH = "offer_kill_switch"


class Action(BaseModel):
    type: ActionType
    params: dict = {}


TABLE: dict[Level, list[ActionType]] = {
    Level.CALM: [],
    Level.CAUTION: [ActionType.SHOW_CARD],
    Level.HIGH: [ActionType.SHOW_CARD, ActionType.COVER_OTP, ActionType.ARM_PAYMENT_HOLD,
                 ActionType.ALERT_FAMILY_SILENT, ActionType.BANK_RISK_CONTEXT],
    Level.CRITICAL: [ActionType.SHOW_CARD, ActionType.COVER_OTP, ActionType.ARM_PAYMENT_HOLD,
                     ActionType.ALERT_FAMILY_APPROVAL, ActionType.BANK_RISK_CONTEXT,
                     ActionType.OFFER_STRIKE, ActionType.OFFER_KILL_SWITCH],
}

# How a held payment may be released. Only the ACCOUNT HOLDER can cancel or release (after the
# cooling-off); a family member can only be alerted and send requests (no legal authority over
# another adult's account without a mandate/POA). Critical holds never auto-release on timeout.
RELEASE_RULES = {
    Level.HIGH: {"cooling_minutes": 10},
    Level.CRITICAL: {"cooling_minutes": 60},
}
CIRCLE_CHANGE_DELAY_HOURS = 24


class Policy:
    def __init__(self, table: dict[Level, list[ActionType]] | None = None) -> None:
        self.table = table or TABLE

    def actions(self, state: RiskState) -> list[Action]:
        rel = RELEASE_RULES.get(state.level, {})
        out = []
        for a in self.table[state.level]:
            params = {"level": state.level.value}
            if a == ActionType.ARM_PAYMENT_HOLD:
                params.update(rel)
            out.append(Action(type=a, params=params))
        return out
