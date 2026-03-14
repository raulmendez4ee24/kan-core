from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import math
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from brain.jarvis_ui_state import publish_state

logger = logging.getLogger("kan_core.voice_engine")

# ---------------------------------------------------------------------------
# ElevenLabs recommended voice IDs (set ELEVENLABS_VOICE_ID in .env)
# ---------------------------------------------------------------------------
# Adam   — pNInz6obpgDQGcFmaJgB  (deep British male — closest to Jarvis)
# Daniel — onwK4e9ZLuTAKqWW03F9  (authoritative male, US English)
# Liam   — TX3LPaxmHKxFdv7VOQHJ  (calm professional male)
# Rachel — 21m00Tcm4TlvDq8ikWAM  (clear female, multilingual — legacy default)
# ---------------------------------------------------------------------------
_JARVIS_DEFAULT_VOICE = "pNInz6obpgDQGcFmaJgB"  # Adam — deep, Jarvis-like


def _voice_provider() -> str:
    provider = str(os.getenv("VOICE_PROVIDER") or "auto").strip().lower()
    if provider in {"auto", "elevenlabs", "pyttsx3"}:
        return provider
    return "auto"


def _compact_voice_text(text: str) -> str:
    """
    Clean and trim text for TTS output.
    Removes markdown, limits to VOICE_MAX_SENTENCES and VOICE_MAX_CHARS.
    Defaults are generous so K'an sounds like a proper assistant, not a chatbot.
    """
    clean = str(text or "").strip()
    if not clean:
        return ""

    # Remove markdown artifacts that sound awkward in TTS.
    clean = re.sub(r"[*_`#>\[\]\(\)]", " ", clean)
    clean = re.sub(r"https?://\S+", "el enlace", clean)  # replace URLs with "el enlace"
    clean = re.sub(r"\s+", " ", clean).strip()

    # Scene-awareness phrasing: avoid robotic "veo una imagen".
    lowered = clean.lower()
    if "veo una imagen" in lowered or "imagen de" in lowered:
        clean = "Parece que estás trabajando en una tarea activa en tu pantalla."

    # Limits — increased from 190/2 to allow complete Jarvis-style responses.
    max_chars = int(str(os.getenv("VOICE_MAX_CHARS") or "420"))
    sentence_budget = int(str(os.getenv("VOICE_MAX_SENTENCES") or "4"))

    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", clean) if p.strip()]
    if sentence_budget > 0 and parts:
        clean = " ".join(parts[:sentence_budget]).strip()
    if len(clean) > max_chars:
        # Cut at last sentence boundary within budget.
        cutoff = clean[: max_chars - 1]
        last_end = max(cutoff.rfind("."), cutoff.rfind("!"), cutoff.rfind("?"))
        if last_end > max_chars // 2:
            clean = cutoff[: last_end + 1]
        else:
            clean = cutoff.rstrip() + "."
    return clean


def _voice_address_suffix() -> str:
    explicit = str(os.getenv("VOICE_ADDRESS_TITLE") or "").strip()
    if explicit:
        return f", {explicit}"
    mode = str(os.getenv("VOICE_ADDRESS_MODE") or "neutral").strip().lower()
    if mode == "senor":
        return ", senor"
    if mode == "jefe":
        return ", jefe"
    return ""


