# Equity Ops Automation Toolkit

Stock Administration teams reconcile the same RSU vest events across three systems every quarter: the HR system, the equity broker, and payroll. When those systems disagree, someone finds out by hand, usually under deadline pressure right before vest day.

This project builds the tooling to catch those disagreements automatically. **Stage 1 (this folder)** is a synthetic data generator that produces realistic source-system extracts with a known set of injected errors, so every later stage (reconciliation, dbt tests, AI triage, dashboards) can be measured against an answer key.

> All data is synthetic. Tax rates and plan rules are simplified and illustrative, and nothing here reflects any company's actual plan terms or tax guidance.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python generate_data.py                  # real NVDA prices via yfinance if reachable
python generate_data.py --offline        # simulated prices, no internet needed
python generate_data.py --employees 2000 --seed 7 --error-rate 0.05
```

Output lands in `data/`. The same seed always produces the same dataset.

## How the data is built

1. **Employees (HRIS):** hire dates from 2021 onward, job levels, six countries, ~15% terminations, ~6% international transfers.
2. **Grants:** a new-hire grant sized by level, plus an annual refresh grant each March 1 after six months of tenure.
3. **Vesting schedule:** 16 quarterly tranches on fixed vest dates (the 15th of Mar/Jun/Sep/Dec). Leftover shares from integer division go to the final tranche. Each tranche is marked VESTED, UNVESTED, or FORFEITED (vest date after termination).
4. **Clean feeds:** for every vested tranche, the broker row and payroll row are built from the same inputs:
   - FMV = closing price on the vest date, or the prior trading day when the market is closed
   - Tax country = the employee's country *on the vest date*, accounting for transfers
   - Net share settlement: shares withheld = `ceil(shares × rate)`, so the company withholds slightly more than the tax owed; payroll refunds the excess (`withholding_refund`)
5. **Sanity checks:** before any errors are injected, the script asserts that withheld + released = vested, that broker and payroll FMVs match, and that every refund is less than one share's value.
6. **Error injection:** about 3% of vest events are corrupted (at most one error per event) and logged in the answer key.

## Output files

| File | Represents | Use in reconciliation? |
|---|---|---|
| `hris_employees.csv` | HR system extract | Yes |
| `broker_releases.csv` | Broker share-release file | Yes |
| `payroll_rsu.csv` | Payroll RSU income lines | Yes |
| `prices.csv` | Daily closing prices | Yes (FMV lookup) |
| `grants.csv` | Grant ledger | Yes |
| `vest_schedule_truth.csv` | What *should* have happened | No: this is the ground truth |
| `_answer_key_injected_errors.csv` | Every injected error | No: use only to score results |
| `run_metadata.json` | Config, row counts, error counts | Reference |

## Injected error types

| Error | System | What a reconciliation should notice |
|---|---|---|
| `POST_TERM_RELEASE` | Broker | A release dated after the HRIS termination date |
| `DUPLICATE_RELEASE` | Broker | Two releases for the same grant and tranche |
| `STALE_FMV_PAYROLL` | Payroll | Payroll FMV ≠ broker FMV / price file on vest date |
| `WITHHOLDING_ROUNDING` | Broker | Shares withheld < `ceil(shares × rate)`, one extra share released |
| `MISSING_PAYROLL` | Payroll | A broker release with no matching payroll line |
| `ID_FORMAT_MISMATCH` | Payroll | Payroll employee ID that doesn't exist in HRIS |
| `STALE_COUNTRY` | Payroll | Tax country ≠ HRIS country on the vest date |

## Known simplifications (future realism upgrades)

- **Stock splits.** Prices from yfinance are split-adjusted, and share counts are generated in split-adjusted units. Real records from before NVIDIA's 2021 (4:1) and 2024 (10:1) splits would be in pre-split shares, which is a real reconciliation challenge worth modeling later.
- **Taxes.** One flat combined rate per country. Real withholding involves supplemental federal rates, state tax, Social Security wage caps, and country-specific rules.
- **Grant types.** No promotion grants, performance awards, or leaves of absence yet.
- **Mobility.** A transfer moves 100% of the taxation to the new country. In reality, income that vests after a move is often split between countries based on where the work was done.

## Roadmap

- [x] Stage 1: synthetic data generator with an answer key
- [x] Stage 2: reconciliation engine (Python/SQL) scored against the answer key
- [x] Stage 3: dbt models with business-rule tests as automated controls
- [x] Stage 4: ESPP purchase calculator (lookback, 15% discount, $25K limit)
- [ ] Stage 5: LLM exception triage and an employee equity FAQ agent
- [ ] Stage 6: vest-event readiness dashboard
- [ ] Docs: process map, controls matrix, SOP
