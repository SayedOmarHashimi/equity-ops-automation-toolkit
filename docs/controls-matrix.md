# Controls Matrix

Every error type Stage 1 can inject, and how each later stage detects it.
Two independent implementations exist for the same seven rules - a Python
reconciliation (Stage 2) and dbt tests (Stage 3) - so a bug in one is caught
by the other.

| # | Error type | Source system | What it means | Business risk | Python control (`reconcile.py`) | dbt control (`dbt test`) | Typical severity* |
|---|---|---|---|---|---|---|---|
| 1 | `POST_TERM_RELEASE` | Broker | A release dated after the employee's HRIS termination date | Forfeited shares released anyway - financial leakage | `check_post_term_release` | `assert_no_post_term_releases` | High |
| 2 | `DUPLICATE_RELEASE` | Broker | The same grant + tranche released twice under different release references | Duplicate share issuance and duplicate tax withholding | `check_duplicate_release` | `assert_no_duplicate_releases` | High |
| 3 | `STALE_FMV_PAYROLL` | Payroll | Payroll priced the vest using the prior trading day's close instead of the vest-date FMV | Incorrect taxable income and withholding | `check_stale_fmv` | `assert_no_stale_fmv` | High (dollar-impact dependent) |
| 4 | `WITHHOLDING_ROUNDING` | Broker | Shares withheld < `ceil(shares_vested x tax_rate)` implies | Broker under-withheld by one share; usually cent-level | `check_withholding_rounding` | `assert_no_withholding_rounding` | Low |
| 5 | `MISSING_PAYROLL` | Payroll | A broker release with no matching payroll line | Payroll compliance gap - vest income never reported/taxed | `check_missing_payroll` | `assert_no_missing_payroll` | High |
| 6 | `ID_FORMAT_MISMATCH` | Payroll | Payroll employee ID doesn't resolve to an HRIS `employee_id` | Data-quality break; blocks joins/reporting until fixed | `check_id_format_mismatch` | generic `relationships` test on `stg_payroll_rsu.emp_id` | Medium |
| 7 | `STALE_COUNTRY` | Payroll | Tax country doesn't match the employee's HRIS country as of the vest date | Wrong country/rate taxed after an international transfer | `check_stale_country` | `assert_no_stale_country` | High (dollar-impact dependent) |

\* From Stage 5's LLM triage of the default seed-42 dataset
(`exceptions_triaged.csv`). Severity is assigned per-instance based on
dollar impact and compliance risk, not hardcoded by error type - the table
above shows the predominant classification, not a fixed rule. See
[`dbt/README.md`](../dbt/README.md) for why `dbt test` is *expected* to fail
on this dataset (it's full of intentional violations, not a broken build).

## Coverage note

Both controls (`reconcile.py` and dbt) score 7/7 error types with 100%
precision and 100% recall against `_answer_key_injected_errors.csv` on the
default seed-42 dataset (verified in Stages 2 and 3) and hold up against a
different seed and population size (verified with `--seed 7`). See
[`README.md`](../README.md) for the full scoring output.
