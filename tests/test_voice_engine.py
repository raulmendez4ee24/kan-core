from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

import brain.voice_engine as voice_engine
from brain.voice_engine import (
    CallResult,
    VoiceEngine,
    _default_synth,
    _select_best_spanish_voice,
    resolve_elevenlabs_voice_id,
)


# ---------------------------------------------------------------------------
# Fake implementations — no real network calls
# ---------------------------------------------------------------------------

_FAKE_AUDIO = b"\xff\xfb\x90\x00" * 64  # minimal fake MP3 header bytes
_FAKE_CALL_SID = "CA_fake_sid_001"
_FAKE_TRANSCRIPT = "Estoy interesado pero necesito más información"


async def _synth_ok(text: str) -> bytes:  # noqa: ARG001
    return _FAKE_AUDIO


async def _synth_fail(text: str) -> bytes:  # noqa: ARG001
    raise RuntimeError("ElevenLabs unavailable")


async def _dial_ok(to: str, twiml_url: str) -> dict:  # noqa: ARG001
    return {"sid": _FAKE_CALL_SID, "status": "queued"}


async def _update_ok(call_sid: str, twiml: str) -> dict:  # noqa: ARG001
    return {"sid": call_sid, "status": "in-progress"}


async def _transcribe_ok(audio: bytes) -> str:  # noqa: ARG001
    return _FAKE_TRANSCRIPT


def _engine(**overrides) -> VoiceEngine:
    defaults = dict(
        synth=_synth_ok,
        dial=_dial_ok,
        update=_update_ok,
        transcribe=_transcribe_ok,
    )
    defaults.update(overrides)
    return VoiceEngine(**defaults)


async def _async_chunks(data: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in data:
        yield chunk


# ---------------------------------------------------------------------------
# ElevenLabs voice resolution
# ---------------------------------------------------------------------------


def test_select_best_spanish_voice_prefers_mexican_professional() -> None:
    voices = [
        {
            "voice_id": "voice_latam_social",
            "name": "Mario",
            "labels": {
                "language": "es",
                "accent": "latin american",
                "use_case": "conversational",
                "descriptive": "excited",
            },
            "category": "professional",
            "description": "Animated Spanish voice.",
            "preview_url": "https://cdn.example/mario.mp3",
        },
        {
            "voice_id": "voice_mx_pro",
            "name": "El Marino Narrador",
            "labels": {
                "language": "es",
                "accent": "mexican",
                "use_case": "narrative_story",
                "descriptive": "professional",
            },
            "category": "professional",
            "description": "Podcast host calm and professional.",
            "preview_url": "https://cdn.example/marino.mp3",
        },
        {
            "voice_id": "voice_en",
            "name": "Roger",
            "labels": {"language": "en", "accent": "american"},
            "category": "premade",
            "description": "English voice.",
        },
    ]

    assert _select_best_spanish_voice(voices) == "voice_mx_pro"


def test_resolve_elevenlabs_voice_id_prefers_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice_env")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "key_123")
    monkeypatch.setattr(voice_engine, "_AUTO_SELECTED_VOICE_ID", None)

    async def _run() -> None:
        assert await resolve_elevenlabs_voice_id() == "voice_env"

    asyncio.run(_run())


def test_resolve_elevenlabs_voice_id_auto_selects_spanish_voice(monkeypatch) -> None:
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "key_123")
    monkeypatch.setattr(voice_engine, "_AUTO_SELECTED_VOICE_ID", None)

    async def _fake_fetch(api_key: str) -> list[dict]:
        assert api_key == "key_123"
        return [
            {
                "voice_id": "voice_en",
                "name": "Roger",
                "labels": {"language": "en", "accent": "american"},
                "category": "premade",
                "description": "English voice.",
            },
            {
                "voice_id": "voice_es",
                "name": "El Marino Narrador",
                "labels": {
                    "language": "es",
                    "accent": "mexican",
                    "use_case": "narrative_story",
                    "descriptive": "professional",
                },
                "category": "professional",
                "description": "Podcast host calm and professional.",
                "preview_url": "https://cdn.example/es.mp3",
            },
        ]

    monkeypatch.setattr(voice_engine, "_fetch_elevenlabs_voices", _fake_fetch)

    async def _run() -> None:
        assert await resolve_elevenlabs_voice_id() == "voice_es"
        assert voice_engine._AUTO_SELECTED_VOICE_ID == "voice_es"

    asyncio.run(_run())


