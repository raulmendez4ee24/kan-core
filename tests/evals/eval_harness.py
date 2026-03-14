"""
Eval harness real: compara Anthropic agentic runtime vs legacy por dominio.
Corre los mismos casos sobre ambos runtimes y produce reporte comparativo.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from database import AsyncSessionLocal, close_db
from brain.autonomous_case_manager import AutonomousCaseManager
from models import AuditLog, Client

FIXTURES_DIR = Path(__file__).parent / "fixtures"
DOMAIN_FIXTURE_MAP = {
    "payment": "payment_cases.json",
    "collections": "collections_cases.json",
    "onboarding": "onboarding_cases.json",
    "campaign_execution": "marketing_cases.json",
    "sales_execution": "sales_cases.json",
}


@dataclass
class CaseResult:
    case_id: str
    scenario: str
    runtime: str
    status_final: str
    actions_taken: List[str]
    did_escalate: bool
    outcome_matched: bool
    tool_switches: int
    total_tokens: int
    anthropic_activated: bool
    duration_seconds: float
    failure_reason: Optional[str] = None
    error: Optional[str] = None


@dataclass
class DomainReport:
    domain: str
    total_cases: int
    anthropic_completion_rate: float = 0.0
    legacy_completion_rate: float = 0.0
    anthropic_avg_tokens: float = 0.0
    legacy_avg_tokens: float = 0.0
    anthropic_avg_duration: float = 0.0
    legacy_avg_duration: float = 0.0
    anthropic_outcome_match_rate: float = 0.0
    legacy_outcome_match_rate: float = 0.0
    anthropic_activation_rate: float = 0.0
    winner: str = "sin_datos"
    results: List[CaseResult] = field(default_factory=list)


def load_fixtures(domain: str) -> List[Dict[str, Any]]:
    filename = DOMAIN_FIXTURE_MAP.get(domain, f"{domain}_cases.json")
    path = FIXTURES_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"No se encontraron fixtures en {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _organization_uuid_for_case(case: Dict[str, Any]) -> UUID:
    payload = dict(case.get("input") or {})
    raw_org = str(payload.get("organization_id") or case.get("case_id") or "eval-client").strip()
    return uuid5(NAMESPACE_URL, f"case-manager:{raw_org}")


async def _seed_rollbackable_audit_if_needed(
    session: Any,
    *,
    case: Dict[str, Any],
    organization_id: UUID,
) -> None:
    context = dict(dict(case.get("input") or {}).get("context") or {})
    seed = dict(context.get("eval_seed_rollbackable") or {})
    if not seed:
        return
    operation_type = str(seed.get("operation_type") or "delete").strip().lower()
    action_name = str(seed.get("action_name") or "delete_customer").strip().lower()
    restore_payload = dict(seed.get("restore_payload") or {})
    await session.execute(
        insert(AuditLog.__table__).values(
            {
                AuditLog.__table__.c.id: uuid4(),
                AuditLog.__table__.c.organization_id: organization_id,
                AuditLog.__table__.c.actor_user_id: None,
                AuditLog.__table__.c.action: "execution_guard.preflight",
                AuditLog.__table__.c.resource: "eval_harness",
                AuditLog.__table__.c.resource_id: str(case.get("case_id") or ""),
                AuditLog.__table__.c.status: "ok",
                AuditLog.__table__.c.detail: "seeded_rollbackable_action",
                AuditLog.__table__.c.metadata: {
                    "operation_type": operation_type,
                    "action_name": action_name,
                    "context_signature": f"eval:{case.get('case_id')}",
                    "rollback_snapshot": {
                        "action": action_name,
                        "operation_type": operation_type,
                        "reversible": True,
                        "impact": {"records": 1, "amount_usd": 0.0, "affected_clients": 1},
                        "targets": {"record_id": str(case.get("case_id") or "")},
                        "restore_payload": restore_payload,
                    },
                },
            }
        )
    )
    await session.commit()


async def _ensure_eval_client(
    session: Any,
    *,
    case: Dict[str, Any],
    organization_id: UUID,
) -> None:
    raw_org = str(dict(case.get("input") or {}).get("organization_id") or case.get("case_id") or "eval-client").strip()
    await session.execute(
        pg_insert(Client.__table__).values(
            id=organization_id,
            name=f"eval_{raw_org}",
            api_key_encrypted="eval_dummy_api_key",
            shopify_token_encrypted=None,
            meta_token_encrypted=None,
            alpaca_token_encrypted=None,
            persona={"source": "eval_harness", "raw_organization_id": raw_org},
            llm_preferences={},
            system_prompt="eval_harness_dummy_client",
            model_name="gemini-1.5-flash",
            temperature=0.4,
            is_active=True,
        ).on_conflict_do_nothing()
    )
    await session.commit()


def evaluate_outcome(result_status: str, actions: List[str], case: Dict[str, Any], did_escalate: bool) -> bool:
    expected = dict(case.get("expected_outcome") or {})
    valid_statuses = list(expected.get("status_validos") or [])
    expected_actions = [str(item).lower() for item in list(expected.get("acciones_esperadas") or [])]
    forbidden_actions = [str(item).lower() for item in list(expected.get("no_debe_hacer") or [])]
    should_escalate = bool(expected.get("debe_escalar"))

    status_ok = result_status in valid_statuses if valid_statuses else True
    escalation_ok = did_escalate if should_escalate else True
    expected_ok = True
    if expected_actions:
        expected_ok = any(any(token in action.lower() for token in expected_actions) for action in actions)
    forbidden_ok = not any(any(token in action.lower() for token in forbidden_actions) for action in actions)
    return status_ok and escalation_ok and expected_ok and forbidden_ok


def _build_failure_reason(
    *,
    case: Dict[str, Any],
    status_final: str,
    did_escalate: bool,
    outcome: Dict[str, Any],
    supervision: Dict[str, Any],
    execution_result: Dict[str, Any],
) -> str:
    expected = dict(case.get("expected_outcome") or {})
    expected_statuses = ",".join(str(item) for item in list(expected.get("status_validos") or [])) or "any"
    expected_escalate = bool(expected.get("debe_escalar"))
    outcome_reason = str(outcome.get("reason") or "outcome_mismatch")
    supervision_action = str(supervision.get("action") or "none")
    attempted_replan = supervision_action in {"replan", "switch_tool"}
    attempted_compensation = bool(execution_result.get("rollback_attempted")) or bool(execution_result.get("compensated")) or supervision_action == "rollback_and_compensate"
    actual_vs_expected = f"status={status_final} expected={expected_statuses}"
    escalation_vs_expected = f"escalated={did_escalate} expected_escalate={expected_escalate}"
    path_summary = (
        f"supervision={supervision_action} "
        f"replan_attempted={attempted_replan} "
        f"compensation_attempted={attempted_compensation}"
    )
    return f"{outcome_reason}; {actual_vs_expected}; {escalation_vs_expected}; {path_summary}"


def _extract_actions(run_result: Dict[str, Any]) -> List[str]:
    actions: List[str] = []
    decision = dict(run_result.get("decision") or {})
    execution_result = dict(run_result.get("execution_result") or {})
    step = dict(run_result.get("step") or {})
    recommended_action = str(decision.get("recommended_action") or "").strip()
    if recommended_action:
        actions.append(recommended_action)
    selected_tool = str(execution_result.get("selected_tool") or "").strip()
    if selected_tool:
        actions.append(f"tool:{selected_tool}")
    attempts = list(execution_result.get("attempts") or [])
    for attempt in attempts:
        if isinstance(attempt, dict):
            tool_name = str(attempt.get("tool") or attempt.get("selected_tool") or "").strip()
            if tool_name:
                actions.append(f"attempt:{tool_name}")
    step_status = str(step.get("status") or "").strip()
    if step_status:
        actions.append(f"step:{step_status}")
    return actions


def _extract_total_tokens(run_result: Dict[str, Any]) -> int:
    decision = dict(run_result.get("decision") or {})
    execution_result = dict(run_result.get("execution_result") or {})
    agentic = dict(decision.get("agentic") or {})
    usage_blocks = [
        dict(agentic.get("usage") or {}),
        dict(execution_result.get("usage") or {}),
    ]
    totals = []
    for usage in usage_blocks:
        if usage:
            try:
                total = int(usage.get("total_tokens") or 0)
            except (TypeError, ValueError):
                total = 0
            if not total:
                try:
                    total = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
                except (TypeError, ValueError):
                    total = 0
            if total:
                totals.append(total)
    for value in (
        agentic.get("total_tokens"),
        execution_result.get("total_tokens"),
    ):
        try:
            token_total = int(value or 0)
        except (TypeError, ValueError):
            token_total = 0
        if token_total:
            totals.append(token_total)
    return sum(totals)


async def _run_case_on_runtime_async(case: Dict[str, Any], runtime: str) -> CaseResult:
    start = time.time()
    case_input = dict(case.get("input") or {})
    try:
        async with AsyncSessionLocal() as session:
            organization_id = _organization_uuid_for_case(case)
            await _ensure_eval_client(
                session,
                case=case,
                organization_id=organization_id,
            )
            await _seed_rollbackable_audit_if_needed(
                session,
                case=case,
                organization_id=organization_id,
            )
            manager = AutonomousCaseManager(use_anthropic=(runtime == "anthropic"))
            run_result = await manager.run_case(
                session,
                case_input,
                domain=str(case.get("_domain") or "").strip().lower(),
                scenario=str(case.get("scenario") or ""),
            )
            final_case = dict(run_result.get("case") or {})
            supervision = dict(run_result.get("supervision") or {})
            actions = _extract_actions(run_result)
            status_final = str(final_case.get("status") or run_result.get("status") or "unknown")
            did_escalate = status_final == "escalated" or str(supervision.get("action") or "") == "escalate"
            outcome = dict(run_result.get("outcome_evaluation") or {})
            outcome_matched = bool(outcome.get("matches_expected"))
            if not outcome:
                outcome_matched = evaluate_outcome(status_final, actions, case, did_escalate)
            attempts = list(dict(run_result.get("execution_result") or {}).get("attempts") or [])
            tool_switches = max(0, len(attempts) - 1)
            if str(supervision.get("action") or "") == "switch_tool" and not tool_switches:
                tool_switches = 1
            anthropic_activated = bool(dict(run_result.get("decision") or {}).get("agentic", {}).get("activated"))
            failure_reason = None
            if not outcome_matched:
                failure_reason = _build_failure_reason(
                    case=case,
                    status_final=status_final,
                    did_escalate=did_escalate,
                    outcome=outcome,
                    supervision=supervision,
                    execution_result=dict(run_result.get("execution_result") or {}),
                )
            duration = time.time() - start
            return CaseResult(
                case_id=str(case.get("case_id") or final_case.get("id") or ""),
                scenario=str(case.get("scenario") or ""),
                runtime=runtime,
                status_final=status_final,
                actions_taken=actions,
                did_escalate=did_escalate,
                outcome_matched=outcome_matched,
                tool_switches=tool_switches,
                total_tokens=_extract_total_tokens(run_result),
                anthropic_activated=anthropic_activated,
                duration_seconds=round(duration, 3),
                failure_reason=failure_reason,
            )
    except Exception as exc:
        duration = time.time() - start
        return CaseResult(
            case_id=str(case.get("case_id") or ""),
            scenario=str(case.get("scenario") or ""),
            runtime=runtime,
            status_final="error",
            actions_taken=[],
            did_escalate=False,
            outcome_matched=False,
            tool_switches=0,
            total_tokens=0,
            anthropic_activated=False,
            duration_seconds=round(duration, 3),
            failure_reason=f"runtime_error; status=error expected=unknown; escalated=False expected_escalate=unknown; supervision=none replan_attempted=False compensation_attempted=False",
            error=str(exc),
        )
    finally:
        await close_db()


def run_case_on_runtime(case: Dict[str, Any], runtime: str) -> CaseResult:
    """
    Ejecuta un caso sobre el runtime especificado usando el autonomous_case_manager real.
    El feature flag ENABLE_ANTHROPIC_AGENTIC_RUNTIME decide si el loop usa runtime Anthropic o legacy.
    """
    return asyncio.run(_run_case_on_runtime_async(case, runtime))


def run_domain_eval(domain: str) -> DomainReport:
    cases = load_fixtures(domain)
    report = DomainReport(domain=domain, total_cases=len(cases))

    anthropic_results: List[CaseResult] = []
    legacy_results: List[CaseResult] = []

    for raw_case in cases:
        case = {**raw_case, "_domain": domain}
        print(f"  [{domain}] {case['case_id']} -> anthropic...")
        anthropic_results.append(run_case_on_runtime(case, "anthropic"))
        print(f"  [{domain}] {case['case_id']} -> legacy...")
        legacy_results.append(run_case_on_runtime(case, "legacy"))

    def rate(results: List[CaseResult], field: str) -> float:
        if not results:
            return 0.0
        vals = [getattr(r, field) for r in results]
        return sum(vals) / len(vals)

    report.anthropic_completion_rate = rate(anthropic_results, "outcome_matched")
    report.legacy_completion_rate = rate(legacy_results, "outcome_matched")
    report.anthropic_avg_tokens = rate(anthropic_results, "total_tokens")
    report.legacy_avg_tokens = rate(legacy_results, "total_tokens")
    report.anthropic_avg_duration = rate(anthropic_results, "duration_seconds")
    report.legacy_avg_duration = rate(legacy_results, "duration_seconds")
    report.anthropic_outcome_match_rate = report.anthropic_completion_rate
    report.legacy_outcome_match_rate = report.legacy_completion_rate
    report.anthropic_activation_rate = rate(anthropic_results, "anthropic_activated")

    if report.anthropic_completion_rate > report.legacy_completion_rate:
        report.winner = "anthropic"
    elif report.legacy_completion_rate > report.anthropic_completion_rate:
        report.winner = "legacy"
    else:
        if report.anthropic_avg_duration < report.legacy_avg_duration:
            report.winner = "anthropic"
        elif report.legacy_avg_duration < report.anthropic_avg_duration:
            report.winner = "legacy"
        else:
            report.winner = "empate"

    report.results = anthropic_results + legacy_results
    return report


def print_report(reports: List[DomainReport]) -> None:
    print("\n" + "=" * 60)
    print("BENCHMARK REAL: Anthropic Runtime vs Legacy")
    print("=" * 60)

    for report in reports:
        print(f"\nDOMINIO: {report.domain.upper()} ({report.total_cases} casos)")
        print("  Outcome match rate:")
        print(f"    Anthropic : {report.anthropic_completion_rate:.0%}")
        print(f"    Legacy    : {report.legacy_completion_rate:.0%}")
        print("  Tokens promedio:")
        print(f"    Anthropic : {report.anthropic_avg_tokens:.0f}")
        print(f"    Legacy    : {report.legacy_avg_tokens:.0f}")
        print(f"  Activacion Anthropic: {report.anthropic_activation_rate:.0%}")
        print("  Duracion promedio:")
        print(f"    Anthropic : {report.anthropic_avg_duration:.3f}s")
        print(f"    Legacy    : {report.legacy_avg_duration:.3f}s")
        print(f"  Ganador: {report.winner.upper()}")
        failures = [item for item in report.results if not item.outcome_matched]
        if failures:
            print("  Casos con failure_reason:")
            for item in failures:
                print(f"    - [{item.runtime}] {item.case_id}: {item.failure_reason}")

    print("\n" + "=" * 60)
    winners = [item.winner for item in reports]
    anthropic_wins = winners.count("anthropic")
    legacy_wins = winners.count("legacy")
    print(f"RESUMEN GLOBAL: Anthropic {anthropic_wins} - Legacy {legacy_wins}")
    if anthropic_wins > legacy_wins:
        print("Anthropic runtime esta listo para gobernar mas casos")
    elif legacy_wins > anthropic_wins:
        print("Legacy sigue siendo mas confiable; revisar antes de migrar")
    else:
        print("Empate; necesitas mas casos para decidir")
    print("=" * 60)


def run_full_benchmark() -> List[DomainReport]:
    setup_script = Path(__file__).with_name("setup_eval_db.py")
    os.environ["N8N_MOCK_MODE"] = "true"
    os.environ["EVAL_HARNESS_SAFE_RUNTIME"] = "true"
    print(f"Preparando eval DB con: {setup_script.name}...")
    subprocess.run([sys.executable, str(setup_script)], check=True)
    domains = ["payment", "collections", "onboarding", "campaign_execution", "sales_execution"]
    reports: List[DomainReport] = []
    for domain in domains:
        print(f"\nEvaluando dominio: {domain}...")
        try:
            reports.append(run_domain_eval(domain))
        except FileNotFoundError as exc:
            print(f"  {exc} - saltando dominio")
    print_report(reports)
    return reports


if __name__ == "__main__":
    run_full_benchmark()
