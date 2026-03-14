"""Add autonomy level-3 platform tables.

Revision ID: 20260217_0003
Revises: 20260217_0002
Create Date: 2026-02-17 00:03:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260217_0003"
down_revision: Union[str, None] = "20260217_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "objectives",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("metric_key", sa.String(length=120), nullable=False),
        sa.Column("target_value", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("autonomy_mode", sa.String(length=32), nullable=False, server_default="confirm"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_objectives_organization_id"), "objectives", ["organization_id"], unique=False)
    op.create_index(op.f("ix_objectives_status"), "objectives", ["status"], unique=False)

    op.create_table(
        "objective_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
        sa.Column("risk_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["objective_id"], ["objectives.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_objective_runs_objective_id"), "objective_runs", ["objective_id"], unique=False)
    op.create_index(op.f("ix_objective_runs_organization_id"), "objective_runs", ["organization_id"], unique=False)
    op.create_index(op.f("ix_objective_runs_status"), "objective_runs", ["status"], unique=False)

    op.create_table(
        "business_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("channel", sa.String(length=80), nullable=True),
        sa.Column("entity_id", sa.String(length=128), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_business_events_organization_id"), "business_events", ["organization_id"], unique=False)
    op.create_index(op.f("ix_business_events_event_type"), "business_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_business_events_source"), "business_events", ["source"], unique=False)
    op.create_index(op.f("ix_business_events_channel"), "business_events", ["channel"], unique=False)
    op.create_index(op.f("ix_business_events_entity_id"), "business_events", ["entity_id"], unique=False)
    op.create_index(op.f("ix_business_events_created_at"), "business_events", ["created_at"], unique=False)

    op.create_table(
        "business_metrics_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_leads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_conversations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_appointments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_sales", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_api_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_business_metrics_snapshots_organization_id"),
        "business_metrics_snapshots",
        ["organization_id"],
        unique=False,
    )
    op.create_index(op.f("ix_business_metrics_snapshots_window_start"), "business_metrics_snapshots", ["window_start"], unique=False)
    op.create_index(op.f("ix_business_metrics_snapshots_window_end"), "business_metrics_snapshots", ["window_end"], unique=False)

    op.create_table(
        "recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("objective_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recommendation_key", sa.String(length=120), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("detected_issue", sa.Text(), nullable=False),
        sa.Column("proposal", sa.Text(), nullable=False),
        sa.Column("expected_impact", sa.Text(), nullable=False),
        sa.Column("desired_functions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="proposed"),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["objective_id"], ["objectives.id"]),
        sa.ForeignKeyConstraint(["objective_run_id"], ["objective_runs.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recommendations_organization_id"), "recommendations", ["organization_id"], unique=False)
    op.create_index(op.f("ix_recommendations_objective_id"), "recommendations", ["objective_id"], unique=False)
    op.create_index(op.f("ix_recommendations_objective_run_id"), "recommendations", ["objective_run_id"], unique=False)
    op.create_index(op.f("ix_recommendations_recommendation_key"), "recommendations", ["recommendation_key"], unique=False)
    op.create_index(op.f("ix_recommendations_status"), "recommendations", ["status"], unique=False)

    op.create_table(
        "recommendation_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="success"),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("workflow_id", sa.String(length=128), nullable=True),
        sa.Column("workflow_url", sa.String(length=2048), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["recommendation_id"], ["recommendations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_recommendation_actions_recommendation_id"), "recommendation_actions", ["recommendation_id"], unique=False)
    op.create_index(op.f("ix_recommendation_actions_organization_id"), "recommendation_actions", ["organization_id"], unique=False)
    op.create_index(op.f("ix_recommendation_actions_action_type"), "recommendation_actions", ["action_type"], unique=False)

    op.create_table(
        "missions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("autonomy_mode", sa.String(length=32), nullable=False, server_default="confirm"),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["objective_id"], ["objectives.id"]),
        sa.ForeignKeyConstraint(["recommendation_id"], ["recommendations.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_missions_organization_id"), "missions", ["organization_id"], unique=False)
    op.create_index(op.f("ix_missions_objective_id"), "missions", ["objective_id"], unique=False)
    op.create_index(op.f("ix_missions_recommendation_id"), "missions", ["recommendation_id"], unique=False)
    op.create_index(op.f("ix_missions_status"), "missions", ["status"], unique=False)

    op.create_table(
        "mission_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("step_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("desired_functions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mission_steps_mission_id"), "mission_steps", ["mission_id"], unique=False)
    op.create_index(op.f("ix_mission_steps_organization_id"), "mission_steps", ["organization_id"], unique=False)
    op.create_index(op.f("ix_mission_steps_status"), "mission_steps", ["status"], unique=False)

    op.create_table(
        "crm_leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("phone", sa.String(length=60), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="new"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("conversion_probability", sa.Float(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=120), nullable=True),
        sa.Column("industry", sa.String(length=120), nullable=True),
        sa.Column("owner", sa.String(length=120), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_interaction_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "external_id", name="uq_crm_leads_org_external_id"),
    )
    op.create_index(op.f("ix_crm_leads_organization_id"), "crm_leads", ["organization_id"], unique=False)
    op.create_index(op.f("ix_crm_leads_email"), "crm_leads", ["email"], unique=False)
    op.create_index(op.f("ix_crm_leads_phone"), "crm_leads", ["phone"], unique=False)
    op.create_index(op.f("ix_crm_leads_status"), "crm_leads", ["status"], unique=False)
    op.create_index(op.f("ix_crm_leads_source"), "crm_leads", ["source"], unique=False)
    op.create_index(op.f("ix_crm_leads_industry"), "crm_leads", ["industry"], unique=False)

    op.create_table(
        "crm_interactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(length=80), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False, server_default="inbound"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["lead_id"], ["crm_leads.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_crm_interactions_organization_id"), "crm_interactions", ["organization_id"], unique=False)
    op.create_index(op.f("ix_crm_interactions_lead_id"), "crm_interactions", ["lead_id"], unique=False)
    op.create_index(op.f("ix_crm_interactions_channel"), "crm_interactions", ["channel"], unique=False)
    op.create_index(op.f("ix_crm_interactions_created_at"), "crm_interactions", ["created_at"], unique=False)

    op.create_table(
        "follow_up_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="scheduled"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["lead_id"], ["crm_leads.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_follow_up_jobs_organization_id"), "follow_up_jobs", ["organization_id"], unique=False)
    op.create_index(op.f("ix_follow_up_jobs_lead_id"), "follow_up_jobs", ["lead_id"], unique=False)
    op.create_index(op.f("ix_follow_up_jobs_status"), "follow_up_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_follow_up_jobs_scheduled_for"), "follow_up_jobs", ["scheduled_for"], unique=False)

    op.create_table(
        "alert_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("metric_key", sa.String(length=120), nullable=False),
        sa.Column("operator", sa.String(length=8), nullable=False, server_default="gte"),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="warning"),
        sa.Column("channels", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alert_rules_organization_id"), "alert_rules", ["organization_id"], unique=False)
    op.create_index(op.f("ix_alert_rules_metric_key"), "alert_rules", ["metric_key"], unique=False)

    op.create_table(
        "alert_incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("incident_key", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alert_incidents_organization_id"), "alert_incidents", ["organization_id"], unique=False)
    op.create_index(op.f("ix_alert_incidents_rule_id"), "alert_incidents", ["rule_id"], unique=False)
    op.create_index(op.f("ix_alert_incidents_incident_key"), "alert_incidents", ["incident_key"], unique=False)
    op.create_index(op.f("ix_alert_incidents_severity"), "alert_incidents", ["severity"], unique=False)
    op.create_index(op.f("ix_alert_incidents_status"), "alert_incidents", ["status"], unique=False)

    op.create_table(
        "roi_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revenue_generated_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("api_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("time_saved_minutes", sa.Float(), nullable=False, server_default="0"),
        sa.Column("conversations_attended", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("appointments_generated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conversion_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_roi_snapshots_organization_id"), "roi_snapshots", ["organization_id"], unique=False)
    op.create_index(op.f("ix_roi_snapshots_period_start"), "roi_snapshots", ["period_start"], unique=False)
    op.create_index(op.f("ix_roi_snapshots_period_end"), "roi_snapshots", ["period_end"], unique=False)

    op.create_table(
        "predictions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=128), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("prediction_key", sa.String(length=120), nullable=False),
        sa.Column("prediction_value", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_predictions_organization_id"), "predictions", ["organization_id"], unique=False)
    op.create_index(op.f("ix_predictions_entity_type"), "predictions", ["entity_type"], unique=False)
    op.create_index(op.f("ix_predictions_entity_id"), "predictions", ["entity_id"], unique=False)
    op.create_index(op.f("ix_predictions_prediction_key"), "predictions", ["prediction_key"], unique=False)

    op.create_table(
        "global_learnings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pattern_key", sa.String(length=180), nullable=False),
        sa.Column("industry", sa.String(length=120), nullable=True),
        sa.Column("function_name", sa.String(length=120), nullable=True),
        sa.Column("provider_id", sa.String(length=120), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("avg_impact_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_global_learnings_pattern_key"), "global_learnings", ["pattern_key"], unique=False)
    op.create_index(op.f("ix_global_learnings_industry"), "global_learnings", ["industry"], unique=False)
    op.create_index(op.f("ix_global_learnings_function_name"), "global_learnings", ["function_name"], unique=False)
    op.create_index(op.f("ix_global_learnings_provider_id"), "global_learnings", ["provider_id"], unique=False)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_organization_id"), "users", ["organization_id"], unique=False)
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_roles_name"), "roles", ["name"], unique=True)

    op.create_table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_permissions_key"), "permissions", ["key"], unique=True)

    op.create_table(
        "role_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_permission"),
    )
    op.create_index(op.f("ix_role_permissions_role_id"), "role_permissions", ["role_id"], unique=False)
    op.create_index(op.f("ix_role_permissions_permission_id"), "role_permissions", ["permission_id"], unique=False)

    op.create_table(
        "org_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_org_memberships_org_user"),
    )
    op.create_index(op.f("ix_org_memberships_organization_id"), "org_memberships", ["organization_id"], unique=False)
    op.create_index(op.f("ix_org_memberships_user_id"), "org_memberships", ["user_id"], unique=False)
    op.create_index(op.f("ix_org_memberships_role_id"), "org_memberships", ["role_id"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=160), nullable=False),
        sa.Column("resource", sa.String(length=160), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="ok"),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_organization_id"), "audit_logs", ["organization_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_actor_user_id"), "audit_logs", ["actor_user_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_resource"), "audit_logs", ["resource"], unique=False)
    op.create_index(op.f("ix_audit_logs_resource_id"), "audit_logs", ["resource_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_resource_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_resource"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_actor_user_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_organization_id"), table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index(op.f("ix_org_memberships_role_id"), table_name="org_memberships")
    op.drop_index(op.f("ix_org_memberships_user_id"), table_name="org_memberships")
    op.drop_index(op.f("ix_org_memberships_organization_id"), table_name="org_memberships")
    op.drop_table("org_memberships")

    op.drop_index(op.f("ix_role_permissions_permission_id"), table_name="role_permissions")
    op.drop_index(op.f("ix_role_permissions_role_id"), table_name="role_permissions")
    op.drop_table("role_permissions")

    op.drop_index(op.f("ix_permissions_key"), table_name="permissions")
    op.drop_table("permissions")

    op.drop_index(op.f("ix_roles_name"), table_name="roles")
    op.drop_table("roles")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_organization_id"), table_name="users")
    op.drop_table("users")

    op.drop_index(op.f("ix_global_learnings_provider_id"), table_name="global_learnings")
    op.drop_index(op.f("ix_global_learnings_function_name"), table_name="global_learnings")
    op.drop_index(op.f("ix_global_learnings_industry"), table_name="global_learnings")
    op.drop_index(op.f("ix_global_learnings_pattern_key"), table_name="global_learnings")
    op.drop_table("global_learnings")

    op.drop_index(op.f("ix_predictions_prediction_key"), table_name="predictions")
    op.drop_index(op.f("ix_predictions_entity_id"), table_name="predictions")
    op.drop_index(op.f("ix_predictions_entity_type"), table_name="predictions")
    op.drop_index(op.f("ix_predictions_organization_id"), table_name="predictions")
    op.drop_table("predictions")

    op.drop_index(op.f("ix_roi_snapshots_period_end"), table_name="roi_snapshots")
    op.drop_index(op.f("ix_roi_snapshots_period_start"), table_name="roi_snapshots")
    op.drop_index(op.f("ix_roi_snapshots_organization_id"), table_name="roi_snapshots")
    op.drop_table("roi_snapshots")

    op.drop_index(op.f("ix_alert_incidents_status"), table_name="alert_incidents")
    op.drop_index(op.f("ix_alert_incidents_severity"), table_name="alert_incidents")
    op.drop_index(op.f("ix_alert_incidents_incident_key"), table_name="alert_incidents")
    op.drop_index(op.f("ix_alert_incidents_rule_id"), table_name="alert_incidents")
    op.drop_index(op.f("ix_alert_incidents_organization_id"), table_name="alert_incidents")
    op.drop_table("alert_incidents")

    op.drop_index(op.f("ix_alert_rules_metric_key"), table_name="alert_rules")
    op.drop_index(op.f("ix_alert_rules_organization_id"), table_name="alert_rules")
    op.drop_table("alert_rules")

    op.drop_index(op.f("ix_follow_up_jobs_scheduled_for"), table_name="follow_up_jobs")
    op.drop_index(op.f("ix_follow_up_jobs_status"), table_name="follow_up_jobs")
    op.drop_index(op.f("ix_follow_up_jobs_lead_id"), table_name="follow_up_jobs")
    op.drop_index(op.f("ix_follow_up_jobs_organization_id"), table_name="follow_up_jobs")
    op.drop_table("follow_up_jobs")

    op.drop_index(op.f("ix_crm_interactions_created_at"), table_name="crm_interactions")
    op.drop_index(op.f("ix_crm_interactions_channel"), table_name="crm_interactions")
    op.drop_index(op.f("ix_crm_interactions_lead_id"), table_name="crm_interactions")
    op.drop_index(op.f("ix_crm_interactions_organization_id"), table_name="crm_interactions")
    op.drop_table("crm_interactions")

    op.drop_index(op.f("ix_crm_leads_industry"), table_name="crm_leads")
    op.drop_index(op.f("ix_crm_leads_source"), table_name="crm_leads")
    op.drop_index(op.f("ix_crm_leads_status"), table_name="crm_leads")
    op.drop_index(op.f("ix_crm_leads_phone"), table_name="crm_leads")
    op.drop_index(op.f("ix_crm_leads_email"), table_name="crm_leads")
    op.drop_index(op.f("ix_crm_leads_organization_id"), table_name="crm_leads")
    op.drop_table("crm_leads")

    op.drop_index(op.f("ix_mission_steps_status"), table_name="mission_steps")
    op.drop_index(op.f("ix_mission_steps_organization_id"), table_name="mission_steps")
    op.drop_index(op.f("ix_mission_steps_mission_id"), table_name="mission_steps")
    op.drop_table("mission_steps")

    op.drop_index(op.f("ix_missions_status"), table_name="missions")
    op.drop_index(op.f("ix_missions_recommendation_id"), table_name="missions")
    op.drop_index(op.f("ix_missions_objective_id"), table_name="missions")
    op.drop_index(op.f("ix_missions_organization_id"), table_name="missions")
    op.drop_table("missions")

    op.drop_index(op.f("ix_recommendation_actions_action_type"), table_name="recommendation_actions")
    op.drop_index(op.f("ix_recommendation_actions_organization_id"), table_name="recommendation_actions")
    op.drop_index(op.f("ix_recommendation_actions_recommendation_id"), table_name="recommendation_actions")
    op.drop_table("recommendation_actions")

    op.drop_index(op.f("ix_recommendations_status"), table_name="recommendations")
    op.drop_index(op.f("ix_recommendations_recommendation_key"), table_name="recommendations")
    op.drop_index(op.f("ix_recommendations_objective_run_id"), table_name="recommendations")
    op.drop_index(op.f("ix_recommendations_objective_id"), table_name="recommendations")
    op.drop_index(op.f("ix_recommendations_organization_id"), table_name="recommendations")
    op.drop_table("recommendations")

    op.drop_index(op.f("ix_business_metrics_snapshots_window_end"), table_name="business_metrics_snapshots")
    op.drop_index(op.f("ix_business_metrics_snapshots_window_start"), table_name="business_metrics_snapshots")
    op.drop_index(op.f("ix_business_metrics_snapshots_organization_id"), table_name="business_metrics_snapshots")
    op.drop_table("business_metrics_snapshots")

    op.drop_index(op.f("ix_business_events_created_at"), table_name="business_events")
    op.drop_index(op.f("ix_business_events_entity_id"), table_name="business_events")
    op.drop_index(op.f("ix_business_events_channel"), table_name="business_events")
    op.drop_index(op.f("ix_business_events_source"), table_name="business_events")
    op.drop_index(op.f("ix_business_events_event_type"), table_name="business_events")
    op.drop_index(op.f("ix_business_events_organization_id"), table_name="business_events")
    op.drop_table("business_events")

    op.drop_index(op.f("ix_objective_runs_status"), table_name="objective_runs")
    op.drop_index(op.f("ix_objective_runs_organization_id"), table_name="objective_runs")
    op.drop_index(op.f("ix_objective_runs_objective_id"), table_name="objective_runs")
    op.drop_table("objective_runs")

    op.drop_index(op.f("ix_objectives_status"), table_name="objectives")
    op.drop_index(op.f("ix_objectives_organization_id"), table_name="objectives")
    op.drop_table("objectives")
