from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List
from uuid import NAMESPACE_URL, UUID, uuid5

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import AsyncSessionLocal, Base, close_db, engine
from models import (
    AuditLog,
    AutonomousCase,
    AutonomousCaseStep,
    BusinessStateSnapshot,
    Client,
    ExecutionLease,
    IdempotencyRecord,
    OperationMemoryEntry,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _fixture_paths() -> List[Path]:
    return [
        FIXTURES_DIR / "payment_cases.json",
        FIXTURES_DIR / "collections_cases.json",
        FIXTURES_DIR / "onboarding_cases.json",
        FIXTURES_DIR / "marketing_cases.json",
        FIXTURES_DIR / "sales_cases.json",
    ]


def _load_fixture_cases() -> List[Dict[str, object]]:
    cases: List[Dict[str, object]] = []
    for path in _fixture_paths():
        if path.exists():
            cases.extend(json.loads(path.read_text(encoding="utf-8")))
    return cases


def _eval_clients() -> List[Dict[str, object]]:
    items: List[Dict[str, object]] = []
    seen: set[UUID] = set()
    for case in _load_fixture_cases():
        payload = dict(case.get("input") or {})
        raw_org = str(payload.get("organization_id") or case.get("case_id") or "eval-client").strip()
        client_id = uuid5(NAMESPACE_URL, f"case-manager:{raw_org}")
        if client_id in seen:
            continue
        seen.add(client_id)
        items.append(
            {
                "id": client_id,
                "name": f"eval_{raw_org}",
                "api_key_encrypted": "eval_dummy_api_key",
                "shopify_token_encrypted": None,
                "meta_token_encrypted": None,
                "alpaca_token_encrypted": None,
                "persona": {"source": "eval_harness", "raw_organization_id": raw_org},
                "llm_preferences": {},
                "system_prompt": "eval_harness_dummy_client",
                "model_name": "gemini-1.5-flash",
                "temperature": 0.4,
                "is_active": True,
            }
        )
    return items


async def prepare_eval_db() -> Dict[str, int]:
    eval_clients = _eval_clients()
    org_ids = [UUID(str(item["id"])) for item in eval_clients]

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        if eval_clients:
            await session.execute(pg_insert(Client.__table__).values(eval_clients).on_conflict_do_nothing())

        if org_ids:
            for model in (
                AuditLog,
                IdempotencyRecord,
                ExecutionLease,
                OperationMemoryEntry,
                BusinessStateSnapshot,
                AutonomousCaseStep,
                AutonomousCase,
            ):
                await session.execute(delete(model).where(model.organization_id.in_(org_ids)))

        await session.commit()

    await close_db()
    return {
        "client_count": len(eval_clients),
        "organization_count": len(org_ids),
    }


def main() -> None:
    summary = asyncio.run(prepare_eval_db())
    print(
        "Prepared eval DB:",
        f"clients={summary['client_count']}",
        f"organizations={summary['organization_count']}",
    )


if __name__ == "__main__":
    main()
