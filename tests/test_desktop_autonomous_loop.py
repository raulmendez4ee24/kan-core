import asyncio
from typing import Any

from brain.desktop_autonomous_loop import run_desktop_autonomy_loop
from schemas.desktop_autonomy import DesktopAutonomyRunRequest


class DummySession:
    async def execute(self, _stmt: Any) -> Any:
        return None


def test_desktop_autonomous_loop_generates_observe_think_act_evaluate(monkeypatch) -> None:
    async def fake_observation(*_args: Any, **_kwargs: Any) -> dict:
        return {
            "image_base64": "aGVsbG8=",
            "mime_type": "image/png",
            "width": 100,
            "height": 100,
            "source_action": "take_screenshot",
        }

    async def fake_send_to_desktop_agent(**_kwargs: Any) -> dict:
        return {"accepted": True, "command_id": "cmd-1", "status": "pending_approval"}

    monkeypatch.setattr(
        "brain.desktop_autonomous_loop._load_latest_observation",
        fake_observation,
    )
    monkeypatch.setattr(
        "brain.desktop_autonomous_loop.send_to_desktop_agent",
        fake_send_to_desktop_agent,
    )

    result = asyncio.run(
        run_desktop_autonomy_loop(
            client_id="11111111-1111-1111-1111-111111111111",
            request=DesktopAutonomyRunRequest(
                goal="Ir al CRM y capturar pantalla",
                max_steps=2,
                start_url="https://app.example.com",
                context={},
            ),
            session=DummySession(),
        )
    )

    phases = [step.phase for step in result.steps]
    assert "observe" in phases
    assert "think" in phases
    assert "act" in phases
    assert "evaluate" in phases
    assert result.status in {"blocked", "completed"}


