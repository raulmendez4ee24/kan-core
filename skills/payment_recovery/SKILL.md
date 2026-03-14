# Payment Recovery

Purpose: recover revenue, validate payment status, and choose the safest followthrough or compensation path.

When to activate:
- payment failures
- invoice or charge issues
- cash exposure with pending collections

Preferred strategies:
- verify case and business state before acting
- prefer non-destructive followup before compensation
- if a rollbackable payment exists, compensate fast

Common mistakes:
- treating dispatch as confirmed success
- retrying the same payment action without changing approach

Anti-patterns:
- duplicate charges
- escalating before checking rollback options

Completion checklist:
- payment status clarified
- next action queued or compensation executed
- case updated with outcome

Rollback criteria:
- use refund/compensation when outcome mismatches and a prior payment action is reversible
