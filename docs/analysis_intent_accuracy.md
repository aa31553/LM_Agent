# Analysis Intent Accuracy Guardrails

The Recipe intent flow uses three layers of protection:

1. Structural validation rejects undeclared Recipe parameters and incompatible columns.
2. Semantic validation compares explicit aggregation words in the question with the compiled plan.
3. Ambiguous numeric aggregation returns a structured clarification instead of silently guessing.

For `category_summary`, `trend_summary`, `period_overlay`, and `group_summary`:

- `count` must not include `value_field`.
- `mean`, `sum`, `median`, `min`, and `max` require a numeric `value_field`.
- A draft with `clarification.code = "AGGREGATION_REQUIRED"` must be confirmed with
  `clarification_choice`, or with an explicitly edited plan.

Use `period_overlay` when each calendar month must be a separate line on a shared
day-of-month X axis. Its deterministic result contract is `day`, `series`, and
`value`; `complete_x_range=true` emits days 1 through 31 for every month and uses
`null` for missing or invalid calendar days instead of inventing zero values.

Example:

```json
{
  "clarification_choice": "mean"
}
```

The versioned regression dataset is
`tests/fixtures/analysis_intent_eval_cases.json`. Save model outputs in the same case order
using `case_id`, `intent`, and optional `clarification`, then score them with:

```bash
lm-agent-eval-analysis-intents \
  --dataset tests/fixtures/analysis_intent_eval_cases.json \
  --predictions path/to/predictions.json \
  --minimum-exact-match 0.85
```

The command exits non-zero when cases are missing or exact-match accuracy falls below the
configured threshold, so it can be used as a CI release gate after prompt or model changes.
