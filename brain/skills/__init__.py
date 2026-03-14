"""
Built-in skills for kan-core.
Each skill is a dict with: name, trigger_keywords, description, instructions, example_action.
"""
from brain.skills.crm_sync import SKILL as crm_sync
from brain.skills.email_automation import SKILL as email_automation
from brain.skills.slack_notify import SKILL as slack_notify
from brain.skills.telegram_notify import SKILL as telegram_notify
from brain.skills.shopify_ops import SKILL as shopify_ops
from brain.skills.calendar_schedule import SKILL as calendar_schedule
from brain.skills.data_export import SKILL as data_export
from brain.skills.lead_qualification import SKILL as lead_qualification
from brain.skills.invoice_generate import SKILL as invoice_generate

ALL_SKILLS = [
    crm_sync,
    email_automation,
    slack_notify,
    telegram_notify,
    shopify_ops,
    calendar_schedule,
    data_export,
    lead_qualification,
    invoice_generate,
]

__all__ = ["ALL_SKILLS"]
