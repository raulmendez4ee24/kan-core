from __future__ import annotations

import asyncio
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
    scheduled: list[tuple[str, str]] = []

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
            scheduled.append((post.post_id, publish_at.isoformat()))
            return {"scheduled": True}

    monkeypatch.setenv("RAUL_WHATSAPP_TO", "+5215512345678")
    monkeypatch.setenv("YCLOUD_WHATSAPP_FROM", "+5215599999999")

    async def _run() -> None:
        director = BrandDirector(
            db_path=tmp_path / "brand.sqlite3",
            fetcher=_fetch,
            whatsapp_sender=_fake_send,
            content_publisher=_StubPublisher(),
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
        assert sent_messages
        assert scheduled
        assert "Post de hoy" in sent_messages[0]

    asyncio.run(_run())


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
