from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import aiosqlite
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from brain.attribution_engine import list_upcoming_bookings
from brain.creative_memory import CreativePattern, get_top_patterns, save_creative_pattern
from brain.meta_ads_autonomy import MetaAdsAnalyzer, MetaAdsReport
from database import AsyncSessionLocal
from models import CrmLead
from tools.ycloud_client import send_ycloud_whatsapp_text_message

logger = logging.getLogger("kan_core.business_mentor")
MENTOR_TZ = ZoneInfo("America/Mexico_City")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _attribution_db_path() -> Path:
    configured = str(os.getenv("ATTRIBUTION_ENGINE_DB_PATH") or "").strip()
    if configured:
        path = Path(configured)
    else:
        path = Path(__file__).resolve().parents[1] / "data" / "attribution_engine.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class RevenueSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    yesterday_revenue_mxn: float = 0.0
    month_revenue_mxn: float = 0.0
    monthly_target_mxn: float = 100000.0
    monthly_progress_pct: float = 0.0
    week_revenue_mxn: float = 0.0
    week_closes: int = 0
    week_bookings: int = 0


class CrmSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    active_leads: int = 0
    pipeline: dict[str, int] = Field(default_factory=dict)
    won_count: int = 0
    lost_count: int = 0
    close_rate: float = 0.0


class MetaCampaignSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: Optional[str] = None
    top_campaigns: list[dict[str, Any]] = Field(default_factory=list)
    fatigued_entities: int = 0
    scaling_opportunities: int = 0
    account_flags: list[str] = Field(default_factory=list)
    summary: str = "Sin señales relevantes en Meta Ads."


class BookingSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scheduled_today: int = 0
    next_booking_at: Optional[str] = None
    bookings_today: list[dict[str, Any]] = Field(default_factory=list)


