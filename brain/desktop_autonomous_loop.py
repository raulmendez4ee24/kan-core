import asyncio
import base64
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from execution.desktop_executor import send_to_desktop_agent
from models import DesktopCommand, DesktopCommandResult
from recovery.recovery_service import trigger_recovery_for_failed_command
from routes.desktop_agent import _dispatch_command_if_online
from brain.runtime_events import runtime_event_bus
from brain.runtime_hooks import HOOK_AFTER_TOOL, HOOK_BEFORE_TOOL, runtime_hooks
from schemas.desktop_autonomy import (
    DesktopAutonomyRunRequest,
    DesktopAutonomyRunResponse,
    DesktopAutonomyStep,
)
from vision.screen_analyzer import analyze_screen

_RUNS: Dict[str, DesktopAutonomyRunResponse] = {}
_RISK_LEVEL_ORDER = {"low": 1, "medium": 2, "high": 3}


def get_autonomy_run(run_id: str) -> Optional[DesktopAutonomyRunResponse]:
    return _RUNS.get(run_id)


async def _enqueue_with_hooks(
    *,
    client_id: str,
    run_id: str,
    session_id: str,
    action: str,
    parameters: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    hook_payload = await runtime_hooks.run(
        HOOK_BEFORE_TOOL,
        {
            "run_id": run_id,
            "session_id": session_id,
            "client_id": client_id,
            "action": action,
            "parameters": dict(parameters or {}),
            "metadata": dict(metadata or {}),
        },
    )
    action_name = str(hook_payload.get("action") or action)
    params = hook_payload.get("parameters")
    meta = hook_payload.get("metadata")
    clean_params = dict(params) if isinstance(params, dict) else dict(parameters or {})
    clean_meta = dict(meta) if isinstance(meta, dict) else dict(metadata or {})

    runtime_event_bus.emit(
        run_id=run_id,
        session_id=session_id,
        event_type="tool.dispatch",
        payload={"action": action_name},
    )
    enqueue_result = await send_to_desktop_agent(
        client_id=client_id,
        action=action_name,
        parameters=clean_params,
        metadata=clean_meta,
    )
    await runtime_hooks.run(
        HOOK_AFTER_TOOL,
        {
            "run_id": run_id,
            "session_id": session_id,
            "client_id": client_id,
            "action": action_name,
            "parameters": clean_params,
            "metadata": clean_meta,
            "result": enqueue_result,
        },
    )
    runtime_event_bus.emit(
        run_id=run_id,
        session_id=session_id,
        event_type="tool.result",
        payload={"action": action_name, "accepted": bool(enqueue_result.get("accepted"))},
    )
    return enqueue_result


def _parse_uuid(raw: str, *, field: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}") from exc


def _goal_action_hints(goal: str) -> list[str]:
    lowered = goal.lower()
    hints: list[str] = []
    if "login" in lowered or "iniciar sesion" in lowered:
        hints.append("login_button")
    if "search" in lowered or "buscar" in lowered:
        hints.append("search_input")
    if "checkout" in lowered or "pago" in lowered:
        hints.append("checkout_button")
    return hints


def _contains_any(text: str, tokens: List[str]) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in tokens)


def _new_task(tasks: List[Dict[str, Any]], *, title: str, category: str) -> Dict[str, Any]:
    task_id = f"t{len(tasks) + 1}"
    task = {
        "task_id": task_id,
        "title": title,
        "category": category,
        "subtasks": [],
    }
    tasks.append(task)
    return task


def _add_subtask(
    *,
    task: Dict[str, Any],
    title: str,
    action: Optional[str],
    parameters: Optional[Dict[str, Any]],
    flattened: List[Dict[str, Any]],
) -> None:
    subtask_id = f"{task['task_id']}.s{len(task['subtasks']) + 1}"
    subtask = {
        "subtask_id": subtask_id,
        "title": title,
        "action": action,
        "parameters": dict(parameters or {}),
    }
    task["subtasks"].append(subtask)
    if action:
        flattened.append(
            {
                "action": action,
                "parameters": dict(parameters or {}),
                "task_id": task["task_id"],
                "task_title": task["title"],
                "task_category": task["category"],
                "subtask_id": subtask_id,
                "subtask_title": title,
            }
        )


