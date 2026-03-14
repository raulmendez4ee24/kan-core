"""Voice endpoints — Text-to-Speech via ElevenLabs + Twilio outbound calls.

POST /voice/speak      — Convert text to MP3 audio
POST /voice/chat       — Generate AI reply and speak it
GET  /voice/voices     — List available ElevenLabs voices
POST /voice/call       — Manually trigger an outbound call (for testing)
GET  /voice/audio/{token} — Serve cached audio to Twilio TwiML
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from brain.core import CognitiveEngine
from observability import capture_exception
from security import require_client_token
from tools.elevenlabs_client import list_voices, text_to_speech

logger = logging.getLogger("kan_core.routes.voice")

router = APIRouter(prefix="/voice", tags=["voice"])


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    voice_id: Optional[str] = Field(default=None, max_length=64)
    model_id: Optional[str] = Field(default=None, max_length=64)


class VoiceChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str = Field(default="voice-default", min_length=1, max_length=128)
    voice_id: Optional[str] = Field(default=None, max_length=64)


@router.post("/speak")
async def voice_speak(
    req: SpeakRequest,
    client_id: str = Depends(require_client_token),
):
    """Convert arbitrary text to MP3 audio.

    Returns raw MP3 bytes with Content-Type: audio/mpeg.
    """
    audio = await text_to_speech(
        req.text,
        voice_id=req.voice_id,
        model_id=req.model_id,
    )
    if audio is None:
        raise HTTPException(
            status_code=502,
            detail="TTS failed. Check ELEVENLABS_API_KEY and voice configuration.",
        )
    return Response(content=audio, media_type="audio/mpeg")


@router.post("/chat")
async def voice_chat(
    req: VoiceChatRequest,
    client_id: str = Depends(require_client_token),
):
    """Send a message to the AI and receive an MP3 audio reply.

    Internally calls CognitiveEngine then converts the text response to audio.
    Returns raw MP3 bytes.
    """
    try:
        engine = CognitiveEngine.get_instance()
        result = await engine.generate_response(
            client_id=client_id,
            session_id=req.session_id,
            message=req.message,
        )
    except Exception as exc:
        capture_exception(exc, {"source": "voice_chat", "client_id": client_id})
        raise HTTPException(status_code=502, detail="AI engine failed") from exc

    reply_text = str(result.get("content") or "").strip()
    if not reply_text:
        raise HTTPException(status_code=204, detail="No text content to speak")

    audio = await text_to_speech(reply_text, voice_id=req.voice_id)
    if audio is None:
        raise HTTPException(status_code=502, detail="TTS conversion failed")

    return Response(content=audio, media_type="audio/mpeg")


@router.get("/voices")
async def voice_list(client_id: str = Depends(require_client_token)):
    """List all available ElevenLabs voices for this API key."""
    voices = await list_voices()
    return {"voices": voices, "count": len(voices)}


class VoiceCallRequest(BaseModel):
    to_number: str = Field(..., min_length=7, max_length=20, description="E.164 phone number")
    script: str = Field(..., min_length=1, max_length=5000, description="Intro script to speak")


@router.post("/call")
async def voice_call(
    req: VoiceCallRequest,
    client_id: str = Depends(require_client_token),
):
    """Trigger an outbound Twilio call with ElevenLabs TTS audio.

    Returns the Twilio call SID and status. Useful for manual testing.
    Requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER,
    and TWILIO_TWIML_BASE_URL to be set.
    """
    try:
        from brain.voice_engine import get_shared_engine

        result = await get_shared_engine().make_call(req.to_number, req.script)
    except Exception as exc:
        capture_exception(exc, {"source": "voice_call", "client_id": client_id})
        raise HTTPException(status_code=502, detail=f"Call failed: {exc}") from exc

    return {
        "call_sid": result.call_sid,
        "status": result.status,
        "to_number": result.to_number,
        "from_number": result.from_number,
    }


@router.get("/audio/{token}")
async def voice_audio(token: str):
    """Serve cached TTS audio to Twilio's <Play> tag (no auth — Twilio fetches this).

    Returns MP3 bytes for the given one-time token. Returns 404 if unknown.
    """
    from brain.voice_engine import get_shared_engine

    audio = get_shared_engine().get_audio(token)
    if audio is None:
        raise HTTPException(status_code=404, detail="Audio token not found or expired.")
    return Response(content=audio, media_type="audio/mpeg")
