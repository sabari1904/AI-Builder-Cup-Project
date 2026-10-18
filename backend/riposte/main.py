"""Riposte API: one Cloud Run service serving the web apps, REST, SSE and audio WebSockets."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from riposte.config import settings
from riposte.controller import CallController
from riposte.guard.bank import MockBank
from riposte.signals import Signal, SignalType as T, Source
from riposte.store.hub import Member, hub
from riposte.transcribe.gemini_live import GeminiTranscriber, Segment

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("riposte")

app = FastAPI(title="Riposte")
app.add_middleware(CORSMiddleware, allow_origins=settings.allow_origins, allow_methods=["*"], allow_headers=["*"])
bank = MockBank(hub)


@app.middleware("http")
async def no_stale_ui(request, call_next):
    """Always serve the latest UI (found: browsers kept showing an old stylesheet after a redesign)."""
    resp = await call_next(request)
    path = request.url.path
    if path.startswith("/static") or not path.startswith("/api"):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp
controllers: dict[str, CallController] = {}
RESUME_GRACE_S = 30   # a dropped listener can reattach to its call within this window
_pending_stop: dict[str, asyncio.Task] = {}

WEB = Path(__file__).resolve().parents[2] / "web"
MAX_AUDIO_FRAME = 64_000  # bytes; ~2 s of 16 kHz PCM16. Larger frames are rejected (abuse guard).


def _circle(cid: str):
    try:
        return hub.circle(cid)
    except KeyError:
        raise HTTPException(404, f"unknown circle {cid}")


# ---- health & state --------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"ok": True, "calls": len(controllers), "models": {
        "transcribe": settings.transcribe_model, "analyzer": settings.analyzer_model,
        "decoy": settings.decoy_model}}


@app.post("/api/circles")
def new_circle(body: dict = Body(default={})):
    """Empty body: the one-click seeded demo (unchanged). With fields: the setup wizard's own circle,
    with no seeded members/contacts, so onboarding input is the only thing in it."""
    c = hub.new_private_demo(
        protected_name=str(body.get("protected_name", ""))[:40],
        protected_number=str(body.get("protected_number", ""))[:20],
        blank=bool(body),
    )
    return {"circle": c.id, "protected_name": c.protected_name}


@app.get("/api/circle/join")
def join_by_code(code: str):
    """Resolves a circle's 6-digit invite code, so a second family member can join without a shared link."""
    c = next((c for c in hub.circles.values() if c.invite_code == code.strip()), None)
    if not c:
        raise HTTPException(404, "no circle with that code")
    return {"circle": c.id, "protected_name": c.protected_name}


class ProfileIn(BaseModel):
    circle: str = "demo"
    protected_name: str = ""
    protected_number: str = ""


@app.post("/api/circle/profile")
def update_profile(body: ProfileIn):
    c = _circle(body.circle)
    if body.protected_name.strip():
        c.protected_name = body.protected_name.strip()[:40]
    c.protected_number = body.protected_number.strip()[:20]
    hub.publish(body.circle, {"type": "profile", "protected_name": c.protected_name, "protected_number": c.protected_number})
    return c


@app.get("/api/state")
def state(circle: str = "demo"):
    _circle(circle)
    return hub.snapshot(circle)


@app.get("/api/stream")
async def stream(circle: str = "demo"):
    _circle(circle)
    q = hub.subscribe(circle)

    async def gen():
        try:
            yield f"data: {json.dumps({'type': 'hello', 'snapshot': hub.snapshot(circle)})}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            hub.unsubscribe(circle, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- protected person's phone (simulated) -----------------------------------------------
class PayIn(BaseModel):
    circle: str = "demo"
    amount: float
    payee: str


@app.post("/api/phone/pay")
async def phone_pay(body: PayIn):
    _circle(body.circle)
    if body.amount <= 0 or body.amount > 10_000_000:
        raise HTTPException(400, "invalid amount")
    p = bank.pay(body.circle, body.amount, body.payee.strip()[:120])
    call = hub.active_call(body.circle)
    if call and call["call_id"] in controllers:
        await controllers[call["call_id"]].session.add_signal(Signal(
            type=T.PAYMENT_STARTED, source=Source.BANK, evidence=f"Payment of Rs {body.amount:,.0f} to {body.payee} started"))
    return p


