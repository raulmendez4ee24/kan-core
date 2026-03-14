import asyncio

import jarvis_listener as jl
from jarvis_listener import _normalize_text, contains_wake_word, is_scene_query_command


def test_normalize_text_removes_accents_and_quotes() -> None:
    assert _normalize_text("  K’Án  ") == "kan"


def test_contains_wake_word_detects_variants() -> None:
    assert contains_wake_word("kan abre notas")
    assert contains_wake_word("can abre notas")
    assert contains_wake_word("k'an ejecuta esto")
    assert contains_wake_word("caan abre safari")
    assert contains_wake_word("khan abre terminal")
    assert not contains_wake_word("oye kan, estas ahi?")
    assert not contains_wake_word("hola sistema")


def test_scene_query_command_detects_spanish_variants() -> None:
    assert is_scene_query_command("K'an, ¿qué estoy haciendo?")
    assert is_scene_query_command("que estoy haciendo")
    assert is_scene_query_command("what am i doing right now?")
    assert not is_scene_query_command("ejecuta el flujo de ventas")


def test_scene_query_timeout_returns_voice_fallback(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_SCENE_TOTAL_TIMEOUT", "0.5")
    monkeypatch.setenv("JARVIS_SCENE_CAPTURE_TIMEOUT", "0.1")
    monkeypatch.setattr(
        jl,
        "take_screenshot",
        lambda _max_width: {"mime_type": "image/png", "image_base64": "ZmFrZQ=="},
    )

    async def slow_vision(*_args, **_kwargs):
        await asyncio.sleep(1.0)
        return "x"

    monkeypatch.setattr(jl, "_describe_scene_with_vision", slow_vision)
    msg = asyncio.run(jl._handle_scene_query())
    assert "motor de visión es lenta" in msg


def test_scene_query_forces_gc_collect(monkeypatch) -> None:
    gc_calls = {"count": 0}
    monkeypatch.setattr(
        jl,
        "take_screenshot",
        lambda _max_width: {"mime_type": "image/png", "image_base64": "ZmFrZQ=="},
    )

    async def ok_vision(*_args, **_kwargs):
        return "escena ok"

    def fake_collect() -> int:
        gc_calls["count"] += 1
        return 0

    monkeypatch.setattr(jl, "_describe_scene_with_vision", ok_vision)
    monkeypatch.setattr(jl.gc, "collect", fake_collect)
    msg = asyncio.run(jl._handle_scene_query())
    assert msg == "escena ok"
    assert gc_calls["count"] == 1


def test_open_app_intent_detection() -> None:
    assert jl._looks_like_open_app_intent("abre safari")
    assert jl._looks_like_open_app_intent("open terminal")
    assert not jl._looks_like_open_app_intent("consulta estado de ventas")
