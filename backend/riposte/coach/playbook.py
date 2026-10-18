"""Silent on-screen coaching. Deterministic cards keyed by signal; Gemini's tip is shown as an extra line.

Ordered by priority: the first matching card is shown. Every card answers:
what is happening, what to say, what to do.
"""
from __future__ import annotations

from riposte.signals import SignalType as T

CARDS: list[tuple[set[T], dict]] = [
    ({T.PRESENCE_DENIED}, {
        "title": "This is NOT your family member",
        "why": "Their own phone says they are not on a call with you.",
        "say": "\"I'll call you back on your number.\"",
        "do": "Hang up. Call them on their saved number."}),
    ({T.ASKS_MOVE_MONEY_SAFE}, {
        "title": "No real 'safe account' exists",
        "why": "Police, RBI and banks never ask you to move money to keep it safe.",
        "say": "\"I will visit the police station in person.\"",
        "do": "Hang up. Call 1930 (cyber crime helpline)."}),
    ({T.CLAIMS_GOVT_POLICE}, {
        "title": "Police don't arrest on phone or video",
        "why": "'Digital arrest' does not exist in Indian law.",
        "say": "\"Send me a written notice. I'll come to the station.\"",
        "do": "Hang up. Don't stay on the call."}),
    ({T.ASKS_OTP}, {
        "title": "Never share an OTP",
        "why": "Anyone with this code can take money or log into your account. Banks never ask for it.",
        "say": "\"I don't share OTPs. I'll call the bank myself.\"",
        "do": "Don't read out any code."}),
    ({T.ASKS_PIN_PASSWORD}, {
        "title": "Never share your PIN or CVV",
        "why": "No bank employee will ever ask for it.",
        "say": "\"No.\"",
        "do": "Hang up."}),
    ({T.ASKS_INSTALL_APP}, {
        "title": "Don't install any app they mention",
        "why": "Apps from links can read your SMS and OTPs and take over your phone.",
        "say": "\"I only install apps from Play Store.\"",
        "do": "Don't tap any link."}),
    ({T.ASKS_SCREEN_SHARE}, {
        "title": "Never share your screen",
        "why": "They will watch you type your PIN and OTP.",
        "say": "\"No screen sharing.\"",
        "do": "Close AnyDesk/TeamViewer if opened."}),
    ({T.LINK_LOOKALIKE}, {
        "title": "That website is fake",
        "why": "It uses the bank's name but is not the bank's official website.",
        "say": "\"I'll go to the branch instead.\"",
        "do": "Don't open the link."}),
    ({T.CLAIMS_FAMILY, T.ASKS_PAYMENT}, {
        "title": "Check it's really them",
        "why": "Voices can be cloned from 3 seconds of audio.",
        "say": "Ask: \"What's our family word?\" or something only they know.",
        "do": "We're checking their phone now."}),
    ({T.SECRECY_ISOLATION}, {
        "title": "'Don't tell anyone' is a scam sign",
        "why": "Scammers isolate you so nobody can warn you.",
        "say": "\"I'm putting my daughter on the line.\"",
        "do": "Tell a family member now."}),
    ({T.CLAIMS_BANK, T.ASKS_PAYMENT}, {
        "title": "Banks don't sell over a call like this",
        "why": "A 'bank' asking for money on an unknown number is a common scam.",
        "say": "\"What's your employee ID? I'll call the number on my card.\"",
        "do": "Call the number on the back of your card."}),
    ({T.CALLER_NOT_1600}, {
        "title": "Real bank calls come from 1600 numbers",
        "why": "Since 2026, banks must call from numbers starting with 1600.",
        "say": "\"I'll call you back on the official number.\"",
        "do": "Check the number on your screen."}),
    ({T.CLAIMS_BANK}, {
        "title": "Is this really your bank?",
        "why": "Verify before you share anything.",
        "say": "\"What's your employee ID and branch?\"",
        "do": "Don't share any details yet."}),
    ({T.URGENCY_FEAR}, {
        "title": "Slow down",
        "why": "Pressure and fear are how scams work.",
        "say": "\"I need time. I'll call back.\"",
        "do": "Take a breath. Nothing is that urgent."}),
]


def card_for(types: set[T], tip: str = "") -> dict | None:
    for need, card in CARDS:
        if need <= types:
            return {**card, "tip": tip}
    return None
