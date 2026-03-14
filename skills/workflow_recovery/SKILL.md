# Workflow Recovery

Purpose: recover degraded workflows and integration pipelines without defaulting blindly to n8n.

When to activate:
- integration failures
- webhook incidents
- workflow degradation

Preferred strategies:
- inspect business state and rollbackable guardrails first
- prefer internal_service or http if integration health is degraded
- use n8n only as fallback

Common mistakes:
- retrying the broken workflow path as first option
- ignoring tripwires and guardrail warnings

Completion checklist:
- pipeline stabilized or fallback queued
- decision path recorded