def test_desktop_autonomous_loop_autonomous_mode_requests_auto_approval(monkeypatch) -> None:
    captured_metadata = []

    async def fake_observation(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def fake_send_to_desktop_agent(**kwargs: Any) -> dict:
        captured_metadata.append(kwargs.get("metadata") or {})
        return {"accepted": True, "command_id": "cmd-2", "status": "approved", "approval_required": False}

    monkeypatch.setattr(
        "brain.desktop_autonomous_loop._load_latest_observation",
        fake_observation,
    )
    monkeypatch.setattr(
        "brain.desktop_autonomous_loop.send_to_desktop_agent",
        fake_send_to_desktop_agent,
    )

    result = asyncio.run(
        run_desktop_autonomy_loop(
            client_id="11111111-1111-1111-1111-111111111111",
            request=DesktopAutonomyRunRequest(
                goal="Extraer texto de la pagina",
                max_steps=1,
                start_url="https://app.example.com",
                execution_mode="autonomous",
                wait_for_results=False,
                enforce_quality_gate=False,
                context={"extract_selector": "h1"},
            ),
            session=DummySession(),
        )
    )

    assert result.status == "completed"
    assert captured_metadata
    # Autonomous mode should not force human approval; policy/executor can still require it.
    assert "approval" not in captured_metadata[0]
    assert "requires_human_approval" not in captured_metadata[0]
    assert captured_metadata[0]["recovery_control"] == "managed"
    assert bool(captured_metadata[0].get("task_id"))
    assert bool(captured_metadata[0].get("subtask_id"))


def test_desktop_autonomous_loop_builds_hierarchical_goal_plan(monkeypatch) -> None:
    async def fake_observation(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    monkeypatch.setattr(
        "brain.desktop_autonomous_loop._load_latest_observation",
        fake_observation,
    )

    result = asyncio.run(
        run_desktop_autonomy_loop(
            client_id="11111111-1111-1111-1111-111111111111",
            request=DesktopAutonomyRunRequest(
                goal="Generar reporte mensual, descargarlo y enviar email",
                max_steps=12,
                start_url="https://app.example.com",
                dry_run=True,
                context={
                    "login": {
                        "username_selector": "#email",
                        "password_selector": "#password",
                        "submit_text": "Iniciar sesion",
                        "username": "demo@example.com",
                        "password": "secret",
                    },
                    "menu_path": ["Reportes", "Mensual"],
                    "report": {
                        "period_selector": "#period",
                        "period_value": "2026-01",
                        "generate_text": "Generar reporte",
                        "download_text": "Descargar",
                    },
                    "email_delivery": {
                        "recipient_selector": "#to",
                        "recipient": "ops@example.com",
                        "subject_selector": "#subject",
                        "subject": "Reporte mensual",
                        "send_text": "Enviar",
                    },
                },
            ),
            session=DummySession(),
        )
    )

    think_steps = [step for step in result.steps if step.phase == "think"]
    assert think_steps
    hierarchy = think_steps[0].metadata.get("hierarchical_plan") or {}
    tasks = hierarchy.get("tasks") or []
    titles = [str(item.get("title")) for item in tasks]
    assert "Abrir sistema" in titles
    assert "Autenticacion" in titles
    assert "Navegar menu" in titles
    assert "Generar reporte" in titles
    assert "Descargar resultado" in titles
    assert "Enviar email" in titles

    act_steps = [step for step in result.steps if step.phase == "act"]
    assert act_steps
    first = act_steps[0]
    assert first.metadata.get("task_id")
    assert first.metadata.get("subtask_id")


def test_desktop_autonomous_loop_quality_gate_blocks_low_quality_completion(monkeypatch) -> None:
    async def fake_observation(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def fake_send_to_desktop_agent(**_kwargs: Any) -> dict:
        return {"accepted": True, "command_id": "cmd-quality-1", "status": "approved", "approval_required": False}

    monkeypatch.setattr(
        "brain.desktop_autonomous_loop._load_latest_observation",
        fake_observation,
    )
    monkeypatch.setattr(
        "brain.desktop_autonomous_loop.send_to_desktop_agent",
        fake_send_to_desktop_agent,
    )

    result = asyncio.run(
        run_desktop_autonomy_loop(
            client_id="11111111-1111-1111-1111-111111111111",
            request=DesktopAutonomyRunRequest(
                goal="Extraer datos del dashboard",
                max_steps=1,
                start_url="https://app.example.com",
                execution_mode="autonomous",
                wait_for_results=False,
                quality_min_score=0.95,
                enforce_quality_gate=True,
                context={"extract_selector": "h1"},
            ),
            session=DummySession(),
        )
    )

    assert result.status == "blocked"
    last = result.steps[-1]
    assert last.phase == "evaluate"
    quality = last.metadata.get("quality") or {}
    assert float(quality.get("score", 0.0)) < 0.95


def test_desktop_autonomous_loop_dynamic_risk_forces_approval(monkeypatch) -> None:
    captured = []

    async def fake_observation(*_args: Any, **_kwargs: Any) -> dict:
        return {}

    async def fake_send_to_desktop_agent(**kwargs: Any) -> dict:
        captured.append(kwargs.get("metadata") or {})
        return {"accepted": True, "command_id": "cmd-risk-1", "status": "pending_approval", "approval_required": True}

    monkeypatch.setattr(
        "brain.desktop_autonomous_loop._load_latest_observation",
        fake_observation,
    )
    monkeypatch.setattr(
        "brain.desktop_autonomous_loop.send_to_desktop_agent",
        fake_send_to_desktop_agent,
    )

    result = asyncio.run(
        run_desktop_autonomy_loop(
            client_id="11111111-1111-1111-1111-111111111111",
            request=DesktopAutonomyRunRequest(
                goal="Delete all test users in dashboard",
                max_steps=1,
                execution_mode="autonomous",
                start_url="https://app.example.com",
                context={},
            ),
            session=DummySession(),
        )
    )

    assert result.status == "blocked"
    assert captured
    assert captured[0].get("requires_human_approval") is True