def _build_hierarchical_plan(req: DesktopAutonomyRunRequest) -> Dict[str, Any]:
    goal = req.goal.strip()
    goal_lower = goal.lower()
    context = dict(req.context or {})
    tasks: List[Dict[str, Any]] = []
    flattened: List[Dict[str, Any]] = []

    system_url = req.start_url or context.get("system_url")
    has_explicit_system_target = isinstance(system_url, str) and bool(system_url.strip())
    if isinstance(system_url, str) and system_url.strip():
        task = _new_task(tasks, title="Abrir sistema", category="open_system")
        _add_subtask(
            task=task,
            title="Inicializar navegador",
            action="browser_launch",
            parameters={},
            flattened=flattened,
        )
        _add_subtask(
            task=task,
            title="Entrar al sistema",
            action="browser_goto",
            parameters={"url": system_url.strip(), "wait_until": "domcontentloaded"},
            flattened=flattened,
        )

    login_ctx = context.get("login")
    needs_login = _contains_any(goal_lower, ["login", "log in", "iniciar sesion", "acceder"]) or bool(
        isinstance(login_ctx, dict) and login_ctx
    )
    if needs_login:
        task = _new_task(tasks, title="Autenticacion", category="login")
        if isinstance(login_ctx, dict):
            user_selector = login_ctx.get("username_selector") or login_ctx.get("email_selector")
            pass_selector = login_ctx.get("password_selector")
            submit_selector = login_ctx.get("submit_selector")
            submit_text = login_ctx.get("submit_text")
            username = login_ctx.get("username")
            password = login_ctx.get("password")
            if isinstance(user_selector, str) and user_selector.strip() and isinstance(username, str):
                _add_subtask(
                    task=task,
                    title="Completar usuario",
                    action="browser_fill",
                    parameters={"selector": user_selector.strip(), "text": username, "clear_first": True},
                    flattened=flattened,
                )
            if isinstance(pass_selector, str) and pass_selector.strip() and isinstance(password, str):
                _add_subtask(
                    task=task,
                    title="Completar contrasena",
                    action="browser_fill",
                    parameters={"selector": pass_selector.strip(), "text": password, "clear_first": True},
                    flattened=flattened,
                )
            if isinstance(submit_selector, str) and submit_selector.strip():
                _add_subtask(
                    task=task,
                    title="Enviar login",
                    action="browser_click",
                    parameters={"selector": submit_selector.strip()},
                    flattened=flattened,
                )
            elif isinstance(submit_text, str) and submit_text.strip():
                _add_subtask(
                    task=task,
                    title="Enviar login",
                    action="browser_click",
                    parameters={"text": submit_text.strip()},
                    flattened=flattened,
                )
        if (not task["subtasks"]) and has_explicit_system_target:
            _add_subtask(
                task=task,
                title="Intentar boton iniciar sesion",
                action="browser_click",
                parameters={"text": "Iniciar sesion"},
                flattened=flattened,
            )

    menu_path = context.get("menu_path")
    report_related_goal = _contains_any(
        goal_lower,
        ["reporte", "report", "mensual", "dashboard", "navegar menu"],
    )
    if isinstance(menu_path, list) and menu_path:
        task = _new_task(tasks, title="Navegar menu", category="navigation")
        for item in menu_path:
            if isinstance(item, str) and item.strip():
                _add_subtask(
                    task=task,
                    title=f"Abrir seccion {item.strip()}",
                    action="browser_click",
                    parameters={"text": item.strip()},
                    flattened=flattened,
                )
    elif report_related_goal and has_explicit_system_target:
        task = _new_task(tasks, title="Navegar menu", category="navigation")
        _add_subtask(
            task=task,
            title="Abrir modulo de reportes",
            action="browser_click",
            parameters={"text": "Reportes"},
            flattened=flattened,
        )

    report_ctx = context.get("report")
    needs_report = _contains_any(goal_lower, ["reporte", "report", "mensual", "monthly"])
    if needs_report or isinstance(report_ctx, dict):
        task = _new_task(tasks, title="Generar reporte", category="report")
        if isinstance(report_ctx, dict):
            report_type_selector = report_ctx.get("type_selector")
            report_type_value = report_ctx.get("type_value")
            period_selector = report_ctx.get("period_selector")
            period_value = report_ctx.get("period_value") or report_ctx.get("period")
            generate_selector = report_ctx.get("generate_selector")
            generate_text = report_ctx.get("generate_text")
            if (
                isinstance(report_type_selector, str)
                and report_type_selector.strip()
                and isinstance(report_type_value, str)
            ):
                _add_subtask(
                    task=task,
                    title="Seleccionar tipo de reporte",
                    action="browser_fill",
                    parameters={
                        "selector": report_type_selector.strip(),
                        "text": report_type_value,
                        "clear_first": True,
                    },
                    flattened=flattened,
                )
            if isinstance(period_selector, str) and period_selector.strip() and isinstance(period_value, str):
                _add_subtask(
                    task=task,
                    title="Configurar periodo",
                    action="browser_fill",
                    parameters={
                        "selector": period_selector.strip(),
                        "text": period_value,
                        "clear_first": True,
                    },
                    flattened=flattened,
                )
            if isinstance(generate_selector, str) and generate_selector.strip():
                _add_subtask(
                    task=task,
                    title="Ejecutar reporte",
                    action="browser_click",
                    parameters={"selector": generate_selector.strip()},
                    flattened=flattened,
                )
            elif isinstance(generate_text, str) and generate_text.strip():
                _add_subtask(
                    task=task,
                    title="Ejecutar reporte",
                    action="browser_click",
                    parameters={"text": generate_text.strip()},
                    flattened=flattened,
                )
        if (not task["subtasks"]) and (has_explicit_system_target or isinstance(report_ctx, dict)):
            _add_subtask(
                task=task,
                title="Generar reporte mensual",
                action="browser_click",
                parameters={"text": "Generar reporte"},
                flattened=flattened,
            )

    download_needed = _contains_any(goal_lower, ["descargar", "download", "exportar", "export"])
    if download_needed or needs_report:
        task = _new_task(tasks, title="Descargar resultado", category="download")
        download_text = "Descargar"
        if isinstance(report_ctx, dict):
            text_value = report_ctx.get("download_text")
            if isinstance(text_value, str) and text_value.strip():
                download_text = text_value.strip()
        if has_explicit_system_target or isinstance(report_ctx, dict):
            _add_subtask(
                task=task,
                title="Descargar archivo",
                action="browser_click",
                parameters={"text": download_text},
                flattened=flattened,
            )

    email_ctx = context.get("email_delivery")
    needs_email = _contains_any(goal_lower, ["enviar email", "enviar correo", "send email", "mandar correo"]) or bool(
        isinstance(email_ctx, dict) and email_ctx
    )
    if needs_email:
        task = _new_task(tasks, title="Enviar email", category="email")
        if isinstance(email_ctx, dict):
            recipient_selector = email_ctx.get("recipient_selector")
            recipient = email_ctx.get("recipient")
            subject_selector = email_ctx.get("subject_selector")
            subject = email_ctx.get("subject")
            body_selector = email_ctx.get("body_selector")
            body = email_ctx.get("body")
            send_selector = email_ctx.get("send_selector")
            send_text = email_ctx.get("send_text")
            if (
                isinstance(recipient_selector, str)
                and recipient_selector.strip()
                and isinstance(recipient, str)
            ):
                _add_subtask(
                    task=task,
                    title="Completar destinatario",
                    action="browser_fill",
                    parameters={"selector": recipient_selector.strip(), "text": recipient, "clear_first": True},
                    flattened=flattened,
                )
            if isinstance(subject_selector, str) and subject_selector.strip() and isinstance(subject, str):
                _add_subtask(
                    task=task,
                    title="Completar asunto",
                    action="browser_fill",
                    parameters={"selector": subject_selector.strip(), "text": subject, "clear_first": True},
                    flattened=flattened,
                )
            if isinstance(body_selector, str) and body_selector.strip() and isinstance(body, str):
                _add_subtask(
                    task=task,
                    title="Completar mensaje",
                    action="browser_fill",
                    parameters={"selector": body_selector.strip(), "text": body, "clear_first": True},
                    flattened=flattened,
                )
            if isinstance(send_selector, str) and send_selector.strip():
                _add_subtask(
                    task=task,
                    title="Enviar correo",
                    action="browser_click",
                    parameters={"selector": send_selector.strip()},
                    flattened=flattened,
                )
            elif isinstance(send_text, str) and send_text.strip():
                _add_subtask(
                    task=task,
                    title="Enviar correo",
                    action="browser_click",
                    parameters={"text": send_text.strip()},
                    flattened=flattened,
                )
        if (not task["subtasks"]) and (has_explicit_system_target or isinstance(email_ctx, dict)):
            _add_subtask(
                task=task,
                title="Enviar correo con reporte",
                action="browser_click",
                parameters={"text": "Enviar"},
                flattened=flattened,
            )

    fill_steps = context.get("fill_steps")
    click_selector = context.get("click_selector")
    click_text = context.get("click_text")
    extract_selector = context.get("extract_selector")
    if isinstance(fill_steps, list) or isinstance(click_selector, str) or isinstance(click_text, str) or isinstance(
        extract_selector, str
    ):
        task = _new_task(tasks, title="Subtareas de contexto", category="context")
        if isinstance(fill_steps, list):
            for item in fill_steps:
                if not isinstance(item, dict):
                    continue
                selector = item.get("selector")
                text = item.get("text")
                if isinstance(selector, str):
                    _add_subtask(
                        task=task,
                        title=f"Llenar campo {selector}",
                        action="browser_fill",
                        parameters={
                            "selector": selector,
                            "text": str(text or ""),
                            "clear_first": bool(item.get("clear_first", True)),
                        },
                        flattened=flattened,
                    )
        if isinstance(click_selector, str) and click_selector.strip():
            _add_subtask(
                task=task,
                title="Click por selector",
                action="browser_click",
                parameters={"selector": click_selector.strip()},
                flattened=flattened,
            )
        if isinstance(click_text, str) and click_text.strip():
            _add_subtask(
                task=task,
                title="Click por texto",
                action="browser_click",
                parameters={"text": click_text.strip()},
                flattened=flattened,
            )
        if isinstance(extract_selector, str) and extract_selector.strip():
            _add_subtask(
                task=task,
                title="Extraer datos",
                action="browser_extract",
                parameters={"selector": extract_selector.strip()},
                flattened=flattened,
            )

    verify_task = _new_task(tasks, title="Verificar resultado", category="evaluate")
    _add_subtask(
        task=verify_task,
        title="Capturar estado final",
        action="browser_screenshot",
        parameters={"full_page": False},
        flattened=flattened,
    )

    trimmed_actions = flattened[: max(1, req.max_steps)]
    return {
        "tasks": tasks,
        "actions": trimmed_actions,
        "total_actions": len(flattened),
        "truncated_actions": max(0, len(flattened) - len(trimmed_actions)),
    }