class MentorDailyBriefing(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    generated_at: str
    revenue: RevenueSnapshot
    crm: CrmSnapshot
    meta_ads: MetaCampaignSnapshot
    bookings: BookingSnapshot
    top_priorities: list[str]
    lesson_of_day: str
    weekly_challenge: str
    message: str


class MentorWeeklyReview(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    generated_at: str
    revenue: RevenueSnapshot
    crm: CrmSnapshot
    meta_ads: MetaCampaignSnapshot
    worked: list[str]
    did_not_work: list[str]
    patterns_learned: list[str]
    next_week_goals: list[str]
    creative_patterns_saved: int = 0
    message: str


class _MentorContext(BaseModel):
    revenue: RevenueSnapshot
    crm: CrmSnapshot
    meta_ads: MetaCampaignSnapshot
    bookings: BookingSnapshot
    top_patterns: list[dict[str, Any]] = Field(default_factory=list)


class BusinessMentor:
    def __init__(
        self,
        *,
        meta_ads_analyzer: MetaAdsAnalyzer | None = None,
        whatsapp_sender: Callable[..., Any] = send_ycloud_whatsapp_text_message,
    ) -> None:
        self.meta_ads_analyzer = meta_ads_analyzer or MetaAdsAnalyzer()
        self.whatsapp_sender = whatsapp_sender

    async def _query_scalar(
        self,
        query: str,
        params: tuple[Any, ...],
        *,
        default: float = 0.0,
    ) -> float:
        async with aiosqlite.connect(str(_attribution_db_path())) as conn:
            cursor = await conn.execute(query, params)
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
        if not row:
            return float(default)
        value = row[0]
        try:
            return float(value or default)
        except (TypeError, ValueError):
            return float(default)

    async def collect_revenue_metrics(self) -> RevenueSnapshot:
        now = _now_utc()
        today_local = now.astimezone(MENTOR_TZ).date()
        yesterday_start_local = datetime.combine(
            today_local - timedelta(days=1),
            time(0, 0),
            tzinfo=MENTOR_TZ,
        )
        yesterday_end_local = yesterday_start_local + timedelta(days=1)
        month_start_local = datetime.combine(today_local.replace(day=1), time(0, 0), tzinfo=MENTOR_TZ)
        week_start_local = datetime.combine(today_local - timedelta(days=today_local.weekday()), time(0, 0), tzinfo=MENTOR_TZ)

        yesterday_revenue = await self._query_scalar(
            """
            SELECT COALESCE(SUM(revenue), 0)
            FROM closes
            WHERE status = 'won'
              AND created_at >= ?
              AND created_at < ?
            """,
            (_to_iso(yesterday_start_local), _to_iso(yesterday_end_local)),
        )
        month_revenue = await self._query_scalar(
            """
            SELECT COALESCE(SUM(revenue), 0)
            FROM closes
            WHERE status = 'won'
              AND created_at >= ?
            """,
            (_to_iso(month_start_local),),
        )
        week_revenue = await self._query_scalar(
            """
            SELECT COALESCE(SUM(revenue), 0)
            FROM closes
            WHERE status = 'won'
              AND created_at >= ?
            """,
            (_to_iso(week_start_local),),
        )
        week_closes = int(
            await self._query_scalar(
                """
                SELECT COUNT(*)
                FROM closes
                WHERE status = 'won'
                  AND created_at >= ?
                """,
                (_to_iso(week_start_local),),
                default=0,
            )
        )
        week_bookings = int(
            await self._query_scalar(
                """
                SELECT COUNT(*)
                FROM bookings
                WHERE confirmed = 1
                  AND created_at >= ?
                """,
                (_to_iso(week_start_local),),
                default=0,
            )
        )
        monthly_target = float(os.getenv("MONTHLY_REVENUE_TARGET_MXN") or 100000.0)
        progress = round((month_revenue / monthly_target) * 100.0, 2) if monthly_target > 0 else 0.0
        return RevenueSnapshot(
            yesterday_revenue_mxn=round(yesterday_revenue, 2),
            month_revenue_mxn=round(month_revenue, 2),
            monthly_target_mxn=round(monthly_target, 2),
            monthly_progress_pct=progress,
            week_revenue_mxn=round(week_revenue, 2),
            week_closes=week_closes,
            week_bookings=week_bookings,
        )

    async def collect_crm_metrics(self) -> CrmSnapshot:
        try:
            async with AsyncSessionLocal() as session:
                active_stmt = select(func.count()).select_from(CrmLead).where(CrmLead.status.notin_(["won", "lost"]))
                active_leads = int((await session.execute(active_stmt)).scalar() or 0)

                pipeline_rows = (
                    await session.execute(
                        select(CrmLead.status, func.count())
                        .select_from(CrmLead)
                        .group_by(CrmLead.status)
                    )
                ).all()
        except Exception:
            logger.exception("BusinessMentor CRM metrics query failed")
            return CrmSnapshot()

        pipeline = {str(status or "unknown"): int(count or 0) for status, count in pipeline_rows}
        won_count = int(pipeline.get("won", 0))
        lost_count = int(pipeline.get("lost", 0))
        denominator = won_count + lost_count
        close_rate = round((won_count / denominator) if denominator else 0.0, 4)
        return CrmSnapshot(
            active_leads=active_leads,
            pipeline=pipeline,
            won_count=won_count,
            lost_count=lost_count,
            close_rate=close_rate,
        )

    async def collect_meta_metrics(self, snapshot: dict[str, Any] | None = None) -> MetaCampaignSnapshot:
        try:
            report = await self.meta_ads_analyzer.daily_report(snapshot=snapshot)
        except Exception:
            logger.exception("BusinessMentor Meta Ads report failed")
            return MetaCampaignSnapshot()
        return self._meta_snapshot_from_report(report)

    def _meta_snapshot_from_report(self, report: MetaAdsReport) -> MetaCampaignSnapshot:
        campaigns = sorted(
            list(report.campaigns or []),
            key=lambda item: (
                int(bool(item.fatigued)) + int(bool(item.rising_cpa)) + int(bool(item.scaling_opportunity)),
                float(item.spend or 0.0),
            ),
            reverse=True,
        )[:3]
        top_campaigns = [
            {
                "id": item.entity_id,
                "name": item.name,
                "spend": item.spend,
                "roas": item.roas,
                "link_ctr": item.link_ctr,
                "flags": list(item.flags or []),
                "suggested_action": item.suggested_action,
            }
            for item in campaigns
        ]
        fatigued_entities = sum(
            1
            for bucket in (report.campaigns, report.adsets, report.ads)
            for item in bucket
            if item.fatigued
        )
        scaling_opportunities = sum(
            1
            for bucket in (report.campaigns, report.adsets, report.ads)
            for item in bucket
            if item.scaling_opportunity
        )
        summary = report.executive_summary or "Sin señales relevantes en Meta Ads."
        return MetaCampaignSnapshot(
            account_id=report.account_id,
            top_campaigns=top_campaigns,
            fatigued_entities=fatigued_entities,
            scaling_opportunities=scaling_opportunities,
            account_flags=list(report.account_flags or []),
            summary=summary,
        )

    async def collect_bookings_metrics(self) -> BookingSnapshot:
        try:
            upcoming = await list_upcoming_bookings(within_hours=24.0)
        except Exception:
            logger.exception("BusinessMentor bookings lookup failed")
            return BookingSnapshot()

        today_local = _now_utc().astimezone(MENTOR_TZ).date()
        todays_bookings: list[dict[str, Any]] = []
        next_booking_at: str | None = None
        for booking in upcoming:
            try:
                scheduled_dt = datetime.fromisoformat(str(booking.scheduled_at).replace("Z", "+00:00"))
            except ValueError:
                continue
            local_dt = scheduled_dt.astimezone(MENTOR_TZ)
            if local_dt.date() != today_local:
                continue
            todays_bookings.append(
                {
                    "lead_id": booking.lead_id,
                    "scheduled_at": booking.scheduled_at,
                    "source_key": booking.source_key,
                }
            )
            if next_booking_at is None:
                next_booking_at = booking.scheduled_at
        return BookingSnapshot(
            scheduled_today=len(todays_bookings),
            next_booking_at=next_booking_at,
            bookings_today=todays_bookings,
        )

    async def _send_to_raul(self, text: str) -> None:
        to_number = str(os.getenv("RAUL_WHATSAPP_TO") or "").strip()
        from_number = str(os.getenv("YCLOUD_WHATSAPP_FROM") or "").strip()
        preview = str(text or "").strip().replace("\n", " ")[:180]
        logger.info(
            "BusinessMentor WhatsApp env snapshot: RAUL_WHATSAPP_TO=%r YCLOUD_WHATSAPP_FROM=%r text_len=%d preview=%r",
            to_number,
            from_number,
            len(str(text or "")),
            preview,
        )
        if not to_number or not from_number or not str(text or "").strip():
            logger.warning(
                "BusinessMentor WhatsApp send skipped: missing to/from/text (to=%r from=%r text_present=%s)",
                to_number,
                from_number,
                bool(str(text or "").strip()),
            )
            return
        response = await self.whatsapp_sender(from_number=from_number, to=to_number, text=text)
        logger.info("BusinessMentor WhatsApp send response: %s", response)
        if not response:
            logger.warning("BusinessMentor WhatsApp send returned empty response for to=%r from=%r", to_number, from_number)

    def _build_top_priorities(
        self,
        revenue: RevenueSnapshot,
        crm: CrmSnapshot,
        meta_ads: MetaCampaignSnapshot,
        bookings: BookingSnapshot,
    ) -> list[str]:
        priorities: list[str] = []
        if crm.active_leads:
            priorities.append(
                f"Cerrar pipeline activo: {crm.active_leads} leads abiertos y close rate histórico de {round(crm.close_rate * 100, 1)}%."
            )
        if meta_ads.fatigued_entities or meta_ads.account_flags:
            priorities.append(
                f"Arreglar adquisición: {meta_ads.fatigued_entities} entidades fatigadas y flags={', '.join(meta_ads.account_flags[:3]) or 'sin flags'}."
            )
        if bookings.scheduled_today:
            priorities.append(
                f"Asegurar agenda: {bookings.scheduled_today} bookings hoy; confirma asistencia y prepara oferta para cada reunión."
            )
        if revenue.monthly_progress_pct < 50:
            priorities.append(
                f"Recuperar velocidad comercial: vas en {revenue.monthly_progress_pct}% de la meta mensual."
            )
        if not priorities:
            priorities.append("Mantener consistencia comercial: seguir prospectando, dar seguimiento y no dejar leads fríos.")
        return priorities[:3]

    def _lesson_of_day(
        self,
        revenue: RevenueSnapshot,
        crm: CrmSnapshot,
        meta_ads: MetaCampaignSnapshot,
    ) -> str:
        if meta_ads.fatigued_entities:
            return "Si el CTR cae y la frecuencia sube, el problema no es solo presupuesto: refresca creativo antes de escalar."
        if crm.close_rate < 0.2 and crm.active_leads:
            return "Tener leads no significa tener dinero. La disciplina de follow-up y booking hoy vale más que traer tráfico nuevo."
        if revenue.yesterday_revenue_mxn > 0:
            return "Lo que se vendió ayer deja una pista: duplica el comportamiento que produjo cierres, no solo el canal."
        return "El negocio mejora cuando conviertes más rápido lo que ya entra antes de buscar más volumen."

    def _weekly_challenge(self, revenue: RevenueSnapshot, crm: CrmSnapshot) -> str:
        if crm.active_leads >= 10:
            return "Reto semanal: convertir al menos 3 leads activos en reuniones confirmadas antes del viernes."
        if revenue.month_revenue_mxn < revenue.monthly_target_mxn * 0.5:
            return "Reto semanal: lanzar una oferta concreta y medir booking rate diario hasta cerrar la brecha de meta."
        return "Reto semanal: documentar 3 objeciones reales y preparar una respuesta comercial más fuerte para cada una."

    def _daily_message(self, briefing: MentorDailyBriefing) -> str:
        priorities = "\n".join(f"{idx}. {item}" for idx, item in enumerate(briefing.top_priorities, start=1))
        return (
            "Mentor diario 6:30 AM\n\n"
            f"Ingreso ayer: ${briefing.revenue.yesterday_revenue_mxn:,.0f} MXN\n"
            f"Mes actual: ${briefing.revenue.month_revenue_mxn:,.0f} MXN "
            f"({briefing.revenue.monthly_progress_pct:.1f}% de meta)\n"
            f"Leads activos: {briefing.crm.active_leads}\n"
            f"Close rate CRM: {round(briefing.crm.close_rate * 100, 1)}%\n"
            f"Bookings hoy: {briefing.bookings.scheduled_today}\n"
            f"Meta Ads: {briefing.meta_ads.summary}\n\n"
            "Top 3 prioridades de hoy:\n"
            f"{priorities}\n\n"
            f"Lección del día: {briefing.lesson_of_day}\n\n"
            f"Reto semanal: {briefing.weekly_challenge}"
        )

    async def daily_briefing(self, *, snapshot: dict[str, Any] | None = None) -> MentorDailyBriefing:
        revenue = await self.collect_revenue_metrics()
        crm = (
            CrmSnapshot.model_validate(snapshot["crm_metrics"])
            if snapshot and snapshot.get("crm_metrics")
            else await self.collect_crm_metrics()
        )
        meta_ads = (
            MetaCampaignSnapshot.model_validate(snapshot["meta_metrics"])
            if snapshot and snapshot.get("meta_metrics")
            else await self.collect_meta_metrics(snapshot=snapshot.get("meta_ads_snapshot") if snapshot else None)
        )
        bookings = (
            BookingSnapshot.model_validate(snapshot["booking_metrics"])
            if snapshot and snapshot.get("booking_metrics")
            else await self.collect_bookings_metrics()
        )
        briefing = MentorDailyBriefing(
            generated_at=_now_utc().isoformat(),
            revenue=revenue,
            crm=crm,
            meta_ads=meta_ads,
            bookings=bookings,
            top_priorities=self._build_top_priorities(revenue, crm, meta_ads, bookings),
            lesson_of_day=self._lesson_of_day(revenue, crm, meta_ads),
            weekly_challenge=self._weekly_challenge(revenue, crm),
            message="",
        )
        briefing.message = self._daily_message(briefing)
        await self._send_to_raul(briefing.message)
        return briefing

    def _worked(self, revenue: RevenueSnapshot, crm: CrmSnapshot, meta_ads: MetaCampaignSnapshot) -> list[str]:
        worked: list[str] = []
        if revenue.week_revenue_mxn > 0:
            worked.append(f"Se cerraron ${revenue.week_revenue_mxn:,.0f} MXN esta semana.")
        if crm.close_rate >= 0.25:
            worked.append(f"El CRM sostuvo un close rate de {round(crm.close_rate * 100, 1)}%.")
        if meta_ads.scaling_opportunities:
            worked.append(f"Meta detectó {meta_ads.scaling_opportunities} oportunidades claras de escalar.")
        return worked or ["No hubo suficiente tracción fuerte; la semana fue más de aprendizaje que de escala."]

    def _did_not_work(self, revenue: RevenueSnapshot, crm: CrmSnapshot, meta_ads: MetaCampaignSnapshot) -> list[str]:
        issues: list[str] = []
        if meta_ads.fatigued_entities:
            issues.append(f"Hubo {meta_ads.fatigued_entities} entidades fatigadas en Meta.")
        if crm.active_leads and crm.close_rate < 0.2:
            issues.append("El pipeline sigue abierto demasiado tiempo y no está cerrando con suficiente velocidad.")
        if revenue.week_bookings == 0:
            issues.append("No hubo bookings suficientes para alimentar la próxima semana.")
        return issues or ["No se detectaron bloqueos críticos; toca repetir lo que ya está funcionando."]

    def _next_week_goals(self, revenue: RevenueSnapshot, crm: CrmSnapshot, bookings: BookingSnapshot) -> list[str]:
        goals = [
            f"Subir la meta semanal a ${max(revenue.week_revenue_mxn * 1.2, 15000):,.0f} MXN.",
            f"Llevar el close rate a {max(round(crm.close_rate * 100 + 5, 1), 20):.1f}%.",
            f"Asegurar al menos {max(bookings.scheduled_today + 3, 5)} bookings nuevos.",
        ]
        return goals

    def _discover_patterns(self, meta_ads: MetaCampaignSnapshot) -> list[CreativePattern]:
        patterns: list[CreativePattern] = []
        for campaign in meta_ads.top_campaigns[:2]:
            name = str(campaign.get("name") or "").strip()
            if not name:
                continue
            roas = float(campaign.get("roas") or 0.0)
            ctr = float(campaign.get("link_ctr") or 0.0)
            patterns.append(
                CreativePattern(
                    domain="weekly_review",
                    offer="Mentoría comercial semanal",
                    hook=f"Campaña destacada: {name}",
                    angle=f"Patrón observado: ROAS {roas:.2f}x con CTR {ctr:.2f}.",
                    cta="Repetir ángulo ganador y testear 2 variantes nuevas.",
                    pattern_used="weekly_review",
                    critic_score=max(0.5, min(roas / 4.0, 0.95)),
                    ctr=ctr,
                    booking_rate=None,
                    close_rate=None,
                    campaign_id=str(campaign.get("id") or "") or None,
                    traffic_temperature="warm",
                )
            )
        return patterns

    def _weekly_message(self, review: MentorWeeklyReview) -> str:
        worked = "\n".join(f"- {item}" for item in review.worked)
        didnt = "\n".join(f"- {item}" for item in review.did_not_work)
        patterns = "\n".join(f"- {item}" for item in review.patterns_learned)
        goals = "\n".join(f"- {item}" for item in review.next_week_goals)
        return (
            "Weekly review\n\n"
            f"Ingreso semana: ${review.revenue.week_revenue_mxn:,.0f} MXN\n"
            f"Bookings semana: {review.revenue.week_bookings}\n"
            f"Cierres semana: {review.revenue.week_closes}\n\n"
            "Qué funcionó:\n"
            f"{worked}\n\n"
            "Qué no funcionó:\n"
            f"{didnt}\n\n"
            "Patrones aprendidos:\n"
            f"{patterns}\n\n"
            "Metas siguiente semana:\n"
            f"{goals}"
        )

    async def weekly_review(self, *, snapshot: dict[str, Any] | None = None) -> MentorWeeklyReview:
        revenue = await self.collect_revenue_metrics()
        crm = (
            CrmSnapshot.model_validate(snapshot["crm_metrics"])
            if snapshot and snapshot.get("crm_metrics")
            else await self.collect_crm_metrics()
        )
        meta_ads = (
            MetaCampaignSnapshot.model_validate(snapshot["meta_metrics"])
            if snapshot and snapshot.get("meta_metrics")
            else await self.collect_meta_metrics(snapshot=snapshot.get("meta_ads_snapshot") if snapshot else None)
        )
        bookings = (
            BookingSnapshot.model_validate(snapshot["booking_metrics"])
            if snapshot and snapshot.get("booking_metrics")
            else await self.collect_bookings_metrics()
        )

        patterns = self._discover_patterns(meta_ads)
        for pattern in patterns:
            save_creative_pattern(pattern)

        review = MentorWeeklyReview(
            generated_at=_now_utc().isoformat(),
            revenue=revenue,
            crm=crm,
            meta_ads=meta_ads,
            worked=self._worked(revenue, crm, meta_ads),
            did_not_work=self._did_not_work(revenue, crm, meta_ads),
            patterns_learned=[pattern.angle for pattern in patterns] or [meta_ads.summary],
            next_week_goals=self._next_week_goals(revenue, crm, bookings),
            creative_patterns_saved=len(patterns),
            message="",
        )
        review.message = self._weekly_message(review)
        await self._send_to_raul(review.message)
        return review

    async def _call_claude(self, prompt: str) -> str:
        api_key = str(os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        model = str(os.getenv("BUSINESS_MENTOR_MODEL") or "claude-sonnet-4-6").strip()
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 900,
                    "system": (
                        "Actua como un mentor de negocio senior para una agencia/estudio de automatizacion, "
                        "marketing y ventas. Responde en espanol con claridad, criterio comercial y enfoque en caja."
                    ),
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            payload = response.json()
        blocks = payload.get("content") or []
        texts = [str(block.get("text") or "").strip() for block in blocks if isinstance(block, dict)]
        return "\n".join(part for part in texts if part).strip()

    async def _load_context(self) -> _MentorContext:
        revenue = await self.collect_revenue_metrics()
        crm = await self.collect_crm_metrics()
        meta_ads = await self.collect_meta_metrics()
        bookings = await self.collect_bookings_metrics()
        top_patterns = [pattern.__dict__ for pattern in get_top_patterns(domain="campaign_execution", limit=5)]
        return _MentorContext(
            revenue=revenue,
            crm=crm,
            meta_ads=meta_ads,
            bookings=bookings,
            top_patterns=top_patterns,
        )

    def _fallback_answer(self, question: str, context: _MentorContext) -> str:
        return (
            "Resumen ejecutivo:\n"
            f"- Ingreso ayer: ${context.revenue.yesterday_revenue_mxn:,.0f} MXN\n"
            f"- Progreso mensual: {context.revenue.monthly_progress_pct:.1f}%\n"
            f"- Leads activos: {context.crm.active_leads}\n"
            f"- Bookings hoy: {context.bookings.scheduled_today}\n\n"
            f"Sobre tu pregunta: {question}\n"
            "Mi recomendación es priorizar cierre de pipeline, calidad de booking y repetir los patrones creativos que ya mostraron tracción."
        )

    async def answer_question(self, question: str) -> str:
        clean_question = str(question or "").strip()
        if not clean_question:
            raise ValueError("question is required")
        logger.info("BusinessMentor answer_question called: %r", clean_question[:240])
        context = await self._load_context()
        prompt = (
            "Contexto del negocio:\n"
            f"{json.dumps(context.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n\n"
            f"Pregunta:\n{clean_question}\n\n"
            "Responde como mentor senior. Prioriza dinero, foco, execution y siguiente accion concreta."
        )
        try:
            answer = await self._call_claude(prompt)
            if answer:
                logger.info("BusinessMentor answer_question returning Claude answer length=%d", len(answer))
                return answer
        except Exception:
            logger.exception("BusinessMentor Claude call failed; using fallback answer")
        fallback = self._fallback_answer(clean_question, context)
        logger.info("BusinessMentor answer_question returning fallback length=%d", len(fallback))
        return fallback
