"""The one data type every module speaks: a Signal.

Analyzers, the link guard, device events and the circle all emit Signals.
The risk engine is the only thing that turns Signals into a decision.
"""
from __future__ import annotations

import time
from enum import Enum

from pydantic import BaseModel, Field


class SignalType(str, Enum):
    # who the caller claims to be
    CLAIMS_BANK = "CLAIMS_BANK"
    CLAIMS_GOVT_POLICE = "CLAIMS_GOVT_POLICE"
    CLAIMS_FAMILY = "CLAIMS_FAMILY"
    CLAIMS_COMPANY = "CLAIMS_COMPANY"  # courier, telecom, tech support, employer
    # what the caller asks for
    ASKS_OTP = "ASKS_OTP"
    ASKS_PIN_PASSWORD = "ASKS_PIN_PASSWORD"
    ASKS_CARD_DETAILS = "ASKS_CARD_DETAILS"
    ASKS_INSTALL_APP = "ASKS_INSTALL_APP"
    ASKS_SCREEN_SHARE = "ASKS_SCREEN_SHARE"
    ASKS_OPEN_LINK = "ASKS_OPEN_LINK"
    ASKS_PAYMENT = "ASKS_PAYMENT"
    ASKS_MOVE_MONEY_SAFE = "ASKS_MOVE_MONEY_SAFE"  # "safe account", "verification account"
    # how the caller pressures
    URGENCY_FEAR = "URGENCY_FEAR"
    SECRECY_ISOLATION = "SECRECY_ISOLATION"
    REFUSES_VERIFICATION = "REFUSES_VERIFICATION"
    TOO_GOOD_TO_BE_TRUE = "TOO_GOOD_TO_BE_TRUE"
    # hard, verifiable facts
    CALLER_UNKNOWN = "CALLER_UNKNOWN"
    CALLER_NOT_1600 = "CALLER_NOT_1600"
    LINK_LOOKALIKE = "LINK_LOOKALIKE"
    LINK_MALICIOUS = "LINK_MALICIOUS"
    DOMAIN_NEW = "DOMAIN_NEW"
    PRESENCE_DENIED = "PRESENCE_DENIED"
    PRESENCE_CONFIRMED = "PRESENCE_CONFIRMED"
    OTP_RECEIVED = "OTP_RECEIVED"
    PAYMENT_STARTED = "PAYMENT_STARTED"
    # benign context (lowers the "caution" band only, never below the floor)
    USER_INITIATED_CONTEXT = "USER_INITIATED_CONTEXT"


class Source(str, Enum):
    ANALYZER = "analyzer"
    RULES = "rules"
    LINKGUARD = "linkguard"
    DEVICE = "device"
    CIRCLE = "circle"
    BANK = "bank"


class Signal(BaseModel):
    type: SignalType
    source: Source
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    evidence: str = Field(..., min_length=1, description="Verbatim quote or verifiable fact")
    detail: dict = Field(default_factory=dict)
    at: float = Field(default_factory=time.time)

    def key(self) -> str:
        """Identity for de-duplication: one signal type per distinct evidence."""
        return f"{self.type.value}|{self.evidence.strip().lower()[:80]}"
