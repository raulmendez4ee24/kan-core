from __future__ import annotations

import asyncio
from pathlib import Path

from brain.asset_manager import AssetManager, READY_PROMPTS, build_image_prompt
from brain.brand_director import ContentPost


def _post() -> ContentPost:
    return ContentPost(
        post_id="asset-post-1",
        day_index=0,
        platform="instagram",
        pillar="oferta",
        vertical="clinics",
        format="static",
        topic="Automatiza tu negocio",
        hook="Tus leads se enfrían mientras tardas en responder.",
        full_script="Script",
        visual_direction="Visual",
        caption="Caption",
        hashtags=["#kanlogic"],
        best_posting_time="09:00",
        cta="Escríbeme y lo armamos.",
    )


def test_upload_image_returns_cloudinary_url(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo-cloud")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "demo-key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "demo-secret")

    image_path = tmp_path / "demo.jpg"
    image_path.write_bytes(b"fake-jpeg")

    seen: dict[str, object] = {}

    async def _request(method: str, url: str, data: dict[str, object], files: dict[str, object]):
        seen["method"] = method
        seen["url"] = url
        seen["data"] = data
        seen["files"] = files
        return {"secure_url": "https://res.cloudinary.com/demo-cloud/image/upload/demo.jpg"}

    manager = AssetManager(requester=_request)

    result = asyncio.run(manager.upload_image(str(image_path)))

    assert result == "https://res.cloudinary.com/demo-cloud/image/upload/demo.jpg"
    assert seen["method"] == "POST"
    assert "demo-cloud" in str(seen["url"])
    assert "signature" in dict(seen["data"])


def test_generate_post_image_creates_local_jpeg_and_uploads(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    generated_path: dict[str, str] = {}

    async def _fake_upload(path: str) -> str:
        generated_path["path"] = path
        assert Path(path).exists()
        assert path.endswith(".jpg")
        return "https://res.cloudinary.com/demo-cloud/image/upload/generated.jpg"

    manager = AssetManager()
    monkeypatch.setattr(manager, "upload_image", _fake_upload)

    async def _fake_gemini(**kwargs) -> Path:
        output = kwargs["output"]
        output.write_bytes(b"gemini-jpeg")
        return output

    monkeypatch.setattr(manager, "_generate_with_gemini", _fake_gemini)

    post = _post()
    result = asyncio.run(
        manager.generate_post_image(
            topic=post.topic,
            hook_text=post.hook,
            style_preset="premium",
            format="square",
            vertical=post.vertical,
            content_type="offer_launch",
            include_logo=True,
            asset_id=post.post_id,
        )
    )

    assert result == "https://res.cloudinary.com/demo-cloud/image/upload/generated.jpg"
    assert generated_path["path"].endswith("asset-post-1.jpg")


def test_generate_post_image_falls_back_to_pillow_when_gemini_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    generated_path: dict[str, str] = {}

    async def _fake_upload(path: str) -> str:
        generated_path["path"] = path
        assert Path(path).exists()
        assert Path(path).stat().st_size > 0
        return "https://res.cloudinary.com/demo-cloud/image/upload/fallback.jpg"

    manager = AssetManager()
    monkeypatch.setattr(manager, "upload_image", _fake_upload)

    async def _boom(**kwargs) -> Path:
        raise RuntimeError("gemini unavailable")

    monkeypatch.setattr(manager, "_generate_with_gemini", _boom)

    post = _post()
    result = asyncio.run(
        manager.generate_post_image(
            topic=post.topic,
            hook_text=post.hook,
            style_preset="premium",
            format="square",
            vertical=post.vertical,
            content_type="offer_launch",
            include_logo=True,
            asset_id=post.post_id,
        )
    )

    assert result == "https://res.cloudinary.com/demo-cloud/image/upload/fallback.jpg"
    assert generated_path["path"].endswith("asset-post-1.jpg")


def test_gemini_model_uses_env_override_and_default(monkeypatch) -> None:
    manager = AssetManager()
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert manager._gemini_image_model() == "models/gemini-3-pro-image-preview"

    monkeypatch.setenv("GEMINI_MODEL", "models/imagen-4.0-generate-001")
    assert manager._gemini_image_model() == "models/imagen-4.0-generate-001"


def test_build_image_prompt_uses_brand_rules_and_style_preset() -> None:
    prompt = build_image_prompt(
        topic="Automatiza tu recepción",
        hook_text="Tus pacientes esperan demasiado para recibir respuesta.",
        style_preset="premium",
        format="square",
        vertical="clinics",
        content_type="booking_push",
        include_logo=True,
    )
    assert "Automatiza tu recepción" in prompt
    assert "Tus pacientes esperan demasiado" in prompt
    assert "Vertical: clinics" in prompt
    assert "Style preset: premium" in prompt
    assert "leave clean space for text overlay, no text in the image" in prompt
    assert "#1a1a2e" in prompt
    assert "#e94560" in prompt


def test_get_ready_prompt_returns_known_prompt() -> None:
    manager = AssetManager()
    prompt = manager.get_ready_prompt("case_study")
    assert prompt == READY_PROMPTS["case_study"]


def test_get_ready_prompt_raises_for_unknown_key() -> None:
    manager = AssetManager()
    try:
        manager.get_ready_prompt("does-not-exist")
    except KeyError as exc:
        assert "Unknown prompt_key" in str(exc)
    else:
        raise AssertionError("get_ready_prompt should fail for unknown keys")


def test_ready_prompts_contains_expected_common_post_types() -> None:
    assert len(READY_PROMPTS) == 12
    for key in ["lead_capture", "offer_launch", "testimonial", "workflow_upgrade", "whatsapp_conversion"]:
        assert key in READY_PROMPTS


def test_font_cache_paths_use_tmp_directory() -> None:
    manager = AssetManager()
    sora_path = manager._font_cache_path("Sora-Bold.ttf")
    mono_path = manager._font_cache_path("JetBrains-Mono-Regular.ttf")
    assert str(sora_path).endswith("/Sora-Bold.ttf")
    assert str(mono_path).endswith("/JetBrains-Mono-Regular.ttf")
