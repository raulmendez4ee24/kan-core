from __future__ import annotations

import asyncio
from pathlib import Path

from brain.asset_manager import AssetManager
from brain.brand_director import ContentPost


def _post() -> ContentPost:
    return ContentPost(
        post_id="asset-post-1",
        day_index=0,
        platform="instagram",
        pillar="oferta",
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

    async def _fake_gemini(post: ContentPost, output: Path) -> Path:
        output.write_bytes(b"gemini-jpeg")
        return output

    monkeypatch.setattr(manager, "_generate_with_gemini", _fake_gemini)

    result = asyncio.run(manager.generate_post_image(_post()))

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

    async def _boom(post: ContentPost, output: Path) -> Path:
        raise RuntimeError("gemini unavailable")

    monkeypatch.setattr(manager, "_generate_with_gemini", _boom)

    result = asyncio.run(manager.generate_post_image(_post()))

    assert result == "https://res.cloudinary.com/demo-cloud/image/upload/fallback.jpg"
    assert generated_path["path"].endswith("asset-post-1.jpg")
