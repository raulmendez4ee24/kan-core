# Competitive Scorecard

Evaluates whether your agent already beats the target thresholds and optionally compares against OpenClaw.

## 1) Run weekly eval (our report)

```bash
python3 scripts/weekly_eval_harness.py --token "$KAN_CLIENT_TOKEN" --out .run/weekly_eval_report.json
```

## 2) Compute scorecard (threshold-only)

```bash
python3 scripts/competitive_scorecard.py \
  --our-report .run/weekly_eval_report.json \
  --out .run/competitive_scorecard.json
```

## 3) Head-to-head vs OpenClaw

```bash
python3 scripts/competitive_scorecard.py \
  --our-report .run/weekly_eval_report.json \
  --openclaw-report .run/openclaw_eval_report.json \
  --out .run/competitive_scorecard.json
```

## Optional: custom thresholds

```bash
python3 scripts/competitive_scorecard.py \
  --our-report .run/weekly_eval_report.json \
  --thresholds-file scripts/competitive_thresholds.example.json
```
