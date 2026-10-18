"""All tunables in one place; model IDs live here so a retirement is a config change."""
from __future__ import annotations

import os

from pydantic import BaseModel


class Settings(BaseModel):
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    google_cloud_project: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    web_risk_api_key: str = os.getenv("WEB_RISK_API_KEY", "")

    transcribe_model: str = os.getenv("TRANSCRIBE_MODEL", "gemini-3.5-transcribe-live")
    analyzer_model: str = os.getenv("ANALYZER_MODEL", "gemini-3.6-flash")
    analyzer_fallbacks: list[str] = os.getenv(
        "ANALYZER_FALLBACKS", "gemini-flash-lite-latest,gemini-3.8-flash,gemini-3.5-flash-lite").split(",")
    analyzer_timeout_s: float = float(os.getenv("ANALYZER_TIMEOUT_S", "8"))
    analyzer_thinking: str = os.getenv("ANALYZER_THINKING", "minimal")
    decoy_model: str = os.getenv("DECOY_MODEL", "gemini-3.8-live")
    agent_model: str = os.getenv("AGENT_MODEL", "gemini-3.8-flash")
    agent_timeout_s: float = float(os.getenv("AGENT_TIMEOUT_S", "45"))

    analyzer: str = os.getenv("ANALYZER", "gemini")  # gemini | keyword
    window_seconds: int = int(os.getenv("WINDOW_SECONDS", "90"))
    transcribe_rotate_seconds: int = int(os.getenv("TRANSCRIBE_ROTATE_SECONDS", "540"))
    store: str = os.getenv("STORE", "memory")  # memory | firestore
    allow_origins: list[str] = os.getenv("ALLOW_ORIGINS", "*").split(",")


settings = Settings()
