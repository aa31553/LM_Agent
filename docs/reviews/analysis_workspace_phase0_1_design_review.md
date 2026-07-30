# Phase 0-1 Design Review

- Review branch: `codex/analysis-phase0-1-windows`
- Baseline: `codex/session-analysis-workspace@9cb5c10ecc80433201834e502f163fe9c3d42f18`
- Review date: 2026-07-30
- Primary runtime: Windows / Python 3.11
- Scope: Phase 0 regression lock and Phase 1 plan normalization/repair

## Decision

The design is approved for Phase 0-1. Per project direction, Windows runner execution is
supplementary and is not a blocking completion gate.
The canonical `AnalysisPlan` and `ChartSpec` remain unchanged; compatibility is
implemented before Pydantic validation and only for unambiguous legacy fields.

## Review findings resolved

### Repair originally covered only Pydantic validation

Resolution: the orchestrator now performs normalization, model validation, approved
file-scope validation, and dataset manifest validation as one bounded validation
stage. A schema-valid plan containing a missing column can therefore use the same
single repair attempt.

### A long schema could hide the invalid plan in the repair prompt

Resolution: the invalid plan and validation error are placed before the bounded
schema context. The schema is capped independently before the complete prompt cap
is applied.

### Repair could not be allowed to expand data access

Resolution: approved file IDs are checked before dataset validation and checked
again after repair. A repaired plan referencing another file returns
`PERMISSION_DENIED`; it is not converted into a generic repair failure.

### Normalization evidence needed to be auditable

Resolution: additive draft fields store the original JSON, normalized plan,
normalization actions, initial validation errors, and whether repair was attempted.
The draft response exposes actions, errors, and repair status without changing
existing required response fields.

## Windows compatibility review

No Phase 0-1 production code introduces:

- shell commands, subprocess execution, `fork`, Unix signals, or symlinks;
- hard-coded POSIX separators or executable-bit assumptions;
- changes to Excel COM/pywin32 preparation;
- dynamic Python, SQL, JavaScript, or ECharts option execution.

The normalizer and repair coordinator use platform-neutral Python and JSON only.
PostgreSQL changes are additive. Existing SQLite databases are rebuilt through the
project's existing cross-platform startup upgrader so Windows development databases
receive the same columns.

The workflow `.github/workflows/windows-phase0-1.yml` runs with PowerShell on
`windows-latest`, installs the Windows dependency marker (including pywin32), runs
Ruff, and executes both the frozen workspace regressions and new Phase 1 tests.

## Compatibility and rollback

- Direct valid `AnalysisPlan` clients are unchanged.
- Existing Chart Schema v1/v2 renderers are unchanged.
- Existing draft confirmation remains mandatory before Job creation.
- New API response fields are additive.
- The migration does not remove or rename columns.
- Code rollback can leave the additive audit columns in place safely.
- Ambiguous `x`/`x_field`, null chart fields, and multi-value legacy `y` are
  rejected instead of guessed.

## Test coverage added

- Frozen reproduction of the legacy `x`/`y` contract failure.
- Deterministic `x` and single-item `y` normalization.
- Input immutability.
- Conflicting and null field rejection.
- Exactly one repair attempt.
- Repair after dataset column validation failure.
- Repair failure error metadata.
- Permission scope revalidation after repair.
- Windows path strings remain unchanged.

## Deferred to later phases

Recipe Registry, histogram compilation, Chart Schema v3, feature-flag rollout, and
statistical recipes remain out of scope for Phase 0-1.