def test_default_synth_uses_resolved_elevenlabs_voice(monkeypatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "key_123")
    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")

    async def _fake_resolve(*, api_key: str | None = None) -> str:
        assert api_key == "key_123"
        return "voice_spanish"

    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        content = b"fake_mp3"
        text = ""

    class _FakeClient:
        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def post(self, url: str, *, json: dict, headers: dict) -> _FakeResponse:
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResponse()

    monkeypatch.setattr(voice_engine, "resolve_elevenlabs_voice_id", _fake_resolve)
    monkeypatch.setattr("httpx.AsyncClient", lambda timeout=30.0: _FakeClient())

    async def _run() -> None:
        audio = await _default_synth("Hola")
        assert audio == b"fake_mp3"

    asyncio.run(_run())
    assert captured["url"] == "https://api.elevenlabs.io/v1/text-to-speech/voice_spanish"
    assert captured["json"]["model_id"] == "eleven_multilingual_v2"
    assert captured["json"]["language_code"] == "es"


# ---------------------------------------------------------------------------
# make_call
# ---------------------------------------------------------------------------


def test_make_call_returns_call_result(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15005550006")
    monkeypatch.setenv("TWILIO_TWIML_BASE_URL", "https://example.railway.app")

    async def _run() -> None:
        result = await _engine().make_call("+521234567890", "Hola, soy K'an.")
        assert isinstance(result, CallResult)
        assert result.call_sid == _FAKE_CALL_SID
        assert result.status == "queued"
        assert result.to_number == "+521234567890"
        assert result.from_number == "+15005550006"
        assert result.call_token  # non-empty token

    asyncio.run(_run())


def test_make_call_caches_audio_for_twiml_serving(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15005550006")
    monkeypatch.setenv("TWILIO_TWIML_BASE_URL", "https://example.railway.app")

    async def _run() -> None:
        eng = _engine()
        result = await eng.make_call("+521234567890", "Test script")
        cached = eng.get_audio(result.call_token)
        assert cached == _FAKE_AUDIO

    asyncio.run(_run())


def test_make_call_twiml_url_contains_token(monkeypatch) -> None:
    """The URL passed to Twilio must include the call token."""
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15005550006")
    monkeypatch.setenv("TWILIO_TWIML_BASE_URL", "https://example.railway.app")

    dialed_urls: list[str] = []

    async def _capture_dial(to: str, twiml_url: str) -> dict:
        dialed_urls.append(twiml_url)
        return {"sid": _FAKE_CALL_SID, "status": "queued"}

    async def _run() -> None:
        eng = _engine(dial=_capture_dial)
        result = await eng.make_call("+521234567890", "Script")
        assert len(dialed_urls) == 1
        url = dialed_urls[0]
        assert url.startswith("https://example.railway.app/voice/audio/")
        assert result.call_token in url

    asyncio.run(_run())


def test_make_call_unique_tokens_per_call(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+1")
    monkeypatch.setenv("TWILIO_TWIML_BASE_URL", "https://x.example")

    async def _run() -> None:
        eng = _engine()
        r1 = await eng.make_call("+1", "A")
        r2 = await eng.make_call("+1", "B")
        assert r1.call_token != r2.call_token

    asyncio.run(_run())


def test_make_call_propagates_synth_error(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+1")
    monkeypatch.setenv("TWILIO_TWIML_BASE_URL", "https://x.example")

    async def _run() -> None:
        eng = _engine(synth=_synth_fail)
        with pytest.raises(RuntimeError, match="ElevenLabs"):
            await eng.make_call("+1", "Script")

    asyncio.run(_run())


def test_make_call_dial_receives_to_number(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+1")
    monkeypatch.setenv("TWILIO_TWIML_BASE_URL", "https://x.example")

    called_with: list[str] = []

    async def _capture(to: str, url: str) -> dict:
        called_with.append(to)
        return {"sid": "CA1", "status": "queued"}

    async def _run() -> None:
        await _engine(dial=_capture).make_call("+529991112233", "Hola")
        assert called_with == ["+529991112233"]

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# transcribe_stream
# ---------------------------------------------------------------------------


def test_transcribe_stream_joins_chunks() -> None:
    received: list[bytes] = []

    async def _capture_transcribe(audio: bytes) -> str:
        received.append(audio)
        return _FAKE_TRANSCRIPT

    async def _run() -> None:
        eng = _engine(transcribe=_capture_transcribe)
        chunks = [b"chunk1", b"chunk2", b"chunk3"]
        result = await eng.transcribe_stream(_async_chunks(chunks))
        assert result == _FAKE_TRANSCRIPT
        assert len(received) == 1
        assert received[0] == b"chunk1chunk2chunk3"

    asyncio.run(_run())


def test_transcribe_stream_returns_empty_for_no_audio() -> None:
    async def _run() -> None:
        eng = _engine()
        result = await eng.transcribe_stream(_async_chunks([]))
        assert result == ""

    asyncio.run(_run())


def test_transcribe_stream_skips_empty_chunks() -> None:
    received: list[bytes] = []

    async def _capture(audio: bytes) -> str:
        received.append(audio)
        return "ok"

    async def _run() -> None:
        eng = _engine(transcribe=_capture)
        result = await eng.transcribe_stream(_async_chunks([b"", b"real", b""]))
        assert result == "ok"
        assert received[0] == b"real"

    asyncio.run(_run())


def test_transcribe_stream_returns_transcript_text() -> None:
    async def _run() -> None:
        eng = _engine()
        result = await eng.transcribe_stream(_async_chunks([b"audio_data"]))
        assert result == _FAKE_TRANSCRIPT

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# say
# ---------------------------------------------------------------------------


def test_say_returns_true_on_success(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_TWIML_BASE_URL", "https://example.railway.app")

    async def _run() -> None:
        eng = _engine()
        ok = await eng.say("Buenas tardes.", call_sid=_FAKE_CALL_SID)
        assert ok is True

    asyncio.run(_run())


def test_say_updates_correct_call_sid(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_TWIML_BASE_URL", "https://x.example")

    updated_sids: list[str] = []

    async def _capture_update(call_sid: str, twiml: str) -> dict:
        updated_sids.append(call_sid)
        return {}

    async def _run() -> None:
        eng = _engine(update=_capture_update)
        await eng.say("Hola", call_sid="CA_target_sid")
        assert updated_sids == ["CA_target_sid"]

    asyncio.run(_run())


def test_say_twiml_contains_play_tag(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_TWIML_BASE_URL", "https://x.example")

    twiml_sent: list[str] = []

    async def _capture_update(call_sid: str, twiml: str) -> dict:
        twiml_sent.append(twiml)
        return {}

    async def _run() -> None:
        eng = _engine(update=_capture_update)
        await eng.say("Texto", call_sid="CA1")
        assert len(twiml_sent) == 1
        assert "<Play>" in twiml_sent[0]
        assert "</Play>" in twiml_sent[0]
        assert "https://x.example/voice/audio/" in twiml_sent[0]

    asyncio.run(_run())


def test_say_caches_audio_under_new_token(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_TWIML_BASE_URL", "https://x.example")

    twiml_sent: list[str] = []

    async def _capture_update(call_sid: str, twiml: str) -> dict:
        twiml_sent.append(twiml)
        return {}

    async def _run() -> None:
        eng = _engine(update=_capture_update)
        await eng.say("Texto", call_sid="CA1")
        # Extract token from the TwiML URL
        url = twiml_sent[0]
        token = url.split("/voice/audio/")[1].split("<")[0].strip()
        assert eng.get_audio(token) == _FAKE_AUDIO

    asyncio.run(_run())


def test_say_propagates_synth_error(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_TWIML_BASE_URL", "https://x.example")

    async def _run() -> None:
        eng = _engine(synth=_synth_fail)
        with pytest.raises(RuntimeError, match="ElevenLabs"):
            await eng.say("Text", call_sid="CA1")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# get_audio
# ---------------------------------------------------------------------------


def test_get_audio_returns_none_for_unknown_token() -> None:
    eng = _engine()
    assert eng.get_audio("no_such_token") is None


def test_get_audio_returns_bytes_after_make_call(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+1")
    monkeypatch.setenv("TWILIO_TWIML_BASE_URL", "https://x.example")

    async def _run() -> None:
        eng = _engine()
        result = await eng.make_call("+1", "Script")
        assert eng.get_audio(result.call_token) == _FAKE_AUDIO

    asyncio.run(_run())
