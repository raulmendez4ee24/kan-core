"""ElevenLabs Text-to-Speech client.

Converts text to audio (MP3) using the ElevenLabs API v1.

Required env vars:
    ELEVENLABS_API_KEY       — API key from elevenlabs.io
    ELEVENLABS_VOICE_ID      — Voice ID (default: Rachel = 21m00Tcm4TlvDq8ikWAM)
    ELEVENLABS_MODEL_ID      — TTS model (default: eleven_multilingual_v2)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("kan_core.elevenlabs_client")

_API_BASE = "https://api.elevenlabs.io/v1"
_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # Rachel — clear, multilingual
_DEFAULT_MODEL = "eleven_multilingual_v2"
_TIMEOUT = 30.0


async def text_to_speech(
    text: str,
    *,
    voice_id: Optional[str] = None,
    model_id: Optional[str] = None,
) -> Optional[bytes]:
    """Convert text to MP3 audio bytes.

    Returns raw MP3 bytes on success, None on failure.
    """
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        logger.warning("ELEVENLABS_API_KEY not set — TTS skipped")
        return None

    voice = voice_id or os.getenv("ELEVENLABS_VOICE_ID", _DEFAULT_VOICE)
    model = model_id or os.getenv("ELEVENLABS_MODEL_ID", _DEFAULT_MODEL)
    url = f"{_API_BASE}/text-to-speech/{voice}"

    payload = {
        "text": text[:5000],  # ElevenLabs limit
        "model_id": model,
        "voice_settings": {
            "stability": float(os.getenv("ELEVENLABS_STABILITY", "0.50")),
            "similarity_boost": float(os.getenv("ELEVENLABS_SIMILARITY", "0.75")),
        },
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.error(
                    "ElevenLabs API error %s: %s", resp.status_code, resp.text[:200]
                )
                return None
            return resp.content
    except Exception as exc:
        logger.exception("ElevenLabs TTS failed: %s", exc)
        return None


async def list_voices() -> list:
    """Return available voices from ElevenLabs."""
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        return []
    url = f"{_API_BASE}/voices"
    headers = {"xi-api-key": api_key}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json().get("voices", [])
    except Exception as exc:
        logger.exception("Failed to list ElevenLabs voices: %s", exc)
        return []
