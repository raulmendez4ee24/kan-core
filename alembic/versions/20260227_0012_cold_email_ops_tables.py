"""Add Cold Email Ops tables: ce_leads, ce_inboxes, ce_campaigns, ce_domains, ce_runs, ce_sends.

Opción B — DB + Sheets en paralelo. Sheets sigue siendo el input; DB guarda
historial, trazabilidad y permite migración futura a "solo DB".

Revision ID: 20260227_0012
Revises: 20260224_0011
Create Date: 2026-02-27 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260227_0012"
down_revision: Union[str, None] = "20260224_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # ce_leads — mirror de la tab 'leads'. Upsert por (spreadsheet_id, sheet_lead_id)
    # ------------------------------------------------------------------
    op.create_table(
        "ce_leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("spreadsheet_id", sa.String(128), nullable=False),
        sa.Column("sheet_lead_id", sa.String(160), nullable=False),
        # Sheet columns
        sa.Column("company", sa.String(240), nullable=True),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("first_name", sa.String(120), nullable=True),
        sa.Column("last_name", sa.String(120), nullable=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("linkedin", sa.String(500), nullable=True),
        sa.Column("country", sa.String(8), nullable=True),
        sa.Column("segment", sa.String(80), nullable=True),
        sa.Column("status", sa.String(40), nullable=True),
        sa.Column("last_contacted_at", sa.String(32), nullable=True),
        sa.Column("source", sa.String(80), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("personalization_snippet", sa.Text, nullable=True),
        sa.Column("email_validation_status", sa.String(20), nullable=True),
        sa.Column("score", sa.Integer, nullable=True),
        # Internal
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("spreadsheet_id", "sheet_lead_id", name="uq_ce_leads_sheet"),
    )
    op.create_index("ix_ce_leads_spreadsheet_id", "ce_leads", ["spreadsheet_id"])
    op.create_index("ix_ce_leads_sheet_lead_id", "ce_leads", ["sheet_lead_id"])
    op.create_index("ix_ce_leads_email", "ce_leads", ["email"])
    op.create_index("ix_ce_leads_status", "ce_leads", ["status"])
    op.create_index("ix_ce_leads_segment", "ce_leads", ["segment"])
    op.create_index("ix_ce_leads_email_validation_status", "ce_leads", ["email_validation_status"])

    # ------------------------------------------------------------------
    # ce_inboxes — mirror de la tab 'inboxes'. Upsert por (spreadsheet_id, sheet_inbox_id)
    # ------------------------------------------------------------------
    op.create_table(
        "ce_inboxes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("spreadsheet_id", sa.String(128), nullable=False),
        sa.Column("sheet_inbox_id", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(40), nullable=True),
        sa.Column("from_name", sa.String(200), nullable=True),
        sa.Column("from_email", sa.String(320), nullable=True),
        sa.Column("domain", sa.String(253), nullable=True),
        sa.Column("warmup_status", sa.String(20), nullable=True),
        sa.Column("daily_limit", sa.Integer, nullable=True),
        sa.Column("sent_today", sa.Integer, nullable=True),
        sa.Column("health_score", sa.Integer, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("rotation_group", sa.String(80), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("spreadsheet_id", "sheet_inbox_id", name="uq_ce_inboxes_sheet"),
    )
    op.create_index("ix_ce_inboxes_spreadsheet_id", "ce_inboxes", ["spreadsheet_id"])
    op.create_index("ix_ce_inboxes_from_email", "ce_inboxes", ["from_email"])
    op.create_index("ix_ce_inboxes_domain", "ce_inboxes", ["domain"])

    # ------------------------------------------------------------------
    # ce_campaigns — mirror de la tab 'campaign'. Upsert por (spreadsheet_id, sheet_campaign_id)
    # ------------------------------------------------------------------
    op.create_table(
        "ce_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("spreadsheet_id", sa.String(128), nullable=False),
        sa.Column("sheet_campaign_id", sa.String(160), nullable=False),
        sa.Column("offer", sa.Text, nullable=True),
        sa.Column("target_segment", sa.String(160), nullable=True),
        sa.Column("sequence", sa.Text, nullable=True),
        sa.Column("spacing_days", sa.Integer, nullable=True),
        sa.Column("send_window_localtime", sa.String(40), nullable=True),
        sa.Column("max_new_leads_per_day", sa.Integer, nullable=True),
        sa.Column("personalization_rules", sa.Text, nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("spreadsheet_id", "sheet_campaign_id", name="uq_ce_campaigns_sheet"),
    )
    op.create_index("ix_ce_campaigns_spreadsheet_id", "ce_campaigns", ["spreadsheet_id"])

    # ------------------------------------------------------------------
    # ce_domains — mirror de la tab 'domains'. Upsert por (spreadsheet_id, domain)
    # ------------------------------------------------------------------
    op.create_table(
        "ce_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("spreadsheet_id", sa.String(128), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("registrar", sa.String(120), nullable=True),
        sa.Column("dns_status", sa.String(20), nullable=True),
        sa.Column("spf_status", sa.String(20), nullable=True),
        sa.Column("dkim_status", sa.String(20), nullable=True),
        sa.Column("dmarc_status", sa.String(20), nullable=True),
        sa.Column("tracking_subdomain_status", sa.String(20), nullable=True),
        sa.Column("tls_status", sa.String(20), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("monthly_cost_estimate", sa.String(40), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("spreadsheet_id", "domain", name="uq_ce_domains_sheet"),
    )
    op.create_index("ix_ce_domains_spreadsheet_id", "ce_domains", ["spreadsheet_id"])
    op.create_index("ix_ce_domains_domain", "ce_domains", ["domain"])

    # ------------------------------------------------------------------
    # ce_runs — una fila por ejecución de /cold-email-ops/run
    # ------------------------------------------------------------------
    op.create_table(
        "ce_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("spreadsheet_id", sa.String(128), nullable=False),
        sa.Column("client_id", sa.String(160), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("leads_synced", sa.Integer, nullable=False, server_default="0"),
        sa.Column("inboxes_synced", sa.Integer, nullable=False, server_default="0"),
        sa.Column("leads_queued", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bloqueos", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ce_runs_spreadsheet_id", "ce_runs", ["spreadsheet_id"])
    op.create_index("ix_ce_runs_client_id", "ce_runs", ["client_id"])
    op.create_index("ix_ce_runs_status", "ce_runs", ["status"])
    op.create_index("ix_ce_runs_started_at", "ce_runs", ["started_at"])

    # ------------------------------------------------------------------
    # ce_sends — una fila por lead encolado por run (snapshot congelado)
    # ------------------------------------------------------------------
    op.create_table(
        "ce_sends",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("spreadsheet_id", sa.String(128), nullable=False),
        # Snapshot congelado del lead al momento de encolar
        sa.Column("sheet_lead_id", sa.String(160), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("company", sa.String(240), nullable=True),
        sa.Column("first_name", sa.String(120), nullable=True),
        sa.Column("last_name", sa.String(120), nullable=True),
        sa.Column("segment", sa.String(80), nullable=True),
        sa.Column("from_email", sa.String(320), nullable=True),
        sa.Column("campaign_id", sa.String(160), nullable=True),
        sa.Column("personalization_snippet", sa.Text, nullable=True),
        sa.Column("email_validation_status", sa.String(20), nullable=True),
        sa.Column("score", sa.Integer, nullable=True),
        # Outcome — se actualiza después del envío real
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["ce_runs.id"], name="fk_ce_sends_run_id"),
        sa.UniqueConstraint("run_id", "sheet_lead_id", name="uq_ce_sends_run_lead"),
    )
    op.create_index("ix_ce_sends_run_id", "ce_sends", ["run_id"])
    op.create_index("ix_ce_sends_email", "ce_sends", ["email"])
    op.create_index("ix_ce_sends_status", "ce_sends", ["status"])
    op.create_index("ix_ce_sends_created_at", "ce_sends", ["created_at"])
    op.create_index("ix_ce_sends_spreadsheet_id", "ce_sends", ["spreadsheet_id"])


def downgrade() -> None:
    op.drop_table("ce_sends")
    op.drop_table("ce_runs")
    op.drop_table("ce_domains")
    op.drop_table("ce_campaigns")
    op.drop_table("ce_inboxes")
    op.drop_table("ce_leads")
