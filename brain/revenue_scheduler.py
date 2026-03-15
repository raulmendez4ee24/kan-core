from __future__ import annotations

import logging
import os
import time
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from brain.attribution_engine import list_upcoming_bookings, save_briefing
from brain.brand_director import BrandDirector
from brain.business_mentor import BusinessMentor
from brain.content_publisher import ContentPublisher
from brain.revenue_brain import DailyBriefing, RevenueBrain

logger = logging.getLogger("kan_core.revenue_scheduler")
REVENUE_TZ = ZoneInfo("America/Mexico_City")


def _env_bool(name: str, default: str = "true") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes"}


class RevenueBrainScheduler:
    def __init__(self, revenue_brain: RevenueBrain | None = None) -> None:
        self.revenue_brain = revenue_brain or RevenueBrain()
        self.brand_director = BrandDirector()
        self.business_mentor = BusinessMentor()
        self.content_publisher = ContentPublisher()
        self.scheduler = AsyncIOScheduler(timezone=REVENUE_TZ)
        self.last_daily_briefing: DailyBriefing | None = None
        self.last_evening_briefing: DailyBriefing | None = None
        self.last_mentor_daily_briefing: dict[str, object] | None = None
        self.last_mentor_weekly_review: dict[str, object] | None = None
        self.last_brand_daily_post: dict[str, object] | None = None
        self.last_brand_weekly_plan: list[dict[str, object]] | None = None
        self.last_published_content: list[dict[str, object]] | None = None
        self._configured = False

    def configure(self) -> None:
        if self._configured:
            return
        self.scheduler.add_job(
            self.run_daily_cycle,
            CronTrigger(hour=6, minute=0, timezone=REVENUE_TZ),
            id="revenue-daily-cycle",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_brand_daily_post,
            CronTrigger(hour=6, minute=0, timezone=REVENUE_TZ),
            id="brand-daily-post",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_brand_weekly_plan,
            CronTrigger(day_of_week="sun", hour=9, minute=0, timezone=REVENUE_TZ),
            id="brand-weekly-plan",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_evening_check,
            CronTrigger(hour=18, minute=0, timezone=REVENUE_TZ),
            id="revenue-evening-check",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_confirmation_calls,
            CronTrigger(minute=0, timezone=REVENUE_TZ),  # every hour on the hour
            id="revenue-confirmation-calls",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_content_publish_queue,
            CronTrigger(minute=0, timezone=REVENUE_TZ),  # every hour on the hour
            id="content-publish-hourly",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_business_mentor_daily_briefing,
            CronTrigger(hour=6, minute=30, timezone=REVENUE_TZ),
            id="business-mentor-daily-briefing",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_business_mentor_weekly_review,
            CronTrigger(day_of_week="sun", hour=10, minute=0, timezone=REVENUE_TZ),
            id="business-mentor-weekly-review",
            replace_existing=True,
        )
        self._configured = True

    def start(self) -> None:
        self.configure()
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info(
                "RevenueBrain scheduler started with jobs: daily_cycle@06:00, brand_daily@06:00, mentor_daily@06:30, evening_check@18:00, brand_weekly@Sun09:00, mentor_weekly@Sun10:00, confirmation_calls@hourly, content_publish@hourly (%s)",
                REVENUE_TZ.key,
            )

    async def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("RevenueBrain scheduler stopped.")

    async def _run_with_logging(self, name: str, runner: Any) -> DailyBriefing | None:
        started = time.perf_counter()
        logger.info("RevenueBrain %s started.", name)
        try:
            briefing = await runner()
            duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
            logger.info("RevenueBrain %s finished in %sms.", name, duration_ms)
            return briefing
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
            logger.exception("RevenueBrain %s failed after %sms.", name, duration_ms)
            return None

    async def run_daily_cycle(self) -> DailyBriefing | None:
        briefing = await self._run_with_logging("daily_cycle", self.revenue_brain.daily_cycle)
        if briefing is not None:
            self.last_daily_briefing = briefing
            try:
                await save_briefing(
                    briefing_type="daily",
                    content=briefing.model_dump(mode="json"),
                    actions_executed=len(briefing.executed),
                    actions_recommended=len(briefing.recommended),
                )
            except Exception:
                logger.exception("Failed to persist daily RevenueBrain briefing.")
        return briefing

    async def run_confirmation_calls(self) -> list[str]:
        """Hourly job: send confirmation calls for bookings within the next 2 hours."""
        brain = self.revenue_brain
        if not brain._voice_say:
            return []
        started = time.perf_counter()
        logger.info("RevenueBrain confirmation_calls started.")
        try:
            from brain.voice_engine import schedule_confirmation_calls

            bookings = await list_upcoming_bookings(within_hours=2.0)
            called = await schedule_confirmation_calls(bookings, say=brain._voice_say)
            duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
            logger.info(
                "RevenueBrain confirmation_calls finished in %sms — called %s bookings.",
                duration_ms,
                len(called),
            )
            return called
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
            logger.exception("RevenueBrain confirmation_calls failed after %sms.", duration_ms)
            return []

    async def run_content_publish_queue(self) -> list[dict[str, object]]:
        started = time.perf_counter()
        logger.info("RevenueBrain content_publish_queue started.")
        try:
            published = await self.content_publisher.check_scheduled_posts()
            duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
            self.last_published_content = [item.model_dump(mode="json") for item in published]
            logger.info(
                "RevenueBrain content_publish_queue finished in %sms — processed %s posts.",
                duration_ms,
                len(published),
            )
            return self.last_published_content
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
            logger.exception("RevenueBrain content_publish_queue failed after %sms.", duration_ms)
            return []

    async def run_evening_check(self) -> DailyBriefing | None:
        briefing = await self._run_with_logging("evening_check", self.revenue_brain.evening_check)
        if briefing is not None:
            self.last_evening_briefing = briefing
            try:
                await save_briefing(
                    briefing_type="evening",
                    content=briefing.model_dump(mode="json"),
                    actions_executed=len(briefing.executed),
                    actions_recommended=len(briefing.recommended),
                )
            except Exception:
                logger.exception("Failed to persist evening RevenueBrain briefing.")
        return briefing

    async def run_brand_daily_post(self) -> dict[str, object] | None:
        post = await self._run_with_logging("brand_daily_post", self.brand_director.generate_daily_post)
        if post is not None:
            self.last_brand_daily_post = post.model_dump(mode="json")
            return self.last_brand_daily_post
        return None

    async def run_brand_weekly_plan(self) -> list[dict[str, object]] | None:
        plan = await self._run_with_logging("brand_weekly_plan", self.brand_director.generate_weekly_content_plan)
        if plan is not None:
            self.last_brand_weekly_plan = [item.model_dump(mode="json") for item in plan]
            return self.last_brand_weekly_plan
        return None

    async def run_business_mentor_daily_briefing(self) -> dict[str, object] | None:
        briefing = await self._run_with_logging("business_mentor_daily_briefing", self.business_mentor.daily_briefing)
        if briefing is not None:
            self.last_mentor_daily_briefing = briefing.model_dump(mode="json")
            return self.last_mentor_daily_briefing
        return None

    async def run_business_mentor_weekly_review(self) -> dict[str, object] | None:
        review = await self._run_with_logging("business_mentor_weekly_review", self.business_mentor.weekly_review)
        if review is not None:
            self.last_mentor_weekly_review = review.model_dump(mode="json")
            return self.last_mentor_weekly_review
        return None


def should_start_revenue_scheduler() -> bool:
    return _env_bool("ENABLE_REVENUE_BRAIN_SCHEDULER", "true")