def _apply_voice_address_mode(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    suffix = _voice_address_suffix()
    if not suffix:
        return clean
    lowered = clean.lower()
    if ", senor" in lowered or ", señor" in lowered or ", jefe" in lowered:
        return clean
    if clean.endswith((".", "!", "?")):
        return clean[:-1].rstrip() + f"{suffix}{clean[-1]}"
    return clean + suffix


async def _play_mp3_bytes(audio_bytes: bytes) -> bool:
    if not audio_bytes:
        return False
    if os.name != "posix":
        return False

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            tmp_path = Path(tmp.name)
        proc = await asyncio.create_subprocess_exec(
            "afplay",
            str(tmp_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await proc.wait()
        return rc == 0
    except Exception as exc:
        logger.warning("voice_engine: failed to play mp3: %s", exc)
        return False
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


async def _speak_with_elevenlabs(text: str) -> bool:
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        logger.debug("ELEVENLABS_API_KEY not set — skipping ElevenLabs TTS")
        return False

    # Voice selection: use ELEVENLABS_VOICE_ID or fall back to Adam (Jarvis-like)
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", _JARVIS_DEFAULT_VOICE).strip() or _JARVIS_DEFAULT_VOICE
    model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip() or "eleven_multilingual_v2"

    # Voice quality settings — tuned for Jarvis-style delivery:
    #   stability=0.65 → consistent tone, minimal variation
    #   similarity_boost=0.82 → faithful to voice identity
    #   style=0.30 → slight expressiveness without sounding dramatic
    #   use_speaker_boost=True → cleaner, crisper output
    stability = float(os.getenv("ELEVENLABS_STABILITY", "0.65"))
    similarity = float(os.getenv("ELEVENLABS_SIMILARITY", "0.82"))
    style = float(os.getenv("ELEVENLABS_STYLE", "0.30"))
    speaker_boost = os.getenv("ELEVENLABS_SPEAKER_BOOST", "true").lower() not in {"0", "false", "no"}

    payload = {
        "text": text[:5000],
        "model_id": model_id,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity,
            "style": style,
            "use_speaker_boost": speaker_boost,
        },
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.error("ElevenLabs API error %s: %s", resp.status_code, resp.text[:200])
                return False
            audio_bytes = resp.content
    except Exception as exc:
        logger.warning("voice_engine: ElevenLabs request failed: %s", exc)
        return False

    return await _play_mp3_bytes(audio_bytes)


def _pyttsx3_speak_blocking(text: str) -> bool:
    """Fallback TTS via pyttsx3 (macOS: uses Alex/Samantha system voice)."""
    try:
        import pyttsx3  # type: ignore
    except Exception as exc:
        logger.warning("voice_engine: pyttsx3 unavailable: %s", exc)
        return False

    try:
        engine = pyttsx3.init()

        rate = os.getenv("VOICE_RATE", "185")     # 185 WPM — crisp, not rushed
        volume = os.getenv("VOICE_VOLUME", "0.95")
        voice_name = str(os.getenv("VOICE_NAME") or "").strip().lower()

        engine.setProperty("rate", int(rate))
        engine.setProperty("volume", float(volume))

        # On macOS prefer Daniel (English, deep) for Jarvis feel.
        # VOICE_NAME=daniel  or  VOICE_NAME=alex  in .env
        if not voice_name:
            voice_name = "daniel"
        if voice_name:
            for voice in list(engine.getProperty("voices") or []):
                name = str(getattr(voice, "name", "")).lower()
                if voice_name in name:
                    engine.setProperty("voice", getattr(voice, "id", ""))
                    break

        engine.say(text[:1200])
        engine.runAndWait()
        engine.stop()
        return True
    except Exception as exc:
        logger.warning("voice_engine: pyttsx3 speak failed: %s", exc)
        return False


async def _speak_with_pyttsx3(text: str) -> bool:
    return await asyncio.to_thread(_pyttsx3_speak_blocking, text)


async def _emit_speaking_levels(text: str, stop_event: asyncio.Event) -> None:
    phase = 0.0
    words = max(1, len(str(text or "").split()))
    speed = min(1.8, 0.45 + (words / 18.0))
    while not stop_event.is_set():
        phase += speed
        wave = 0.5 + (0.5 * math.sin(phase))
        level = 0.25 + (0.7 * wave)
        publish_state("speaking", source="tts", text=text[:220], level=level)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=0.08)
        except asyncio.TimeoutError:
            continue


async def speak_text(text: str) -> bool:
    """
    Speak text via the configured TTS provider.
    Priority: ElevenLabs → pyttsx3 (fallback).
    Set VOICE_PROVIDER=elevenlabs | pyttsx3 | auto  in .env
    """
    clean = _apply_voice_address_mode(_compact_voice_text(text))
    if not clean:
        return False

    publish_state("speaking", source="tts", text=clean[:220], level=0.35)
    stop_event = asyncio.Event()
    wave_task = asyncio.create_task(_emit_speaking_levels(clean, stop_event), name="jarvis-tts-wave")
    try:
        provider = _voice_provider()
        has_elevenlabs_key = bool(str(os.getenv("ELEVENLABS_API_KEY") or "").strip())

        if provider in {"auto", "elevenlabs"} and has_elevenlabs_key:
            ok = await _speak_with_elevenlabs(clean)
            if ok or provider == "elevenlabs":
                return ok

        return await _speak_with_pyttsx3(clean)
    finally:
        stop_event.set()
        with suppress(asyncio.CancelledError):
            await wave_task
        publish_state("standby", source="tts", text="", level=0.1)


# ===========================================================================
# VoiceEngine — Twilio + ElevenLabs + Deepgram outbound calling
# ===========================================================================
#
# Env vars consumed:
#   TWILIO_ACCOUNT_SID        — Twilio account identifier
#   TWILIO_AUTH_TOKEN         — Twilio auth token (HTTP Basic password)
#   TWILIO_FROM_NUMBER        — Caller ID registered in Twilio (+E.164)
#   TWILIO_TWIML_BASE_URL     — Public base URL where this app serves TwiML
#                               e.g. https://your-app.railway.app
#   ELEVENLABS_API_KEY        — ElevenLabs API key
#   ELEVENLABS_VOICE_ID       — Voice ID (default: Adam / pNInz6obpgDQGcFmaJgB)
#   ELEVENLABS_MODEL_ID       — TTS model (default: eleven_multilingual_v2)
#   DEEPGRAM_API_KEY          — Deepgram API key for transcription
# ===========================================================================


@dataclass
class CallResult:
    call_sid: str
    status: str
    to_number: str
    from_number: str
    call_token: str  # token that identifies the cached audio for TwiML serving


# Callable type aliases (used for injection in tests)
_SynthFn = Callable[[str], Awaitable[bytes]]
_DialFn = Callable[[str, str], Awaitable[dict[str, Any]]]   # (to, twiml_url) → Twilio call dict
_UpdateFn = Callable[[str, str], Awaitable[dict[str, Any]]]  # (call_sid, twiml) → Twilio resp
_TranscribeFn = Callable[[bytes], Awaitable[str]]


def _el_voice() -> str:
    return (os.getenv("ELEVENLABS_VOICE_ID") or _JARVIS_DEFAULT_VOICE).strip()


def _el_model() -> str:
    return (os.getenv("ELEVENLABS_MODEL_ID") or "eleven_multilingual_v2").strip()


async def _default_synth(text: str) -> bytes:
    """ElevenLabs TTS → raw MP3 bytes."""
    import httpx

    api_key = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{_el_voice()}"
    payload = {
        "text": text[:5000],
        "model_id": _el_model(),
        "voice_settings": {
            "stability": float(os.getenv("ELEVENLABS_STABILITY", "0.65")),
            "similarity_boost": float(os.getenv("ELEVENLABS_SIMILARITY", "0.82")),
        },
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"ElevenLabs error {resp.status_code}: {resp.text[:200]}")
        return resp.content


async def _default_dial(to: str, twiml_url: str) -> dict[str, Any]:
    """Create a Twilio outbound call pointing to *twiml_url* for instructions."""
    import httpx

    sid = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
    from_number = (os.getenv("TWILIO_FROM_NUMBER") or "").strip()
    if not sid or not token or not from_number:
        raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER not set")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            auth=(sid, token),
            data={"To": to, "From": from_number, "Url": twiml_url},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Twilio dial error {resp.status_code}: {resp.text[:200]}")
        return resp.json()


async def _default_update(call_sid: str, twiml: str) -> dict[str, Any]:
    """Update a live Twilio call with new inline TwiML (e.g. to inject audio mid-call)."""
    import httpx

    sid = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
    if not sid or not token:
        raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls/{call_sid}.json"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            auth=(sid, token),
            data={"Twiml": twiml},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Twilio update error {resp.status_code}: {resp.text[:200]}")
        return resp.json()


async def _default_transcribe(audio: bytes) -> str:
    """Deepgram batch transcription — returns the best transcript string."""
    import httpx

    api_key = (os.getenv("DEEPGRAM_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("DEEPGRAM_API_KEY not set")
    url = "https://api.deepgram.com/v1/listen"
    params = {"model": "nova-2", "smart_format": "true", "language": "es"}
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "audio/mpeg",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, params=params, headers=headers, content=audio)
        if resp.status_code != 200:
            raise RuntimeError(f"Deepgram error {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
    try:
        return str(
            data["results"]["channels"][0]["alternatives"][0]["transcript"]
        )
    except (KeyError, IndexError):
        return ""


class VoiceEngine:
    """Outbound voice call orchestrator.

    Flow for make_call:
      1. ElevenLabs converts *script* to MP3 bytes.
      2. Audio bytes are cached under a one-time token.
      3. Twilio is asked to dial *to_number*, with a TwiML URL pointing back to
         ``{TWILIO_TWIML_BASE_URL}/voice/audio/{call_token}`` — the app must
         serve that route using ``get_audio(call_token)``.

    Flow for say:
      1. ElevenLabs synthesises *text* to MP3 bytes (new token cached).
      2. Twilio's call update API injects ``<Play>`` TwiML into the live call.

    Flow for transcribe_stream:
      1. All chunks from *audio_stream* are collected.
      2. Deepgram batch API returns the transcript.
    """

    def __init__(
        self,
        *,
        synth: _SynthFn | None = None,
        dial: _DialFn | None = None,
        update: _UpdateFn | None = None,
        transcribe: _TranscribeFn | None = None,
    ) -> None:
        self._synth: _SynthFn = synth or _default_synth
        self._dial: _DialFn = dial or _default_dial
        self._update: _UpdateFn = update or _default_update
        self._transcribe: _TranscribeFn = transcribe or _default_transcribe
        self._audio_cache: dict[str, bytes] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def make_call(self, to_number: str, script: str) -> CallResult:
        """Dial *to_number* and play *script* as ElevenLabs audio.

        Returns a ``CallResult`` with the Twilio call SID and the audio token
        needed to serve TwiML from the application layer.
        """
        audio = await self._synth(script)
        call_token = secrets.token_urlsafe(16)
        self._audio_cache[call_token] = audio

        twiml_base = (os.getenv("TWILIO_TWIML_BASE_URL") or "").rstrip("/")
        twiml_url = f"{twiml_base}/voice/audio/{call_token}"

        result = await self._dial(to_number, twiml_url)

        return CallResult(
            call_sid=str(result.get("sid") or ""),
            status=str(result.get("status") or "queued"),
            to_number=to_number,
            from_number=(os.getenv("TWILIO_FROM_NUMBER") or "").strip(),
            call_token=call_token,
        )

    async def transcribe_stream(self, audio_stream: AsyncIterator[bytes]) -> str:
        """Collect all chunks from *audio_stream* and transcribe via Deepgram.

        Suitable for post-call recording transcription or short real-time
        buffers. For low-latency live transcription, use a Deepgram WebSocket
        connection directly.
        """
        chunks: list[bytes] = []
        async for chunk in audio_stream:
            if chunk:
                chunks.append(chunk)
        audio = b"".join(chunks)
        if not audio:
            return ""
        return await self._transcribe(audio)

    async def say(self, text: str, *, call_sid: str) -> bool:
        """Inject *text* as ElevenLabs audio into a live Twilio call.

        Returns True if Twilio accepted the update.
        """
        audio = await self._synth(text)
        call_token = secrets.token_urlsafe(16)
        self._audio_cache[call_token] = audio

        twiml_base = (os.getenv("TWILIO_TWIML_BASE_URL") or "").rstrip("/")
        audio_url = f"{twiml_base}/voice/audio/{call_token}"
        twiml = f"<Response><Play>{audio_url}</Play></Response>"

        await self._update(call_sid, twiml)
        return True

    def get_audio(self, call_token: str) -> bytes | None:
        """Return cached audio bytes for *call_token*, or None if not found.

        The application's ``/voice/audio/{call_token}`` route should call this
        and return the bytes as ``audio/mpeg`` so Twilio's ``<Play>`` works.
        """
        return self._audio_cache.get(call_token)


# ---------------------------------------------------------------------------
# Module-level shared engine (singleton for audio serving)
# ---------------------------------------------------------------------------

_shared_engine: VoiceEngine | None = None


def get_shared_engine() -> VoiceEngine:
    """Return (or create) the module-level VoiceEngine singleton.

    This singleton is used by the ``/voice/audio/{token}`` route so that
    audio cached during ``make_call`` / ``say`` can be served back to Twilio.
    """
    global _shared_engine
    if _shared_engine is None:
        _shared_engine = VoiceEngine()
    return _shared_engine


# ===========================================================================
# VoiceCall — autonomous call loop
# ===========================================================================

import time as _time  # module-level alias avoids shadowing the built-in

# Rejection signal keywords (Spanish + English).
_REJECTION_KEYWORDS: frozenset[str] = frozenset(
    [
        "no me interesa",
        "no gracias",
        "no quiero",
        "no necesito",
        "no estoy interesado",
        "no estoy interesada",
        "no por favor",
        "adiós",
        "hasta luego",
        "no llames",
        "no me llames",
        "not interested",
        "no thanks",
        "don't call",
        "do not call",
        "goodbye",
        "stop calling",
    ]
)

# Maps transcript substrings → CloserOperator objection_type strings.
_OBJECTION_SIGNALS: dict[str, str] = {
    "precio": "price",
    "caro": "price",
    "costoso": "price",
    "dinero": "price",
    "presupuesto": "price",
    "expensive": "price",
    "tiempo": "time",
    "ocupado": "time",
    "busy": "time",
    "ahorita no": "time",
    "confío": "trust",
    "confianza": "trust",
    "seguro": "trust",
    "comprobado": "trust",
    "referencia": "trust",
    "garantía": "trust",
    "competencia": "competition",
    "otro proveedor": "competition",
    "estoy viendo opciones": "competition",
    "comparing": "competition",
}


def _is_rejection(transcript: str) -> bool:
    low = transcript.strip().lower()
    return any(kw in low for kw in _REJECTION_KEYWORDS)


def _extract_objection(transcript: str) -> str | None:
    low = transcript.strip().lower()
    for signal, objection_type in _OBJECTION_SIGNALS.items():
        if signal in low:
            return objection_type
    return None


@dataclass
class CallOutcome:
    status: str          # "booking_confirmed" | "rejected" | "timeout" | "error"
    lead_id: str
    call_sid: str
    turns: int
    duration_seconds: float
    rejection_reason: str | None = None
    booking_id: str | None = None
    turn_log: list[dict[str, Any]] = field(default_factory=list)


# Type aliases for injectable functions
_SayFn = Callable[[str], Awaitable[None]]
_ListenFn = Callable[[], Awaitable[str]]
_TrackBookingFn = Callable[..., Awaitable[Any]]


class VoiceCall:
    """Autonomous call loop: intro → listen → closer → reply → repeat.

    The loop continues until one of:
      - ``push_booking`` action fires  → ``track_booking`` is called, status = "booking_confirmed"
      - A rejection phrase is detected → reason is logged, status = "rejected"
      - 5 minutes elapsed             → status = "timeout"
      - MAX_TURNS reached             → status = "timeout"

    For production use, wire ``_say`` and ``_listen`` to a ``VoiceEngine``
    instance and a Twilio Media Streams WebSocket respectively.  For tests,
    inject synchronous-style async callables that return preset strings.
    """

    MAX_DURATION_SECONDS: float = 300.0  # 5 minutes
    MAX_TURNS: int = 10

    def __init__(
        self,
        *,
        lead: Any,          # PipelineLead — typed as Any to avoid circular import
        call_sid: str,
        operator: Any,      # CloserOperator — typed as Any for the same reason
        say: _SayFn,
        listen: _ListenFn,
        track_booking: _TrackBookingFn | None = None,
    ) -> None:
        self._lead = lead
        self._call_sid = call_sid
        self._operator = operator
        self._say = say
        self._listen = listen
        self._track_booking = track_booking or _default_track_booking
        self._log: list[dict[str, Any]] = []  # turn log for post-call analysis

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, intro_script: str) -> CallOutcome:
        """Execute the call loop starting with *intro_script*.

        Returns a ``CallOutcome`` describing how the call ended.
        """
        start = _time.monotonic()
        turns = 0

        try:
            await self._say(intro_script)
        except Exception as exc:
            logger.error("VoiceCall.run: intro say failed: %s", exc)
            return CallOutcome(
                status="error",
                lead_id=self._lead.lead_id,
                call_sid=self._call_sid,
                turns=0,
                duration_seconds=0.0,
            )

        lead = self._lead  # mutable copy rebuilt each turn

        for turn in range(self.MAX_TURNS):
            elapsed = _time.monotonic() - start
            if elapsed >= self.MAX_DURATION_SECONDS:
                logger.info("VoiceCall: timeout after %.1fs (%d turns)", elapsed, turn)
                return CallOutcome(
                    status="timeout",
                    lead_id=lead.lead_id,
                    call_sid=self._call_sid,
                    turns=turn,
                    duration_seconds=elapsed,
                    turn_log=list(self._log),
                )

            try:
                transcript = await self._listen()
            except Exception as exc:
                logger.warning("VoiceCall: listen failed on turn %d: %s", turn, exc)
                continue

            turns = turn + 1
            transcript = (transcript or "").strip()
            self._log.append({"turn": turns, "transcript": transcript})

            if not transcript:
                logger.debug("VoiceCall: silence on turn %d, continuing", turn)
                continue

            if _is_rejection(transcript):
                logger.info("VoiceCall: rejection detected — %r", transcript[:120])
                elapsed = _time.monotonic() - start
                return CallOutcome(
                    status="rejected",
                    lead_id=lead.lead_id,
                    call_sid=self._call_sid,
                    turns=turns,
                    duration_seconds=elapsed,
                    rejection_reason=transcript,
                    turn_log=list(self._log),
                )

            # Enrich the lead with objection signal extracted from transcript
            objection = _extract_objection(transcript)
            if objection:
                # Rebuild lead with detected objection so CloserOperator can use it
                lead = lead.model_copy(update={"objection": objection})

            try:
                action = await self._operator.build_followup_action(lead)
            except Exception as exc:
                logger.error("VoiceCall: operator failed on turn %d: %s", turn, exc)
                continue

            self._log[-1]["action_type"] = action.action_type
            self._log[-1]["strategy"] = action.strategy.value if hasattr(action.strategy, "value") else str(action.strategy)

            try:
                await self._say(action.message)
            except Exception as exc:
                logger.warning("VoiceCall: say failed on turn %d: %s", turn, exc)

            if action.action_type == "push_booking":
                elapsed = _time.monotonic() - start
                booking_id = await self._do_track_booking(lead)
                return CallOutcome(
                    status="booking_confirmed",
                    lead_id=lead.lead_id,
                    call_sid=self._call_sid,
                    turns=turns,
                    duration_seconds=elapsed,
                    booking_id=booking_id,
                    turn_log=list(self._log),
                )

            # Reset objection after it's been handled so next turn starts fresh
            if objection:
                lead = lead.model_copy(update={"objection": None})

        elapsed = _time.monotonic() - start
        return CallOutcome(
            status="timeout",
            lead_id=lead.lead_id,
            call_sid=self._call_sid,
            turns=turns,
            duration_seconds=elapsed,
            turn_log=list(self._log),
        )

    @property
    def turn_log(self) -> list[dict[str, Any]]:
        """Read-only view of the per-turn transcript + action log."""
        return list(self._log)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _do_track_booking(self, lead: Any) -> str | None:
        from datetime import datetime, timezone

        try:
            record = await self._track_booking(
                lead_id=lead.lead_id,
                scheduled_at=datetime.now(timezone.utc),
                metadata={"source": "voice_call", "call_sid": self._call_sid},
            )
            return str(getattr(record, "id", "") or "")
        except Exception as exc:
            logger.error("VoiceCall: track_booking failed: %s", exc)
            return None


async def _default_track_booking(**kwargs: Any) -> Any:
    """Real implementation: delegates to attribution_engine.track_booking."""
    from brain.attribution_engine import track_booking

    return await track_booking(**kwargs)


# ===========================================================================
# High-level call launchers
# ===========================================================================
# Each launcher:
#  1. Builds a natural-language script from domain data.
#  2. Wraps VoiceCall (or a single `say`) to execute the call.
#  3. Is fully injectable for tests via say/listen callables.
# ===========================================================================


# ---------------------------------------------------------------------------
# Script builders
# ---------------------------------------------------------------------------

def build_outreach_script(opportunity: Any, offer: Any | None = None) -> str:
    """Generate a cold-call intro from a Hunter OpportunityScore (+ optional Offer)."""
    biz = str(getattr(opportunity, "business_name", "su negocio")).strip()
    vertical = str(getattr(opportunity, "vertical", "negocio")).strip()
    city = str(getattr(opportunity, "city", "la ciudad")).strip()
    reasons: list[str] = list(getattr(opportunity, "reasons", []) or [])

    pain_hint = f" Notamos que {reasons[0].lower()}." if reasons else ""

    if offer:
        pkg = str(getattr(offer, "package_name", "")).strip()
        summary = str(getattr(offer, "summary", "")).strip()
        monthly = int(getattr(offer, "monthly_price_mxn", 0))
        offer_line = (
            f" Tenemos el paquete {pkg}: {summary}, desde ${monthly:,} MXN al mes."
            if pkg and summary
            else ""
        )
    else:
        offer_line = ""

    script = (
        f"Hola, buenos días. Llamo de parte de K'an, agente de inteligencia artificial para negocios en {city}. "
        f"Le contactamos a {biz} porque trabajamos con negocios de {vertical} en la zona.{pain_hint}"
        f"{offer_line} "
        f"¿Tiene un momento para que le cuente cómo podríamos ayudarle?"
    )
    try:
        from brain.humanizer import humanize_text

        script = humanize_text(script, channel="voice")
    except Exception:
        pass
    return script


def build_followup_script(lead: Any) -> str:
    """Generate a follow-up intro for a lead that hasn't responded in 3+ days."""
    name = str(getattr(lead, "name", "")).strip() or "que nos contactó hace unos días"
    vertical = str(getattr(lead, "vertical", "negocio")).strip()
    source = str(getattr(lead, "source", "")).strip()
    source_hint = f" a través de {source}" if source and source != "unknown" else ""

    script = (
        f"Hola, buenos días. Le llamo de parte de K'an. "
        f"Hace unos días me contacté con usted{source_hint} sobre cómo apoyar a su {vertical}. "
        f"Quería dar seguimiento para ver si tiene alguna duda o si podemos avanzar. "
        f"¿Es un buen momento para hablar?"
    )
    try:
        from brain.humanizer import humanize_text

        script = humanize_text(script, channel="voice")
    except Exception:
        pass
    return script


def build_confirmation_script(booking: Any) -> str:
    """Generate an appointment-confirmation reminder for a BookingRecord."""
    lead_id = str(getattr(booking, "lead_id", "")).strip()
    scheduled_at = str(getattr(booking, "scheduled_at", "")).strip()
    meta: dict[str, Any] = dict(getattr(booking, "metadata", None) or {})
    name = str(meta.get("name") or lead_id or "cliente").strip()

    # Try to format the datetime nicely
    time_str = scheduled_at
    try:
        from datetime import datetime as _dt

        dt = _dt.fromisoformat(scheduled_at.replace("Z", "+00:00"))
        time_str = dt.strftime("%H:%M")
    except Exception:
        pass

    script = (
        f"Hola, {name}. Le llamo de parte de K'an para confirmar su cita programada para hoy a las {time_str}. "
        f"¿Todo está bien por su parte? Nos vemos en un momento."
    )
    try:
        from brain.humanizer import humanize_text

        script = humanize_text(script, channel="voice")
    except Exception:
        pass
    return script


# ---------------------------------------------------------------------------
# Call launchers
# ---------------------------------------------------------------------------

async def outreach_call(
    opportunity: Any,
    *,
    call_sid: str,
    operator: Any | None = None,
    say: _SayFn,
    listen: _ListenFn,
    offer: Any | None = None,
    track_booking: _TrackBookingFn | None = None,
) -> CallOutcome:
    """Cold-call a prospect using the Hunter opportunity as the conversation context.

    Builds a lead from the opportunity fields, generates the intro script, and
    runs a full VoiceCall loop so the agent can handle objections and book.
    """
    from brain.revenue_operators.closer import CloserOperator, PipelineLead

    lead_id = f"opp_{str(getattr(opportunity, 'business_name', 'unknown')).lower().replace(' ', '_')}"
    lead = PipelineLead(
        lead_id=lead_id,
        name=str(getattr(opportunity, "business_name", "Lead")),
        vertical=str(getattr(opportunity, "vertical", "general")),
        source="hunter_outreach",
    )

    script = build_outreach_script(opportunity, offer=offer)
    op = operator if operator is not None else CloserOperator()

    vc = VoiceCall(
        lead=lead,
        call_sid=call_sid,
        operator=op,
        say=say,
        listen=listen,
        track_booking=track_booking,
    )
    return await vc.run(script)


async def followup_call(
    lead: Any,
    *,
    call_sid: str,
    operator: Any | None = None,
    say: _SayFn,
    listen: _ListenFn,
    track_booking: _TrackBookingFn | None = None,
) -> CallOutcome:
    """Follow up a pipeline lead that has not responded in 3+ days.

    Generates a personalised re-engagement intro and runs a full VoiceCall loop.
    """
    from brain.revenue_operators.closer import CloserOperator

    script = build_followup_script(lead)
    op = operator if operator is not None else CloserOperator()

    vc = VoiceCall(
        lead=lead,
        call_sid=call_sid,
        operator=op,
        say=say,
        listen=listen,
        track_booking=track_booking,
    )
    return await vc.run(script)


async def confirmation_call(booking: Any, *, say: _SayFn) -> bool:
    """Say an appointment-confirmation reminder. No loop — single outbound message.

    Returns True if the say call completed without raising.
    """
    script = build_confirmation_script(booking)
    try:
        await say(script)
        return True
    except Exception as exc:
        logger.error("confirmation_call: say failed: %s", exc)
        return False


async def schedule_confirmation_calls(
    bookings: list[Any],
    *,
    say: _SayFn,
    window_hours: float = 2.0,
) -> list[str]:
    """Fire confirmation_call for each booking whose scheduled_at falls within *window_hours* from now.

    Returns a list of lead_ids for which a confirmation was attempted.
    ``window_hours`` defaults to 2 — call this from a scheduler every ~10 min
    so bookings are confirmed roughly 2h in advance.
    """
    from datetime import datetime, timedelta, timezone as _tz

    now = datetime.now(_tz.utc)
    cutoff_lo = now
    cutoff_hi = now + timedelta(hours=window_hours)

    confirmed: list[str] = []
    for booking in bookings:
        scheduled_at_raw = str(getattr(booking, "scheduled_at", "") or "").strip()
        if not scheduled_at_raw:
            continue
        try:
            sched = datetime.fromisoformat(scheduled_at_raw.replace("Z", "+00:00"))
            if sched.tzinfo is None:
                sched = sched.replace(tzinfo=_tz.utc)
        except Exception:
            continue
        if cutoff_lo <= sched <= cutoff_hi:
            lead_id = str(getattr(booking, "lead_id", "unknown"))
            try:
                await confirmation_call(booking, say=say)
                confirmed.append(lead_id)
            except Exception as exc:
                logger.error("schedule_confirmation_calls: failed for %s: %s", lead_id, exc)
    return confirmed
