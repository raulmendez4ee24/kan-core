from __future__ import annotations

import asyncio
from pathlib import Path

from datetime import datetime, timedelta, timezone

from brain.meta_ads_autonomy import (
    DryRunAction,
    MetaAdsAnalyzer,
    MetaAdsExecutor,
    MetaAdsGuardrails,
    approve_pending_action,
    build_meta_ads_dry_run,
    generate_and_store_meta_ads_dry_run,
    reject_pending_action,
)
from brain.revenue_brain import DailyBriefing, RevenueBrain, RevenueDiagnosis
from brain.meta_ads_operator.models import EntityInsight, EntityType, MetaAdsMetrics, MetaAdsSnapshot
from brain.revenue_brain import RevenueBrain


def _entity(
    *,
    entity_type: EntityType,
    entity_id: str,
    name: str,
    spend: float = 0.0,
    impressions: float = 0.0,
    link_ctr: float = 0.0,
    ctr_drop_pct: float = 0.0,
    frequency: float = 0.0,
    roas: float = 0.0,
    cost_per_lead: float = 0.0,
    cost_per_booking: float = 0.0,
    reply_rate: float = 0.0,
    booked_rate: float = 0.0,
    close_rate: float = 0.0,
    revenue: float = 0.0,
    budget_change_pct: float = 0.0,
    leads: float = 0.0,
    bookings: float = 0.0,
    purchases: float = 0.0,
) -> EntityInsight:
    return EntityInsight(
        entity_type=entity_type,
        entity_id=entity_id,
        name=name,
        metrics=MetaAdsMetrics(
            spend=spend,
            impressions=impressions,
            link_ctr=link_ctr,
            ctr_drop_pct=ctr_drop_pct,
            frequency=frequency,
            roas=roas,
            cost_per_lead=cost_per_lead,
            cost_per_booking=cost_per_booking,
            reply_rate=reply_rate,
            booked_rate=booked_rate,
            close_rate=close_rate,
            revenue=revenue,
            budget_change_pct=budget_change_pct,
            leads=leads,
            bookings=bookings,
            purchases=purchases,
        ),
    )


def test_meta_ads_guardrails_constants_match_spec() -> None:
    assert MetaAdsGuardrails.MAX_DAILY_SPEND == 500
    assert MetaAdsGuardrails.MAX_BUDGET_INCREASE == 0.20
    assert MetaAdsGuardrails.MIN_ROAS_TO_SCALE == 2.0
    assert MetaAdsGuardrails.MAX_CPA_MULTIPLIER == 2.0
    assert MetaAdsGuardrails.ROLLBACK_THRESHOLD == 0.30
    assert MetaAdsGuardrails.COOL_DOWN_HOURS == 24


def test_meta_ads_analyzer_detects_fatigue_rising_cpa_and_scaling() -> None:
    async def _run() -> None:
        analyzer = MetaAdsAnalyzer()
        snapshot = MetaAdsSnapshot(
            account_id="act_test",
            campaigns=[
                _entity(
                    entity_type=EntityType.CAMPAIGN,
                    entity_id="camp_fatigue",
                    name="Fatigued Campaign",
                    spend=45,
                    impressions=6000,
                    link_ctr=0.8,
                    ctr_drop_pct=31,
                    frequency=3.4,
                ),
                _entity(
                    entity_type=EntityType.CAMPAIGN,
                    entity_id="camp_cpa",
                    name="CPA Rising Campaign",
                    spend=60,
                    impressions=5000,
                    link_ctr=2.4,
                    roas=0.9,
                    cost_per_lead=120,
                    reply_rate=0.08,
                    booked_rate=0.05,
                    leads=5,
                    revenue=200,
                ),
                _entity(
                    entity_type=EntityType.CAMPAIGN,
                    entity_id="camp_scale",
                    name="Scaling Campaign",
                    spend=180,
                    impressions=8200,
                    link_ctr=3.6,
                    frequency=1.5,
                    roas=3.2,
                    cost_per_booking=45,
                    bookings=8,
                    revenue=1440,
                    budget_change_pct=10,
                ),
            ],
        )

        report = await analyzer.daily_report(snapshot=snapshot)
        by_id = {item.entity_id: item for item in report.campaigns}

        assert by_id["camp_fatigue"].fatigued is True
        assert by_id["camp_fatigue"].suggested_action == "mark_for_creative_refresh"
        assert by_id["camp_cpa"].rising_cpa is True
        assert by_id["camp_cpa"].suggested_action == "inspect_followup_funnel"
        assert by_id["camp_scale"].scaling_opportunity is True
        assert by_id["camp_scale"].suggested_action == "increase_budget"
        assert "creative_fatigue" in report.account_flags
        assert "rising_cpa" in report.account_flags
        assert "scaling_opportunity" in report.account_flags

    asyncio.run(_run())


