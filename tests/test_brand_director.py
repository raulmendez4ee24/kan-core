from __future__ import annotations

import asyncio
import inspect
from datetime import date
from pathlib import Path

from brain.brand_director import BrandDirector


def test_brand_audit_builds_brand_bible(tmp_path: Path) -> None:
    async def _fetch(url: str) -> str:
        if "instagram.com" in url:
            return """
            <html>
              <head>
                <title>kanlogic | Instagram</title>
                <meta name="description" content="Automatización, ventas y sitios web para negocios." />
              </head>
              <body>
                <div style="color:#112233;background:#f5f1e8;font-family:'Poppins', sans-serif">caso real automatizacion resultados</div>
              </body>
            </html>
            """
        return """
        <html>
          <head>
            <title>KAN Logic</title>
            <meta name="description" content="Agentes, automatización y páginas web para crecer ventas." />
            <style>
              body { color:#112233; background:#f5f1e8; font-family:'Poppins', sans-serif; }
              h1 { color:#cc8844; font-family:'Playfair Display', serif; }
            </style>
          </head>
          <body>
            <h1>Agentes para vender más</h1>
            <p>Casos, estrategia, resultados y ofertas para clínicas y restaurantes.</p>
          </body>
        </html>
        """

    async def _run() -> None:
        director = BrandDirector(db_path=tmp_path / "brand.sqlite3", fetcher=_fetch)
        bible = await director.brand_audit("kanlogic", "https://kanlogic.com")
        assert bible.identity.who == "KAN Logic"
        assert bible.visual_identity.colors
        assert bible.visual_identity.fonts
        assert bible.narrative.content_pillars
        assert bible.current_score > 60

    asyncio.run(_run())


def test_generate_weekly_content_plan_and_daily_post(tmp_path: Path, monkeypatch) -> None:
    sent_messages: list[str] = []
    scheduled: list[tuple[str, str, str | None]] = []
    generated_assets: list[dict[str, object]] = []
    hook_calls: list[dict[str, str]] = []

    async def _fetch(_url: str) -> str:
        return """
        <html>
          <head>
            <title>KAN Logic</title>
            <meta name="description" content="Automatización comercial y páginas web." />
            <style>body{color:#223344;font-family:'Montserrat', sans-serif}</style>
          </head>
          <body>resultados casos oferta estrategia</body>
        </html>
        """

    async def _fake_send(*, from_number: str, to: str, text: str):
        sent_messages.append(text)
        return {"sent": True}

    class _StubPublisher:
        async def schedule_post(self, post, publish_at):
            scheduled.append((post.post_id, publish_at.isoformat(), post.media_url))
            return {"scheduled": True}

    class _StubAssetManager:
        async def generate_post_image(self, **kwargs):
            generated_assets.append(kwargs)
            return f"https://cdn.example.com/{kwargs['asset_id']}.jpg"

    async def _hook_generator(**kwargs):
        hook_calls.append(
            {
                "topic": kwargs["topic"],
                "pillar": kwargs["pillar"],
                "platform": kwargs["platform"],
            }
        )
        return "¿Cuánto te cuesta responder tarde?"

    monkeypatch.setenv("RAUL_WHATSAPP_TO", "+5215512345678")
    monkeypatch.setenv("YCLOUD_WHATSAPP_FROM", "+5215599999999")

    async def _run() -> None:
        director = BrandDirector(
            db_path=tmp_path / "brand.sqlite3",
            fetcher=_fetch,
            whatsapp_sender=_fake_send,
            content_publisher=_StubPublisher(),
            asset_manager=_StubAssetManager(),
            hook_generator=_hook_generator,
        )
        plan = await director.generate_weekly_content_plan(
            instagram_handle="kanlogic",
            website_url="https://kanlogic.com",
            reference_date=date(2026, 3, 15),
        )
        assert len(plan) == 7
        assert len({post.pillar for post in plan}) >= 4

        post = await director.generate_daily_post(reference_date=date(2026, 3, 17))
        assert post.day_index == 1
        assert post.full_script
        assert post.media_url == "https://cdn.example.com/2026-03-16-1.jpg"
        assert len(post.hook.split()) <= 6
        assert post.hook == "¿Cuánto te cuesta responder tarde?"
        assert sent_messages
        assert scheduled
        assert generated_assets
        assert hook_calls
        assert generated_assets[0]["topic"] == post.topic
        assert generated_assets[0]["hook_text"] == post.hook
        assert generated_assets[0]["style_preset"] == "premium"
        assert post.pillar == "educacion"
        assert generated_assets[0]["content_type"] == "educational_tip"
        assert scheduled[0][2] == "https://cdn.example.com/2026-03-16-1.jpg"
        assert "Post de hoy" in sent_messages[0]
        assert "¿Cuánto te cuesta responder tarde?" in sent_messages[0]

    asyncio.run(_run())


def test_normalize_hook_enforces_max_six_words_and_removes_conditional(tmp_path: Path) -> None:
    director = BrandDirector(db_path=tmp_path / "brand.sqlite3")
    hook = director._normalize_hook(
        "Si respondes tarde, pierdes dinero todos los días",
        pillar="educacion",
    )
    assert len(hook.split()) <= 6
    assert not hook.lower().startswith("si ")


def test_generate_hook_prompt_contains_billboard_rules() -> None:
    source = inspect.getsource(BrandDirector._generate_hook_with_claude)
    assert "Generate a hook for an Instagram post. Maximum 6 words." in source
    assert "Must stop the scroll." in source
    assert "No conditional sentences." in source
    assert "Think billboard, not paragraph." in source


def test_analyze_feed_consistency_returns_specific_recommendations(tmp_path: Path) -> None:
    async def _fetch(_url: str) -> str:
        cards = []
        for idx in range(18):
            cards.append(
                f'<div class="post" style="color:#{idx % 3}{idx % 3}{idx % 3}111">'
                f'<img alt="kanlogic reel resultado {idx}" />'
                f'<span>reel carousel video post {idx}</span>'
                "</div>"
            )
        return "<html><body>" + "".join(cards) + "</body></html>"

    async def _run() -> None:
        director = BrandDirector(db_path=tmp_path / "brand.sqlite3", fetcher=_fetch)
        report = await director.analyze_feed_consistency("kanlogic")
        assert report.overall_score > 0
        assert report.recommendations
        assert report.handle == "kanlogic"

    asyncio.run(_run())
