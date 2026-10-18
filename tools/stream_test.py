"""End-to-end test: stream a WAV in real time through transcribe -> analyze -> risk.

Prints when each transcript segment and each risk change happens, measured from
the start of the audio, so we can see detection latency.

usage: python tools/stream_test.py evals/audio/icici_card_protection.wav [--fast]
"""
from __future__ import annotations

import asyncio
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from riposte.analyze.rules import KeywordAnalyzer  # noqa: E402
from riposte.analyze.resilient import ResilientAnalyzer  # noqa: E402
from riposte.config import settings  # noqa: E402
from riposte.linkguard.guard import LinkGuard  # noqa: E402
from riposte.session import CallSession  # noqa: E402
from riposte.transcribe.gemini_live import GeminiTranscriber, Segment  # noqa: E402


async def main(path: str, fast: bool) -> None:
    t0 = time.time()
    session = CallSession(
        analyzers=[KeywordAnalyzer(),
                   ResilientAnalyzer([settings.analyzer_model, *settings.analyzer_fallbacks],
                                     timeout_s=settings.analyzer_timeout_s)],
        linkguard=LinkGuard())
    last_level = {"v": "calm"}

    async def on_event(e: dict) -> None:
        ts = time.time() - t0
        if e["type"] == "transcript" and e["final"]:
            print(f"[{ts:5.1f}s] TEXT  {e['text']}")
        elif e["type"] == "risk":
            lvl = e["risk"]["level"]
            new = [s["type"] for s in e["new_signals"]]
            if new or lvl != last_level["v"]:
                print(f"[{ts:5.1f}s] RISK  {lvl.upper():8s} score={e['risk']['score']:2d} +{new}")
                last_level["v"] = lvl
        elif e["type"] == "entity":
            print(f"[{ts:5.1f}s] INTEL {e['entity']['kind']}={e['entity']['value']}")

    session.subscribe(on_event)
    session.start()

    async def on_segment(seg: Segment) -> None:
        await session.add_transcript(seg.text, seg.final)

    tr = GeminiTranscriber(on_segment)
    await tr.start()
    with wave.open(path, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        chunk = 1600  # 100 ms
        while True:
            data = w.readframes(chunk)
            if not data:
                break
            await tr.send(data)
            await asyncio.sleep(0 if fast else 0.1)
    await asyncio.sleep(4)
    await tr.close()
    await asyncio.sleep(3)
    await session.close()
    st = session.engine.state
    print(f"\nFINAL level={st.level.value} score={st.score} combos={st.combos}")
    print("reasons:", [(r.type.value, r.evidence[:50]) for r in st.reasons])
    print(f"transcriber status={tr.status} errors={tr.errors}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], "--fast" in sys.argv))