def _action_risk_level(action: str, parameters: Dict[str, Any]) -> str:
    if action in {"take_screenshot", "browser_screenshot", "browser_extract", "browser_goto"}:
        return "low"
    if action == "browser_click":
        has_x = "x" in parameters
        has_y = "y" in parameters
        if has_x or has_y:
            return "high"
        return "medium"
    if action in {"browser_fill", "browser_press", "browser_launch", "browser_close", "type_text", "press_key"}:
        return "medium"
    return "high"


def _max_auto_approval_risk(*, mode: str, allow_high_risk: bool) -> Optional[str]:
    if mode == "safe":
        return None
    if mode == "balanced":
        return "low"
    if mode == "autonomous":
        return "high" if allow_high_risk else "medium"
    return None


def _approval_required_for_action(
    *,
    action: str,
    parameters: Dict[str, Any],
    mode: str,
    allow_high_risk: bool,
    force_human_approval: bool,
    max_auto_risk_override: Optional[str] = None,
) -> tuple[bool, str]:
    risk_level = _action_risk_level(action, parameters)
    if force_human_approval:
        return True, risk_level
    max_risk = max_auto_risk_override or _max_auto_approval_risk(
        mode=mode,
        allow_high_risk=allow_high_risk,
    )
    if max_risk is None:
        return True, risk_level
    requires = _RISK_LEVEL_ORDER[risk_level] > _RISK_LEVEL_ORDER[max_risk]
    return requires, risk_level


def _cap_risk_level(current: Optional[str], cap: Optional[str]) -> Optional[str]:
    if current is None:
        return None
    if cap is None:
        return current
    return current if _RISK_LEVEL_ORDER[current] <= _RISK_LEVEL_ORDER[cap] else cap


def _dynamic_max_auto_approval_risk(
    *,
    request: DesktopAutonomyRunRequest,
    base_max_risk: Optional[str],
    plan_actions: List[Dict[str, Any]],
) -> Tuple[Optional[str], List[str]]:
    if not request.dynamic_risk_controls:
        return base_max_risk, ["dynamic_risk_controls_disabled"]
    current = base_max_risk
    reasons: List[str] = []
    goal_lower = str(request.goal or "").lower()
    high_risk_tokens = [
        "delete",
        "drop",
        "transfer",
        "wire",
        "payment",
        "pago",
        "checkout",
        "remove",
        "overwrite",
    ]
    if any(token in goal_lower for token in high_risk_tokens):
        current = _cap_risk_level(current, "low")
        reasons.append("goal_contains_sensitive_operation")

    context = dict(request.context or {})
    if bool(context.get("high_impact_operation")):
        current = _cap_risk_level(current, "low")
        reasons.append("high_impact_operation_context")
    if bool(context.get("force_human_approval")):
        current = None
        reasons.append("force_human_approval_context")

    web_research = context.get("web_research")
    if isinstance(web_research, dict) and not bool(web_research.get("verified", False)):
        current = _cap_risk_level(current, "low")
        reasons.append("web_research_not_verified")

    high_risk_actions = 0
    for item in plan_actions:
        action = str(item.get("action") or "")
        params = dict(item.get("parameters") or {})
        if _action_risk_level(action, params) == "high":
            high_risk_actions += 1
    if high_risk_actions > 0 and request.execution_mode != "autonomous":
        current = _cap_risk_level(current, "low")
        reasons.append("plan_contains_high_risk_actions")

    return current, reasons


