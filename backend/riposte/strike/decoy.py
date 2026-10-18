"""The Strike: a Gemini Live decoy that talks to the scammer through the speaker.

Safety design:
  - The decoy has NO access to the user's real data, only planted fake details
    (honey tokens) that the bank watches as tripwires.
  - Everything the scammer says is fed to the same CallSession, so payment
    details are extracted and validated by the normal intel pipeline.
  - Echo: the browser pauses mic upload while decoy audio plays (half-duplex)
    and uses echo cancellation, so the decoy doesn't hear itself.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import WebSocket
from google import genai
from google.genai import types

from riposte.config import settings
from riposte.store.hub import HoneyTokens

log = logging.getLogger(__name__)


def persona(h: HoneyTokens) -> str:
    return f"""You are playing {h.name}, a 64-year-old retired school teacher from Pune, on a phone
call with someone who is almost certainly a scammer. You are an intelligence gatherer, not a
time-waster: every turn should move toward getting one more detail.

YOUR CHECKLIST (ask for these, one at a time, in a natural, slightly confused way):
 1. Their full name ("Sorry beta, what is your good name?")
 2. Their employee ID / branch / department ("Which branch are you calling from? Your ID?")
 3. A number to call them back on ("Give me your number, my grandson will call you back")
 4. WHERE to send money: UPI ID or bank account + IFSC ("Tell me the UPI ID slowly, I am writing")
 5. Any website or app they want you to open ("Spell the website name for me")
After each detail, READ IT BACK slowly, digit by digit or letter by letter, so it is captured clearly.

Rules:
- Short sentences, warm, a little slow. Indian English, occasional Hindi words.
- If they ask for card details, OTP or PIN, stall once, then give ONLY these planted values, slowly:
  card {h.card_number}, expiry {h.card_expiry}, CVV {h.card_cvv}, OTP {h.otp}, UPI {h.upi_id},
  account {h.account}. Never invent or reveal any other personal information.
- Never really install anything; say it is "loading". Never pay.
- Never reveal you are an AI or that you suspect a scam. Never threaten or insult them.
- When you receive a message in brackets starting with "(WRAP UP", politely end the call:
  say your grandson has just come home and will call them back, thank them, say goodbye. Then stop.
Start by saying "Hello? Hello, sorry, my grandson said someone was calling... who is this?"
"""


WRAP_UP_AFTER_S = 240          # stop after 4 minutes even if not everything was captured
WRAP_UP_AFTER_DETAILS = 2      # ...or once this many payment details (UPI / account / IFSC / site) are captured


async def run_decoy(ws: WebSocket, ctl, honey: HoneyTokens) -> None:
    client = genai.Client(api_key=settings.gemini_api_key or None)
    cfg = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=persona(honey),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon"))),
    )
    try:
        async with client.aio.live.connect(model=settings.decoy_model, config=cfg) as live:
            await ws.send_json({"type": "decoy_ready"})
            await live.send_realtime_input(text="(The call has been handed to you. Begin.)")

            async def uplink() -> None:
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    if msg.get("bytes"):
                        await live.send_realtime_input(
                            audio=types.Blob(data=msg["bytes"], mime_type="audio/pcm;rate=16000"))
                    elif msg.get("text"):
                        j = json.loads(msg["text"])
                        if j.get("type") == "end":
                            return
                        if j.get("type") == "text":   # typed scammer line (simulation)
                            await live.send_realtime_input(text=str(j["text"])[:1000])
                            await ctl.session.add_transcript(f"[caller] {j['text']}", True)

            async def downlink() -> None:
                scammer_buf: list[str] = []
                decoy_buf: list[str] = []

                async def handle(r) -> None:
                    if r.data:
                        await ws.send_bytes(r.data)   # 24 kHz PCM16 for the browser to play
                    sc = r.server_content
                    if not sc:
                        return
                    if sc.input_transcription and sc.input_transcription.text:
                        scammer_buf.append(sc.input_transcription.text)
                    if sc.output_transcription and sc.output_transcription.text:
                        decoy_buf.append(sc.output_transcription.text)
                    if sc.interrupted:
                        await ws.send_json({"type": "interrupted"})
                    if sc.turn_complete:
                        if scammer_buf:
                            said = "".join(scammer_buf).strip()
                            scammer_buf.clear()
                            await ws.send_json({"type": "decoy_heard", "text": said})
                            await ctl.session.add_transcript(said, True)   # intel extraction
                        if decoy_buf:
                            said = "".join(decoy_buf).strip()
                            decoy_buf.clear()
                            await ws.send_json({"type": "decoy_said", "text": said})
                        await ws.send_json({"type": "turn_complete"})

                while True:  # the SDK's receive() ends after each turn; keep listening across turns
                    async for r in live.receive():
                        await handle(r)

            async def wrap_up_watch() -> None:
                started = asyncio.get_running_loop().time()
                kinds = {"upi", "account", "ifsc", "domain", "phone"}
                while True:
                    await asyncio.sleep(2)
                    got = [e for e in ctl.session.entities.values() if e.kind in kinds]
                    await ws.send_json({"type": "decoy_progress", "captured": [
                        {"kind": e.kind, "value": e.value} for e in got],
                        "claims": ctl.session.claims.model_dump()})
                    elapsed = asyncio.get_running_loop().time() - started
                    pay = [e for e in got if e.kind in ("upi", "account", "ifsc", "domain")]
                    if len(pay) >= WRAP_UP_AFTER_DETAILS or elapsed > WRAP_UP_AFTER_S:
                        await live.send_realtime_input(text="(WRAP UP NOW: you have what we need. End the call politely.)")
                        await ws.send_json({"type": "decoy_done", "reason":
                                            "Details captured" if pay else "Time limit reached",
                                            "captured": [{"kind": e.kind, "value": e.value} for e in got]})
                        return

            watch = asyncio.create_task(wrap_up_watch())
            up = asyncio.create_task(uplink())
            down = asyncio.create_task(downlink())
            done, pending = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
            for t in (*pending, watch):
                t.cancel()
            for t in done:
                if t.exception():
                    raise t.exception()  # type: ignore[misc]
    except Exception as e:
        log.warning("decoy ended: %r", e)
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "error", "message": f"Decoy unavailable: {type(e).__name__}"})
    finally:
        with contextlib.suppress(Exception):
            await ws.close()
