# Stage 3: dbt models and controls

A local dbt project (dbt-duckdb, no warehouse required) that rebuilds Stage 2's
reconciliation as dbt models and business-rule tests.

## Quick start

```bash
cd dbt
source ../.venv/bin/activate        # dbt-duckdb is installed in the repo's .venv
pip install dbt-duckdb              # if not already installed
export DBT_PROFILES_DIR=.
dbt run                             # builds the staging models + fct_vest_reconciliation
dbt test                            # runs the controls
```

Run `dbt run` and `dbt test` separately, not `dbt build`. `dbt build` skips
downstream models/tests as soon as an upstream test fails, which hides most
of the controls on this dataset since it's full of intentional violations.

## Models

- `models/staging/` - typed views over the Stage 1 CSVs (`stg_hris_employees`,
  `stg_broker_releases`, `stg_payroll_rsu`), read directly via DuckDB's
  `read_csv_auto` from `../data/`.
- `models/marts/fct_vest_reconciliation.sql` - one row per vest event known to
  either the broker or payroll feed (full outer join on grant_id + tranche),
  enriched with HRIS facts. The base table the controls run against.

## Controls (`dbt test`)

Generic tests (`models/staging/schema.yml`): `unique`/`not_null` on natural
keys, plus a `relationships` test asserting every payroll `emp_id` resolves to
an HRIS `employee_id` (catches `ID_FORMAT_MISMATCH`).

Singular tests (`tests/*.sql`), one per remaining error type from Stage 1:
`assert_no_post_term_releases`, `assert_no_duplicate_releases`,
`assert_no_stale_fmv`, `assert_no_withholding_rounding`,
`assert_no_missing_payroll`, `assert_no_stale_country`. Each selects the
violating rows directly (rather than reimplementing the checks in Python) and
persists failures to `main_dbt_test__audit` via `store_failures: true`.

**On the default seed-42 dataset, every one of these 7 controls is expected
to fail with exactly 58 results** - Stage 1 injects 58 of each error type, and
these controls are built to catch them all, matching `reconcile.py`'s and
`score_reconciliation.py`'s results in Stage 2. `dbt test` exiting non-zero
here means the controls are working, not broken. Run against a clean feed (no
injected errors), all 13 tests pass.