def test_revenue_brain_gather_metrics_includes_meta_ads_report() -> None:
    async def _run() -> None:
        brain = RevenueBrain()
        snapshot = MetaAdsSnapshot(
            account_id="act_test",
            campaigns=[
                _entity(
                    entity_type=EntityType.CAMPAIGN,
                    entity_id="camp_scale",
                    name="Scaling Campaign",
                    spend=150,
                    impressions=9000,
                    link_ctr=3.9,
                    frequency=1.4,
                    roas=3.0,
                    bookings=6,
                    revenue=900,
                    budget_change_pct=5,
                )
            ],
        )

        metrics = await brain.gather_metrics({"meta_ads_snapshot": snapshot})

        assert "meta_ads_report" in metrics
        assert "meta_ads_dry_run" in metrics
        assert metrics["meta_ads_report"]["account_id"] == "act_test"
        assert metrics["meta_ads_report"]["counts"]["campaigns"] == 1
        assert "campaign_flags" in metrics
        assert "scaling_opportunity" in metrics["campaign_flags"]
        assert metrics["meta_ads_dry_run"]["actions"][0]["action_type"] == "scale"

    asyncio.run(_run())


def test_meta_ads_dry_run_persists_and_sends_report(tmp_path, monkeypatch) -> None:
    async def _run() -> None:
        db_path = tmp_path / "meta_ads_autonomy.sqlite3"
        monkeypatch.setenv("META_ADS_AUTONOMY_DB_PATH", str(db_path))
        monkeypatch.setenv("RAUL_WHATSAPP_TO", "+5215550001111")
        monkeypatch.setenv("YCLOUD_WHATSAPP_FROM", "+5215559990000")

        sent_messages: list[dict[str, str]] = []

        async def _fake_send(*, from_number: str, to: str, text: str):
            sent_messages.append({"from": from_number, "to": to, "text": text})
            return {"ok": True}

        monkeypatch.setattr("brain.meta_ads_autonomy.send_ycloud_whatsapp_text_message", _fake_send)

        analyzer = MetaAdsAnalyzer()
        snapshot = MetaAdsSnapshot(
            account_id="act_test",
            campaigns=[
                _entity(
                    entity_type=EntityType.CAMPAIGN,
                    entity_id="camp_scale",
                    name="Scaling Campaign",
                    spend=180,
                    impressions=8200,
                    link_ctr=3.6,
                    frequency=1.5,
                    roas=3.2,
                    cost_per_booking=45,
                    bookings=8,
                    revenue=1440,
                    budget_change_pct=10,
                ),
                _entity(
                    entity_type=EntityType.CAMPAIGN,
                    entity_id="camp_fatigue",
                    name="Fatigued Campaign",
                    spend=45,
                    impressions=6000,
                    link_ctr=0.8,
                    ctr_drop_pct=31,
                    frequency=3.4,
                ),
            ],
        )

        report = await analyzer.daily_report(snapshot=snapshot)
        dry_run = await generate_and_store_meta_ads_dry_run(report)

        assert dry_run.counts["total"] == 1
        assert {action.action_type for action in dry_run.actions} == {"scale"}
        assert len(sent_messages) == 1
        assert "Meta Ads dry-run del día" in sent_messages[0]["text"]
        assert Path(db_path).exists()

    asyncio.run(_run())


def _dry_run_action(
    *,
    action_id: str = "meta_ads::scale::camp_1",
    action_type: str = "scale",
    target_id: str = "camp_1",
    guardrail_check_passed: bool = True,
) -> DryRunAction:
    return DryRunAction(
        action_id=action_id,
        action_type=action_type,
        target_id=target_id,
        reason="test",
        expected_impact={},
        guardrail_check_passed=guardrail_check_passed,
        source_entity_type="campaign",
        source_entity_name="Test Campaign",
    )


