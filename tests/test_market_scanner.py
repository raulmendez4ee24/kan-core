from __future__ import annotations

from brain.market_scanner import MarketScanner


def test_scan_job_boards_from_fixture_rows():
    scanner = MarketScanner()
    results = [
        {
            "company": "Clinica Sonrisas",
            "job_title": "Recepcionista / atención a pacientes",
            "job_type": "recepcionista",
            "vertical": "clinics",
        },
        {
            "company": "Barber Bros",
            "job_title": "Community Manager",
            "job_type": "community_manager",
            "vertical": "barbershops",
        },
    ]

    opportunities = __import__("asyncio").run(scanner.scan_job_boards(results=results))
    assert len(opportunities) == 2
    assert opportunities[0].source == "job_boards"
    assert opportunities[0].vertical == "clinics"
    assert opportunities[1].vertical == "barbershops"


def test_scan_trends_from_override_results():
    scanner = MarketScanner()
    opportunities = __import__("asyncio").run(
        scanner.scan_trends(
            results=[
                {"keyword": "chatbot para negocio", "trend_score": 63},
                {"keyword": "automatización whatsapp", "trend_score": 58},
            ]
        )
    )
    assert len(opportunities) == 2
    assert opportunities[0].source == "google_trends"
    assert opportunities[0].trend_score == 63


def test_scan_competitor_activity_detects_changes():
    scanner = MarketScanner()

    async def fetcher(_: str) -> str:
        return """
        <html>
          <body>
            <a href="/servicios/chatbot-whatsapp">Chatbot</a>
            <a href="/pricing">Pricing</a>
            <div>$3,000 MXN</div>
          </body>
        </html>
        """

    opportunities = __import__("asyncio").run(
        scanner.scan_competitor_activity(
            ["https://competidor.test"],
            fetcher=fetcher,
            baseline_snapshots={
                "https://competidor.test": {
                    "service_pages": ["/pricing"],
                    "prices": [],
                }
            },
        )
    )
    assert len(opportunities) == 1
    assert opportunities[0].source == "competitor_activity"
    assert "/servicios/chatbot-whatsapp" in opportunities[0].metadata["new_service_pages"]


def test_scan_complaints_groups_themes_with_threshold():
    scanner = MarketScanner()
    rows = [
        {"platform": "reddit:mexico", "text": f"Necesito una pagina web para mi negocio {idx}"}
        for idx in range(11)
    ]
    rows.extend(
        {"platform": "x", "text": f"Es muy caro contratar community manager {idx}"}
        for idx in range(4)
    )

    opportunities = __import__("asyncio").run(scanner.scan_complaints(results=rows))
    assert len(opportunities) == 1
    assert opportunities[0].source == "complaints"
    assert opportunities[0].metadata["theme"] == "web_presence"
    assert opportunities[0].metadata["signals_count"] == 11


def test_scan_arbitrage_returns_products_without_spanish_competitor():
    scanner = MarketScanner()
    opportunities = __import__("asyncio").run(
        scanner.scan_arbitrage(
            results=[
                {"name": "VoiceCloser", "tagline": "Voice sales agent for clinics", "has_spanish_competitor": False},
                {"name": "LocalCRM", "tagline": "CRM for local business", "has_spanish_competitor": True},
            ]
        )
    )
    assert len(opportunities) == 1
    assert opportunities[0].source == "arbitrage"
    assert opportunities[0].metadata["product_name"] == "VoiceCloser"


def test_scan_internal_demand_ranks_unmet_needs():
    scanner = MarketScanner()
    opportunities = __import__("asyncio").run(
        scanner.scan_internal_demand(
            crm_texts=[
                "El cliente pide pagina web y seguimiento por whatsapp",
                "Necesita una página web para su negocio",
                "No tiene sitio web y quiere landing page",
            ],
            conversation_texts=[
                "Quiero automatización whatsapp para vender más",
                "Busco pagina web y chatbot",
                "Necesito pagina web urgente",
            ],
        )
    )
    assert opportunities
    assert opportunities[0].source == "internal_demand"
    assert opportunities[0].metadata["theme"] == "web_presence"
    assert opportunities[0].metadata["signals_count"] >= 4
