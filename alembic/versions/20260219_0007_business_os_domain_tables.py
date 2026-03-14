"""Add Business OS domain tables for omnichannel, sales, collections, recruiting, analytics, content and world model.

Revision ID: 20260219_0007
Revises: 20260218_0006
Create Date: 2026-02-19 00:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260219_0007"
down_revision: Union[str, None] = "20260218_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("email", sa.String(length=220), nullable=True),
        sa.Column("phone", sa.String(length=80), nullable=True),
        sa.Column("preferred_channel", sa.String(length=64), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "customer_id", name="uq_customer_profiles_org_customer"),
    )
    op.create_index(op.f("ix_customer_profiles_organization_id"), "customer_profiles", ["organization_id"], unique=False)
    op.create_index(op.f("ix_customer_profiles_customer_id"), "customer_profiles", ["customer_id"], unique=False)
    op.create_index(op.f("ix_customer_profiles_email"), "customer_profiles", ["email"], unique=False)
    op.create_index(op.f("ix_customer_profiles_phone"), "customer_profiles", ["phone"], unique=False)
    op.create_index(op.f("ix_customer_profiles_preferred_channel"), "customer_profiles", ["preferred_channel"], unique=False)
    op.create_index(op.f("ix_customer_profiles_created_at"), "customer_profiles", ["created_at"], unique=False)
    op.create_index(op.f("ix_customer_profiles_updated_at"), "customer_profiles", ["updated_at"], unique=False)

    op.create_table(
        "omnichannel_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_id", sa.String(length=160), nullable=False),
        sa.Column("primary_channel", sa.String(length=64), nullable=False, server_default="webchat"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["customer_profile_id"], ["customer_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_omnichannel_threads_organization_id"), "omnichannel_threads", ["organization_id"], unique=False)
    op.create_index(op.f("ix_omnichannel_threads_customer_profile_id"), "omnichannel_threads", ["customer_profile_id"], unique=False)
    op.create_index(op.f("ix_omnichannel_threads_customer_id"), "omnichannel_threads", ["customer_id"], unique=False)
    op.create_index(op.f("ix_omnichannel_threads_status"), "omnichannel_threads", ["status"], unique=False)
    op.create_index(op.f("ix_omnichannel_threads_last_message_at"), "omnichannel_threads", ["last_message_at"], unique=False)
    op.create_index(op.f("ix_omnichannel_threads_created_at"), "omnichannel_threads", ["created_at"], unique=False)
    op.create_index(op.f("ix_omnichannel_threads_updated_at"), "omnichannel_threads", ["updated_at"], unique=False)
    op.create_index(
        "ix_omnichannel_threads_org_status_last_message",
        "omnichannel_threads",
        ["organization_id", "status", "last_message_at"],
        unique=False,
    )

    op.create_table(
        "omnichannel_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.String(length=160), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False, server_default="inbound"),
        sa.Column("external_message_id", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="received"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["thread_id"], ["omnichannel_threads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_omnichannel_messages_organization_id"), "omnichannel_messages", ["organization_id"], unique=False)
    op.create_index(op.f("ix_omnichannel_messages_thread_id"), "omnichannel_messages", ["thread_id"], unique=False)
    op.create_index(op.f("ix_omnichannel_messages_customer_id"), "omnichannel_messages", ["customer_id"], unique=False)
    op.create_index(op.f("ix_omnichannel_messages_channel"), "omnichannel_messages", ["channel"], unique=False)
    op.create_index(op.f("ix_omnichannel_messages_direction"), "omnichannel_messages", ["direction"], unique=False)
    op.create_index(op.f("ix_omnichannel_messages_external_message_id"), "omnichannel_messages", ["external_message_id"], unique=False)
    op.create_index(op.f("ix_omnichannel_messages_status"), "omnichannel_messages", ["status"], unique=False)
    op.create_index(op.f("ix_omnichannel_messages_created_at"), "omnichannel_messages", ["created_at"], unique=False)
    op.create_index(
        "ix_omnichannel_messages_org_thread_created",
        "omnichannel_messages",
        ["organization_id", "thread_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "sales_pipeline_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_id", sa.String(length=160), nullable=True),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["thread_id"], ["omnichannel_threads.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["crm_leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sales_pipeline_events_organization_id"), "sales_pipeline_events", ["organization_id"], unique=False)
    op.create_index(op.f("ix_sales_pipeline_events_thread_id"), "sales_pipeline_events", ["thread_id"], unique=False)
    op.create_index(op.f("ix_sales_pipeline_events_lead_id"), "sales_pipeline_events", ["lead_id"], unique=False)
    op.create_index(op.f("ix_sales_pipeline_events_customer_id"), "sales_pipeline_events", ["customer_id"], unique=False)
    op.create_index(op.f("ix_sales_pipeline_events_stage"), "sales_pipeline_events", ["stage"], unique=False)
    op.create_index(op.f("ix_sales_pipeline_events_event_type"), "sales_pipeline_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_sales_pipeline_events_created_at"), "sales_pipeline_events", ["created_at"], unique=False)
    op.create_index(
        "ix_sales_pipeline_events_org_stage_created",
        "sales_pipeline_events",
        ["organization_id", "stage", "created_at"],
        unique=False,
    )

    op.create_table(
        "collections_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_id", sa.String(length=160), nullable=True),
        sa.Column("amount_due", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=16), nullable=False, server_default="USD"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="open"),
        sa.Column("risk_level", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("strategy", sa.String(length=120), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["crm_leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_collections_cases_organization_id"), "collections_cases", ["organization_id"], unique=False)
    op.create_index(op.f("ix_collections_cases_lead_id"), "collections_cases", ["lead_id"], unique=False)
    op.create_index(op.f("ix_collections_cases_customer_id"), "collections_cases", ["customer_id"], unique=False)
    op.create_index(op.f("ix_collections_cases_due_at"), "collections_cases", ["due_at"], unique=False)
    op.create_index(op.f("ix_collections_cases_status"), "collections_cases", ["status"], unique=False)
    op.create_index(op.f("ix_collections_cases_created_at"), "collections_cases", ["created_at"], unique=False)
    op.create_index(op.f("ix_collections_cases_updated_at"), "collections_cases", ["updated_at"], unique=False)
    op.create_index(
        "ix_collections_cases_org_status_updated",
        "collections_cases",
        ["organization_id", "status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "collections_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False, server_default="whatsapp"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="sent"),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["collections_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_collections_actions_organization_id"), "collections_actions", ["organization_id"], unique=False)
    op.create_index(op.f("ix_collections_actions_case_id"), "collections_actions", ["case_id"], unique=False)
    op.create_index(op.f("ix_collections_actions_action_type"), "collections_actions", ["action_type"], unique=False)
    op.create_index(op.f("ix_collections_actions_status"), "collections_actions", ["status"], unique=False)
    op.create_index(op.f("ix_collections_actions_created_at"), "collections_actions", ["created_at"], unique=False)
    op.create_index(
        "ix_collections_actions_org_case_created",
        "collections_actions",
        ["organization_id", "case_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "recruiting_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.String(length=140), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=220), nullable=True),
        sa.Column("phone", sa.String(length=80), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=True),
        sa.Column("cv_text", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="new"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recruiting_candidates_organization_id"), "recruiting_candidates", ["organization_id"], unique=False)
    op.create_index(op.f("ix_recruiting_candidates_job_id"), "recruiting_candidates", ["job_id"], unique=False)
    op.create_index(op.f("ix_recruiting_candidates_email"), "recruiting_candidates", ["email"], unique=False)
    op.create_index(op.f("ix_recruiting_candidates_phone"), "recruiting_candidates", ["phone"], unique=False)
    op.create_index(op.f("ix_recruiting_candidates_source"), "recruiting_candidates", ["source"], unique=False)
    op.create_index(op.f("ix_recruiting_candidates_status"), "recruiting_candidates", ["status"], unique=False)
    op.create_index(op.f("ix_recruiting_candidates_created_at"), "recruiting_candidates", ["created_at"], unique=False)
    op.create_index(op.f("ix_recruiting_candidates_updated_at"), "recruiting_candidates", ["updated_at"], unique=False)
    op.create_index(
        "ix_recruiting_candidates_org_job_status",
        "recruiting_candidates",
        ["organization_id", "job_id", "status"],
        unique=False,
    )

    op.create_table(
        "recruiting_interviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False, server_default="meet"),
        sa.Column("interviewer", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="scheduled"),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["recruiting_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recruiting_interviews_organization_id"), "recruiting_interviews", ["organization_id"], unique=False)
    op.create_index(op.f("ix_recruiting_interviews_candidate_id"), "recruiting_interviews", ["candidate_id"], unique=False)
    op.create_index(op.f("ix_recruiting_interviews_scheduled_at"), "recruiting_interviews", ["scheduled_at"], unique=False)
    op.create_index(op.f("ix_recruiting_interviews_status"), "recruiting_interviews", ["status"], unique=False)
    op.create_index(op.f("ix_recruiting_interviews_created_at"), "recruiting_interviews", ["created_at"], unique=False)
    op.create_index(
        "ix_recruiting_interviews_org_status_scheduled",
        "recruiting_interviews",
        ["organization_id", "status", "scheduled_at"],
        unique=False,
    )

    op.create_table(
        "recruiting_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dimensions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("recommendation", sa.String(length=80), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["recruiting_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recruiting_scores_organization_id"), "recruiting_scores", ["organization_id"], unique=False)
    op.create_index(op.f("ix_recruiting_scores_candidate_id"), "recruiting_scores", ["candidate_id"], unique=False)
    op.create_index(op.f("ix_recruiting_scores_created_at"), "recruiting_scores", ["created_at"], unique=False)
    op.create_index(
        "ix_recruiting_scores_org_candidate_created",
        "recruiting_scores",
        ["organization_id", "candidate_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "process_audit_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("documents", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="queued"),
        sa.Column("findings_summary", sa.Text(), nullable=True),
        sa.Column("estimated_roi", sa.Float(), nullable=True),
        sa.Column("implementation_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_process_audit_jobs_organization_id"), "process_audit_jobs", ["organization_id"], unique=False)
    op.create_index(op.f("ix_process_audit_jobs_status"), "process_audit_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_process_audit_jobs_created_at"), "process_audit_jobs", ["created_at"], unique=False)
    op.create_index(op.f("ix_process_audit_jobs_updated_at"), "process_audit_jobs", ["updated_at"], unique=False)
    op.create_index(
        "ix_process_audit_jobs_org_status_updated",
        "process_audit_jobs",
        ["organization_id", "status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "process_audit_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_type", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("estimated_impact", sa.Float(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["process_audit_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_process_audit_findings_organization_id"), "process_audit_findings", ["organization_id"], unique=False)
    op.create_index(op.f("ix_process_audit_findings_job_id"), "process_audit_findings", ["job_id"], unique=False)
    op.create_index(op.f("ix_process_audit_findings_finding_type"), "process_audit_findings", ["finding_type"], unique=False)
    op.create_index(op.f("ix_process_audit_findings_created_at"), "process_audit_findings", ["created_at"], unique=False)
    op.create_index(
        "ix_process_audit_findings_org_job_created",
        "process_audit_findings",
        ["organization_id", "job_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "analytics_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_name", sa.String(length=260), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False, server_default="text/csv"),
        sa.Column("rows_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="uploaded"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_analytics_uploads_organization_id"), "analytics_uploads", ["organization_id"], unique=False)
    op.create_index(op.f("ix_analytics_uploads_status"), "analytics_uploads", ["status"], unique=False)
    op.create_index(op.f("ix_analytics_uploads_created_at"), "analytics_uploads", ["created_at"], unique=False)
    op.create_index(
        "ix_analytics_uploads_org_status_created",
        "analytics_uploads",
        ["organization_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "analytics_insights",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("upload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ready"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recommendations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["upload_id"], ["analytics_uploads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_analytics_insights_organization_id"), "analytics_insights", ["organization_id"], unique=False)
    op.create_index(op.f("ix_analytics_insights_upload_id"), "analytics_insights", ["upload_id"], unique=False)
    op.create_index(op.f("ix_analytics_insights_status"), "analytics_insights", ["status"], unique=False)
    op.create_index(op.f("ix_analytics_insights_created_at"), "analytics_insights", ["created_at"], unique=False)
    op.create_index(op.f("ix_analytics_insights_updated_at"), "analytics_insights", ["updated_at"], unique=False)
    op.create_index(
        "ix_analytics_insights_org_status_updated",
        "analytics_insights",
        ["organization_id", "status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "content_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False, server_default="instagram"),
        sa.Column("cadence", sa.String(length=64), nullable=False, server_default="weekly"),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_content_plans_organization_id"), "content_plans", ["organization_id"], unique=False)
    op.create_index(op.f("ix_content_plans_status"), "content_plans", ["status"], unique=False)
    op.create_index(op.f("ix_content_plans_created_at"), "content_plans", ["created_at"], unique=False)
    op.create_index(op.f("ix_content_plans_updated_at"), "content_plans", ["updated_at"], unique=False)
    op.create_index(
        "ix_content_plans_org_status_updated",
        "content_plans",
        ["organization_id", "status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "content_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("asset_type", sa.String(length=64), nullable=False, server_default="post"),
        sa.Column("channel", sa.String(length=64), nullable=False, server_default="instagram"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["content_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_content_assets_organization_id"), "content_assets", ["organization_id"], unique=False)
    op.create_index(op.f("ix_content_assets_plan_id"), "content_assets", ["plan_id"], unique=False)
    op.create_index(op.f("ix_content_assets_status"), "content_assets", ["status"], unique=False)
    op.create_index(op.f("ix_content_assets_published_at"), "content_assets", ["published_at"], unique=False)
    op.create_index(op.f("ix_content_assets_created_at"), "content_assets", ["created_at"], unique=False)
    op.create_index(
        "ix_content_assets_org_status_created",
        "content_assets",
        ["organization_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "internal_docs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_internal_docs_organization_id"), "internal_docs", ["organization_id"], unique=False)
    op.create_index(op.f("ix_internal_docs_created_at"), "internal_docs", ["created_at"], unique=False)
    op.create_index(op.f("ix_internal_docs_updated_at"), "internal_docs", ["updated_at"], unique=False)
    op.create_index(
        "ix_internal_docs_org_updated",
        "internal_docs",
        ["organization_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "internal_doc_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("doc_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["doc_id"], ["internal_docs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_internal_doc_chunks_organization_id"), "internal_doc_chunks", ["organization_id"], unique=False)
    op.create_index(op.f("ix_internal_doc_chunks_doc_id"), "internal_doc_chunks", ["doc_id"], unique=False)
    op.create_index(op.f("ix_internal_doc_chunks_created_at"), "internal_doc_chunks", ["created_at"], unique=False)
    op.create_index(
        "ix_internal_doc_chunks_org_doc_idx",
        "internal_doc_chunks",
        ["organization_id", "doc_id", "chunk_index"],
        unique=False,
    )

    op.create_table(
        "internal_queries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_internal_queries_organization_id"), "internal_queries", ["organization_id"], unique=False)
    op.create_index(op.f("ix_internal_queries_created_at"), "internal_queries", ["created_at"], unique=False)
    op.create_index(
        "ix_internal_queries_org_created",
        "internal_queries",
        ["organization_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "ui_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ui_signature", sa.String(length=180), nullable=False),
        sa.Column("domain", sa.String(length=180), nullable=True),
        sa.Column("app_name", sa.String(length=180), nullable=True),
        sa.Column("screenshot_hash", sa.String(length=80), nullable=True),
        sa.Column("extracted_elements", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ui_states_organization_id"), "ui_states", ["organization_id"], unique=False)
    op.create_index(op.f("ix_ui_states_ui_signature"), "ui_states", ["ui_signature"], unique=False)
    op.create_index(op.f("ix_ui_states_domain"), "ui_states", ["domain"], unique=False)
    op.create_index(op.f("ix_ui_states_app_name"), "ui_states", ["app_name"], unique=False)
    op.create_index(op.f("ix_ui_states_screenshot_hash"), "ui_states", ["screenshot_hash"], unique=False)
    op.create_index(op.f("ix_ui_states_created_at"), "ui_states", ["created_at"], unique=False)
    op.create_index(
        "ix_ui_states_org_signature_created",
        "ui_states",
        ["organization_id", "ui_signature", "created_at"],
        unique=False,
    )

    op.create_table(
        "ui_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_signature", sa.String(length=180), nullable=False),
        sa.Column("to_signature", sa.String(length=180), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("success_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("avg_duration_ms", sa.Float(), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ui_transitions_organization_id"), "ui_transitions", ["organization_id"], unique=False)
    op.create_index(op.f("ix_ui_transitions_from_signature"), "ui_transitions", ["from_signature"], unique=False)
    op.create_index(op.f("ix_ui_transitions_to_signature"), "ui_transitions", ["to_signature"], unique=False)
    op.create_index(op.f("ix_ui_transitions_action"), "ui_transitions", ["action"], unique=False)
    op.create_index(op.f("ix_ui_transitions_created_at"), "ui_transitions", ["created_at"], unique=False)
    op.create_index(op.f("ix_ui_transitions_updated_at"), "ui_transitions", ["updated_at"], unique=False)
    op.create_index(
        "ix_ui_transitions_org_route_updated",
        "ui_transitions",
        ["organization_id", "from_signature", "to_signature", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ui_transitions_org_route_updated", table_name="ui_transitions")
    op.drop_index(op.f("ix_ui_transitions_updated_at"), table_name="ui_transitions")
    op.drop_index(op.f("ix_ui_transitions_created_at"), table_name="ui_transitions")
    op.drop_index(op.f("ix_ui_transitions_action"), table_name="ui_transitions")
    op.drop_index(op.f("ix_ui_transitions_to_signature"), table_name="ui_transitions")
    op.drop_index(op.f("ix_ui_transitions_from_signature"), table_name="ui_transitions")
    op.drop_index(op.f("ix_ui_transitions_organization_id"), table_name="ui_transitions")
    op.drop_table("ui_transitions")

    op.drop_index("ix_ui_states_org_signature_created", table_name="ui_states")
    op.drop_index(op.f("ix_ui_states_created_at"), table_name="ui_states")
    op.drop_index(op.f("ix_ui_states_screenshot_hash"), table_name="ui_states")
    op.drop_index(op.f("ix_ui_states_app_name"), table_name="ui_states")
    op.drop_index(op.f("ix_ui_states_domain"), table_name="ui_states")
    op.drop_index(op.f("ix_ui_states_ui_signature"), table_name="ui_states")
    op.drop_index(op.f("ix_ui_states_organization_id"), table_name="ui_states")
    op.drop_table("ui_states")

    op.drop_index("ix_internal_queries_org_created", table_name="internal_queries")
    op.drop_index(op.f("ix_internal_queries_created_at"), table_name="internal_queries")
    op.drop_index(op.f("ix_internal_queries_organization_id"), table_name="internal_queries")
    op.drop_table("internal_queries")

    op.drop_index("ix_internal_doc_chunks_org_doc_idx", table_name="internal_doc_chunks")
    op.drop_index(op.f("ix_internal_doc_chunks_created_at"), table_name="internal_doc_chunks")
    op.drop_index(op.f("ix_internal_doc_chunks_doc_id"), table_name="internal_doc_chunks")
    op.drop_index(op.f("ix_internal_doc_chunks_organization_id"), table_name="internal_doc_chunks")
    op.drop_table("internal_doc_chunks")

    op.drop_index("ix_internal_docs_org_updated", table_name="internal_docs")
    op.drop_index(op.f("ix_internal_docs_updated_at"), table_name="internal_docs")
    op.drop_index(op.f("ix_internal_docs_created_at"), table_name="internal_docs")
    op.drop_index(op.f("ix_internal_docs_organization_id"), table_name="internal_docs")
    op.drop_table("internal_docs")

    op.drop_index("ix_content_assets_org_status_created", table_name="content_assets")
    op.drop_index(op.f("ix_content_assets_created_at"), table_name="content_assets")
    op.drop_index(op.f("ix_content_assets_published_at"), table_name="content_assets")
    op.drop_index(op.f("ix_content_assets_status"), table_name="content_assets")
    op.drop_index(op.f("ix_content_assets_plan_id"), table_name="content_assets")
    op.drop_index(op.f("ix_content_assets_organization_id"), table_name="content_assets")
    op.drop_table("content_assets")

    op.drop_index("ix_content_plans_org_status_updated", table_name="content_plans")
    op.drop_index(op.f("ix_content_plans_updated_at"), table_name="content_plans")
    op.drop_index(op.f("ix_content_plans_created_at"), table_name="content_plans")
    op.drop_index(op.f("ix_content_plans_status"), table_name="content_plans")
    op.drop_index(op.f("ix_content_plans_organization_id"), table_name="content_plans")
    op.drop_table("content_plans")

    op.drop_index("ix_analytics_insights_org_status_updated", table_name="analytics_insights")
    op.drop_index(op.f("ix_analytics_insights_updated_at"), table_name="analytics_insights")
    op.drop_index(op.f("ix_analytics_insights_created_at"), table_name="analytics_insights")
    op.drop_index(op.f("ix_analytics_insights_status"), table_name="analytics_insights")
    op.drop_index(op.f("ix_analytics_insights_upload_id"), table_name="analytics_insights")
    op.drop_index(op.f("ix_analytics_insights_organization_id"), table_name="analytics_insights")
    op.drop_table("analytics_insights")

    op.drop_index("ix_analytics_uploads_org_status_created", table_name="analytics_uploads")
    op.drop_index(op.f("ix_analytics_uploads_created_at"), table_name="analytics_uploads")
    op.drop_index(op.f("ix_analytics_uploads_status"), table_name="analytics_uploads")
    op.drop_index(op.f("ix_analytics_uploads_organization_id"), table_name="analytics_uploads")
    op.drop_table("analytics_uploads")

    op.drop_index("ix_process_audit_findings_org_job_created", table_name="process_audit_findings")
    op.drop_index(op.f("ix_process_audit_findings_created_at"), table_name="process_audit_findings")
    op.drop_index(op.f("ix_process_audit_findings_finding_type"), table_name="process_audit_findings")
    op.drop_index(op.f("ix_process_audit_findings_job_id"), table_name="process_audit_findings")
    op.drop_index(op.f("ix_process_audit_findings_organization_id"), table_name="process_audit_findings")
    op.drop_table("process_audit_findings")

    op.drop_index("ix_process_audit_jobs_org_status_updated", table_name="process_audit_jobs")
    op.drop_index(op.f("ix_process_audit_jobs_updated_at"), table_name="process_audit_jobs")
    op.drop_index(op.f("ix_process_audit_jobs_created_at"), table_name="process_audit_jobs")
    op.drop_index(op.f("ix_process_audit_jobs_status"), table_name="process_audit_jobs")
    op.drop_index(op.f("ix_process_audit_jobs_organization_id"), table_name="process_audit_jobs")
    op.drop_table("process_audit_jobs")

    op.drop_index("ix_recruiting_scores_org_candidate_created", table_name="recruiting_scores")
    op.drop_index(op.f("ix_recruiting_scores_created_at"), table_name="recruiting_scores")
    op.drop_index(op.f("ix_recruiting_scores_candidate_id"), table_name="recruiting_scores")
    op.drop_index(op.f("ix_recruiting_scores_organization_id"), table_name="recruiting_scores")
    op.drop_table("recruiting_scores")

    op.drop_index("ix_recruiting_interviews_org_status_scheduled", table_name="recruiting_interviews")
    op.drop_index(op.f("ix_recruiting_interviews_created_at"), table_name="recruiting_interviews")
    op.drop_index(op.f("ix_recruiting_interviews_status"), table_name="recruiting_interviews")
    op.drop_index(op.f("ix_recruiting_interviews_scheduled_at"), table_name="recruiting_interviews")
    op.drop_index(op.f("ix_recruiting_interviews_candidate_id"), table_name="recruiting_interviews")
    op.drop_index(op.f("ix_recruiting_interviews_organization_id"), table_name="recruiting_interviews")
    op.drop_table("recruiting_interviews")

    op.drop_index("ix_recruiting_candidates_org_job_status", table_name="recruiting_candidates")
    op.drop_index(op.f("ix_recruiting_candidates_updated_at"), table_name="recruiting_candidates")
    op.drop_index(op.f("ix_recruiting_candidates_created_at"), table_name="recruiting_candidates")
    op.drop_index(op.f("ix_recruiting_candidates_status"), table_name="recruiting_candidates")
    op.drop_index(op.f("ix_recruiting_candidates_source"), table_name="recruiting_candidates")
    op.drop_index(op.f("ix_recruiting_candidates_phone"), table_name="recruiting_candidates")
    op.drop_index(op.f("ix_recruiting_candidates_email"), table_name="recruiting_candidates")
    op.drop_index(op.f("ix_recruiting_candidates_job_id"), table_name="recruiting_candidates")
    op.drop_index(op.f("ix_recruiting_candidates_organization_id"), table_name="recruiting_candidates")
    op.drop_table("recruiting_candidates")

    op.drop_index("ix_collections_actions_org_case_created", table_name="collections_actions")
    op.drop_index(op.f("ix_collections_actions_created_at"), table_name="collections_actions")
    op.drop_index(op.f("ix_collections_actions_status"), table_name="collections_actions")
    op.drop_index(op.f("ix_collections_actions_action_type"), table_name="collections_actions")
    op.drop_index(op.f("ix_collections_actions_case_id"), table_name="collections_actions")
    op.drop_index(op.f("ix_collections_actions_organization_id"), table_name="collections_actions")
    op.drop_table("collections_actions")

    op.drop_index("ix_collections_cases_org_status_updated", table_name="collections_cases")
    op.drop_index(op.f("ix_collections_cases_updated_at"), table_name="collections_cases")
    op.drop_index(op.f("ix_collections_cases_created_at"), table_name="collections_cases")
    op.drop_index(op.f("ix_collections_cases_status"), table_name="collections_cases")
    op.drop_index(op.f("ix_collections_cases_due_at"), table_name="collections_cases")
    op.drop_index(op.f("ix_collections_cases_customer_id"), table_name="collections_cases")
    op.drop_index(op.f("ix_collections_cases_lead_id"), table_name="collections_cases")
    op.drop_index(op.f("ix_collections_cases_organization_id"), table_name="collections_cases")
    op.drop_table("collections_cases")

    op.drop_index("ix_sales_pipeline_events_org_stage_created", table_name="sales_pipeline_events")
    op.drop_index(op.f("ix_sales_pipeline_events_created_at"), table_name="sales_pipeline_events")
    op.drop_index(op.f("ix_sales_pipeline_events_event_type"), table_name="sales_pipeline_events")
    op.drop_index(op.f("ix_sales_pipeline_events_stage"), table_name="sales_pipeline_events")
    op.drop_index(op.f("ix_sales_pipeline_events_customer_id"), table_name="sales_pipeline_events")
    op.drop_index(op.f("ix_sales_pipeline_events_lead_id"), table_name="sales_pipeline_events")
    op.drop_index(op.f("ix_sales_pipeline_events_thread_id"), table_name="sales_pipeline_events")
    op.drop_index(op.f("ix_sales_pipeline_events_organization_id"), table_name="sales_pipeline_events")
    op.drop_table("sales_pipeline_events")

    op.drop_index("ix_omnichannel_messages_org_thread_created", table_name="omnichannel_messages")
    op.drop_index(op.f("ix_omnichannel_messages_created_at"), table_name="omnichannel_messages")
    op.drop_index(op.f("ix_omnichannel_messages_status"), table_name="omnichannel_messages")
    op.drop_index(op.f("ix_omnichannel_messages_external_message_id"), table_name="omnichannel_messages")
    op.drop_index(op.f("ix_omnichannel_messages_direction"), table_name="omnichannel_messages")
    op.drop_index(op.f("ix_omnichannel_messages_channel"), table_name="omnichannel_messages")
    op.drop_index(op.f("ix_omnichannel_messages_customer_id"), table_name="omnichannel_messages")
    op.drop_index(op.f("ix_omnichannel_messages_thread_id"), table_name="omnichannel_messages")
    op.drop_index(op.f("ix_omnichannel_messages_organization_id"), table_name="omnichannel_messages")
    op.drop_table("omnichannel_messages")

    op.drop_index("ix_omnichannel_threads_org_status_last_message", table_name="omnichannel_threads")
    op.drop_index(op.f("ix_omnichannel_threads_updated_at"), table_name="omnichannel_threads")
    op.drop_index(op.f("ix_omnichannel_threads_created_at"), table_name="omnichannel_threads")
    op.drop_index(op.f("ix_omnichannel_threads_last_message_at"), table_name="omnichannel_threads")
    op.drop_index(op.f("ix_omnichannel_threads_status"), table_name="omnichannel_threads")
    op.drop_index(op.f("ix_omnichannel_threads_customer_id"), table_name="omnichannel_threads")
    op.drop_index(op.f("ix_omnichannel_threads_customer_profile_id"), table_name="omnichannel_threads")
    op.drop_index(op.f("ix_omnichannel_threads_organization_id"), table_name="omnichannel_threads")
    op.drop_table("omnichannel_threads")

    op.drop_index(op.f("ix_customer_profiles_updated_at"), table_name="customer_profiles")
    op.drop_index(op.f("ix_customer_profiles_created_at"), table_name="customer_profiles")
    op.drop_index(op.f("ix_customer_profiles_preferred_channel"), table_name="customer_profiles")
    op.drop_index(op.f("ix_customer_profiles_phone"), table_name="customer_profiles")
    op.drop_index(op.f("ix_customer_profiles_email"), table_name="customer_profiles")
    op.drop_index(op.f("ix_customer_profiles_customer_id"), table_name="customer_profiles")
    op.drop_index(op.f("ix_customer_profiles_organization_id"), table_name="customer_profiles")
    op.drop_table("customer_profiles")