class OtpIn(BaseModel):
    circle: str = "demo"
    purpose: str = "transaction"   # transaction | login | application
    amount: float | None = None


OTP_PURPOSE_TEXT = {
    "transaction": "is your OTP for a transaction of Rs {amt} at MERCHANT. Do not share it with anyone.",
    "login": "is your OTP to register a new device for net banking. Do not share it with anyone.",
    "application": "is your OTP to submit your credit card application. Do not share it with anyone.",
}


@app.post("/api/phone/otp")
async def phone_otp(body: OtpIn):
    _circle(body.circle)
    code = str(100000 + int(time.time() * 1000) % 900000)
    purpose = body.purpose if body.purpose in OTP_PURPOSE_TEXT else "transaction"
    sms = f"{code} " + OTP_PURPOSE_TEXT[purpose].format(amt=f"{body.amount or 4999:,.0f}")
    risky = hub.recent_risky_call(body.circle) or hub.active_call(body.circle)
    level = (risky or {}).get("peak", "calm")
    # graded OTP guard: what the OTP is for x how risky the call is
    if purpose in ("transaction", "login") and level in ("high", "critical"):
        guard = "block_warning"
    elif level in ("high", "critical") or (risky and purpose != "application"):
        guard = "strong_warning"
    elif risky:
        guard = "gentle_note"
    else:
        guard = "none"
    call = hub.active_call(body.circle)
    if call and call["call_id"] in controllers:
        await controllers[call["call_id"]].session.add_signal(Signal(
            type=T.OTP_RECEIVED, source=Source.DEVICE, evidence=f"An OTP for '{purpose}' arrived during the call"))
    ev = {"type": "otp", "sms": sms, "code": code, "purpose": purpose, "guard": guard, "level": level}
    hub.publish(body.circle, ev)
    return ev


class FreezeIn(BaseModel):
    circle: str = "demo"
    frozen: bool = True
    by: str = "Ramesh"


@app.post("/api/bank/freeze")
def freeze(body: FreezeIn):
    _circle(body.circle)
    bank.freeze(body.circle, body.frozen, body.by[:40])
    return {"frozen": body.frozen}


@app.post("/api/phone/cancel/{payment_id}")
def cancel_payment(payment_id: str):
    """Only the account holder (on their own phone) can cancel or release a held payment."""
    if payment_id not in hub.payments:
        raise HTTPException(404, "unknown payment")
    return bank.decide(payment_id, "cancel", hub.circle(hub.payments[payment_id].circle_id).protected_name)


class FamilyRequestIn(BaseModel):
    circle: str = "demo"
    kind: str           # stop_payment | call_me | freeze
    by: str = "Priya"
    payment_id: str | None = None


FAMILY_REQUEST_TEXT = {
    "stop_payment": "{by} thinks this is a scam and asks you to cancel the payment.",
    "call_me": "{by} asks you to hang up and call them now.",
    "freeze": "{by} asks you to freeze your cards and UPI until you talk.",
}


@app.post("/api/family/request")
def family_request(body: FamilyRequestIn):
    """A trusted contact can't act on another adult's account; they can only ask the account holder."""
    _circle(body.circle)
    if body.kind not in FAMILY_REQUEST_TEXT:
        raise HTTPException(400, "unknown request")
    ev = {"type": "family_request", "kind": body.kind, "by": body.by[:40], "payment_id": body.payment_id,
          "text": FAMILY_REQUEST_TEXT[body.kind].format(by=body.by[:40])}
    hub.publish(body.circle, ev)
    return ev


class ContactIn(BaseModel):
    circle: str = "demo"
    name: str
    number: str
    source: str = "manual"   # manual | synced (from the browser's contact picker)


