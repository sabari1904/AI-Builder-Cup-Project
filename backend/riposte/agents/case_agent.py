"""Case agent (Google ADK): turns a finished call into an action plan + complaint draft.

The agent works only from tool results (facts the system recorded), so it can't
invent evidence. If the model is unavailable, `fallback_report` builds the same
structure deterministically: the victim always gets next steps.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from riposte.config import settings
from riposte.linkguard.guard import lookalike_brand, registrable
from riposte.store.hub import Hub

log = logging.getLogger(__name__)

INSTRUCTION = """You are Riposte's case agent. A protected person just had a suspicious phone call.
Use the tools to get the facts, then write a short, calm report in simple English for an elderly
person and their family. Use ONLY facts returned by tools; never invent numbers, names or links.

Format (markdown):
## What happened
2-3 sentences.
## Evidence
Bullets, each quoting the caller's words or a verified fact.
## Your money
One line: was any payment held, stopped, sent or none.
## Do this now
Numbered steps, most urgent first (from reporting_channels).
## Complaint draft
A short paragraph the person can paste into cybercrime.gov.in, including date, caller number,
what was claimed, what was asked, and any UPI IDs/accounts/websites the caller gave.
"""


def make_tools(hub: Hub):
    def get_case(call_id: str) -> dict:
        """Get the recorded facts of a call: caller, risk level, evidence, claims, payment details the
        caller gave, payments made during or after it, and the last part of the transcript."""
        c = hub.calls.get(call_id)
        if not c:
            return {"error": "unknown call"}
        intel = [i.model_dump() for i in hub.intel if i.call_id == call_id]
        pays = [p.model_dump() for p in hub.payments.values() if p.call_id == call_id]
        return {
            "date": time.strftime("%d %b %Y %H:%M", time.localtime(c["started_at"])),
            "caller_number": c.get("caller") or "unknown", "risk_level": c.get("peak"),
            "scam_patterns": c.get("combos", []),
            "evidence": [{"signal": r["type"], "quote_or_fact": r["evidence"]} for r in c.get("reasons", [])],
            "caller_claims": c.get("claims", {}),
            "payment_details_caller_gave": [{"type": i["kind"], "value": i["value"]} for i in intel],
            "payments": [{"amount": p["amount"], "payee": p["payee"], "status": p["status"]} for p in pays],
            "transcript_tail": c.get("transcript", [])[-15:],
        }

    def check_website(domain: str) -> dict:
        """Check whether a website pretends to be a bank or brand."""
        brand = lookalike_brand(domain)
        return {"domain": registrable(domain), "impersonates": brand,
                "verdict": f"FAKE: uses '{brand}' but is not its official site" if brand else "no brand impersonation found"}

    def reporting_channels(money_lost: bool, scam_type: str) -> dict:
        """Official Indian channels to report a scam, ordered by urgency."""
        steps = []
        if money_lost:
            steps.append("Call 1930 (National Cyber Crime Helpline) immediately. The first hour matters most for freezing the money.")
            steps.append("Call your bank's 24x7 fraud line (number on the back of your card) and ask them to block the card/UPI and raise a dispute.")
        steps.append("File a complaint at https://cybercrime.gov.in (National Cyber Crime Reporting Portal).")
        steps.append("Report the caller's number on Sanchar Saathi -> Chakshu: https://sancharsaathi.gov.in")
        if "bank" in scam_type.lower() or "card" in scam_type.lower():
            steps.append("Do not open any link or install any app the caller mentioned. If you did, uninstall it and change your net-banking password from a different phone.")
        steps.append("Tell your family. Scammers often call back with a new story.")
        return {"steps": steps}

    return get_case, check_website, reporting_channels


def fallback_report(hub: Hub, call_id: str) -> str:
    get_case, check_website, reporting_channels = make_tools(hub)
    f = get_case(call_id)
    if "error" in f:
        return "No such call."
    lost = any(p["status"] in ("completed", "released") for p in f["payments"])
    steps = reporting_channels(lost, " ".join(f["scam_patterns"]) + json.dumps(f["caller_claims"]))["steps"]
    ev = "\n".join(f"- {e['quote_or_fact']}" for e in f["evidence"]) or "- (none recorded)"
    pays = "; ".join(f"Rs {p['amount']:,.0f} to {p['payee']}: {p['status']}" for p in f["payments"]) or "No payment was made."
    gave = ", ".join(f"{i['type']} {i['value']}" for i in f["payment_details_caller_gave"]) or "none"
    claimed = f["caller_claims"].get("claimed_identity") or next(
        (f"'{e['quote_or_fact']}'" for e in f["evidence"] if e["signal"].startswith("CLAIMS_")), "an organisation")
    return (f"## What happened\nOn {f['date']} a caller from {f['caller_number']} claimed to be {claimed}. "
            f"Riposte rated the call {f['risk_level'].upper()}.\n\n## Evidence\n{ev}\n\n## Your money\n{pays}\n\n"
            "## Do this now\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps)) +
            f"\n\n## Complaint draft\nOn {f['date']} I received a call from {f['caller_number']}. The caller claimed to be "
            f"{claimed}. {' '.join(f['scam_patterns'])}. Details the caller gave: {gave}. I request action against this number "
            "and the accounts/websites mentioned.")


def call_record(hub: Hub, call_id: str) -> dict:
    """The factual record of the call, attached to every report (what the user reviews and submits)."""
    c = hub.calls.get(call_id, {})
    return {
        "call_id": call_id,
        "started": time.strftime("%d %b %Y, %H:%M:%S", time.localtime(c.get("started_at", time.time()))),
        "ended": time.strftime("%d %b %Y, %H:%M:%S", time.localtime(c["ended_at"])) if c.get("ended_at") else "",
        "caller": c.get("caller") or "unknown", "caller_name": c.get("caller_name", ""),
        "risk": c.get("peak", "calm"), "patterns": c.get("combos", []),
        "evidence": c.get("reasons", []),
        "details_given": [{"type": i.kind, "value": i.value} for i in hub.intel if i.call_id == call_id],
        "payments": [{"id": p.id, "amount": p.amount, "payee": p.payee, "status": p.status, "reason": p.reason}
                     for p in hub.payments.values() if p.call_id == call_id],
        "transcript": c.get("transcript", []),
    }


async def case_report(hub: Hub, call_id: str, timeout_s: float | None = None) -> dict:
    out = await _case_report(hub, call_id, timeout_s)
    out["record"] = call_record(hub, call_id)
    return out


async def _case_report(hub: Hub, call_id: str, timeout_s: float | None = None) -> dict:
    timeout_s = timeout_s or settings.agent_timeout_s
    try:
        from google.adk.agents import LlmAgent
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        agent = LlmAgent(name="riposte_case_agent", model=settings.agent_model,
                         instruction=INSTRUCTION, tools=list(make_tools(hub)))
        runner = InMemoryRunner(agent=agent, app_name="riposte")
        session = await runner.session_service.create_session(app_name="riposte", user_id="case")
        msg = types.Content(role="user", parts=[types.Part(text=f"Write the case report for call_id={call_id}.")])

        async def run() -> tuple[str, list[str]]:
            text, tools_used = "", []
            async for ev in runner.run_async(user_id="case", session_id=session.id, new_message=msg):
                for fc in ev.get_function_calls() or []:
                    tools_used.append(fc.name)
                if ev.is_final_response() and ev.content and ev.content.parts:
                    text = "".join(p.text or "" for p in ev.content.parts)
            return text, tools_used

        text, tools_used = await asyncio.wait_for(run(), timeout=timeout_s)
        if text.strip():
            return {"report": text, "by": f"ADK agent ({settings.agent_model})", "tools_used": tools_used}
    except Exception as e:
        log.warning("case agent failed, using fallback: %s", f"{type(e).__name__}: {str(e)[:160]}")
    return {"report": fallback_report(hub, call_id), "by": "rules (AI unavailable)", "tools_used": []}
