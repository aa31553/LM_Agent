# Analysis Workspace Phase 0 Baseline

- Baseline branch: `codex/session-analysis-workspace`
- Baseline commit: `9cb5c10ecc80433201834e502f163fe9c3d42f18`
- Application version: `0.7.0`
- API prefix: `/api/v1`
- Existing workspace migration head: `999_session_analysis_workspace_phase3_4.sql`
- Primary production platform: Windows
- Recorded: 2026-07-30

## Frozen contracts

The Phase 0 baseline keeps these public contracts unchanged:

- Existing direct `AnalysisPlan` submissions remain valid.
- Existing chart types remain `bar`, `line`, and `scatter`.
- The current chart contract uses `x_field` and `y_field`.
- Analysis drafts require explicit user confirmation before a Job is created.
- Chart Schema v1/v2 frontend rendering remains supported.
- XLSX preparation continues through the existing Windows Excel COM/pywin32 path.

## Existing regression coverage

`tests/test_session_analysis_workspace.py` already covers:

- aggregation alias uniqueness and group-by conflicts;
- result row counts and truncation metadata;
- formula cache and Excel display-format warnings;
- analysis Job list, cancel, and retry behavior;
- XLSX/CSV inspection and deterministic aggregation;
- upload size limits and partial-file cleanup.

Phase 0 adds a frozen reproduction proving that legacy chart payloads using `x`/`y`
fail before normalization. Phase 1 adds the compatibility behavior without weakening
the canonical `ChartSpec`.

## Windows compatibility gate

Phase 0-1 changes must not introduce:

- POSIX-only shell commands or path separators;
- `fork`, Unix signals, symlinks, or executable-bit assumptions;
- dynamic Python, SQL, JavaScript, or subprocess execution;
- changes to the Excel COM preparation lifecycle;
- case-sensitive filesystem assumptions.

The Windows CI job runs on `windows-latest` with Python 3.11 and executes the
baseline workspace tests plus the Phase 1 normalizer/repair tests.
