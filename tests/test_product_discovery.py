from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from brain.product_discovery import ProductDiscovery, ProductProposal
from brain.revenue_brain import RevenueBrain


def test_product_discovery_generates_and_persists_proposal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "product_discovery.sqlite3"
    monkeypatch.setenv("PRODUCT_DISCOVERY_DB_PATH", str(db_path))
    monkeypatch.setenv("RAUL_WHATSAPP_TO", "+5215512345678")
    monkeypatch.setenv("YCLOUD_WHATSAPP_FROM", "+5215599999999")

    sent_messages: list[dict[str, str]] = []

    async def _fake_send(*, from_number: str, to: str, text: str) -> dict[str, object]:
        sent_messages.append({"from": from_number, "to": to, "text": text})
        return {"sent": True}

    monkeypatch.setattr("brain.product_discovery.send_ycloud_whatsapp_text_message", _fake_send)

    snapshot = {
        "crm_signals": ["community manager urgente"] * 6,
        "conversation_signals": ["necesito manejo de redes sociales"] * 4,
        "opportunity_signals": ["manejo de redes para instagram"] * 2,
    }

    async def _run() -> None:
        discovery = ProductDiscovery(db_path=str(db_path))
        proposals = await discovery.run(snapshot=snapshot)

        assert len(proposals) == 1
        proposal = proposals[0]
        assert proposal.service_name == "Manejo de Redes Sociales"
        assert proposal.raw_signals_count >= 10

        persisted = await discovery.list_proposals()
        assert len(persisted) == 1
        assert persisted[0].unmet_need_key == proposal.unmet_need_key
        assert sent_messages
        assert "APPROVE / REJECT / MODIFY" in sent_messages[0]["text"]

    asyncio.run(_run())


def test_product_discovery_below_threshold_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "product_discovery.sqlite3"
    monkeypatch.setenv("PRODUCT_DISCOVERY_DB_PATH", str(db_path))

    async def _run() -> None:
        discovery = ProductDiscovery(db_path=str(db_path))
        proposals = await discovery.run(
            snapshot={
                "crm_signals": ["quiero una pagina web"] * 4,
                "conversation_signals": ["sitio web"] * 3,
            }
        )

        assert proposals == []
        assert await discovery.list_proposals() == []

    asyncio.run(_run())


def test_revenue_brain_daily_cycle_includes_product_discovery_opportunity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_run(self: ProductDiscovery, *, snapshot=None, session=None) -> list[ProductProposal]:
        return [
            ProductProposal(
                unmet_need_key="website_development",
                service_name="Landing + Sitio Web Comercial",
                suggested_price_mxn=6000,
                delivery_cost_mxn=2200,
                margin_pct=63.33,
                revenue_projection={"months_3": 18000, "months_6": 36000, "months_12": 72000},
                required_stack=["web_builder", "analytics"],
                go_to_market="Vender a leads que dependen solo de Instagram.",
                raw_signals_count=12,
            )
        ]

    monkeypatch.setattr("brain.revenue_brain.ProductDiscovery.run", _fake_run)

    async def _run() -> None:
        brain = RevenueBrain()
        briefing = await brain.daily_cycle(snapshot={"active_leads": 1})

        assert any("Landing + Sitio Web Comercial" in item for item in briefing.diagnosis.opportunities)

    asyncio.run(_run())
