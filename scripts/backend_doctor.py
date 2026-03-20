import argparse
import asyncio
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.env_helpers import env_bool


def _present(name: str) -> str:
    return "set" if os.getenv(name) else "missing"


def _print_section(title: str) -> None:
    print("")
    print(f"== {title} ==")


async def _check_db() -> Tuple[bool, str]:
    try:
        from database import AsyncSessionLocal
    except Exception as exc:
        return False, f"import_database_failed: {exc!s}"

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


async def _check_tables(expected: List[str]) -> Tuple[bool, Dict[str, Any]]:
    from database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    """
                )
            )
            present = {str(x[0]) for x in rows.fetchall() if x and x[0]}
    except Exception as exc:
        return False, {"error": str(exc)}

    missing = [name for name in expected if name not in present]
    return len(missing) == 0, {"missing": missing, "present_count": len(present)}


async def _db_checks(expected: List[str]) -> Tuple[bool, str, bool, Dict[str, Any]]:
    db_ok, db_detail = await _check_db()
    if not db_ok:
        return False, db_detail, False, {}
    tables_ok, table_detail = await _check_tables(expected)
    return db_ok, db_detail, tables_ok, table_detail


def _check_imports() -> Tuple[bool, str]:
    try:
        import main  # noqa: F401
    except Exception as exc:
        return False, str(exc)
    return True, "ok"


def _summarize_flags() -> Dict[str, Any]:
    keys = [
        "ENABLE_AUTONOMOUS_RUNNER",
        "ENABLE_AI_COO",
        "ENABLE_ENTERPRISE_POLICIES",
        "ENABLE_WORKFLOW_RECORDER",
        "ENABLE_HUMAN_RECORDER",
        "ENABLE_DESKTOP_AGENT",
        "ENABLE_BROWSER_AGENT",
        "ENABLE_DESKTOP_AUTONOMY_LOOP",
        "ENABLE_BUSINESS_CONTEXT_REASONING",
        "ENABLE_TASK_STUDIO",
        "ENABLE_EXECUTION_QUEUE",
        "ENABLE_PREDICTIVE_AUTOMATION",
        "ENABLE_PACKS",
        "ENABLE_OMNICHANNEL",
        "ENABLE_SALES_AUTOPILOT",
        "ENABLE_COLLECTIONS_AI",
        "ENABLE_RECRUITING_AI",
        "ENABLE_PROCESS_AUDIT",
        "ENABLE_ANALYTICS_AI",
        "ENABLE_CONTENT_FACTORY",
        "ENABLE_INTERNAL_ASSISTANT",
    ]
    return {k: env_bool(k, "false") for k in keys}


def _check_full_auto_profile() -> Dict[str, Any]:
    preset = str(os.getenv("POLICY_AUTO_APPLY_PRESET", "")).strip().lower()
    return {
        "autonomy_default_execution_mode": str(
            os.getenv("AUTONOMY_DEFAULT_EXECUTION_MODE", "")
        ).strip()
        or "(unset)",
        "autonomy_default_max_auto_risk": str(
            os.getenv("AUTONOMY_DEFAULT_MAX_AUTO_RISK", "")
        ).strip()
        or "(unset)",
        "policy_auto_apply_preset": preset or "(unset)",
        "full_auto_profile_ok": (
            (os.getenv("AUTONOMY_DEFAULT_EXECUTION_MODE", "").strip().lower() == "full_auto")
            and (os.getenv("AUTONOMY_DEFAULT_MAX_AUTO_RISK", "").strip().lower() == "high")
            and (preset in {"openclaw", "openclaw_allow_all"})
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backend environment doctor / VM smoke checks")
    parser.add_argument("--skip-db", action="store_true", help="Skip DB connectivity and schema checks")
    args = parser.parse_args()

    print("kan-core backend doctor")
    print(f"python={sys.version.split()[0]}")

    ok = True

    _print_section("Imports")
    imp_ok, imp_detail = _check_imports()
    print(f"import main: {imp_detail}")
    ok = ok and imp_ok

    _print_section("Env")
    print(f"DATABASE_URL: {_present('DATABASE_URL')} (required for runner/autonomy)")
    print(f"MASTER_KEY/DATA_ENCRYPTION_KEY: {_present('MASTER_KEY') if os.getenv('MASTER_KEY') else _present('DATA_ENCRYPTION_KEY')}")
    print(f"OPENAI_API_KEY: {_present('OPENAI_API_KEY')} (required for vision if enabled)")
    print(f"N8N_API_URL: {_present('N8N_API_URL')} (required for export n8n)")
    print(f"N8N_API_KEY: {_present('N8N_API_KEY')} (required for export n8n)")

    _print_section("Feature Flags")
    for k, v in _summarize_flags().items():
        print(f"{k}={str(v).lower()}")

    _print_section("Autonomy Profile")
    profile = _check_full_auto_profile()
    print(f"AUTONOMY_DEFAULT_EXECUTION_MODE={profile['autonomy_default_execution_mode']}")
    print(f"AUTONOMY_DEFAULT_MAX_AUTO_RISK={profile['autonomy_default_max_auto_risk']}")
    print(f"POLICY_AUTO_APPLY_PRESET={profile['policy_auto_apply_preset']}")
    print(f"full_auto_profile_ok={str(bool(profile['full_auto_profile_ok'])).lower()}")

    if not args.skip_db:
        _print_section("Database")
        expected_tables = [
            "clients",
            "desktop_agents",
            "desktop_commands",
            "desktop_command_results",
            "workflow_recordings",
            "workflow_recording_steps",
            "workflow_playbooks",
            "workflow_playbook_runs",
            "tasks",
            "task_runs",
            "task_run_steps",
            "evidence_assets",
            "execution_jobs",
            "omnichannel_threads",
            "omnichannel_messages",
            "customer_profiles",
            "sales_pipeline_events",
            "collections_cases",
            "collections_actions",
            "recruiting_candidates",
            "recruiting_interviews",
            "recruiting_scores",
            "process_audit_jobs",
            "process_audit_findings",
            "analytics_uploads",
            "analytics_insights",
            "content_plans",
            "content_assets",
            "internal_docs",
            "internal_doc_chunks",
            "internal_queries",
            "ui_states",
            "ui_transitions",
        ]
        db_ok, db_detail, tables_ok, table_detail = asyncio.run(_db_checks(expected_tables))
        print(f"connectivity: {('ok' if db_ok else 'failed')} ({db_detail})")
        ok = ok and db_ok

        if db_ok:
            if isinstance(table_detail, dict) and table_detail.get("error"):
                print(f"schema: error={table_detail['error']}")
            else:
                missing = table_detail.get("missing") if isinstance(table_detail, dict) else None
                if missing:
                    print(f"schema: missing_tables={missing}")
                else:
                    print("schema: ok")
            ok = ok and tables_ok

    _print_section("Result")
    if ok:
        print("OK: backend looks healthy for VM run.")
        raise SystemExit(0)

    print("FAIL: backend has issues. Fix DB/env flags and retry.")
    print("Hint: if DB is missing, start Postgres or set DATABASE_URL; then run `alembic upgrade head` or `AUTO_CREATE_DB=true` once.")
    raise SystemExit(2)


if __name__ == "__main__":
    main()
