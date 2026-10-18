"""Offline keyword analyzer: the fallback when Gemini is slow/unavailable, and the test double.

Deliberately simple and conservative. It always runs alongside the Gemini analyzer,
so a model outage degrades detection rather than disabling it.
"""
from __future__ import annotations

import re

from riposte.analyze.base import Assessment, Claims, Finding
from riposte.signals import SignalType as T

PATTERNS: list[tuple[T, str]] = [
    (T.CLAIMS_BANK, r"\b(calling from|this is|on behalf of|i am from|i'm from|from)\b[^.?!]{0,30}\b(bank|icici|hdfc|sbi|axis|kotak|card department|credit card)\b"),
    (T.CLAIMS_GOVT_POLICE, r"\b(cbi|police|customs|narcotics|ed officer|enforcement directorate|trai|cyber cell|income tax|court|rbi officer)\b"),
    (T.CLAIMS_FAMILY, r"\b(papa|mummy|mom|dad|it'?s me|beta|bhaiya|didi)\b"),
    (T.ASKS_OTP, r"\b(otp|one time password|verification code|6 digit code|code (we|you) (just )?(sent|received))\b"),
    (T.ASKS_PIN_PASSWORD, r"\b(upi pin|atm pin|mpin|password|cvv)\b"),
    (T.ASKS_CARD_DETAILS, r"\b(card number|expiry date|16 digit|cvv)\b"),
    (T.ASKS_INSTALL_APP, r"\b(install|download)\b[^.?!]{0,40}\b(app|apk|anydesk|teamviewer|quick ?support|lite)\b|\bapk\b"),
    (T.ASKS_SCREEN_SHARE, r"\b(share (your )?screen|screen ?shar|anydesk|teamviewer)\b"),
    (T.ASKS_OPEN_LINK, r"\b(click (on )?(the|this) (link|button)|open (this|the) (link|website|site)|dot (com|in|services|xyz|top|online)|www\.)"),
    (T.ASKS_PAYMENT, r"\b(pay|send|transfer)\b[^.?!]{0,30}\b(rs\.?|rupees|₹|\d{3,})|\b(fee|charges?) of\b|\b\d{1,3}(,\d{3})+\b|\brs\.? ?\d+"),
    (T.ASKS_MOVE_MONEY_SAFE, r"\b(safe account|secure account|verification account|rbi account|move (your|the) (money|funds))\b"),
    (T.URGENCY_FEAR, r"\b(immediately|right now|within (\d+|one|two) (minutes|hours)|will be (blocked|suspended|deactivated|arrested)|arrest warrant|last chance|urgent)\b"),
    (T.SECRECY_ISOLATION, r"\b(don'?t tell|do not tell|keep (this|it) (secret|confidential)|don'?t disconnect|stay on (the )?(call|line)|don'?t inform)\b"),
    (T.REFUSES_VERIFICATION, r"\b(only (through|via) (the |this )?link|not (available )?on (play ?store|app ?store)|can'?t call (you )?back|don'?t call the (branch|bank))\b"),
    (T.TOO_GOOD_TO_BE_TRUE, r"\b(guaranteed returns?|double your money|lottery|you have won|cashback of|refund of)\b"),
]
_compiled = [(t, re.compile(p, re.IGNORECASE)) for t, p in PATTERNS]


class KeywordAnalyzer:
    async def assess(self, window: str, prior: Claims) -> Assessment:
        findings = []
        for t, rx in _compiled:
            m = rx.search(window)
            if m:
                findings.append(Finding(type=t, quote=m.group(0), confidence=0.7))
        return Assessment(findings=findings, claims=prior)