@app.post("/api/circle/contacts")
def add_contact(body: ContactIn):
    from riposte.controller import normalize_number
    c = _circle(body.circle)
    num = normalize_number(body.number)
    if len(num.replace("+", "")) < 6:
        raise HTTPException(400, "invalid number")
    hub.add_contact(body.circle, body.name.strip()[:40] or num, num, source=body.source if body.source == "synced" else "manual")
    hub.publish(body.circle, {"type": "contacts", "contacts": [x.model_dump() for x in c.contacts]})
    return c.contacts


@app.delete("/api/circle/contacts")
def remove_contact(number: str, circle: str = "demo"):
    from riposte.controller import normalize_number
    c = _circle(circle)
    num = normalize_number(number)
    c.contacts = [x for x in c.contacts if x.number != num]
    hub.publish(circle, {"type": "contacts", "contacts": [x.model_dump() for x in c.contacts]})
    return c.contacts


@app.get("/api/circle/lookup")
def lookup(number: str, circle: str = "demo"):
    from riposte.controller import normalize_number
    _circle(circle)
    k = hub.contact_for(circle, normalize_number(number))
    return {"number": normalize_number(number), "known": bool(k), "name": k.name if k else ""}


@app.post("/api/phone/release/{payment_id}")
def release(payment_id: str):
    if payment_id not in hub.payments:
        raise HTTPException(404, "unknown payment")
    p = bank.release_after_cooling(payment_id)
    if p.status == "held":
        raise HTTPException(409, f"still in cooling-off for {int((p.release_after or 0) - time.time())} s")
    return p


class PresenceIn(BaseModel):
    circle: str = "demo"
    member_id: str
    on_call_with_protected: bool | None = None
    ping_id: str | None = None
    on_call: bool | None = None


@app.post("/api/family/presence")
async def family_presence(body: PresenceIn):
    c = _circle(body.circle)
    m = next((m for m in c.members if m.id == body.member_id), None)
    if not m:
        raise HTTPException(404, "unknown member")
    m.online_at = time.time()
    if body.on_call_with_protected is not None:
        changed = m.on_call_with_protected != body.on_call_with_protected
        m.on_call_with_protected = body.on_call_with_protected
        # The member's call state changed mid-call after we already checked: re-apply it.
        call = hub.active_call(body.circle)
        ctl = controllers.get(call["call_id"]) if call else None
        if changed and ctl and ctl._pinged and not body.ping_id:
            asyncio.create_task(ctl.presence_result(m.name, m.on_call_with_protected))
    if body.ping_id and body.ping_id in hub.ping_waiters:
        fut = hub.ping_waiters[body.ping_id]
        if not fut.done():
            fut.set_result(bool(body.on_call if body.on_call is not None else m.on_call_with_protected))
    return m


class MemberIn(BaseModel):
    circle: str = "demo"
    name: str
    relation: str = "family"


@app.post("/api/circle/members")
def add_member(body: MemberIn):
    c = _circle(body.circle)
    m = Member(name=body.name.strip()[:40] or "Family", relation=body.relation[:20])
    c.members.append(m)
    hub.publish(body.circle, {"type": "circle", "members": [x.model_dump() for x in c.members]})
    return m


@app.delete("/api/circle/members/{member_id}")
def remove_member(member_id: str, circle: str = "demo"):
    """Removals are delayed 24 h so a scammer can't coach a victim into cutting family out."""
    c = _circle(circle)
    m = next((m for m in c.members if m.id == member_id), None)
    if not m:
        raise HTTPException(404, "unknown member")
    m.removal_scheduled_at = time.time() + 24 * 3600
    hub.publish(circle, {"type": "circle", "members": [x.model_dump() for x in c.members],
                         "notice": f"{m.name} will be removed in 24 hours. They have been told."})
    return m


class CardAttemptIn(BaseModel):
    circle: str = "demo"
    card: str
    merchant: str


@app.post("/api/bank/card-attempt")
def card_attempt(body: CardAttemptIn):
    _circle(body.circle)
    return bank.card_attempt(body.circle, body.card, body.merchant[:80])


@app.post("/api/case/{call_id}/report")
async def case_report_endpoint(call_id: str):
    from riposte.agents.case_agent import case_report
    if call_id not in hub.calls:
        raise HTTPException(404, "unknown call")
    return await case_report(hub, call_id)


