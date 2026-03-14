# Rollback Procedures

Purpose: apply compensation or rollback with minimal extra damage when execution outcomes mismatch.

When to activate:
- reversible failures
- payment compensation
- delete/customer restoration
- corrective messaging

Preferred strategies:
- inspect rollback snapshot before acting
- choose the narrowest compensation path
- record the compensation result back into the case

Anti-patterns:
- compensating twice
- rolling back without matching metadata

Completion checklist:
- rollback attempted only once
- result recorded
- case marked completed, waiting, or escalated based on outcome
