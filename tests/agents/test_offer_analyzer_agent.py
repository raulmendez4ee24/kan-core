from __future__ import annotations

from brain.agents.offer_analyzer_agent import analyze_offer


def test_offer_analyzer_maps_meta_ads_need_to_growth_offer() -> None:
    result = analyze_offer(
        goal="quiero mejorar mis campañas de meta ads y vender mas por whatsapp",
        context={"channel": "meta", "service_type": "marketing"},
    )

    assert result["primary_need"] == "meta_ads"
    assert result["recommended_offer"] == "Growth"
    assert result["buying_temperature"] == "cold"