def _build_self_healing_variants(
    *,
    action: str,
    parameters: Dict[str, Any],
    vision_hint: Dict[str, Any],
) -> List[Tuple[str, str, Dict[str, Any]]]:
    variants: List[Tuple[str, str, Dict[str, Any]]] = [("retry_same_action", action, dict(parameters))]
    if action == "browser_click":
        if "x" in parameters and "y" in parameters:
            try:
                x = int(parameters["x"])
                y = int(parameters["y"])
                variants.append(
                    (
                        "adjust_coordinates",
                        "browser_click",
                        {**dict(parameters), "x": max(0, x + 12), "y": max(0, y + 8)},
                    )
                )
            except Exception:
                pass
        if vision_hint.get("x") is not None and vision_hint.get("y") is not None:
            variants.append(
                (
                    "search_again",
                    "browser_click",
                    {"x": int(vision_hint["x"]), "y": int(vision_hint["y"])},
                )
            )
    elif action == "browser_fill":
        if isinstance(parameters.get("selector"), str) and isinstance(parameters.get("text"), str):
            variants.append(
                (
                    "retry_with_clear_first",
                    "browser_fill",
                    {**dict(parameters), "clear_first": True},
                )
            )
    elif action == "browser_goto":
        if isinstance(parameters.get("url"), str):
            variants.append(
                (
                    "wait_and_retry",
                    "browser_goto",
                    {**dict(parameters), "wait_until": "load"},
                )
            )
    deduped: List[Tuple[str, str, Dict[str, Any]]] = []
    seen = set()
    for strategy, variant_action, variant_params in variants:
        key = (
            strategy,
            variant_action,
            tuple(sorted((str(k), str(v)) for k, v in variant_params.items())),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append((strategy, variant_action, variant_params))
    return deduped


def _evaluate_autonomy_quality(
    *,
    request: DesktopAutonomyRunRequest,
    steps: List[DesktopAutonomyStep],
    planned_actions: int,
    queued: int,
    failed_to_queue: int,
    approvals_pending: int,
    executed_ok: int,
    executed_failed: int,
    recovered_ok: int,
) -> Dict[str, Any]:
    def _dynamic_quality_threshold() -> float:
        base = float(request.quality_min_score)
        goal_lower = str(request.goal or "").lower()
        context = dict(request.context or {}) if isinstance(request.context, dict) else {}
        eval_profile = str(context.get("evaluation_profile") or "").strip().lower()
        allow_eval_relax = bool(request.wait_for_results or eval_profile == "real_outcome")
        evidence_tokens = ["screenshot", "captura", "evidence", "estado", "status", "dashboard", "reporte", "report"]
        if allow_eval_relax and any(token in goal_lower for token in evidence_tokens):
            base = min(base, 0.45)
        if allow_eval_relax and planned_actions <= 2:
            base = min(base, 0.45)
        if request.execution_mode == "safe":
            base = max(base, 0.7)
        return max(0.2, min(base, 0.95))

    quality_notes: List[str] = []
    if planned_actions <= 0:
        return {
            "score": 0.0,
            "objective_met": False,
            "quality_gate_passed": False,
            "notes": ["no_planned_actions"],
        }

    if request.wait_for_results:
        success_value = executed_ok + recovered_ok
        success_ratio = success_value / float(planned_actions)
    else:
        success_value = queued
        success_ratio = queued / float(planned_actions)
        quality_notes.append("wait_for_results_disabled_score_estimated_from_queue")

    queue_failure_ratio = failed_to_queue / float(planned_actions)
    execution_failure_ratio = executed_failed / float(planned_actions)
    blocked_ratio = approvals_pending / float(planned_actions)
    recovery_ratio = recovered_ok / float(planned_actions)

    score = (
        0.58 * max(0.0, min(1.0, success_ratio))
        + 0.18 * max(0.0, min(1.0, recovery_ratio))
        + 0.24 * max(0.0, min(1.0, 1.0 - queue_failure_ratio - execution_failure_ratio - blocked_ratio))
    )
    score = max(0.0, min(1.0, round(score, 4)))

    web_research = request.context.get("web_research") if isinstance(request.context, dict) else None
    if isinstance(web_research, dict) and bool(web_research.get("verified", False)):
        score = min(1.0, round(score + 0.05, 4))
        quality_notes.append("verified_web_research_bonus")

    dynamic_threshold = _dynamic_quality_threshold()
    quality_gate_passed = score >= dynamic_threshold
    real_execution_success = (
        ((executed_ok + recovered_ok) > 0)
        if request.wait_for_results
        else (queued > 0)
    )
    # Real-execution criterion: in wait_for_results mode we only count objective as met
    # when at least one action reached effective execution or recovery success.
    objective_met = (
        quality_gate_passed
        and failed_to_queue == 0
        and approvals_pending == 0
        and real_execution_success
    )
    if (not quality_gate_passed) and request.wait_for_results and real_execution_success:
        # Soft override for low-entropy tasks (e.g., screenshot/status checks) that executed
        # successfully but under-score due plan-length penalty.
        soft_floor = max(0.35, dynamic_threshold - 0.2)
        if score >= soft_floor and failed_to_queue == 0 and approvals_pending == 0:
            quality_gate_passed = True
            objective_met = True
            quality_notes.append("quality_gate_soft_override_real_execution")
    if not quality_gate_passed:
        quality_notes.append("quality_score_below_threshold")
    if approvals_pending > 0:
        quality_notes.append("manual_approvals_pending")
    if executed_failed > 0 or failed_to_queue > 0:
        quality_notes.append("has_failed_actions")

    evaluate_steps = [step for step in steps if step.phase == "evaluate"]
    quality_notes.append(f"evaluate_steps={len(evaluate_steps)}")
    return {
        "score": score,
        "objective_met": objective_met,
        "quality_gate_passed": quality_gate_passed,
        "threshold": float(dynamic_threshold),
        "base_threshold": float(request.quality_min_score),
        "real_execution_success": bool(real_execution_success),
        "success_ratio": round(success_ratio, 4),
        "queue_failure_ratio": round(queue_failure_ratio, 4),
        "execution_failure_ratio": round(execution_failure_ratio, 4),
        "blocked_ratio": round(blocked_ratio, 4),
        "notes": quality_notes,
    }


async def _load_latest_observation(
    session: AsyncSession,
    *,
    organization_id: UUID,
    agent_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    stmt = (
        select(DesktopCommandResult.data, DesktopCommand.action)
        .join(DesktopCommand, DesktopCommand.id == DesktopCommandResult.command_id)
        .where(
            DesktopCommand.organization_id == organization_id,
            DesktopCommandResult.status == "success",
            DesktopCommand.action.in_(["take_screenshot", "browser_screenshot"]),
        )
        .order_by(desc(DesktopCommandResult.received_at))
        .limit(1)
    )
    if agent_id:
        stmt = stmt.where(DesktopCommand.agent_id == _parse_uuid(agent_id, field="agent_id"))

    row = (await session.execute(stmt)).first()
    if not row:
        return None
    payload, action = row
    if not isinstance(payload, dict):
        return None
    image_base64 = payload.get("image_base64")
    if not isinstance(image_base64, str) or not image_base64.strip():
        return None
    return {
        "image_base64": image_base64,
        "mime_type": str(payload.get("mime_type") or "image/png"),
        "width": int(payload.get("width") or 0),
        "height": int(payload.get("height") or 0),
        "source_action": str(action or ""),
    }


async def _wait_for_command_result(
    session: AsyncSession,
    *,
    command_id: UUID,
    timeout_seconds: float,
) -> Optional[DesktopCommandResult]:
    started = asyncio.get_event_loop().time()
    while True:
        stmt = select(DesktopCommandResult).where(DesktopCommandResult.command_id == command_id).limit(1)
        row = (await session.execute(stmt)).scalars().first()
        if row:
            return row
        if asyncio.get_event_loop().time() - started >= timeout_seconds:
            return None
        await asyncio.sleep(0.4)


async def _dispatch_if_possible(session: AsyncSession, *, command_id: UUID) -> None:
    cmd = (await session.execute(select(DesktopCommand).where(DesktopCommand.id == command_id))).scalars().first()
    if not cmd:
        return
    await _dispatch_command_if_online(session, command=cmd)


async def _run_self_healing_attempts(
    *,
    client_id: str,
    session: AsyncSession,
    run_id: str,
    goal: str,
    step_index_start: int,
    action: str,
    parameters: Dict[str, Any],
    request: DesktopAutonomyRunRequest,
    task_meta: Dict[str, str],
    vision_hint: Dict[str, Any],
    max_auto_risk: Optional[str],
    allow_high_risk: bool,
    force_human_approval: bool,
) -> Dict[str, Any]:
    variants = _build_self_healing_variants(
        action=action,
        parameters=parameters,
        vision_hint=vision_hint,
    )
    max_attempts = max(1, min(int(request.self_heal_max_attempts), len(variants)))
    attempts = variants[:max_attempts]
    steps: List[DesktopAutonomyStep] = []
    step_index = step_index_start
    attempts_log: List[Dict[str, Any]] = []
    healed = False

    for strategy, heal_action, heal_params in attempts:
        requires_approval, heal_risk = _approval_required_for_action(
            action=heal_action,
            parameters=heal_params,
            mode=request.execution_mode,
            allow_high_risk=allow_high_risk,
            force_human_approval=force_human_approval,
            max_auto_risk_override=max_auto_risk,
        )
        metadata = {
            "autonomy_run_id": run_id,
            "step_id": step_index,
            "goal": goal,
            "source": "desktop_autonomy",
            "recovery_control": "managed",
            "agent_id": request.agent_id,
            "self_healing": {
                "enabled": True,
                "strategy": strategy,
                "attempt": len(attempts_log) + 1,
            },
            **task_meta,
        }
        if requires_approval:
            metadata["approval"] = {"required": True, "status": "pending"}
            metadata["requires_human_approval"] = True

        enqueue_result = await _enqueue_with_hooks(
            client_id=client_id,
            run_id=run_id,
            session_id=f"desktop_autonomy:{run_id}",
            action=heal_action,
            parameters=heal_params,
            metadata=metadata,
        )
        accepted = bool(enqueue_result.get("accepted"))
        command_id = str(enqueue_result.get("command_id") or "") or None
        approval_required = bool(enqueue_result.get("approval_required", False))
        attempts_log.append(
            {
                "strategy": strategy,
                "action": heal_action,
                "accepted": accepted,
                "command_id": command_id,
                "approval_required": approval_required,
                "risk_level": heal_risk,
            }
        )
        steps.append(
            DesktopAutonomyStep(
                step_index=step_index,
                phase="evaluate",
                status=("queued" if accepted else "failed"),
                action=heal_action,
                parameters=heal_params,
                command_id=command_id,
                detail=(
                    f"Self-healing queued ({strategy})"
                    if accepted
                    else f"Self-healing enqueue failed ({strategy})"
                ),
                metadata={
                    "self_healing": True,
                    "strategy": strategy,
                    "attempt": len(attempts_log),
                    "enqueue_result": enqueue_result,
                    "risk_level": heal_risk,
                    **task_meta,
                },
            )
        )
        step_index += 1
        if not accepted:
            continue
        if approval_required:
            break
        if not request.wait_for_results:
            healed = True
            break

        try:
            command_uuid = UUID(str(command_id))
        except Exception:
            command_uuid = None
        if not command_uuid:
            continue
        await _dispatch_if_possible(session, command_id=command_uuid)
        final = await _wait_for_command_result(
            session,
            command_id=command_uuid,
            timeout_seconds=float(request.step_timeout_seconds),
        )
        if final and final.status == "success":
            healed = True
            steps.append(
                DesktopAutonomyStep(
                    step_index=step_index,
                    phase="evaluate",
                    status="ok",
                    action=heal_action,
                    command_id=command_id,
                    detail=f"Self-healing success ({strategy})",
                    metadata={
                        "self_healing": True,
                        "strategy": strategy,
                        "duration_ms": final.duration_ms,
                        "risk_level": heal_risk,
                        **task_meta,
                    },
                )
            )
            step_index += 1
            break

    return {
        "healed": healed,
        "steps": steps,
        "attempts": attempts_log,
        "next_step_index": step_index,
    }


async def _vision_hint_from_observation(
    *,
    observation: Optional[Dict[str, Any]],
    goal: str,
) -> Dict[str, Any]:
    if not observation:
        return {}
    image_base64 = observation.get("image_base64")
    if not isinstance(image_base64, str) or not image_base64:
        return {}
    hints = _goal_action_hints(goal)
    if not hints:
        return {}
    instruction = f"Find '{hints[0]}' in this screenshot"
    try:
        image_bytes = base64.b64decode(image_base64)
        analysis = await asyncio.to_thread(
            analyze_screen,
            image_bytes,
            instruction,
            image_mime_type=str(observation.get("mime_type") or "image/png"),
        )
        return {
            "element": analysis.get("element"),
            "x": analysis.get("x"),
            "y": analysis.get("y"),
            "confidence": analysis.get("confidence"),
        }
    except Exception:
        return {}


async def run_desktop_autonomy_loop(
    *,
    client_id: str,
    request: DesktopAutonomyRunRequest,
    session: AsyncSession,
) -> DesktopAutonomyRunResponse:
    organization_id = _parse_uuid(client_id, field="client_id")
    now = datetime.now(timezone.utc)
    run_id = str(uuid4())
    steps: List[DesktopAutonomyStep] = []

    observation = await _load_latest_observation(
        session,
        organization_id=organization_id,
        agent_id=request.agent_id,
    )
    steps.append(
        DesktopAutonomyStep(
            step_index=1,
            phase="observe",
            status="ok" if observation else "skipped",
            detail=("Loaded latest screenshot context" if observation else "No previous screenshot found"),
            metadata={
                "source_action": (observation or {}).get("source_action"),
                "width": (observation or {}).get("width"),
                "height": (observation or {}).get("height"),
            },
        )
    )

    vision_hint = await _vision_hint_from_observation(observation=observation, goal=request.goal)
    hierarchical_plan = _build_hierarchical_plan(request)
    plan_actions = list(hierarchical_plan.get("actions") or [])
    plan_tasks = list(hierarchical_plan.get("tasks") or [])
    allow_high_risk = bool(request.context.get("allow_high_risk_auto_approval"))
    force_human_approval = bool(request.context.get("force_human_approval"))
    base_max_auto_risk = _max_auto_approval_risk(
        mode=request.execution_mode,
        allow_high_risk=allow_high_risk,
    )
    max_auto_risk, risk_control_reasons = _dynamic_max_auto_approval_risk(
        request=request,
        base_max_risk=base_max_auto_risk,
        plan_actions=plan_actions,
    )
    steps.append(
        DesktopAutonomyStep(
            step_index=2,
            phase="think",
            status="planned",
            detail=f"Generated {len(plan_actions)} action steps across {len(plan_tasks)} tasks",
            metadata={
                "vision_hint": vision_hint,
                "goal_hints": _goal_action_hints(request.goal),
                "execution_mode": request.execution_mode,
                "base_auto_approval_max_risk": base_max_auto_risk,
                "auto_approval_max_risk": max_auto_risk,
                "dynamic_risk_controls": bool(request.dynamic_risk_controls),
                "dynamic_risk_control_reasons": risk_control_reasons,
                "allow_high_risk_auto_approval": allow_high_risk,
                "force_human_approval": force_human_approval,
                "hierarchical_plan": {
                    "tasks": plan_tasks,
                    "total_actions": int(hierarchical_plan.get("total_actions") or len(plan_actions)),
                    "truncated_actions": int(hierarchical_plan.get("truncated_actions") or 0),
                },
            },
        )
    )

    step_index = 3
    queued = 0
    failed = 0
    approvals_pending = 0
    auto_approved = 0
    executed_ok = 0
    executed_failed = 0
    recovered_ok = 0
    self_healed_ok = 0
    if request.dry_run:
        for item in plan_actions:
            steps.append(
                DesktopAutonomyStep(
                    step_index=step_index,
                    phase="act",
                    status="planned",
                    action=item["action"],
                    parameters=item["parameters"],
                    detail="Dry run - action not dispatched",
                    metadata={
                        "task_id": item.get("task_id"),
                        "task_title": item.get("task_title"),
                        "subtask_id": item.get("subtask_id"),
                        "subtask_title": item.get("subtask_title"),
                        "task_category": item.get("task_category"),
                    },
                )
            )
            step_index += 1
        status = "dry_run"
        summary = f"Dry-run completed with {len(plan_actions)} planned actions."
        response = DesktopAutonomyRunResponse(
            run_id=run_id,
            goal=request.goal,
            status=status,
            steps=steps,
            summary=summary,
            error=None,
            created_at=now,
            updated_at=datetime.now(timezone.utc),
        )
        _RUNS[run_id] = response
        return response

    for action_item in plan_actions:
        action = str(action_item["action"])
        parameters = dict(action_item.get("parameters") or {})
        task_id = str(action_item.get("task_id") or "")
        task_title = str(action_item.get("task_title") or "")
        subtask_id = str(action_item.get("subtask_id") or "")
        subtask_title = str(action_item.get("subtask_title") or "")
        task_category = str(action_item.get("task_category") or "")
        if action == "browser_click" and vision_hint.get("x") is not None and "selector" not in parameters and "text" not in parameters:
            parameters.setdefault("x", int(vision_hint["x"]))
            parameters.setdefault("y", int(vision_hint["y"]))
        # We only force approvals in safe/balanced modes (or when explicitly forced).
        # In autonomous mode we let enterprise policies decide (auto-approve when allowed,
        # otherwise the command will be queued pending approval and we stop the run).
        approval_override: Optional[bool] = None
        risk_level = _action_risk_level(action, parameters)
        if force_human_approval or request.execution_mode == "safe":
            approval_override = True
        elif request.execution_mode == "balanced":
            requires, _ = _approval_required_for_action(
                action=action,
                parameters=parameters,
                mode=request.execution_mode,
                allow_high_risk=allow_high_risk,
                force_human_approval=force_human_approval,
                max_auto_risk_override=max_auto_risk,
            )
            approval_override = True if requires else None
        elif request.execution_mode == "autonomous" and max_auto_risk is not None:
            requires, _ = _approval_required_for_action(
                action=action,
                parameters=parameters,
                mode=request.execution_mode,
                allow_high_risk=allow_high_risk,
                force_human_approval=force_human_approval,
                max_auto_risk_override=max_auto_risk,
            )
            approval_override = True if requires else None

        meta: Dict[str, Any] = {
            "autonomy_run_id": run_id,
            "step_id": step_index,
            "goal": request.goal,
            "source": "desktop_autonomy",
            # Desktop autonomy manages recovery synchronously; avoid background recovery duplication.
            "recovery_control": "managed",
            "agent_id": request.agent_id,
            "task_id": task_id,
            "task_title": task_title,
            "subtask_id": subtask_id,
            "subtask_title": subtask_title,
            "task_category": task_category,
        }
        if approval_override is True:
            meta["approval"] = {"required": True, "status": "pending"}
            meta["requires_human_approval"] = True
            meta["force_human_approval"] = True

        enqueue_result = await _enqueue_with_hooks(
            client_id=client_id,
            run_id=run_id,
            session_id=f"desktop_autonomy:{run_id}",
            action=action,
            parameters=parameters,
            metadata=meta,
        )
        accepted = bool(enqueue_result.get("accepted"))
        command_id = str(enqueue_result.get("command_id") or "") or None
        reason = str(enqueue_result.get("reason") or "")
        accepted_status = str(enqueue_result.get("status") or "")
        approval_required = bool(enqueue_result.get("approval_required", False))
        risk_level = str(enqueue_result.get("risk_level") or risk_level)

        if not accepted and reason == "runner_offline":
            from execution.desktop_executor import runner_offline_instructions
            steps.append(
                DesktopAutonomyStep(
                    step_index=step_index,
                    phase="act",
                    status="failed",
                    action=action,
                    parameters=parameters,
                    command_id=command_id,
                    detail="Runner offline: no desktop connected. See summary for how to start it.",
                    metadata={"reason": "runner_offline", "enqueue_result": enqueue_result},
                )
            )
            _RUNS[run_id] = DesktopAutonomyRunResponse(
                run_id=run_id,
                goal=request.goal,
                status="runner_offline",
                steps=steps,
                summary=runner_offline_instructions(),
                error="runner_offline",
                created_at=now,
                updated_at=datetime.now(timezone.utc),
            )
            return _RUNS[run_id]

        steps.append(
            DesktopAutonomyStep(
                step_index=step_index,
                phase="act",
                status=("queued" if accepted else "failed"),
                action=action,
                parameters=parameters,
                command_id=command_id,
                detail=(
                    (
                        f"{subtask_title}: queued pending approval"
                        if subtask_title
                        else "Command queued pending approval"
                    )
                    if accepted and approval_required
                    else (
                        (
                            f"{subtask_title}: auto-approved and queued"
                            if subtask_title
                            else "Command auto-approved and queued"
                        )
                        if accepted
                        else reason or "Dispatch rejected"
                    )
                ),
                metadata={
                    "enqueue_result": enqueue_result,
                    "approval_required": approval_required,
                    "risk_level": risk_level,
                    "queue_status": accepted_status or None,
                    "task_id": task_id,
                    "task_title": task_title,
                    "subtask_id": subtask_id,
                    "subtask_title": subtask_title,
                    "task_category": task_category,
                },
            )
        )
        step_index += 1
        if accepted:
            queued += 1
            if approval_required:
                approvals_pending += 1
            else:
                auto_approved += 1
        else:
            failed += 1

        if not accepted:
            steps.append(
                DesktopAutonomyStep(
                    step_index=step_index,
                    phase="evaluate",
                    status="failed",
                    action=action,
                    command_id=command_id,
                    detail="Action could not be queued",
                    metadata={
                        "approval_required": approval_required,
                        "risk_level": risk_level,
                        "task_id": task_id,
                        "task_title": task_title,
                        "subtask_id": subtask_id,
                        "subtask_title": subtask_title,
                        "task_category": task_category,
                    },
                )
            )
            step_index += 1
            if request.enable_self_healing:
                healing = await _run_self_healing_attempts(
                    client_id=client_id,
                    session=session,
                    run_id=run_id,
                    goal=request.goal,
                    step_index_start=step_index,
                    action=action,
                    parameters=parameters,
                    request=request,
                    task_meta={
                        "task_id": task_id,
                        "task_title": task_title,
                        "subtask_id": subtask_id,
                        "subtask_title": subtask_title,
                        "task_category": task_category,
                    },
                    vision_hint=vision_hint,
                    max_auto_risk=max_auto_risk,
                    allow_high_risk=allow_high_risk,
                    force_human_approval=force_human_approval,
                )
                steps.extend(healing["steps"])
                step_index = int(healing["next_step_index"])
                if bool(healing["healed"]):
                    self_healed_ok += 1
                    continue
            if request.stop_on_failure:
                break
            continue

        if approval_required:
            steps.append(
                DesktopAutonomyStep(
                    step_index=step_index,
                    phase="evaluate",
                    status="blocked",
                    action=action,
                    command_id=command_id,
                    detail=(
                        f"{subtask_title}: waiting for human approval"
                        if subtask_title
                        else "Waiting for human approval"
                    ),
                    metadata={
                        "approval_required": True,
                        "risk_level": risk_level,
                        "task_id": task_id,
                        "task_title": task_title,
                        "subtask_id": subtask_id,
                        "subtask_title": subtask_title,
                        "task_category": task_category,
                    },
                )
            )
            step_index += 1
            # Cannot proceed in an autonomous run if we hit a manual approval gate.
            break

        if not request.wait_for_results:
            steps.append(
                DesktopAutonomyStep(
                    step_index=step_index,
                    phase="evaluate",
                    status="queued",
                    action=action,
                    command_id=command_id,
                    detail=(
                        f"{subtask_title}: queued for automatic execution"
                        if subtask_title
                        else "Action queued for automatic execution"
                    ),
                    metadata={
                        "approval_required": False,
                        "risk_level": risk_level,
                        "task_id": task_id,
                        "task_title": task_title,
                        "subtask_id": subtask_id,
                        "subtask_title": subtask_title,
                        "task_category": task_category,
                    },
                )
            )
            step_index += 1
            continue

        # Wait for execution result.
        try:
            command_uuid = UUID(str(command_id))
        except Exception:
            command_uuid = None
        if not command_uuid:
            steps.append(
                DesktopAutonomyStep(
                    step_index=step_index,
                    phase="evaluate",
                    status="failed",
                    action=action,
                    command_id=command_id,
                    detail="Invalid command_id returned by executor",
                    metadata={
                        "approval_required": False,
                        "risk_level": risk_level,
                        "task_id": task_id,
                        "task_title": task_title,
                        "subtask_id": subtask_id,
                        "subtask_title": subtask_title,
                        "task_category": task_category,
                    },
                )
            )
            step_index += 1
            executed_failed += 1
            if request.enable_self_healing:
                healing = await _run_self_healing_attempts(
                    client_id=client_id,
                    session=session,
                    run_id=run_id,
                    goal=request.goal,
                    step_index_start=step_index,
                    action=action,
                    parameters=parameters,
                    request=request,
                    task_meta={
                        "task_id": task_id,
                        "task_title": task_title,
                        "subtask_id": subtask_id,
                        "subtask_title": subtask_title,
                        "task_category": task_category,
                    },
                    vision_hint=vision_hint,
                    max_auto_risk=max_auto_risk,
                    allow_high_risk=allow_high_risk,
                    force_human_approval=force_human_approval,
                )
                steps.extend(healing["steps"])
                step_index = int(healing["next_step_index"])
                if bool(healing["healed"]):
                    self_healed_ok += 1
                    continue
            if request.stop_on_failure:
                break
            continue

        await _dispatch_if_possible(session, command_id=command_uuid)
        final = await _wait_for_command_result(
            session,
            command_id=command_uuid,
            timeout_seconds=float(request.step_timeout_seconds),
        )
        if not final:
            steps.append(
                DesktopAutonomyStep(
                    step_index=step_index,
                    phase="evaluate",
                    status="failed",
                    action=action,
                    command_id=command_id,
                    detail="Timeout waiting for execution result",
                    metadata={
                        "approval_required": False,
                        "risk_level": risk_level,
                        "timeout_seconds": float(request.step_timeout_seconds),
                        "task_id": task_id,
                        "task_title": task_title,
                        "subtask_id": subtask_id,
                        "subtask_title": subtask_title,
                        "task_category": task_category,
                    },
                )
            )
            step_index += 1
            executed_failed += 1
            if request.enable_self_healing:
                healing = await _run_self_healing_attempts(
                    client_id=client_id,
                    session=session,
                    run_id=run_id,
                    goal=request.goal,
                    step_index_start=step_index,
                    action=action,
                    parameters=parameters,
                    request=request,
                    task_meta={
                        "task_id": task_id,
                        "task_title": task_title,
                        "subtask_id": subtask_id,
                        "subtask_title": subtask_title,
                        "task_category": task_category,
                    },
                    vision_hint=vision_hint,
                    max_auto_risk=max_auto_risk,
                    allow_high_risk=allow_high_risk,
                    force_human_approval=force_human_approval,
                )
                steps.extend(healing["steps"])
                step_index = int(healing["next_step_index"])
                if bool(healing["healed"]):
                    self_healed_ok += 1
                    continue
            if request.stop_on_failure:
                break
            continue

        if final.status == "success":
            steps.append(
                DesktopAutonomyStep(
                    step_index=step_index,
                    phase="evaluate",
                    status="ok",
                    action=action,
                    command_id=command_id,
                    detail=(
                        f"{subtask_title}: executed successfully"
                        if subtask_title
                        else "Executed successfully"
                    ),
                    metadata={
                        "approval_required": False,
                        "risk_level": risk_level,
                        "duration_ms": final.duration_ms,
                        "task_id": task_id,
                        "task_title": task_title,
                        "subtask_id": subtask_id,
                        "subtask_title": subtask_title,
                        "task_category": task_category,
                    },
                )
            )
            step_index += 1
            executed_ok += 1
            continue

        # Failed: try recovery (if enabled).
        executed_failed += 1
        recovery_payload: Optional[Dict[str, Any]] = None
        recovered = False
        if request.enable_recovery:
            retry = await trigger_recovery_for_failed_command(
                organization_id=str(organization_id),
                failed_command_id=str(command_uuid),
                dispatch_command=_dispatch_command_if_online,
            )
            recovery_payload = {
                "success": bool(retry.success),
                "attempts_used": int(retry.attempts_used or 0),
                "final_reason": retry.final_reason,
                "final_strategy": retry.final_strategy,
            }
            recovered = bool(retry.success)

        steps.append(
            DesktopAutonomyStep(
                step_index=step_index,
                phase="evaluate",
                status=("ok" if recovered else "failed"),
                action=action,
                command_id=command_id,
                detail=(
                    f"{subtask_title}: recovered automatically"
                    if recovered and subtask_title
                    else ("Recovered automatically" if recovered else "Command failed")
                ),
                metadata={
                    "approval_required": False,
                    "risk_level": risk_level,
                    "duration_ms": final.duration_ms,
                    "error": final.error,
                    "recovery": recovery_payload,
                    "task_id": task_id,
                    "task_title": task_title,
                    "subtask_id": subtask_id,
                    "subtask_title": subtask_title,
                    "task_category": task_category,
                },
            )
        )
        step_index += 1
        if recovered:
            recovered_ok += 1
            continue
        if request.enable_self_healing:
            healing = await _run_self_healing_attempts(
                client_id=client_id,
                session=session,
                run_id=run_id,
                goal=request.goal,
                step_index_start=step_index,
                action=action,
                parameters=parameters,
                request=request,
                task_meta={
                    "task_id": task_id,
                    "task_title": task_title,
                    "subtask_id": subtask_id,
                    "subtask_title": subtask_title,
                    "task_category": task_category,
                },
                vision_hint=vision_hint,
                max_auto_risk=max_auto_risk,
                allow_high_risk=allow_high_risk,
                force_human_approval=force_human_approval,
            )
            steps.extend(healing["steps"])
            step_index = int(healing["next_step_index"])
            if bool(healing["healed"]):
                self_healed_ok += 1
                continue
        if request.stop_on_failure:
            break

    if queued > 0 and failed == 0 and approvals_pending == 0:
        status = "completed"
        action_line = (
            f"{executed_ok} actions executed."
            if executed_ok >= queued and queued > 0
            else f"{queued} actions dispatched."
        )
        summary = (
            f"{action_line} "
            f"{auto_approved} auto-approved, {executed_ok} executed ok, "
            f"{recovered_ok} recovered, {self_healed_ok} self-healed."
        )
        error = None
    elif queued > 0 and failed == 0:
        status = "blocked"
        summary = (
            f"{queued} actions queued. "
            f"{approvals_pending} waiting for approval, {auto_approved} auto-approved."
        )
        error = None
    elif queued > 0 and failed > 0 and approvals_pending > 0:
        status = "blocked"
        summary = (
            f"{queued} actions queued ({auto_approved} auto-approved, {approvals_pending} pending approval), "
            f"{failed} actions failed to queue."
        )
        error = "partial_dispatch_failure"
    elif queued > 0 and failed > 0:
        status = "failed"
        summary = f"{queued} actions queued automatically, {failed} actions failed to queue."
        error = "partial_dispatch_failure"
    else:
        status = "failed"
        summary = "No actions could be queued."
        error = "all_actions_failed"

    quality = _evaluate_autonomy_quality(
        request=request,
        steps=steps,
        planned_actions=len(plan_actions),
        queued=queued,
        failed_to_queue=failed,
        approvals_pending=approvals_pending,
        executed_ok=executed_ok,
        executed_failed=executed_failed,
        recovered_ok=recovered_ok + self_healed_ok,
    )
    if request.enforce_quality_gate and not bool(quality.get("quality_gate_passed")):
        if status == "completed":
            status = "blocked"
        error = "quality_gate_failed"

    step_index += 1
    steps.append(
        DesktopAutonomyStep(
            step_index=step_index,
            phase="evaluate",
            status=(
                "ok"
                if bool(quality.get("quality_gate_passed"))
                else ("blocked" if request.enforce_quality_gate else "failed")
            ),
            detail=(
                f"Quality score {quality.get('score')} "
                f"(threshold {quality.get('threshold')})"
            ),
            metadata={
                "quality": quality,
                "enforce_quality_gate": bool(request.enforce_quality_gate),
                "self_healing": {
                    "enabled": bool(request.enable_self_healing),
                    "max_attempts": int(request.self_heal_max_attempts),
                    "successes": int(self_healed_ok),
                },
                "risk_controls": {
                    "dynamic_enabled": bool(request.dynamic_risk_controls),
                    "max_auto_risk": max_auto_risk,
                },
            },
        )
    )
    summary = f"{summary} quality_score={quality.get('score')}"

    response = DesktopAutonomyRunResponse(
        run_id=run_id,
        goal=request.goal,
        status=status,
        steps=steps,
        summary=summary,
        error=error,
        created_at=now,
        updated_at=datetime.now(timezone.utc),
    )
    _RUNS[run_id] = response
    return response
