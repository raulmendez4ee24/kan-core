from __future__ import annotations

import asyncio
from calendar import monthrange
from datetime import datetime, time, timedelta, timezone
import html
import json
import os
from pathlib import Path
import secrets
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite
import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from brain.attribution_engine import get_latest_briefing
from brain.business_mentor import BusinessMentor
from brain.content_publisher import ContentPublisher

router = APIRouter(tags=["dashboard"])

DASHBOARD_TZ = ZoneInfo("America/Mexico_City")


def _dashboard_token() -> str:
    return str(os.getenv("DASHBOARD_TOKEN") or "").strip()


def _require_dashboard_token(token: str | None) -> None:
    expected = _dashboard_token()
    if not expected:
        raise HTTPException(status_code=503, detail="DASHBOARD_TOKEN not configured")
    if not token or not secrets.compare_digest(str(token), expected):
        raise HTTPException(status_code=401, detail="Invalid dashboard token")


def _attribution_db_path() -> Path:
    configured = str(os.getenv("ATTRIBUTION_ENGINE_DB_PATH") or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "data" / "attribution_engine.sqlite3"


def _hunter_outbound_db_path() -> Path:
    configured = str(os.getenv("HUNTER_OUTBOUND_DB_PATH") or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "data" / "hunter_outbound.sqlite3"


def _fmt_money(value: float | int | None) -> str:
    amount = float(value or 0.0)
    return f"${amount:,.0f} MXN"


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        local = parsed.astimezone(DASHBOARD_TZ)
        return local.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def _goal_pair(monthly_goal: float) -> tuple[float, float]:
    now = datetime.now(DASHBOARD_TZ)
    days = max(1, monthrange(now.year, now.month)[1])
    daily_goal = float(monthly_goal or 0.0) / days if monthly_goal else 0.0
    return daily_goal, float(monthly_goal or 0.0)


async def _query_revenue() -> dict[str, float]:
    db_path = _attribution_db_path()
    if not db_path.exists():
        return {"today_revenue": 0.0, "month_revenue": 0.0}

    now_local = datetime.now(DASHBOARD_TZ)
    day_start = datetime.combine(now_local.date(), time(0, 0), tzinfo=DASHBOARD_TZ)
    month_start = datetime.combine(now_local.date().replace(day=1), time(0, 0), tzinfo=DASHBOARD_TZ)
    async with aiosqlite.connect(str(db_path)) as conn:
        today_cursor = await conn.execute(
            """
            SELECT COALESCE(SUM(revenue), 0)
            FROM closes
            WHERE status = 'won'
              AND created_at >= ?
              AND created_at < ?
            """,
            (
                day_start.astimezone(timezone.utc).isoformat(),
                (day_start + timedelta(days=1)).astimezone(timezone.utc).isoformat(),
            ),
        )
        month_cursor = await conn.execute(
            """
            SELECT COALESCE(SUM(revenue), 0)
            FROM closes
            WHERE status = 'won'
              AND created_at >= ?
            """,
            (month_start.astimezone(timezone.utc).isoformat(),),
        )
        today_row = await today_cursor.fetchone()
        month_row = await month_cursor.fetchone()
        await today_cursor.close()
        await month_cursor.close()
    return {
        "today_revenue": float(today_row[0] or 0.0) if today_row else 0.0,
        "month_revenue": float(month_row[0] or 0.0) if month_row else 0.0,
    }


async def _query_outbound_messages() -> dict[str, Any]:
    db_path = _hunter_outbound_db_path()
    if not db_path.exists():
        return {"today_count": 0, "items": []}

    today_local = datetime.now(DASHBOARD_TZ).date().isoformat()
    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        count_cursor = await conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM hunter_outbound_log
            WHERE status = 'sent' AND sent_date_local = ?
            """,
            (today_local,),
        )
        list_cursor = await conn.execute(
            """
            SELECT business_name, normalized_phone, template_name, status, sent_at, reason
            FROM hunter_outbound_log
            WHERE status = 'sent'
            ORDER BY datetime(COALESCE(sent_at, created_at)) DESC
            LIMIT 5
            """
        )
        count_row = await count_cursor.fetchone()
        rows = await list_cursor.fetchall()
        await count_cursor.close()
        await list_cursor.close()
    return {
        "today_count": int(count_row["total"] or 0) if count_row else 0,
        "items": [dict(row) for row in rows],
    }


async def _query_instagram_posts() -> list[dict[str, Any]]:
    publisher = ContentPublisher()
    rows = await publisher.list_scheduled_posts(limit=50)
    published = [row for row in rows if row.status == "published"]
    published.sort(key=lambda row: row.updated_at, reverse=True)
    return [
        {
            "post_id": row.post.post_id,
            "hook": row.post.hook,
            "platform": row.post.platform,
            "media_id": row.media_id,
            "updated_at": row.updated_at,
        }
        for row in published[:3]
    ]


async def _query_scheduler_jobs(request: Request) -> list[dict[str, str]]:
    scheduler_wrapper = getattr(request.app.state, "revenue_scheduler", None)
    if scheduler_wrapper is None or not getattr(scheduler_wrapper, "scheduler", None):
        return []
    jobs = []
    for job in scheduler_wrapper.scheduler.get_jobs():
        jobs.append(
            {
                "id": job.id,
                "name": job.name or job.id,
                "last_run": str(getattr(scheduler_wrapper, "job_last_run", {}).get(job.id) or ""),
                "next_run": job.next_run_time.isoformat() if job.next_run_time else "",
            }
        )
    jobs.sort(key=lambda item: item["id"])
    return jobs


async def _check_http_status(name: str, url: str, *, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> dict[str, str]:
    started = asyncio.get_running_loop().time()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url, headers=headers, params=params)
        latency_ms = int((asyncio.get_running_loop().time() - started) * 1000)
        if response.status_code < 400:
            return {"name": name, "status": "up", "detail": f"{response.status_code} · {latency_ms}ms"}
        return {"name": name, "status": "down", "detail": f"{response.status_code} · {latency_ms}ms"}
    except Exception as exc:
        latency_ms = int((asyncio.get_running_loop().time() - started) * 1000)
        return {"name": name, "status": "down", "detail": f"{type(exc).__name__} · {latency_ms}ms"}


async def _check_api_statuses() -> list[dict[str, str]]:
    checks: list[asyncio.Future] = []

    ycloud_key = str(os.getenv("YCLOUD_API_KEY") or "").strip()
    if ycloud_key:
        checks.append(
            _check_http_status(
                "YCloud",
                "https://api.ycloud.com/v2/whatsapp/phoneNumbers",
                headers={"X-API-Key": ycloud_key},
            )
        )
    else:
        checks.append(asyncio.sleep(0, result={"name": "YCloud", "status": "down", "detail": "missing key"}))

    stripe_key = str(os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if stripe_key:
        checks.append(
            _check_http_status(
                "Stripe",
                "https://api.stripe.com/v1/balance",
                headers={"Authorization": f"Bearer {stripe_key}"},
            )
        )
    else:
        checks.append(asyncio.sleep(0, result={"name": "Stripe", "status": "down", "detail": "missing key"}))

    meta_token = str(os.getenv("META_ACCESS_TOKEN") or "").strip()
    meta_account = str(os.getenv("INSTAGRAM_ACCOUNT_ID") or os.getenv("META_AD_ACCOUNT_ID") or "").strip()
    if meta_token and meta_account:
        checks.append(
            _check_http_status(
                "Meta",
                f"https://graph.facebook.com/v22.0/{meta_account}",
                params={"fields": "id", "access_token": meta_token},
            )
        )
    elif meta_token:
        checks.append(
            _check_http_status(
                "Meta",
                "https://graph.facebook.com/v22.0/me",
                params={"access_token": meta_token},
            )
        )
    else:
        checks.append(asyncio.sleep(0, result={"name": "Meta", "status": "down", "detail": "missing key"}))

    google_key = str(os.getenv("GOOGLE_API_KEY") or "").strip()
    if google_key:
        checks.append(
            _check_http_status(
                "Gemini",
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": google_key},
            )
        )
    else:
        checks.append(asyncio.sleep(0, result={"name": "Gemini", "status": "down", "detail": "missing key"}))

    eleven_key = str(os.getenv("ELEVENLABS_API_KEY") or "").strip()
    if eleven_key:
        checks.append(
            _check_http_status(
                "ElevenLabs",
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": eleven_key},
            )
        )
    else:
        checks.append(asyncio.sleep(0, result={"name": "ElevenLabs", "status": "down", "detail": "missing key"}))

    return list(await asyncio.gather(*checks))


async def collect_dashboard_data(request: Request) -> dict[str, Any]:
    mentor = BusinessMentor()
    api_statuses, revenue_numbers, crm, outbound, instagram_posts, scheduler_jobs, briefing = await asyncio.gather(
        _check_api_statuses(),
        _query_revenue(),
        mentor.collect_crm_metrics(),
        _query_outbound_messages(),
        _query_instagram_posts(),
        _query_scheduler_jobs(request),
        get_latest_briefing("daily"),
    )

    monthly_goal = float(os.getenv("MONTHLY_REVENUE_TARGET_MXN") or 100000.0)
    daily_goal, monthly_goal = _goal_pair(monthly_goal)

    return {
        "generated_at": datetime.now(DASHBOARD_TZ).isoformat(),
        "api_statuses": api_statuses,
        "revenue": {
            "today": revenue_numbers["today_revenue"],
            "today_goal": daily_goal,
            "month": revenue_numbers["month_revenue"],
            "month_goal": monthly_goal,
        },
        "crm": crm.model_dump(mode="json"),
        "outbound": outbound,
        "instagram_posts": instagram_posts,
        "scheduler_jobs": scheduler_jobs,
        "daily_briefing": briefing.model_dump(mode="json") if briefing else None,
    }


def _render_daily_briefing(briefing: dict[str, Any] | None) -> str:
    if not briefing:
        return "<div class='empty'>No daily briefing found.</div>"
    content = dict(briefing.get("content") or {})
    diagnosis = dict(content.get("diagnosis") or {})
    recommended = list(content.get("recommended") or [])
    executed = list(content.get("executed") or [])
    lines = [
        f"<div class='briefing-meta'>Generated: {_fmt_dt(briefing.get('created_at'))}</div>",
        f"<div class='briefing-summary'>{html.escape(str(diagnosis.get('summary') or 'Sin resumen.'))}</div>",
    ]
    opportunities = list(diagnosis.get("opportunities") or [])
    risks = list(diagnosis.get("risks") or [])
    if opportunities:
        lines.append("<h4>Opportunities</h4><ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in opportunities[:5]) + "</ul>")
    if risks:
        lines.append("<h4>Risks</h4><ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in risks[:5]) + "</ul>")
    if recommended:
        lines.append(
            "<h4>Recommended</h4><ul>"
            + "".join(
                f"<li>{html.escape(str(item.get('operator') or ''))} · {html.escape(str(item.get('action_type') or ''))}</li>"
                for item in recommended[:5]
            )
            + "</ul>"
        )
    if executed:
        lines.append(
            "<h4>Executed</h4><ul>"
            + "".join(
                f"<li>{html.escape(str(item.get('operator') or ''))} · {html.escape(str(item.get('action_type') or ''))}</li>"
                for item in executed[:5]
            )
            + "</ul>"
        )
    return "".join(lines)


def build_dashboard_html(data: dict[str, Any]) -> str:
    api_rows = "".join(
        f"""
        <div class="status-row">
          <span class="dot {'up' if item['status'] == 'up' else 'down'}">{'🟢' if item['status'] == 'up' else '🔴'}</span>
          <span class="label">{html.escape(item['name'])}</span>
          <span class="detail">{html.escape(item['detail'])}</span>
        </div>
        """
        for item in data["api_statuses"]
    )
    whatsapp_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(item.get('business_name') or ''))}</td>
          <td>{html.escape(str(item.get('normalized_phone') or ''))}</td>
          <td>{html.escape(str(item.get('template_name') or ''))}</td>
          <td>{_fmt_dt(item.get('sent_at'))}</td>
        </tr>
        """
        for item in data["outbound"]["items"]
    ) or "<tr><td colspan='4' class='empty'>No outbound messages yet.</td></tr>"
    instagram_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(item.get('hook') or ''))}</td>
          <td>{html.escape(str(item.get('media_id') or ''))}</td>
          <td>{_fmt_dt(item.get('updated_at'))}</td>
        </tr>
        """
        for item in data["instagram_posts"]
    ) or "<tr><td colspan='3' class='empty'>No Instagram posts published yet.</td></tr>"
    scheduler_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(str(item.get('id') or ''))}</td>
          <td>{_fmt_dt(item.get('last_run'))}</td>
          <td>{_fmt_dt(item.get('next_run'))}</td>
        </tr>
        """
        for item in data["scheduler_jobs"]
    ) or "<tr><td colspan='3' class='empty'>Scheduler not initialized.</td></tr>"
    pipeline = dict(data["crm"].get("pipeline") or {})
    pipeline_rows = "".join(
        f"<div class='mini'><span>{html.escape(str(key))}</span><strong>{int(value)}</strong></div>"
        for key, value in sorted(pipeline.items())
    ) or "<div class='empty'>No pipeline data.</div>"
    generated_at = _fmt_dt(data.get("generated_at"))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="30">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>KAN Logic Dashboard</title>
  <style>
    :root {{
      --bg: #0a0a14;
      --panel: #11111c;
      --panel-2: #151526;
      --border: #1f2033;
      --text: #f3f4f7;
      --muted: #8c90a6;
      --accent: #e94560;
      --secondary: #f97316;
      --green: #34d399;
      --red: #fb7185;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: radial-gradient(circle at top right, rgba(233,69,96,0.08), transparent 30%), var(--bg);
      color: var(--text);
      font: 14px/1.5 "SF Mono", "JetBrains Mono", ui-monospace, monospace;
    }}
    .wrap {{ max-width: 1480px; margin: 0 auto; padding: 24px; }}
    .top {{
      display: flex; justify-content: space-between; align-items: baseline; gap: 16px; margin-bottom: 20px;
      border-bottom: 1px solid var(--border); padding-bottom: 14px;
    }}
    h1 {{ margin: 0; font-size: 24px; letter-spacing: 0.04em; }}
    .stamp {{ color: var(--muted); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 16px;
    }}
    .card {{
      background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01));
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
    }}
    .span-4 {{ grid-column: span 4; }}
    .span-5 {{ grid-column: span 5; }}
    .span-6 {{ grid-column: span 6; }}
    .span-7 {{ grid-column: span 7; }}
    .span-8 {{ grid-column: span 8; }}
    .span-12 {{ grid-column: span 12; }}
    .title {{
      color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; font-size: 11px; margin-bottom: 14px;
    }}
    .metrics {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }}
    .metric {{
      background: var(--panel-2); border: 1px solid var(--border); border-radius: 10px; padding: 14px;
    }}
    .metric strong {{ display: block; font-size: 24px; margin-top: 6px; }}
    .goal {{ color: var(--muted); font-size: 12px; }}
    .status-row {{
      display: grid; grid-template-columns: 40px 1fr auto; align-items: center; gap: 10px;
      padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.04);
    }}
    .status-row:last-child {{ border-bottom: 0; }}
    .label {{ font-weight: 700; }}
    .detail {{ color: var(--muted); }}
    .mini-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }}
    .mini {{
      background: var(--panel-2); border: 1px solid var(--border); border-radius: 10px; padding: 10px;
      display: flex; justify-content: space-between; gap: 8px;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid rgba(255,255,255,0.05); vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }}
    .briefing-summary {{ font-size: 16px; margin: 10px 0 14px; color: #f0f0f5; }}
    .briefing-meta {{ color: var(--muted); margin-bottom: 8px; }}
    .empty {{ color: var(--muted); }}
    h4 {{ margin: 14px 0 8px; color: var(--secondary); }}
    ul {{ margin: 0; padding-left: 18px; }}
    li {{ margin-bottom: 6px; }}
    @media (max-width: 1100px) {{
      .span-4, .span-5, .span-6, .span-7, .span-8 {{ grid-column: span 12; }}
      .metrics, .mini-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <h1>KAN LOGIC / TERMINAL</h1>
      <div class="stamp">Updated {generated_at}</div>
    </div>
    <div class="grid">
      <section class="card span-4">
        <div class="title">API Status</div>
        {api_rows}
      </section>
      <section class="card span-4">
        <div class="title">Revenue</div>
        <div class="metrics">
          <div class="metric">
            <span>Today</span>
            <strong>{_fmt_money(data['revenue']['today'])}</strong>
            <div class="goal">Goal { _fmt_money(data['revenue']['today_goal']) }</div>
          </div>
          <div class="metric">
            <span>This Month</span>
            <strong>{_fmt_money(data['revenue']['month'])}</strong>
            <div class="goal">Goal { _fmt_money(data['revenue']['month_goal']) }</div>
          </div>
        </div>
      </section>
      <section class="card span-4">
        <div class="title">Pipeline</div>
        <div class="metrics">
          <div class="metric">
            <span>Active Leads</span>
            <strong>{int(data['crm'].get('active_leads') or 0)}</strong>
            <div class="goal">CRM active pipeline</div>
          </div>
          <div class="metric">
            <span>Outbound Today</span>
            <strong>{int(data['outbound'].get('today_count') or 0)}</strong>
            <div class="goal">WhatsApp messages sent</div>
          </div>
        </div>
        <div class="title" style="margin-top:14px;">Pipeline Breakdown</div>
        <div class="mini-grid">{pipeline_rows}</div>
      </section>
      <section class="card span-6">
        <div class="title">Last 5 WhatsApp Messages Sent</div>
        <table>
          <thead><tr><th>Business</th><th>Phone</th><th>Template</th><th>Sent</th></tr></thead>
          <tbody>{whatsapp_rows}</tbody>
        </table>
      </section>
      <section class="card span-6">
        <div class="title">Last 3 Instagram Posts Published</div>
        <table>
          <thead><tr><th>Hook</th><th>Media ID</th><th>Published</th></tr></thead>
          <tbody>{instagram_rows}</tbody>
        </table>
      </section>
      <section class="card span-12">
        <div class="title">Scheduler Jobs</div>
        <table>
          <thead><tr><th>Job</th><th>Last Run</th><th>Next Run</th></tr></thead>
          <tbody>{scheduler_rows}</tbody>
        </table>
      </section>
      <section class="card span-12">
        <div class="title">Last Daily Briefing</div>
        {_render_daily_briefing(data.get('daily_briefing'))}
      </section>
    </div>
  </div>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    token: str | None = Query(default=None),
):
    _require_dashboard_token(token)
    data = await collect_dashboard_data(request)
    return HTMLResponse(build_dashboard_html(data))
