from __future__ import annotations

from brain.agents.creative_critic_agent import critique_creative_plan


def test_creative_critic_scores_specific_angle_above_generic() -> None:
    review = critique_creative_plan(
        offer_brief={
            "problem_statement": "pierde conversaciones por respuesta lenta y falta de seguimiento",
            "buying_temperature": "warm",
        },
        creative_plan={
            "ideas": [
                {
                    "name": "specific",
                    "hook": "Cada mensaje que dejas sin seguimiento te cuesta ventas.",
                    "angle": "Mostrar como Growth elimina el cuello de botella de seguimiento.",
                    "cta": "pedirme una demo",
                    "route": "landing_to_whatsapp",
                    "pattern_used": "pain_loss",
                    "traffic_temperature": "warm",
                },
                {
                    "name": "generic",
                    "hook": "Tu negocio puede crecer con IA.",
                    "angle": "Mensaje general de automatizacion.",
                    "cta": "mas informacion",
                    "route": "landing_to_whatsapp",
                    "pattern_used": "authority",
                    "traffic_temperature": "warm",
                },
            ]
        },
    )

    assert review["winner"]["name"] == "specific"
    assert review["approved"] is True
    assert review["weakest"]["approved"] is False
    assert "generic" in review["weakest"]["flags"]
    assert "weak_cta" in review["weakest"]["flags"]