# ---- the listener: audio in, risk out ---------------------------------------------------
@app.websocket("/ws/listen")
async def ws_listen(ws: WebSocket, circle: str = Query("demo"), caller: str = Query(""),
                    mode: str = Query("audio"), resume: str = Query("")):
    await ws.accept()
    if circle not in hub.circles:
        await ws.send_json({"type": "error", "message": "unknown circle"})
        await ws.close()
        return
    resumed = resume in controllers and controllers[resume].circle_id == circle
    if resumed:
        ctl = controllers[resume]
        t = _pending_stop.pop(resume, None)
        if t:
            t.cancel()
        while not ctl.outbox.empty():   # drop events queued for the dead socket; client gets a fresh state
            ctl.outbox.get_nowait()
    else:
        ctl = CallController(hub, bank, circle, caller=caller)
        controllers[ctl.call_id] = ctl
        await ctl.start()
    await ws.send_json({"type": "call", "call_id": ctl.call_id, "caller": ctl.caller, "resumed": resumed,
                        "risk": ctl.session.engine.state.model_dump(mode="json")})

    async def on_segment(seg: Segment) -> None:
        await ctl.session.add_transcript(seg.text, seg.final)

    transcriber = None
    if mode == "audio":
        transcriber = GeminiTranscriber(on_segment)
        await transcriber.start()

    async def downlink() -> None:
        while True:
            ev = await ctl.outbox.get()
            await ws.send_text(json.dumps(ev, default=str))

    down = asyncio.create_task(downlink())
    ended = False
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                data = msg["bytes"]
                if transcriber and 0 < len(data) <= MAX_AUDIO_FRAME and len(data) % 2 == 0:
                    await transcriber.send(data)
            elif msg.get("text"):
                try:
                    j = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                if j.get("type") == "text" and j.get("text"):          # typed / scripted simulation
                    await ctl.session.add_transcript(str(j["text"])[:2000], True)
                elif j.get("type") == "status" and transcriber:
                    await ws.send_json({"type": "status", "transcriber": transcriber.status})
                elif j.get("type") == "end":
                    ended = True
                    break
    except WebSocketDisconnect:
        pass
    finally:
        down.cancel()
        if transcriber:
            await transcriber.close()
        if ended:
            await _finish(ctl.call_id)
        else:   # unexpected drop: keep the call alive briefly so the client can resume it
            _pending_stop[ctl.call_id] = asyncio.create_task(_finish_later(ctl.call_id))
        with contextlib.suppress(Exception):
            await ws.close()


async def _finish(call_id: str) -> None:
    ctl = controllers.pop(call_id, None)
    if ctl:
        await ctl.stop()


async def _finish_later(call_id: str) -> None:
    await asyncio.sleep(RESUME_GRACE_S)
    _pending_stop.pop(call_id, None)
    await _finish(call_id)


# ---- the strike: AI decoy speaks into the call -------------------------------------------
@app.websocket("/ws/decoy")
async def ws_decoy(ws: WebSocket, call_id: str = Query(...)):
    from riposte.strike.decoy import run_decoy  # lazy: keeps startup light
    await ws.accept()
    ctl = controllers.get(call_id)
    if not ctl:
        await ws.send_json({"type": "error", "message": "call not active"})
        await ws.close()
        return
    hub.calls[call_id]["decoy"] = True
    hub.publish(ctl.circle_id, {"type": "decoy", "call_id": call_id, "active": True})
    try:
        await run_decoy(ws, ctl, hub.circle(ctl.circle_id).honey)
    finally:
        hub.calls[call_id]["decoy"] = False
        hub.publish(ctl.circle_id, {"type": "decoy", "call_id": call_id, "active": False})


# ---- static web apps ----------------------------------------------------------------------
if WEB.exists():
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(WEB / "favicon.svg", media_type="image/svg+xml")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/{page}")
    def page(page: str):
        f = WEB / f"{page}.html"
        if f.exists():
            return FileResponse(f)
        raise HTTPException(404)
