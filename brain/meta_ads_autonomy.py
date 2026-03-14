from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from brain.meta_ads_operator import MetaAdsApiClient
from brain.meta_ads_operator.models import EntityInsight, MetaAdsSnapshot, OperatorThresholds
from tools.ycloud_client import send_ycloud_whatsapp_text_message


class MetaAdsGuardrails:
    MAX_DAILY_SPEND = 500.0
    MAX_BUDGET_INCREASE = 0.20
    MIN_ROAS_TO_SCALE = 2.0
    MAX_CPA_MULTIPLIER = 2.0
    ROLLBACK_THRESHOLD = 0.30
    COOL_DOWN_HOURS = 24


class MetaAdsEntityAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entity_type: str
    entity_id: str
    name: str
    spend: float = 0.0
    impressions: float = 0.0
    link_ctr: float = 0.0
    ctr_drop_pct: float = 0.0
    frequency: float = 0.0
    roas: float = 0.0
    cpa_metric_name: str | None = None
    cpa_value: float | None = None
    fatigued: bool = False
    rising_cpa: bool = False
    scaling_opportunity: bool = False
    flags: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    suggested_action: str = "hold_steady"
    risk_score: float = 0.0


class MetaAdsReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    generated_at: str
    account_id: str
    executive_summary: str
    account_flags: list[str] = Field(default_factory=list)
    campaigns: list[MetaAdsEntityAssessment] = Field(default_factory=list)
    adsets: list[MetaAdsEntityAssessment] = Field(default_factory=list)
    ads: list[MetaAdsEntityAssessment] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class DryRunAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    action_type: str
    target_id: str
    reason: str
    expected_impact: dict[str, Any] = Field(default_factory=dict)
    guardrail_check_passed: bool
    source_entity_type: str
    source_entity_name: str


class MetaAdsDryRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: str
    account_id: str
    actions: list[DryRunAction] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class MetaAdsAnalyzer:
    def __init__(
        self,
        *,
        client: Optional[MetaAdsApiClient] = None,
        thresholds: Optional[OperatorThresholds] = None,
    ) -> None:
        self.client = client or MetaAdsApiClient()
        self.thresholds = thresholds or OperatorThresholds()

    async def daily_report(self, *, snapshot: Optional[MetaAdsSnapshot] = None, account_id: str | None = None) -> MetaAdsReport:
        snapshot = snapshot or await self.client.fetch_snapshot(account_id=account_id)
        campaigns = [self._assess_entity(item) for item in snapshot.campaigns]
        adsets = [self._assess_entity(item) for item in snapshot.adsets]
        ads = [self._assess_entity(item) for item in snapshot.ads]
        account_flags = self._collect_account_flags((*campaigns, *adsets, *ads))
        summary = self._build_summary(snapshot, account_flags, campaigns, adsets, ads)
        return MetaAdsReport(
            generated_at=datetime.now(timezone.utc).isoformat(),
            account_id=snapshot.account_id,
            executive_summary=summary,
            account_flags=account_flags,
            campaigns=campaigns,
            adsets=adsets,
            ads=ads,
            counts={
                "campaigns": len(campaigns),
                "adsets": len(adsets),
                "ads": len(ads),
                "fatigued": sum(1 for item in (*campaigns, *adsets, *ads) if item.fatigued),
                "rising_cpa": sum(1 for item in (*campaigns, *adsets, *ads) if item.rising_cpa),
                "scaling_opportunities": sum(1 for item in (*campaigns, *adsets, *ads) if item.scaling_opportunity),
            },
        )

    def _assess_entity(self, entity: EntityInsight) -> MetaAdsEntityAssessment:
        metrics = entity.metrics
        cpa_metric_name, cpa_value = self._pick_cpa_metric(entity)
        flags: list[str] = []
        reasons: list[str] = []

        fatigued = bool(
            metrics.frequency >= self.thresholds.fatigue_frequency
            and (
                metrics.ctr_drop_pct >= self.thresholds.fatigue_ctr_drop_pct
                or metrics.link_ctr <= self.thresholds.low_link_ctr
            )
        )
        if fatigued:
            flags.append("creative_fatigue")
            reasons.append(
                f"Frecuencia {metrics.frequency:.2f} con caída de CTR {metrics.ctr_drop_pct:.1f}% sugiere fatiga creativa."
            )

        rising_cpa = self._is_rising_cpa(entity, cpa_value)
        if rising_cpa:
            flags.append("rising_cpa")
            reasons.append(
                f"{cpa_metric_name or 'CPA'} de {cpa_value or 0:.2f} está fuera de rango eficiente para este nivel de conversión."
            )

        scaling_opportunity = bool(
            metrics.roas >= MetaAdsGuardrails.MIN_ROAS_TO_SCALE
            and metrics.spend <= MetaAdsGuardrails.MAX_DAILY_SPEND
            and metrics.budget_change_pct <= MetaAdsGuardrails.MAX_BUDGET_INCREASE * 100
            and not fatigued
            and not rising_cpa
        )
        if scaling_opportunity:
            flags.append("scaling_opportunity")
            reasons.append(
                f"ROAS {metrics.roas:.2f} con spend {metrics.spend:.2f} permite escalar de forma controlada."
            )

        if fatigued:
            suggested_action = "mark_for_creative_refresh"
            risk_score = 0.45
        elif rising_cpa:
            suggested_action = "inspect_followup_funnel"
            risk_score = 0.58
        elif scaling_opportunity:
            suggested_action = "increase_budget"
            risk_score = 0.38
        else:
            suggested_action = "hold_steady"
            risk_score = 0.18
            reasons.append("No hay señales suficientemente fuertes para tomar una acción agresiva hoy.")

        return MetaAdsEntityAssessment(
            entity_type=entity.entity_type.value,
            entity_id=entity.entity_id,
            name=entity.name,
            spend=float(metrics.spend or 0.0),
            impressions=float(metrics.impressions or 0.0),
            link_ctr=float(metrics.link_ctr or 0.0),
            ctr_drop_pct=float(metrics.ctr_drop_pct or 0.0),
            frequency=float(metrics.frequency or 0.0),
            roas=float(metrics.roas or 0.0),
            cpa_metric_name=cpa_metric_name,
            cpa_value=cpa_value,
            fatigued=fatigued,
            rising_cpa=rising_cpa,
            scaling_opportunity=scaling_opportunity,
            flags=flags,
            reasons=reasons,
            suggested_action=suggested_action,
            risk_score=risk_score,
        )

    def _pick_cpa_metric(self, entity: EntityInsight) -> tuple[str | None, float | None]:
        metrics = entity.metrics
        candidates = [
            ("cost_per_booking", metrics.cost_per_booking),
            ("cost_per_lead", metrics.cost_per_lead),
            ("cost_per_message", metrics.cost_per_message),
            ("cpc", metrics.cpc),
        ]
        for name, value in candidates:
            numeric = float(value or 0.0)
            if numeric > 0:
                return name, numeric
        return None, None

    def _is_rising_cpa(self, entity: EntityInsight, cpa_value: float | None) -> bool:
        if not cpa_value:
            return False
        metrics = entity.metrics
        target_cpa = self._infer_target_cpa(entity)
        if target_cpa is not None and cpa_value > target_cpa * MetaAdsGuardrails.MAX_CPA_MULTIPLIER:
            return True
        if metrics.spend < self.thresholds.min_spend_for_decision:
            return False
        weak_followup = (
            (metrics.reply_rate and metrics.reply_rate < self.thresholds.low_reply_rate)
            or (metrics.booked_rate and metrics.booked_rate < self.thresholds.low_booked_rate)
            or (metrics.close_rate and metrics.close_rate < self.thresholds.min_close_rate_for_winner)
        )
        return bool(weak_followup and metrics.roas < MetaAdsGuardrails.MIN_ROAS_TO_SCALE)

    def _infer_target_cpa(self, entity: EntityInsight) -> float | None:
        metrics = entity.metrics
        conversions = 0.0
        actual_cpa = 0.0
        if metrics.cost_per_booking and metrics.bookings:
            conversions = metrics.bookings
            actual_cpa = metrics.cost_per_booking
        elif metrics.cost_per_lead and metrics.leads:
            conversions = metrics.leads
            actual_cpa = metrics.cost_per_lead
        elif metrics.cost_per_message and metrics.messaging_conversations:
            conversions = metrics.messaging_conversations
            actual_cpa = metrics.cost_per_message
        elif metrics.cpc and metrics.link_clicks:
            conversions = metrics.link_clicks
            actual_cpa = metrics.cpc
        if conversions <= 0 or actual_cpa <= 0 or metrics.revenue <= 0:
            return None
        value_per_conversion = metrics.revenue / conversions
        if value_per_conversion <= 0:
            return None
        return value_per_conversion / MetaAdsGuardrails.MIN_ROAS_TO_SCALE

    def _collect_account_flags(self, assessments: Iterable[MetaAdsEntityAssessment]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in assessments:
            for flag in item.flags:
                if flag not in seen:
                    seen.add(flag)
                    ordered.append(flag)
        return ordered

    def _build_summary(
        self,
        snapshot: MetaAdsSnapshot,
        account_flags: list[str],
        campaigns: list[MetaAdsEntityAssessment],
        adsets: list[MetaAdsEntityAssessment],
        ads: list[MetaAdsEntityAssessment],
    ) -> str:
        total_entities = len(campaigns) + len(adsets) + len(ads)
        if not total_entities:
            return f"No encontré entidades activas para revisar en {snapshot.account_id}."
        if not account_flags:
            return f"Revisé {total_entities} entidades y hoy no veo señales fuertes para cambiar la cuenta."
        labels = ", ".join(account_flags[:3])
        return f"Revisé {total_entities} entidades y detecté estas señales principales: {labels}."


def _db_path() -> Path:
    override = os.getenv("META_ADS_AUTONOMY_DB_PATH")
    path = Path(override).expanduser() if override else Path("data/meta_ads_autonomy.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


async def _connect() -> aiosqlite.Connection:
    db = await aiosqlite.connect(_db_path())
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON;")
    await db.execute("PRAGMA journal_mode = WAL;")
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_ads_pending_approvals (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            action_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            expected_impact_json TEXT NOT NULL,
            guardrail_check_passed INTEGER NOT NULL DEFAULT 0,
            source_entity_type TEXT NOT NULL,
            source_entity_name TEXT NOT NULL
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_ads_execution_log (
            id TEXT PRIMARY KEY,
            executed_at TEXT NOT NULL,
            action_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            rollback_data TEXT NOT NULL DEFAULT '{}',
            rolled_back INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # Add columns to existing DBs created before these columns were defined.
    for _col_ddl in (
        "ALTER TABLE meta_ads_execution_log ADD COLUMN rollback_data TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE meta_ads_execution_log ADD COLUMN rolled_back INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            await db.execute(_col_ddl)
        except Exception:
            pass
    await db.commit()
    return db


def _as_float_safe(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _parse_roas_from_entity(raw: dict[str, Any]) -> float:
    """Extract ROAS from a Graph API entity response that includes insights."""
    insights = raw.get("insights") or {}
    rows = (insights.get("data") or [{}]) if isinstance(insights, dict) else [{}]
    row = (rows[0] if rows else {}) or {}
    return _as_float_safe(row.get("purchase_roas") or row.get("roas") or 0)


async def _send_rollback_notification(reverts: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    to_number = (os.getenv("RAUL_WHATSAPP_TO") or "").strip()
    from_number = (os.getenv("YCLOUD_WHATSAPP_FROM") or "").strip()
    if not to_number or not from_number or not reverts:
        return None
    lines = [f"Meta Ads rollback automático ({len(reverts)} acción(es)):"]
    for r in reverts:
        lines.append(f"- {r['action_type']} | {r['target_id']} | {r['reason']}")
    return await send_ycloud_whatsapp_text_message(
        from_number=from_number,
        to=to_number,
        text="\n".join(lines),
    )


class MetaAdsExecutor:
    """Executes approved Meta Ads actions, enforcing guardrails before each run."""

    def __init__(self, *, client: Optional[MetaAdsApiClient] = None) -> None:
        self.client = client or MetaAdsApiClient()

    async def can_execute(self, action: DryRunAction) -> bool:
        """Return True only if both guardrails pass.

        Checks:
        - MAX_DAILY_SPEND: delegated to ``action.guardrail_check_passed``
        - COOL_DOWN_HOURS: no execution for the same target within the last 24 h
        """
        if not action.guardrail_check_passed:
            return False

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=MetaAdsGuardrails.COOL_DOWN_HOURS)
        ).isoformat()

        db = await _connect()
        try:
            cursor = await db.execute(
                "SELECT id FROM meta_ads_execution_log WHERE target_id = ? AND executed_at >= ?",
                (action.target_id, cutoff),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()

        return row is None

    async def execute(self, action: DryRunAction) -> dict[str, Any]:
        """Auto-execute a pause or scale action if guardrails allow it.

        Returns a dict with ``skipped=True`` when ``can_execute`` blocks the run,
        or the API result and rollback snapshot on success.
        """
        if not await self.can_execute(action):
            return {"skipped": True, "action_id": action.action_id, "reason": "guardrail_or_cooldown"}

        if action.action_type == "pause":
            entity = await self.client.fetch_entity_insights(action.target_id)
            rollback_data: dict[str, Any] = {
                "status": entity.get("status", "ACTIVE"),
                "roas": _parse_roas_from_entity(entity),
            }
            api_result = await self.client.pause_entity(action.target_id)
            result: dict[str, Any] = {
                "skipped": False,
                "action_id": action.action_id,
                "action": "pause",
                "target_id": action.target_id,
                "api_result": api_result,
            }

        elif action.action_type == "scale":
            entity = await self.client.fetch_entity_insights(action.target_id)
            prev_budget_cents = int(entity.get("daily_budget") or 0)
            new_budget_cents = int(prev_budget_cents * (1 + MetaAdsGuardrails.MAX_BUDGET_INCREASE))
            rollback_data = {
                "daily_budget": prev_budget_cents,
                "roas": _parse_roas_from_entity(entity),
            }
            api_result = await self.client.update_daily_budget(action.target_id, new_budget_cents)
            result = {
                "skipped": False,
                "action_id": action.action_id,
                "action": "scale",
                "target_id": action.target_id,
                "prev_budget_cents": prev_budget_cents,
                "new_budget_cents": new_budget_cents,
                "api_result": api_result,
            }

        else:
            return {"skipped": True, "action_id": action.action_id, "reason": "unsupported_action_type"}

        await self._log_execution(action, rollback_data)
        return result

    async def _log_execution(self, action: DryRunAction, rollback_data: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        db = await _connect()
        try:
            await db.execute(
                """
                INSERT OR REPLACE INTO meta_ads_execution_log
                    (id, executed_at, action_type, target_id, rollback_data, rolled_back)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (
                    action.action_id,
                    now,
                    action.action_type,
                    action.target_id,
                    json.dumps(rollback_data, ensure_ascii=False),
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def _mark_rolled_back(self, action_id: str) -> None:
        db = await _connect()
        try:
            await db.execute(
                "UPDATE meta_ads_execution_log SET rolled_back = 1 WHERE id = ?",
                (action_id,),
            )
            await db.commit()
        finally:
            await db.close()

    async def check_rollback(self) -> list[dict[str, Any]]:
        """Check executions from the last 24 h and auto-revert if performance dropped 30%+.

        - scale: fetch current ROAS via API; revert budget if ROAS dropped >= ROLLBACK_THRESHOLD
          relative to the baseline stored at execution time.
        - pause: ROAS at pause time is stored in rollback_data; if it was still above
          MIN_ROAS_TO_SCALE * (1 - ROLLBACK_THRESHOLD), the pause was premature → activate.

        Sends a WhatsApp notification to Raul for every action reverted.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=MetaAdsGuardrails.COOL_DOWN_HOURS)
        ).isoformat()

        db = await _connect()
        try:
            cursor = await db.execute(
                """
                SELECT id, action_type, target_id, rollback_data
                FROM meta_ads_execution_log
                WHERE executed_at >= ? AND rolled_back = 0
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()
        finally:
            await db.close()

        revert_floor = MetaAdsGuardrails.MIN_ROAS_TO_SCALE * (1 - MetaAdsGuardrails.ROLLBACK_THRESHOLD)
        reverts: list[dict[str, Any]] = []

        for row in rows:
            action_id: str = row["id"]
            action_type: str = row["action_type"]
            target_id: str = row["target_id"]
            snapshot: dict[str, Any] = json.loads(row["rollback_data"] or "{}")

            should_revert = False
            reason = ""
            api_result: dict[str, Any] = {}

            if action_type == "scale":
                current = await self.client.fetch_entity_insights(target_id)
                current_roas = _parse_roas_from_entity(current)
                roas_baseline = _as_float_safe(snapshot.get("roas") or 0)

                if roas_baseline > 0:
                    drop_pct = (roas_baseline - current_roas) / roas_baseline
                    should_revert = drop_pct >= MetaAdsGuardrails.ROLLBACK_THRESHOLD
                    reason = (
                        f"ROAS cayó {drop_pct * 100:.1f}% "
                        f"({roas_baseline:.2f} → {current_roas:.2f})"
                    )
                else:
                    # No baseline available — fall back to fixed floor.
                    should_revert = 0 < current_roas < revert_floor
                    reason = f"ROAS {current_roas:.2f} bajo el piso mínimo {revert_floor:.2f}"

                if should_revert:
                    orig_budget = int(snapshot.get("daily_budget") or 0)
                    api_result = await self.client.update_daily_budget(target_id, orig_budget)

            elif action_type == "pause":
                roas_at_pause = _as_float_safe(snapshot.get("roas") or 0)
                should_revert = roas_at_pause > revert_floor
                reason = (
                    f"ROAS al pausar era {roas_at_pause:.2f} (>{revert_floor:.2f}); "
                    "posible pausa prematura"
                )
                if should_revert:
                    api_result = await self.client.activate_entity(target_id)

            if should_revert:
                await self._mark_rolled_back(action_id)
                reverts.append(
                    {
                        "action_id": action_id,
                        "action_type": action_type,
                        "target_id": target_id,
                        "reason": reason,
                        "api_result": api_result,
                    }
                )

        await _send_rollback_notification(reverts)
        return reverts


def _guardrail_check(assessment: MetaAdsEntityAssessment) -> bool:
    if assessment.spend > MetaAdsGuardrails.MAX_DAILY_SPEND:
        return False
    if assessment.suggested_action == "increase_budget":
        if assessment.roas < MetaAdsGuardrails.MIN_ROAS_TO_SCALE:
            return False
    return True


def _normalize_action_type(suggested_action: str) -> str | None:
    mapping = {
        "pause_ad": "pause",
        "increase_budget": "scale",
        "duplicate_winner": "duplicate",
    }
    return mapping.get(suggested_action)


def _build_action_id(action_type: str, target_id: str) -> str:
    return f"meta_ads::{action_type}::{target_id}"


def _expected_impact(assessment: MetaAdsEntityAssessment, action_type: str) -> dict[str, Any]:
    impact: dict[str, Any] = {
        "entity": assessment.name,
        "risk_score": round(float(assessment.risk_score or 0.0), 3),
    }
    if action_type == "pause":
        impact["spend_saved_usd_per_day"] = round(float(assessment.spend or 0.0), 2)
        impact["likely_reason"] = "fatigue_or_high_cpa"
    elif action_type == "scale":
        impact["budget_change_pct"] = MetaAdsGuardrails.MAX_BUDGET_INCREASE
        impact["required_roas_floor"] = MetaAdsGuardrails.MIN_ROAS_TO_SCALE
    elif action_type == "duplicate":
        impact["goal"] = "preserve_winner_and_test_new_variation"
        impact["current_roas"] = assessment.roas
    return impact


def _build_dry_run_action(assessment: MetaAdsEntityAssessment) -> DryRunAction | None:
    action_type = _normalize_action_type(assessment.suggested_action)
    if not action_type:
        return None
    reason = "; ".join(assessment.reasons or assessment.flags or ["meta_ads_signal"])
    return DryRunAction(
        action_id=_build_action_id(action_type, assessment.entity_id),
        action_type=action_type,
        target_id=assessment.entity_id,
        reason=reason,
        expected_impact=_expected_impact(assessment, action_type),
        guardrail_check_passed=_guardrail_check(assessment),
        source_entity_type=assessment.entity_type,
        source_entity_name=assessment.name,
    )


def build_meta_ads_dry_run(report: MetaAdsReport) -> MetaAdsDryRunReport:
    actions: list[DryRunAction] = []
    for collection in (report.campaigns, report.adsets, report.ads):
        for assessment in collection:
            action = _build_dry_run_action(assessment)
            if action:
                actions.append(action)
    return MetaAdsDryRunReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        account_id=report.account_id,
        actions=actions,
        counts={
            "total": len(actions),
            "auto_ready": sum(1 for action in actions if action.guardrail_check_passed),
            "blocked": sum(1 for action in actions if not action.guardrail_check_passed),
        },
    )


async def store_pending_approvals(report: MetaAdsDryRunReport) -> MetaAdsDryRunReport:
    db = await _connect()
    try:
        for action in report.actions:
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """
                INSERT INTO meta_ads_pending_approvals (
                    id,
                    created_at,
                    updated_at,
                    status,
                    action_type,
                    target_id,
                    reason,
                    expected_impact_json,
                    guardrail_check_passed,
                    source_entity_type,
                    source_entity_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    status=excluded.status,
                    reason=excluded.reason,
                    expected_impact_json=excluded.expected_impact_json,
                    guardrail_check_passed=excluded.guardrail_check_passed,
                    source_entity_type=excluded.source_entity_type,
                    source_entity_name=excluded.source_entity_name
                """,
                (
                    action.action_id,
                    now,
                    now,
                    "pending",
                    action.action_type,
                    action.target_id,
                    action.reason,
                    json.dumps(action.expected_impact, ensure_ascii=False),
                    1 if action.guardrail_check_passed else 0,
                    action.source_entity_type,
                    action.source_entity_name,
                ),
            )
        await db.commit()
    finally:
        await db.close()
    return report


async def _send_dry_run_report_to_raul(report: MetaAdsDryRunReport) -> Optional[dict[str, Any]]:
    to_number = (os.getenv("RAUL_WHATSAPP_TO") or "").strip()
    from_number = (os.getenv("YCLOUD_WHATSAPP_FROM") or "").strip()
    if not to_number or not from_number or not report.actions:
        return None
    lines = [
        "Meta Ads dry-run del día:",
        f"Cuenta: {report.account_id}",
        f"Acciones propuestas: {len(report.actions)}",
    ]
    for action in report.actions[:10]:
        flag = "OK" if action.guardrail_check_passed else "BLOQUEADA"
        lines.append(f"- {action.action_type} | {action.source_entity_name} | {flag} | {action.reason}")
    if len(report.actions) > 10:
        lines.append(f"... y {len(report.actions) - 10} más")
    return await send_ycloud_whatsapp_text_message(
        from_number=from_number,
        to=to_number,
        text="\n".join(lines),
    )


async def generate_and_store_meta_ads_dry_run(report: MetaAdsReport) -> MetaAdsDryRunReport:
    dry_run = build_meta_ads_dry_run(report)
    await store_pending_approvals(dry_run)
    await _send_dry_run_report_to_raul(dry_run)
    return dry_run


async def _set_action_status(action_id: str, status: str) -> Optional[dict[str, Any]]:
    db = await _connect()
    try:
        cursor = await db.execute(
            """
            SELECT id, action_type, target_id, reason, expected_impact_json,
                   guardrail_check_passed, source_entity_type, source_entity_name
            FROM meta_ads_pending_approvals
            WHERE id = ?
            """,
            (action_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE meta_ads_pending_approvals SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, action_id),
        )
        await db.commit()
    finally:
        await db.close()
    return {
        "action_id": row["id"],
        "status": status,
        "action_type": row["action_type"],
        "target_id": row["target_id"],
        "reason": row["reason"],
        "expected_impact": json.loads(row["expected_impact_json"] or "{}"),
        "guardrail_check_passed": bool(row["guardrail_check_passed"]),
        "source_entity_type": row["source_entity_type"],
        "source_entity_name": row["source_entity_name"],
    }


async def approve_pending_action(action_id: str) -> Optional[dict[str, Any]]:
    return await _set_action_status(action_id, "approved")


async def reject_pending_action(action_id: str) -> Optional[dict[str, Any]]:
    return await _set_action_status(action_id, "rejected")


def report_to_metrics_payload(report: MetaAdsReport) -> dict[str, Any]:
    return report.model_dump(mode="json")


def snapshot_to_metrics_payload(snapshot: MetaAdsSnapshot) -> dict[str, Any]:
    return {
        "account_id": snapshot.account_id,
        "objective": snapshot.objective,
        "campaigns": [asdict(item) for item in snapshot.campaigns],
        "adsets": [asdict(item) for item in snapshot.adsets],
        "ads": [asdict(item) for item in snapshot.ads],
    }
