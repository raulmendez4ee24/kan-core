from __future__ import annotations

from brain.creative_memory import CreativePattern, save_creative_pattern
from brain.agents.creative_strategist_agent import build_creative_plan
from brain.agents.creative_strategist_agent import mutate_hook


def test_creative_strategist_prefers_instagram_for_cold_traffic(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(tmp_path / "creative.sqlite3"))
    plan = build_creative_plan(
        offer_brief={
            "primary_need": "meta_ads",
            "recommended_offer": "Growth",
            "problem_statement": "genera trafico o mensajes pero no convierte suficiente en citas o ventas",
            "promise": "convertir mas conversaciones en seguimiento y ventas",
            "cta": "agendar una llamada para revisar tu flujo actual",
            "buying_temperature": "cold",
        },
        context={"left_on_read_pct": 80},
    )

    assert plan["angles"][0]["route"] == "instagram_profile"
    assert len(plan["ideas"]) >= 15
    assert len({item["category"] for item in plan["ideas"]}) >= 8
    assert all(item["pattern_used"] for item in plan["ideas"])


def test_creative_strategist_uses_memory_as_inspiration(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CREATIVE_MEMORY_DB_PATH", str(tmp_path / "creative.sqlite3"))
    save_creative_pattern(
        CreativePattern(
            domain="campaign_execution",
            offer="Growth",
            hook="No necesitas mas trafico; necesitas responder y seguir mejor.",
            angle="Usar el seguimiento como cuello principal del embudo.",
            cta="pedirme una demo",
            pattern_used="contrarian",
            critic_score=0.91,
            campaign_id="cmp-1",
            reply_rate=0.4,
            booking_rate=0.2,
        )
    )

    plan = build_creative_plan(
        offer_brief={
            "primary_need": "meta_ads",
            "recommended_offer": "Growth",
            "problem_statement": "genera trafico o mensajes pero no convierte suficiente en citas o ventas",
            "promise": "convertir mas conversaciones en seguimiento y ventas",
            "cta": "agendar una llamada para revisar tu flujo actual",
            "buying_temperature": "warm",
        },
        context={},
    )

    assert plan["inspiration"]
    assert any(item["category"] == "memory_variation" for item in plan["ideas"])


def test_hook_mutation_preserves_pattern_with_multiple_variations() -> None:
    variants = mutate_hook(
        "Cada lead que dejas sin seguimiento te cuesta ventas.",
        pattern_used="pain_loss",
        limit=7,
    )

    assert len(variants) >= 5
    assert all("seguimiento" in item.lower() or "ventas" in item.lower() for item in variants)
