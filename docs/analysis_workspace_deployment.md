# Analysis Workspace Phase 6 Deployment and Rollback

> Updated: 2026-07-31
> Scope: Phase 0–6 analysis workspace upgrade

## Pre-deployment gate

- Back up PostgreSQL and record the current migration head.
- Run `python -m app.db.init_db` in a staging copy and verify the additive
  `error_code`, `error_details_json`, and `execution_duration_ms` columns.
- Verify PostgreSQL, Redis, analysis worker storage, and the Excel COM worker identity.
- Run Python unit/integration/security/load/cancel tests and the JavaScript ECharts
  adapter test.
- Run one authorized protected-XLSX smoke test through upload, profile, confirm, Job,
  chart, and artifact download.
- Deploy a frontend that supports Chart Schema v3 before enabling IntentDraft.

## Rollout configuration

```dotenv
ANALYSIS_INTENT_FLOW_ENABLED=false
ANALYSIS_RECIPES_ENABLED=true
```

`ANALYSIS_INTENT_FLOW_ENABLED=false` keeps `/analysis/plan-drafts` on the legacy
AnalysisPlan prompt. `ANALYSIS_RECIPES_ENABLED=true` permits the Recipe metadata API,
structured intent validation, and explicitly submitted Recipe plans.

## Deployment order

1. Apply the additive migration and deploy the backend with IntentDraft disabled.
2. Deploy the v3 frontend adapter.
3. Smoke-test Recipe list/detail and structured intent validation.
4. Enable IntentDraft in a test Workspace deployment.
5. Observe `GET /api/v1/analysis/metrics/recipes?workspace_id=...`.
6. Promote by Workspace/deployment group only when error, repair, timeout, and p95
   latency remain within the organization’s approved thresholds.
7. Record the enabled population, timestamp, backend commit, frontend version, and
   migration head.

## Required observations

- Job success rate and failed/cancelled counts by Recipe.
- Initial validation failure rate and repair rate.
- Average and p95 execution duration.
- Structured `error_code` distribution.
- Worker timeout and cancellation latency.
- Result/chart truncation warnings and memory/CPU utilization.

The metrics endpoint is Workspace scoped and requires read permission. Central
operations may aggregate the same additive database fields through the approved
monitoring stack.

## Rollback

1. Set `ANALYSIS_INTENT_FLOW_ENABLED=false`.
2. If Recipe execution itself is unhealthy, also set
   `ANALYSIS_RECIPES_ENABLED=false`.
3. Keep `/analysis/plan-drafts`, `/analysis/plans/validate`, direct legacy Jobs, and
   Chart v1/v2 available.
4. Keep additive columns and completed Recipe results; do not run a destructive
   database rollback.
5. If the v3 renderer is unhealthy, display the trusted result table and artifact
   download while rolling back the frontend.
6. Preserve failed Jobs, structured errors, lineage, and audit records for review.

## Completion record

- [ ] PostgreSQL backup completed
- [ ] Migration applied and schema verified
- [ ] Backend deployed with IntentDraft disabled
- [ ] v3 frontend deployed
- [ ] Protected-XLSX smoke test passed
- [ ] Security/load/cancel checks passed
- [ ] Canary Workspace enabled and observed
- [ ] Metrics accepted by the release owner
- [ ] Production groups enabled
- [ ] Rollback switches tested
