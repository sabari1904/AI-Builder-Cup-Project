import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for evals/

from evals.scripts import ALL, ICICI_REAL  # noqa: E402
from riposte.analyze.base import Assessment, Claims, Finding, verify_findings  # noqa: E402
from riposte.analyze.rules import KeywordAnalyzer  # noqa: E402
from riposte.intel.entities import extract  # noqa: E402
from riposte.linkguard.guard import LinkGuard, domains_in, lookalike_brand  # noqa: E402
from riposte.risk.engine import Level, RiskEngine  # noqa: E402
from riposte.session import CallSession  # noqa: E402
from riposte.signals import Signal, SignalType as T, Source  # noqa: E402

ORDER = ["calm", "caution", "high", "critical"]


async def run_script(script) -> CallSession:
    s = CallSession(analyzers=[KeywordAnalyzer()], linkguard=LinkGuard(online=False))
    for line in script["lines"]:
        await s.add_transcript(line)
        await s.analyze_now()
    # let link-guard tasks finish
    import asyncio
    await asyncio.sleep(0.05)
    return s


@pytest.mark.asyncio
@pytest.mark.parametrize("script", ALL, ids=lambda s: s["name"])
async def test_scripts_reach_expected_level(script):
    s = await run_script(script)
    level = s.engine.state.level.value
    if "expect" in script:
        assert ORDER.index(level) >= ORDER.index(script["expect"]), s.engine.state
    if "max" in script:
        assert ORDER.index(level) <= ORDER.index(script["max"]), s.engine.state


@pytest.mark.asyncio
async def test_icici_flags_lookalike_domain():
    s = await run_script(ICICI_REAL)
    types = set(s.engine.state.types)
    assert T.LINK_LOOKALIKE in types
    assert any("bank" in c.lower() for c in s.engine.state.combos)


def test_spoken_domain_parsing():
    assert "icicicards.services" in domains_in("open this website icici cards dot services on your phone")
    assert lookalike_brand("icicicards.services") == "icici"
    assert lookalike_brand("icicibank.com") is None
    assert lookalike_brand("m.icicibank.com") is None
    assert lookalike_brand("hdfc-kyc-update.top") == "hdfc"
    assert lookalike_brand("google.com") is None


def test_quote_verification_drops_hallucinations_and_injection():
    window = "Sir I am from HDFC bank, kindly tell me the OTP you just received"
    a = Assessment(findings=[
        Finding(type=T.ASKS_OTP, quote="tell me the OTP you just received"),
        Finding(type=T.ASKS_INSTALL_APP, quote="install anydesk now"),          # not in transcript
        Finding(type=T.LINK_MALICIOUS, quote="HDFC bank"),                      # hard-fact type: forbidden
    ])
    sigs = verify_findings(a, window)
    assert [s.type for s in sigs] == [T.ASKS_OTP]


def test_engine_floor_never_drops_after_critical():
    e = RiskEngine()
    e.update(Signal(type=T.CLAIMS_BANK, source=Source.ANALYZER, evidence="from ICICI"))
    e.update(Signal(type=T.ASKS_OTP, source=Source.ANALYZER, evidence="share the OTP"))
    assert e.state.level == Level.CRITICAL
    e.update(Signal(type=T.USER_INITIATED_CONTEXT, source=Source.ANALYZER, evidence="you asked"))
    assert e.state.level == Level.CRITICAL


def test_presence_denied_is_critical_for_family_claim():
    e = RiskEngine()
    e.update(Signal(type=T.CLAIMS_FAMILY, source=Source.ANALYZER, evidence="papa it's me"))
    e.update(Signal(type=T.PRESENCE_DENIED, source=Source.CIRCLE, evidence="Priya's phone: not on a call"))
    assert e.state.level == Level.CRITICAL


def test_low_confidence_ignored_and_duplicates_count_once():
    e = RiskEngine()
    e.update(Signal(type=T.ASKS_OTP, source=Source.ANALYZER, evidence="otp", confidence=0.2))
    assert e.state.score == 0
    for q in ["send the otp", "send the otp", "the otp please"]:
        e.update(Signal(type=T.ASKS_OTP, source=Source.ANALYZER, evidence=q))
    assert e.state.score == 6


def test_entity_extraction_spoken():
    ents = {(e.kind, e.value) for e in extract(
        "send to priya.help at the rate ybl, account nine eight seven six five four three two one zero one two, IFSC SBIN0001234")}
    assert ("upi", "priya.help@ybl") in ents
    assert ("ifsc", "SBIN0001234") in ents
    assert ("account", "987654321012") in ents