def test_meta_ads_executor_blocked_by_guardrail(tmp_path, monkeypatch) -> None:
    """can_execute returns False when guardrail_check_passed is False."""

    async def _run() -> None:
        monkeypatch.setenv("META_ADS_AUTONOMY_DB_PATH", str(tmp_path / "meta.sqlite3"))
        executor = MetaAdsExecutor()
        action = _dry_run_action(guardrail_check_passed=False)
        assert await executor.can_execute(action) is False

    asyncio.run(_run())


def test_meta_ads_executor_allowed_when_no_prior_execution(tmp_path, monkeypatch) -> None:
    """can_execute returns True when guardrail passes and no execution in DB."""

    async def _run() -> None:
        monkeypatch.setenv("META_ADS_AUTONOMY_DB_PATH", str(tmp_path / "meta.sqlite3"))
        executor = MetaAdsExecutor()
        action = _dry_run_action(guardrail_check_passed=True)
        assert await executor.can_execute(action) is True

    asyncio.run(_run())


def test_meta_ads_executor_blocked_by_cool_down(tmp_path, monkeypatch) -> None:
    """can_execute returns False when the same target was executed within COOL_DOWN_HOURS."""
    import aiosqlite

    async def _run() -> None:
        db_path = str(tmp_path / "meta.sqlite3")
        monkeypatch.setenv("META_ADS_AUTONOMY_DB_PATH", db_path)

        # Seed an execution log entry within the cool-down window.
        recent = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS meta_ads_execution_log (
                    id TEXT PRIMARY KEY,
                    executed_at TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    target_id TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "INSERT INTO meta_ads_execution_log (id, executed_at, action_type, target_id) VALUES (?, ?, ?, ?)",
                ("log_1", recent, "scale", "camp_1"),
            )
            await db.commit()

        executor = MetaAdsExecutor()
        action = _dry_run_action(target_id="camp_1", guardrail_check_passed=True)
        assert await executor.can_execute(action) is False

    asyncio.run(_run())


def test_meta_ads_executor_allowed_after_cool_down_expires(tmp_path, monkeypatch) -> None:
    """can_execute returns True when the prior execution is older than COOL_DOWN_HOURS."""
    import aiosqlite

    async def _run() -> None:
        db_path = str(tmp_path / "meta.sqlite3")
        monkeypatch.setenv("META_ADS_AUTONOMY_DB_PATH", db_path)

        # Seed an execution log entry outside the cool-down window.
        old = (
            datetime.now(timezone.utc) - timedelta(hours=MetaAdsGuardrails.COOL_DOWN_HOURS + 1)
        ).isoformat()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS meta_ads_execution_log (
                    id TEXT PRIMARY KEY,
                    executed_at TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    target_id TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "INSERT INTO meta_ads_execution_log (id, executed_at, action_type, target_id) VALUES (?, ?, ?, ?)",
                ("log_old", old, "scale", "camp_1"),
            )
            await db.commit()

        executor = MetaAdsExecutor()
        action = _dry_run_action(target_id="camp_1", guardrail_check_passed=True)
        assert await executor.can_execute(action) is True

    asyncio.run(_run())


def _fake_client(
    *,
    status: str = "ACTIVE",
    daily_budget: int = 10000,
    roas: float = 0.0,
) -> "object":
    """Return a minimal async mock of MetaAdsApiClient for execute() tests."""

    class _FakeClient:
        def __init__(self) -> None:
            self.pause_calls: list[str] = []
            self.budget_calls: list[tuple[str, int]] = []
            self.activate_calls: list[str] = []

        def _entity_payload(self, entity_id: str) -> dict:
            insights_data = [{"purchase_roas": roas}] if roas else []
            return {
                "id": entity_id,
                "status": status,
                "daily_budget": daily_budget,
                "insights": {"data": insights_data},
            }

        async def fetch_entity(self, entity_id: str, *, fields: str = "") -> dict:
            return self._entity_payload(entity_id)

        async def fetch_entity_insights(self, entity_id: str) -> dict:
            return self._entity_payload(entity_id)

        async def pause_entity(self, entity_id: str) -> dict:
            self.pause_calls.append(entity_id)
            return {"success": True}

        async def activate_entity(self, entity_id: str) -> dict:
            self.activate_calls.append(entity_id)
            return {"success": True}

        async def update_daily_budget(self, entity_id: str, new_budget_cents: int) -> dict:
            self.budget_calls.append((entity_id, new_budget_cents))
            return {"success": True}

    return _FakeClient()


