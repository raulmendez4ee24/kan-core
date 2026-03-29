from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

from brain.asset_manager import (
    AssetManager,
    DallEProvider,
    FORMAT_ASPECT_RATIOS,
    FORMAT_DIMENSIONS,
    ProviderRouter,
    READY_PROMPTS,
    STYLE_PRESETS,
    build_image_prompt,
    _select_creative_direction,
)
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


def test_post_processing_uses_expected_order_and_strengths() -> None:
    source = inspect.getsource(AssetManager._apply_post_processing)
    color_index = source.index("ImageEnhance.Color")
    contrast_index = source.index("ImageEnhance.Contrast")
    brightness_index = source.index("ImageEnhance.Brightness")
    grain_index = source.index("np.random.normal")
    vignette_index = source.index("vig_strength =")

    assert color_index < contrast_index < brightness_index < grain_index < vignette_index
    # Default profile values are used when style_preset="default"
    manager = AssetManager()
    default_profile = manager._get_post_processing_profile("default")
    assert default_profile["saturation"] == 0.88
    assert default_profile["contrast"] == 1.15
    assert default_profile["brightness"] == 0.87
    assert default_profile["vignette"] == 0.19


def test_generate_post_image_runs_post_processing_before_overlay() -> None:
    source = inspect.getsource(AssetManager.generate_post_image)
    post_index = source.index("self._apply_post_processing(output")
    overlay_index = source.index("self._overlay_text_and_wordmark(")
    assert post_index < overlay_index


def test_gemini_model_uses_env_override_and_default(monkeypatch) -> None:
    manager = AssetManager()
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert manager._gemini_image_model() == "models/gemini-3-pro-image-preview"

    monkeypatch.setenv("GEMINI_MODEL", "models/imagen-4.0-generate-001")
    assert manager._gemini_image_model() == "models/imagen-4.0-generate-001"


def test_provider_router_selects_flux_for_tech_noir_styles() -> None:
    router = ProviderRouter()
    assert router.select_provider_name("glassmorphism_dark") == "flux"
    assert router.select_provider_name("terminal_luxury") == "flux"
    assert router.select_provider_name("hardware_precision") == "flux"


def test_provider_router_selects_gemini_for_editorial_family() -> None:
    router = ProviderRouter()
    assert router.select_provider_name("premium") == "gemini"
    assert router.select_provider_name("editorial") == "gemini"
    assert router.select_provider_name("human") == "gemini"
    assert router.select_provider_name("documentary") == "gemini"


def test_generate_background_falls_back_to_gemini_when_flux_fails(monkeypatch, tmp_path: Path) -> None:
    manager = AssetManager()

    async def _boom(**kwargs) -> Path:
        raise RuntimeError("flux unavailable")

    async def _fake_gemini(**kwargs) -> Path:
        output = kwargs["output"]
        output.write_bytes(b"gemini-jpeg")
        return output

    monkeypatch.setattr(manager, "_generate_with_flux", _boom)
    monkeypatch.setattr(manager, "_generate_with_gemini", _fake_gemini)

    result = asyncio.run(
        manager._generate_background(
            topic="Tema",
            hook_text="Hook",
            style_preset="glassmorphism_dark",
            format="square",
            vertical="clinics",
            content_type="offer_launch",
            include_logo=True,
            output=tmp_path / "flux-fallback.jpg",
        )
    )

    assert result.exists()
    assert result.read_bytes() == b"gemini-jpeg"


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
    assert "Creative director style:" in prompt
    assert "Use real photography language or premium design direction" in prompt
    assert "ABSOLUTELY NO TEXT, WORDS, LETTERS, OR TYPOGRAPHY IN THE IMAGE." in prompt
    assert "Any text in the image will ruin the design." in prompt
    assert "No emojis, no icons, no symbols, no UI elements, no text overlays, no watermarks." in prompt
    assert "leave clean space for text overlay, no text in the image" in prompt
    assert "CRITICAL COLOR RULES:" in prompt
    assert "The image must be 70%+ very dark (near black, #0a0a14)." in prompt
    assert "NEVER a monochromatic teal/cyan/blue wash over everything." in prompt
    assert "#0a0a14" in prompt
    assert "orange accent #f97316" in prompt


def test_creative_direction_is_deterministic_for_same_topic_and_vertical() -> None:
    style_one = _select_creative_direction("Automatiza tu recepción", "clinics")
    style_two = _select_creative_direction("Automatiza tu recepción", "clinics")
    assert style_one["key"] == style_two["key"]


def test_creative_direction_varies_across_topics() -> None:
    style_one = _select_creative_direction("Automatiza tu recepción", "clinics")
    style_two = _select_creative_direction("Cierra más ventas por WhatsApp", "clinics")
    assert style_one["key"] != ""
    assert style_two["key"] != ""


def test_tech_noir_prompt_uses_real_photographed_scene() -> None:
    prompt = build_image_prompt(
        topic="Infraestructura premium",
        hook_text="Tus leads se enfrían mientras respondes tarde.",
        style_preset="glassmorphism_dark",
        format="square",
        vertical="clinics",
        content_type="offer_launch",
        include_logo=True,
    )
    assert "Modern server room behind glass partition at night." in prompt
    assert "Real datacenter photography." in prompt
    assert "NOT a 3D render. NOT CGI. A REAL photograph." in prompt
    assert "Single light source with consistent shadows." in prompt


