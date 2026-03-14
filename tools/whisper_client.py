"""
Whisper STT client — transcribe audio a texto.

Descarga el archivo de audio desde Telegram Bot API y lo transcribe
usando OpenAI Whisper (whisper-1). Sin Whisper el agente sería sordo
a comandos de voz.

Env vars:
    OPENAI_API_KEY   — requerido
    WHISPER_MODEL    — default: whisper-1
    WHISPER_LANGUAGE — hint de idioma (ej: "es"), default: auto-detect
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger("kan_core.whisper_client")

_TELEGRAM_API = "https://api.telegram.org"
_OPENAI_TRANSCRIBE = "https://api.openai.com/v1/audio/transcriptions"
_NOISE_TOKENS = {
    "...",
    ".",
    "..",
    "mmm",
    "hmm",
    "uh",
    "eh",
    "silence",
    "silencio",
    "[silence]",
    "(silence)",
    "[noise]",
    "(noise)",
    "noise",
}


def _openai_key() -> Optional[str]:
    return (os.getenv("OPENAI_API_KEY") or "").strip() or None


def _normalize_transcript(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _is_noise_transcript(text: str) -> bool:
    cleaned = _normalize_transcript(text).lower()
    if not cleaned:
        return True
    if cleaned in _NOISE_TOKENS:
        return True
    alnum = re.sub(r"[^a-z0-9áéíóúñü]+", "", cleaned, flags=re.IGNORECASE)
    if not alnum:
        return True
    # 1-char outputs are almost always background artifacts.
    if len(alnum) == 1:
        return True
    return False


async def _get_telegram_file_url(file_id: str, bot_token: str) -> Optional[str]:
    """Resuelve el file_id de Telegram a una URL de descarga real."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{_TELEGRAM_API}/bot{bot_token}/getFile", params={"file_id": file_id})
            data = r.json()
            file_path = (data.get("result") or {}).get("file_path")
            if file_path:
                return f"{_TELEGRAM_API}/file/bot{bot_token}/{file_path}"
    except Exception as exc:
        logger.debug("getFile failed for %s: %s", file_id, exc)
    return None


async def _download_audio(url: str) -> Optional[bytes]:
    """Descarga el audio de Telegram."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.content
    except Exception as exc:
        logger.debug("Audio download failed: %s", exc)
    return None


async def transcribe_audio_bytes(
    audio_bytes: bytes,
    *,
    filename: str = "audio.wav",
    content_type: str = "audio/wav",
    hint_language: Optional[str] = None,
) -> Optional[str]:
    """
    Transcribe raw audio bytes using Whisper.

    This is the generic entry point for local audio (microphone, files, etc.).
    """
    api_key = _openai_key()
    if not api_key:
        logger.warning("OPENAI_API_KEY no configurada — no se puede transcribir")
        return None
    if not audio_bytes:
        return None

    model = os.getenv("WHISPER_MODEL", "whisper-1")
    language = hint_language or os.getenv("WHISPER_LANGUAGE", "")

    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            files = {
                "file": (filename, audio_bytes, content_type),
                "model": (None, model),
            }
            if language:
                files["language"] = (None, language)

            r = await c.post(
                _OPENAI_TRANSCRIBE,
                headers={"Authorization": f"Bearer {api_key}"},
                files=files,
            )
            r.raise_for_status()
            text = _normalize_transcript(r.json().get("text", ""))
            if text:
                if _is_noise_transcript(text):
                    logger.debug("Whisper transcript descartado por ruido/silencio: %s", text[:80])
                    return None
                logger.info("Whisper transcription: %s", text[:120])
                return text
    except Exception as exc:
        logger.exception("Whisper transcription failed: %s", exc)

    return None


async def transcribe_telegram_voice(
    file_id: str,
    bot_token: str,
    *,
    hint_language: Optional[str] = None,
) -> Optional[str]:
    """
    Descarga un archivo de voz de Telegram y lo transcribe con Whisper.

    Returns:
        Texto transcrito, o None si falla.
    """
    api_key = _openai_key()
    if not api_key:
        logger.warning("OPENAI_API_KEY no configurada — no se puede transcribir")
        return None

    # 1. Obtener URL real del archivo
    file_url = await _get_telegram_file_url(file_id, bot_token)
    if not file_url:
        logger.warning("No se pudo obtener URL para file_id=%s", file_id)
        return None

    # 2. Descargar audio
    audio_bytes = await _download_audio(file_url)
    if not audio_bytes:
        logger.warning("No se pudo descargar audio desde %s", file_url)
        return None

    # 3. Transcribir con Whisper (pipeline genérico)
    return await transcribe_audio_bytes(
        audio_bytes,
        filename="voice.ogg",
        content_type="audio/ogg",
        hint_language=hint_language,
    )