def test_meta_ads_executor_execute_pause(tmp_path, monkeypatch) -> None:
    """execute() pauses the ad, stores rollback snapshot, and logs to DB."""
    import aiosqlite

    async def _run() -> None:
        db_path = str(tmp_path / "meta.sqlite3")
        monkeypatch.setenv("META_ADS_AUTONOMY_DB_PATH", db_path)
        client = _fake_client(status="ACTIVE")
        executor = MetaAdsExecutor(client=client)
        action = _dry_run_action(
            action_id="meta_ads::pause::ad_1",
            action_type="pause",
            target_id="ad_1",
            guardrail_check_passed=True,
        )

        result = await executor.execute(action)

        assert result["skipped"] is False
        assert result["action"] == "pause"
        assert result["target_id"] == "ad_1"
        assert client.pause_calls == ["ad_1"]

        # Verify rollback snapshot was persisted.
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT rollback_data FROM meta_ads_execution_log WHERE id = ?", (action.action_id,))
            row = await cur.fetchone()
        assert row is not None
        import json
        rollback = json.loads(row["rollback_data"])
        assert rollback["status"] == "ACTIVE"

    asyncio.run(_run())


def test_meta_ads_executor_execute_scale(tmp_path, monkeypatch) -> None:
    """execute() increases daily budget by 20% and logs the rollback snapshot."""
    import aiosqlite

    async def _run() -> None:
        db_path = str(tmp_path / "meta.sqlite3")
        monkeypatch.setenv("META_ADS_AUTONOMY_DB_PATH", db_path)
        client = _fake_client(daily_budget=10000)
        executor = MetaAdsExecutor(client=client)
        action = _dry_run_action(
            action_id="meta_ads::scale::camp_1",
            action_type="scale",
            target_id="camp_1",
            guardrail_check_passed=True,
        )

        result = await executor.execute(action)

        assert result["skipped"] is False
        assert result["action"] == "scale"
        assert result["prev_budget_cents"] == 10000
        assert result["new_budget_cents"] == 12000  # 10000 * 1.20
        assert client.budget_calls == [("camp_1", 12000)]

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT rollback_data FROM meta_ads_execution_log WHERE id = ?", (action.action_id,))
            row = await cur.fetchone()
        assert row is not None
        import json
        rollback = json.loads(row["rollback_data"])
        assert rollback["daily_budget"] == 10000

    asyncio.run(_run())


def test_meta_ads_executor_execute_skips_when_guardrail_fails(tmp_path, monkeypatch) -> None:
    """execute() returns skipped=True and makes no API calls when can_execute is False."""

    async def _run() -> None:
        monkeypatch.setenv("META_ADS_AUTONOMY_DB_PATH", str(tmp_path / "meta.sqlite3"))
        client = _fake_client()
        executor = MetaAdsExecutor(client=client)
        action = _dry_run_action(
            action_type="pause",
            target_id="ad_blocked",
            guardrail_check_passed=False,
        )

        result = await executor.execute(action)

        assert result["skipped"] is True
        assert client.pause_calls == []

    asyncio.run(_run())


