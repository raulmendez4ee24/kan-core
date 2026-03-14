"""Tests for brain/humanizer.py — multi-agent text humanizer.

All LLM calls are mocked so tests run without API keys or network.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from brain import humanizer as _h


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_llm_response(text: str) -> MagicMock:
    """Build a mock OpenAI ChatCompletion response."""
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _enable(monkeypatch, mode: str = "multiagent"):
    monkeypatch.setenv("HUMANIZER_ENABLED", "true")
    monkeypatch.setenv("HUMANIZER_MODE", mode)


# ---------------------------------------------------------------------------
# Mode: off
# ---------------------------------------------------------------------------


def test_mode_off_returns_original(monkeypatch):
    _enable(monkeypatch, "off")
    original = "Hola, el precio es $5,000 MXN. ¿Agendamos una llamada?"
    assert _h.humanize_text(original) == original


def test_disabled_returns_original(monkeypatch):
    monkeypatch.setenv("HUMANIZER_ENABLED", "false")
    original = "Texto sin cambios."
    assert _h.humanize_text(original) == original


def test_empty_text_returns_empty():
    assert _h.humanize_text("") == ""
    assert _h.humanize_text(None) == ""


def test_short_text_skipped(monkeypatch):
    _enable(monkeypatch, "simple")
    monkeypatch.setenv("HUMANIZER_MIN_LEN", "50")
    assert _h.humanize_text("Hola") == "Hola"


# ---------------------------------------------------------------------------
# Mode: simple (single LLM call)
# ---------------------------------------------------------------------------


def test_simple_mode_calls_llm_once(monkeypatch):
    _enable(monkeypatch, "simple")
    humanized = "Oye, el plan cuesta $5,000 MXN. ¿Cuándo te late una llamada?"

    with patch.object(_h, "_llm_call", return_value=humanized) as mock_call:
        result = _h.humanize_text(
            "Con gusto le informo que el plan tiene un costo de $5,000 MXN. "
            "Quedo a sus órdenes para agendar una llamada."
        )
        assert result == humanized
        assert mock_call.call_count == 1


# ---------------------------------------------------------------------------
# Mode: multiagent (Rewriter → Trimmer → Guard)
# ---------------------------------------------------------------------------


def test_multiagent_full_pipeline(monkeypatch):
    _enable(monkeypatch, "multiagent")

    original = (
        "Con gusto le informo que el servicio de automatización de WhatsApp "
        "tiene un costo de $3,500 MXN mensuales. Incluye seguimiento "
        "automático y reportes. Le sugiero agendar una llamada de 15 minutos "
        "para revisar su caso."
    )
    rewritten = (
        "El servicio de automatización de WhatsApp cuesta $3,500 MXN al mes. "
        "Incluye seguimiento automático y reportes. ¿Te late una llamada de "
        "15 minutos para ver tu caso?"
    )
    trimmed = (
        "La automatización de WhatsApp cuesta $3,500 MXN/mes con seguimiento "
        "y reportes incluidos.\n¿Te late una llamada de 15 min para ver tu caso?"
    )

    calls = [rewritten, trimmed]

    with patch.object(_h, "_llm_call", side_effect=calls):
        result = _h.humanize_text(original)

    assert result == trimmed
    assert "$3,500" in result
    assert "15" in result


def test_multiagent_guard_reverts_when_trimmer_loses_data(monkeypatch):
    _enable(monkeypatch, "multiagent")

    original = "El precio es $8,200 MXN. Agenda tu cita en https://cal.kan.mx/demo"
    rewritten = "Cuesta $8,200 MXN. Agenda en https://cal.kan.mx/demo"
    trimmed_bad = "Agenda tu cita para ver el servicio."

    with patch.object(_h, "_llm_call", side_effect=[rewritten, trimmed_bad]):
        result = _h.humanize_text(original)

    assert result == rewritten
    assert "$8,200" in result
    assert "https://cal.kan.mx/demo" in result


def test_multiagent_guard_reverts_to_original_when_both_lose_data(monkeypatch):
    _enable(monkeypatch, "multiagent")

    original = "Contacta a María al +52 55 1234 5678 por el paquete de $12,000."
    rewritten_bad = "Contáctanos para más información sobre el paquete."
    trimmed_bad = "Escríbenos para saber más."

    with patch.object(_h, "_llm_call", side_effect=[rewritten_bad, trimmed_bad]):
        result = _h.humanize_text(original)

    assert result == original


# ---------------------------------------------------------------------------
# Mode: auto (multiagent → simple → original)
# ---------------------------------------------------------------------------


def test_auto_falls_to_simple_on_multiagent_failure(monkeypatch):
    _enable(monkeypatch, "auto")
    simple_result = "Versión simple del mensaje. El costo es $2,000 MXN."

    def _side_effect(system: str, text: str):
        if "editor" in system.lower() or "experto" in system.lower():
            if "acortar" not in system and "Reescribe" not in system:
                raise RuntimeError("Rewriter API error")
            return simple_result
        raise RuntimeError("Agent failure")

    with patch.object(_h, "_llm_call") as mock_call:
        mock_call.side_effect = [
            RuntimeError("rewriter fail"),
            simple_result,
        ]
        result = _h.humanize_text(
            "El costo del servicio es de $2,000 MXN por mes.",
            mode="auto",
        )

    assert result == simple_result


def test_auto_returns_original_when_all_fail(monkeypatch):
    _enable(monkeypatch, "auto")
    original = "El costo es de $7,500 MXN. Agenda tu demo."

    with patch.object(_h, "_llm_call", side_effect=RuntimeError("all fail")):
        result = _h.humanize_text(original)

    assert result == original


# ---------------------------------------------------------------------------
# Data preservation
# ---------------------------------------------------------------------------


def test_guard_check_prices():
    assert _h._guard_check(
        "El precio es $5,000 MXN",
        "Cuesta $5,000 MXN",
    ) is True
    assert _h._guard_check(
        "El precio es $5,000 MXN",
        "Cuesta algo",
    ) is False


def test_guard_check_urls():
    assert _h._guard_check(
        "Agenda en https://cal.kan.mx/demo",
        "Entra a https://cal.kan.mx/demo",
    ) is True
    assert _h._guard_check(
        "Agenda en https://cal.kan.mx/demo",
        "Agenda tu cita",
    ) is False


def test_guard_check_numbers():
    assert _h._guard_check(
        "Son 15 minutos y 3 sesiones por $4,800",
        "15 minutos, 3 sesiones, $4,800",
    ) is True


def test_guard_check_empty_original():
    assert _h._guard_check("Hola, ¿cómo estás?", "Qué onda, ¿cómo vas?") is True


# ---------------------------------------------------------------------------
# WhatsApp truncation
# ---------------------------------------------------------------------------


def test_whatsapp_truncation(monkeypatch):
    _enable(monkeypatch, "simple")
    long_response = "Palabra " * 500

    with patch.object(_h, "_llm_call", return_value=long_response):
        result = _h.humanize_text("Texto original suficientemente largo para activar humanización.")

    assert len(result) <= _h._WHATSAPP_MAX_LEN + 10
    assert result.endswith("…")


# ---------------------------------------------------------------------------
# Commercial examples in Spanish (integration-style with mocks)
# ---------------------------------------------------------------------------


def test_commercial_example_sales_followup(monkeypatch):
    _enable(monkeypatch, "multiagent")

    original = (
        "Estimado prospecto, en relación a nuestra conversación anterior, "
        "le confirmo que el plan Growth tiene un costo de $9,500 MXN mensuales. "
        "Este incluye: agente de WhatsApp, seguimiento automático de leads, "
        "y dashboard de métricas. Le sugiero agendar una llamada de diagnóstico "
        "de 20 minutos para el día jueves a las 10:00 AM."
    )
    rewritten = (
        "Como platicamos, el plan Growth cuesta $9,500 MXN al mes. Incluye "
        "agente de WhatsApp, seguimiento automático y dashboard de métricas. "
        "¿Te funciona una llamada de 20 min el jueves a las 10:00 AM?"
    )
    trimmed = (
        "El plan Growth: $9,500 MXN/mes (agente WhatsApp + seguimiento + "
        "métricas).\n¿El jueves a las 10:00 AM para una llamada de 20 min?"
    )

    with patch.object(_h, "_llm_call", side_effect=[rewritten, trimmed]):
        result = _h.humanize_text(original)

    assert "$9,500" in result
    assert "10:00" in result
    assert "20" in result
    assert "jueves" in result


def test_commercial_example_cold_outreach(monkeypatch):
    _enable(monkeypatch, "multiagent")

    original = (
        "Buenos días. Mi nombre es Carlos de KAN Logic. Espero que esta "
        "información sea de su interés. Ofrecemos soluciones de automatización "
        "con agentes de IA para WhatsApp que ayudan a mejorar el seguimiento "
        "comercial. ¿Le gustaría agendar una breve demostración?"
    )
    rewritten = (
        "Hola, soy Carlos de KAN Logic. Tenemos agentes de IA para WhatsApp "
        "que mejoran tu seguimiento comercial. ¿Te interesa una demo rápida?"
    )
    trimmed = (
        "Hola, soy Carlos de KAN Logic. Nuestros agentes de IA para WhatsApp "
        "mejoran tu seguimiento.\n¿Te late una demo rápida?"
    )

    with patch.object(_h, "_llm_call", side_effect=[rewritten, trimmed]):
        result = _h.humanize_text(original)

    assert "Carlos" in result
    assert "KAN Logic" in result
    assert "demo" in result.lower()


# ---------------------------------------------------------------------------
# Never crashes the pipeline
# ---------------------------------------------------------------------------


def test_humanizer_never_raises_on_unexpected_error(monkeypatch):
    _enable(monkeypatch, "multiagent")

    with patch.object(_h, "_llm_call", side_effect=Exception("catastrophic")):
        result = _h.humanize_text(
            "Texto largo suficiente para activar el humanizer sin problemas."
        )

    assert result == "Texto largo suficiente para activar el humanizer sin problemas."


def test_mode_override_parameter(monkeypatch):
    _enable(monkeypatch, "multiagent")
    original = "Un texto que no debe ser modificado por override."
    assert _h.humanize_text(original, mode="off") == original
