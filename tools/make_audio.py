"""Synthesize test calls as audio (Gemini TTS) and degrade them to sound like a
phone on speaker: 8 kHz telephone band, room noise, lower level. Output: 16 kHz
mono PCM16 WAV in evals/audio/<name>.wav

usage: python tools/make_audio.py [script_name ...]
"""
from __future__ import annotations

import os
import sys
import wave
from pathlib import Path

import numpy as np
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.scripts import ALL  # noqa: E402

OUT = ROOT / "evals" / "audio"
TTS_MODELS = ["gemini-2.5-flash-preview-tts", "gemini-3.1-flash-tts-preview", "gemini-3.8-flash-tts"]


def tts(client: genai.Client, text: str, voice: str = "Kore") -> np.ndarray:
    last = None
    for m in TTS_MODELS:
        try:
            r = client.models.generate_content(
                model=m, contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)))))
            pcm = r.candidates[0].content.parts[0].inline_data.data  # 24 kHz s16le
            return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
        except Exception as e:
            last = e
    raise RuntimeError(f"TTS failed: {last}")


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    n = int(len(x) * dst / src)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def phone_on_speaker(x24: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    tel = resample(x24, 24000, 8000)             # telephone band limit
    tel = np.clip(tel * 1.6, -0.9, 0.9)          # loud, slightly compressed speaker
    x = resample(tel, 8000, 16000)
    x = np.convolve(x, np.array([0.6, 0.25, 0.1, 0.05]), mode="same")  # small room smear
    x = x * 0.5 + rng.normal(0, 0.01, len(x)).astype(np.float32)        # distance + room noise
    return x


def main(names: list[str]) -> None:
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    for s in ALL:
        if names and s["name"] not in names:
            continue
        parts = []
        for line in s["lines"]:
            parts.append(phone_on_speaker(tts(client, line), rng))
            parts.append(rng.normal(0, 0.01, 16000 * 1).astype(np.float32))  # 1 s pause
        audio = np.concatenate(parts)
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        path = OUT / f"{s['name']}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm.tobytes())
        print(f"{path.relative_to(ROOT)}  {len(pcm)/16000:.1f}s")


if __name__ == "__main__":
    main(sys.argv[1:])