def test_meta_ads_executor_check_rollback_reverts_scale_on_roas_drop(tmp_path, monkeypatch) -> None:
    """check_rollback reverts budget when ROAS dropped ≥30% since scaling."""
    import aiosqlite
    import json as _json

    async def _run() -> None:
        db_path = str(tmp_path / "meta.sqlite3")
        monkeypatch.setenv("META_ADS_AUTONOMY_DB_PATH", db_path)
        monkeypatch.delenv("RAUL_WHATSAPP_TO", raising=False)
        monkeypatch.delenv("YCLOUD_WHATSAPP_FROM", raising=False)

        class _DropClient:
            def __init__(self) -> None:
                self.budget_calls: list[tuple[str, int]] = []

            async def fetch_entity_insights(self, entity_id: str) -> dict:
                # Current ROAS = 2.0 — dropped 43% from baseline 3.5
                return {"id": entity_id, "insights": {"data": [{"purchase_roas": 2.0}]}}

            async def update_daily_budget(self, entity_id: str, new_budget_cents: int) -> dict:
                self.budget_calls.append((entity_id, new_budget_cents))
                return {"success": True}

        client = _DropClient()
        executor = MetaAdsExecutor(client=client)
        action = _dry_run_action(
            action_id="meta_ads::scale::camp_x",
            action_type="scale",
            target_id="camp_x",
            guardrail_check_passed=True,
        )
        await executor._log_execution(action, {"daily_budget": 10000, "roas": 3.5})

        reverts = await executor.check_rollback()

        assert len(reverts) == 1
        assert reverts[0]["action_type"] == "scale"
        assert reverts[0]["target_id"] == "camp_x"
        assert client.budget_calls == [("camp_x", 10000)]

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT rolled_back FROM meta_ads_execution_log WHERE id = ?",
                (action.action_id,),
            )
            row = await cur.fetchone()
        assert row is not None
        assert row["rolled_back"] == 1

    asyncio.run(_run())


def test_meta_ads_executor_check_rollback_no_revert_scale_roas_ok(tmp_path, monkeypatch) -> None:
    """check_rollback does NOT revert when ROAS is still within the 30% tolerance."""

    async def _run() -> None:
        db_path = str(tmp_path / "meta.sqlite3")
        monkeypatch.setenv("META_ADS_AUTONOMY_DB_PATH", db_path)
        monkeypatch.delenv("RAUL_WHATSAPP_TO", raising=False)
        monkeypatch.delenv("YCLOUD_WHATSAPP_FROM", raising=False)

        class _StableClient:
            def __init__(self) -> None:
                self.budget_calls: list = []

            async def fetch_entity_insights(self, entity_id: str) -> dict:
                # ROAS dropped only 5% (3.5 → 3.33) — well below 30%
                return {"id": entity_id, "insights": {"data": [{"purchase_roas": 3.33}]}}

            async def update_daily_budget(self, entity_id: str, new_budget_cents: int) -> dict:
                self.budget_calls.append((entity_id, new_budget_cents))
                return {"success": True}

        client = _StableClient()
        executor = MetaAdsExecutor(client=client)
        action = _dry_run_action(
            action_id="meta_ads::scale::camp_y",
            action_type="scale",
            target_id="camp_y",
        )
        await executor._log_execution(action, {"daily_budget": 10000, "roas": 3.5})

        reverts = await executor.check_rollback()

        assert reverts == []
        assert client.budget_calls == []

    asyncio.run(_run())


def test_meta_ads_executor_check_rollback_reverts_pause_on_good_roas(tmp_path, monkeypatch) -> None:
    """check_rollback un-pauses when ROAS at pause time was above the revert floor."""

    async def _run() -> None:
        db_path = str(tmp_path / "meta.sqlite3")
        monkeypatch.setenv("META_ADS_AUTONOMY_DB_PATH", db_path)
        monkeypatch.delenv("RAUL_WHATSAPP_TO", raising=False)
        monkeypatch.delenv("YCLOUD_WHATSAPP_FROM", raising=False)

        class _PauseClient:
            def __init__(self) -> None:
                self.activate_calls: list[str] = []

            async def fetch_entity_insights(self, entity_id: str) -> dict:
                return {"id": entity_id, "status": "PAUSED", "insights": {"data": []}}

            async def activate_entity(self, entity_id: str) -> dict:
                self.activate_calls.append(entity_id)
                return {"success": True}

        client = _PauseClient()
        executor = MetaAdsExecutor(client=client)
        action = _dry_run_action(
            action_id="meta_ads::pause::ad_y",
            action_type="pause",
            target_id="ad_y",
            guardrail_check_passed=True,
        )
        # roas=3.5 > floor (2.0 * 0.7 = 1.4) → should revert
        await executor._log_execution(action, {"status": "ACTIVE", "roas": 3.5})

        reverts = await executor.check_rollback()

        assert len(reverts) == 1
        assert reverts[0]["action_type"] == "pause"
        assert client.activate_calls == ["ad_y"]

    asyncio.run(_run())