def test_build_image_prompt_uses_vertical_accent_mapping() -> None:
    dental_prompt = build_image_prompt(
        topic="Más confianza clínica",
        hook_text="Tus pacientes dudan si respondes tarde.",
        style_preset="premium",
        format="square",
        vertical="dental",
        content_type="offer_launch",
        include_logo=True,
    )
    restaurant_prompt = build_image_prompt(
        topic="Más reservas",
        hook_text="Tu restaurante pierde mesas cuando respondes lento.",
        style_preset="premium",
        format="square",
        vertical="restaurants",
        content_type="offer_launch",
        include_logo=True,
    )
    assert "blue accent #3b82f6" in dental_prompt
    assert "warm amber accent #eab308" in restaurant_prompt


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
    assert len(READY_PROMPTS) == 22
    for key in ["lead_capture", "offer_launch", "testimonial", "workflow_upgrade", "whatsapp_conversion",
                "product_showcase", "pain_point", "social_proof_stats", "urgency_scarcity",
                "behind_the_scenes", "event_announcement"]:
        assert key in READY_PROMPTS


def test_font_cache_paths_use_assets_directory() -> None:
    manager = AssetManager()
    sora_path = manager._font_cache_path("Sora-Bold.ttf")
    mono_path = manager._font_cache_path("JetBrains-Mono-Medium.ttf")
    assert str(sora_path).endswith("/assets/fonts/Sora-Bold.ttf")
    assert str(mono_path).endswith("/assets/fonts/JetBrains-Mono-Medium.ttf")


def test_overlay_uses_pill_badge_and_accent_spec() -> None:
    source = inspect.getsource(AssetManager._overlay_text_and_wordmark)
    assert "letter_spacing = 0.5" in source
    assert "accent_width = 60" in source
    assert "accent_height = 3" in source
    assert "fill=\"#8888a0\"" in source
    assert "radius=6" in source
    assert "fill=(10, 10, 20, int(255 * 0.8))" in source
    assert "outline=(26, 26, 46, 255)" in source


def test_strip_emojis_removes_problematic_characters() -> None:
    manager = AssetManager()
    cleaned = manager._strip_emojis("Test de publicación automática 🤖🔥✅")
    assert cleaned == "Test de publicación automática"


def test_format_dimensions_contains_all_formats() -> None:
    for fmt in ["square", "story", "reel", "landscape", "carousel", "cover"]:
        assert fmt in FORMAT_DIMENSIONS
        w, h = FORMAT_DIMENSIONS[fmt]
        assert w > 0 and h > 0


def test_format_aspect_ratios_match_dimensions() -> None:
    assert FORMAT_ASPECT_RATIOS["square"] == "1:1"
    assert FORMAT_ASPECT_RATIOS["story"] == "9:16"
    assert FORMAT_ASPECT_RATIOS["reel"] == "9:16"
    assert FORMAT_ASPECT_RATIOS["landscape"] == "16:9"


def test_build_image_prompt_uses_format_dimensions() -> None:
    prompt_story = build_image_prompt(
        topic="Test", hook_text="Hook",
        style_preset="premium", format="story",
    )
    assert "1080x1920" in prompt_story

    prompt_landscape = build_image_prompt(
        topic="Test", hook_text="Hook",
        style_preset="premium", format="landscape",
    )
    assert "1920x1080" in prompt_landscape


def test_provider_router_selects_dalle_for_new_verticals() -> None:
    router = ProviderRouter()
    assert router.select_provider_name("gym") == "dalle"
    assert router.select_provider_name("beauty") == "dalle"
    assert router.select_provider_name("ecommerce") == "dalle"
    assert router.select_provider_name("education") == "dalle"
    assert router.select_provider_name("professional_services") == "dalle"


def test_provider_router_fallback_chain_returns_all_providers() -> None:
    router = ProviderRouter()
    chain = router.fallback_chain("premium")
    assert chain[0] == "gemini"
    assert len(chain) == 3
    assert set(chain) == {"gemini", "flux", "dalle"}

    chain_flux = router.fallback_chain("glassmorphism_dark")
    assert chain_flux[0] == "flux"
    assert len(chain_flux) == 3


def test_style_presets_include_new_verticals() -> None:
    for vertical in ["gym", "beauty", "ecommerce", "education", "professional_services"]:
        assert vertical in STYLE_PRESETS
        preset = STYLE_PRESETS[vertical]
        assert "mood" in preset
        assert "scene" in preset
        assert "composition" in preset
        assert "palette_hint" in preset
        assert "objects" in preset


def test_post_processing_is_style_aware() -> None:
    manager = AssetManager()
    default_p = manager._get_post_processing_profile("default")
    editorial_p = manager._get_post_processing_profile("editorial")
    beauty_p = manager._get_post_processing_profile("beauty")

    # Editorial should be more desaturated and contrasty than default
    assert editorial_p["saturation"] < default_p["saturation"]
    assert editorial_p["contrast"] > default_p["contrast"]
    # Beauty should be less grainy and vignetted
    assert beauty_p["grain"] < default_p["grain"]
    assert beauty_p["vignette"] < default_p["vignette"]


def test_resolve_vertical_aliases() -> None:
    manager = AssetManager()
    assert manager._resolve_vertical("fitness") == "gym"
    assert manager._resolve_vertical("gimnasio") == "gym"
    assert manager._resolve_vertical("salon") == "beauty"
    assert manager._resolve_vertical("tienda") == "ecommerce"
    assert manager._resolve_vertical("escuela") == "education"
    assert manager._resolve_vertical("consulting") == "professional_services"


def test_dalle_provider_class_exists() -> None:
    provider = DallEProvider()
    assert hasattr(provider, "generate")
