# Microagents Known Issues

Microagents should remain disabled in production for now.

Recommended production setting:

```env
ENABLE_MICROAGENTS=false
```

The legacy runtime is currently more stable than the microagent path in benchmark runs.

## Next Sprint Focus

The three benchmark cases that remain open as known issues are the compensation scenarios:

1. `eval_payment_006_compensation`
   - Domain: `payment_followthrough`
   - Current behavior: reaches `rollback_and_compensate` but ends in `failed`
   - Expected direction: complete compensation path or escalate cleanly

2. `eval_collections_005_compensation`
   - Domain: `collections_followthrough`
   - Current behavior: unstable in microagent path (`runtime_error`) and incomplete in legacy (`in_progress`)
   - Expected direction: complete compensation without runtime error

3. `eval_onboarding_005_compensation`
   - Domain: `onboarding_execution`
   - Current behavior: unstable in microagent path (`runtime_error`) and incomplete in legacy (`in_progress`)
   - Expected direction: complete compensation without runtime error

## Notes

- Replan benchmark cases are currently forced by the eval harness and are not the primary blocker for production rollout.
- Do not expand microagents in production until the three compensation cases above are stable in benchmark runs.