def test_meta_ads_executor_check_rollback_no_revert_pause_bad_roas(tmp_path, monkeypatch) -> None:
    """check_rollback keeps the pause when ROAS at pause time was already below the floor."""

    async def _run() -> None:
        db_path = str(tmp_path / "meta.sqlite3")
        monkeypatch.setenv("META_ADS_AUTONOMY_DB_PATH", db_path)
        monkeypatch.delenv("RAUL_WHATSAPP_TO", raising=False)
        monkeypatch.delenv("YCLOUD_WHATSAPP_FROM", raising=False)

        class _NoPauseRevertClient:
            def __init__(self) -> None:
                self.activate_calls: list[str] = []

            async def fetch_entity_insights(self, entity_id: str) -> dict:
                return {"id": entity_id, "status": "PAUSED", "insights": {"data": []}}

            async def activate_entity(self, entity_id: str) -> dict:
                self.activate_calls.append(entity_id)
                return {"success": True}

        client = _NoPauseRevertClient()
        executor = MetaAdsExecutor(client=client)
        action = _dry_run_action(
            action_id="meta_ads::pause::ad_z",
            action_type="pause",
            target_id="ad_z",
        )
        # roas=0.8 < floor 1.4 → stay paused
        await executor._log_execution(action, {"status": "ACTIVE", "roas": 0.8})

        reverts = await executor.check_rollback()

        assert reverts == []
        assert client.activate_calls == []

    asyncio.run(_run())


def test_revenue_brain_evening_check_calls_check_rollback(monkeypatch) -> None:
    """evening_check() invokes meta_ads_executor.check_rollback() exactly once."""

    async def _run() -> None:
        called: list[bool] = []

        async def _fake_check_rollback() -> list:
            called.append(True)
            return []

        brain = RevenueBrain()
        brain.meta_ads_executor.check_rollback = _fake_check_rollback  # type: ignore[method-assign]

        async def _noop_gather(snapshot=None) -> dict:
            return {}

        async def _noop_diagnose(metrics: dict) -> RevenueDiagnosis:
            return RevenueDiagnosis(
                generated_at=datetime.now(timezone.utc).isoformat(),
                summary="ok",
            )

        async def _noop_close(leads: list) -> list:
            return []

        async def _noop_farm(customers: list) -> list:
            return []

        monkeypatch.setattr(brain, "gather_metrics", _noop_gather)
        monkeypatch.setattr(brain, "diagnose", _noop_diagnose)
        monkeypatch.setattr(brain.closer, "daily_close_plan", _noop_close)
        monkeypatch.setattr(brain.farmer, "daily_farm", _noop_farm)

        await brain.evening_check()

        assert called == [True]

    asyncio.run(_run())


def test_meta_ads_pending_approval_roundtrip(tmp_path, monkeypatch) -> None:
    async def _run() -> None:
        db_path = tmp_path / "meta_ads_autonomy.sqlite3"
        monkeypatch.setenv("META_ADS_AUTONOMY_DB_PATH", str(db_path))
        monkeypatch.delenv("RAUL_WHATSAPP_TO", raising=False)
        monkeypatch.delenv("YCLOUD_WHATSAPP_FROM", raising=False)

        analyzer = MetaAdsAnalyzer()
        snapshot = MetaAdsSnapshot(
            account_id="act_test",
            campaigns=[
                _entity(
                    entity_type=EntityType.CAMPAIGN,
                    entity_id="camp_scale",
                    name="Scaling Campaign",
                    spend=180,
                    impressions=8200,
                    link_ctr=3.6,
                    frequency=1.5,
                    roas=3.2,
                    cost_per_booking=45,
                    bookings=8,
                    revenue=1440,
                    budget_change_pct=10,
                )
            ],
        )
        report = await analyzer.daily_report(snapshot=snapshot)
        dry_run = build_meta_ads_dry_run(report)
        from brain.meta_ads_autonomy import store_pending_approvals

        await store_pending_approvals(dry_run)
        action_id = dry_run.actions[0].action_id

        approved = await approve_pending_action(action_id)
        assert approved is not None
        assert approved["status"] == "approved"

        rejected = await reject_pending_action(action_id)
        assert rejected is not None
        assert rejected["status"] == "rejected"

    asyncio.run(_run())
