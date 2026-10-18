"""Streaming transcription with Gemini Transcribe Live.

Interface (any transcriber): feed PCM16 16 kHz mono chunks with `send()`, receive
Segment(text, final) via the `on_segment` callback. The session is rotated before
the 10-minute cap; audio is buffered during reconnects so nothing is lost.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from google import genai
from google.genai import types

from riposte.config import settings

log = logging.getLogger(__name__)

SCAM_VOCAB = ["OTP", "UPI", "UPI PIN", "KYC", "CVV", "AnyDesk", "TeamViewer", "QuickSupport", "APK",
              "iMobile", "ICICI", "HDFC", "SBI", "Axis", "Kotak", "Paytm", "PhonePe", "Google Pay", "BHIM",
              "CBI", "ED", "TRAI", "RBI", "Aadhaar", "PAN card", "digital arrest", "customs", "FedEx",
              "dot com", "dot in", "dot services", "at the rate", "safe account", "IFSC", "net banking"]


@dataclass
class Segment:
    text: str
    final: bool


SegmentCallback = Callable[[Segment], Awaitable[None]]


class GeminiTranscriber:
    def __init__(self, on_segment: SegmentCallback, model: str | None = None,
                 rotate_seconds: int | None = None) -> None:
        self.on_segment = on_segment
        self.model = model or settings.transcribe_model
        self.rotate_seconds = rotate_seconds or settings.transcribe_rotate_seconds
        self.client = genai.Client(api_key=settings.gemini_api_key) if settings.gemini_api_key else genai.Client()
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=600)  # ~60 s of 100 ms chunks
        self._task: asyncio.Task | None = None
        self._closed = False
        self.status = "idle"
        self.errors = 0

    def _config(self) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(
                custom_vocabulary=SCAM_VOCAB),
        )

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def send(self, pcm16: bytes) -> None:
        if self._closed:
            return
        try:
            self._q.put_nowait(pcm16)
        except asyncio.QueueFull:  # drop oldest audio rather than block the socket
            with contextlib.suppress(asyncio.QueueEmpty):
                self._q.get_nowait()
            self._q.put_nowait(pcm16)

    async def close(self) -> None:
        self._closed = True
        await self._q.put(None)
        if self._task:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._task, timeout=5)

    async def _run(self) -> None:
        backoff = 0.5
        while not self._closed:
            try:
                await self._one_session()
                backoff = 0.5
            except Exception as e:
                self.errors += 1
                self.status = f"reconnecting ({type(e).__name__})"
                log.warning("transcribe session error: %r", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 8)

    async def _one_session(self) -> None:
        async with self.client.aio.live.connect(model=self.model, config=self._config()) as session:
            self.status = "listening"
            started = time.time()
            stop = asyncio.Event()

            async def pump() -> None:
                while not stop.is_set():
                    if time.time() - started > self.rotate_seconds:
                        stop.set()  # rotate before the server-side cap
                        return
                    try:
                        chunk = await asyncio.wait_for(self._q.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if chunk is None:
                        self._closed = True
                        with contextlib.suppress(Exception):
                            await session.send_realtime_input(audio_stream_end=True)
                        stop.set()
                        return
                    await session.send_realtime_input(
                        audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))

            async def handle(msg) -> None:
                sc = msg.server_content
                if sc is not None:
                    interim = getattr(sc, "interim_input_transcription", None)
                    if interim is not None and interim.text:
                        await self.on_segment(Segment(interim.text, final=False))
                    if sc.input_transcription is not None and sc.input_transcription.text:
                        await self.on_segment(Segment(sc.input_transcription.text, final=True))
                if msg.go_away is not None:
                    stop.set()

            async def recv() -> None:
                # The SDK's receive() iterator ends at turn boundaries, so keep re-entering it.
                while not stop.is_set():
                    async for msg in session.receive():
                        await handle(msg)
                        if stop.is_set():
                            return

            pump_t = asyncio.create_task(pump())
            recv_t = asyncio.create_task(recv())
            await stop.wait()
            # give the server a moment to flush the last transcript
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(recv_t, timeout=2.0)
            for t in (pump_t, recv_t):
                t.cancel()
            self.status = "rotating" if not self._closed else "closed"
