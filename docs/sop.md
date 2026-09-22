# Standard Operating Procedure

How to run this toolkit end to end, and what to do with what it finds.
All commands assume you're in the repo root with the venv created (see
[`README.md`](../README.md) Quick Start).

## 1. One-time environment setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install dbt-duckdb streamlit anthropic   # Stage 3, 6, 5 respectively
```

Stage 5 additionally needs an Anthropic API key (separate from any Claude
Code/claude.ai subscription - see [`README.md`](../README.md)):

```bash
export ANTHROPIC_API_KEY=...
```

## 2. Generate the source data (Stage 1)

```bash
python generate_data.py                    # real NVDA prices if reachable
python generate_data.py --offline          # simulated prices, no internet needed
```

Output lands in `data/` (gitignored - local only). Re-running with the same
`--seed` always reproduces the same dataset.

## 3. Reconcile (Stage 2)

```bash
python reconcile.py                         # writes data/exceptions.csv
python score_reconciliation.py              # precision/recall vs the answer key
```

Expect 100% precision/recall on the default seed-42 dataset - if either
number drops, a control has regressed and needs investigation before moving
on.

## 4. Run the dbt controls (Stage 3)

```bash
cd dbt
export DBT_PROFILES_DIR=.
dbt run     # builds the models
dbt test    # runs the controls - see step 4a
cd ..
```

**4a. Reading `dbt test` output on this dataset:** every one of the 7
business-rule tests is *expected* to fail with exactly 58 results on the
default seed-42 data, because Stage 1 injects 58 of each error type on
purpose. A non-zero `dbt test` exit code here means the controls are
working, not broken. Run `dbt test` against a clean/real feed and all 13
tests should pass. See [`dbt/README.md`](../dbt/README.md).

## 5. Compute ESPP purchases (Stage 4, as needed)

Only needed when an ESPP purchase period lands in the current cycle:

```bash
python espp_calculator.py                   # writes data/espp_purchases.csv
```

## 6. Triage exceptions (Stage 5)

```bash
python triage_exceptions.py --limit 10      # smoke test first - costs real API credits
python triage_exceptions.py                 # full run once the smoke test looks right
```

Writes `data/exceptions_triaged.csv` with a `severity` (High/Medium/Low),
`root_cause`, and `recommended_action` per exception. Work the backlog in
severity order:

- **High** - forfeiture leakage, missing/incorrect withholding, payroll
  compliance gaps. Investigate and resolve before vest day.
- **Medium** - data-quality issues with a clear fix (e.g. malformed IDs).
  Fix before the next reporting cycle.
- **Low** - cent-level rounding noise. Document as acceptable if under
  threshold; no urgent action.

## 7. Check the dashboard (Stage 6)

```bash
streamlit run dashboard.py
```

Before a vest date, confirm:

- The "Next vest event" panel's headcount/shares/dollar figures match
  expectations for that cycle.
- The exceptions backlog has no unresolved **High** severity items tied to
  grants/tranches vesting in this cycle.
- The ESPP snapshot looks sane if a purchase period is in this window.

## 8. Employee questions

Point employees (or a support team fielding their questions) at the FAQ
agent for grounded, per-employee answers:

```bash
python equity_faq_agent.py --employee-id E00001
```

The agent only ever sees that one employee's data - it can't answer
questions about anyone else, by construction (the data isn't in its
context).

## Escalation

| Finding | Action |
|---|---|
| A control's precision/recall drops below 100% on a re-run | Stop; the check logic or the underlying data model has changed. Investigate before triaging further. |
| A High-severity exception on shares/tranches vesting within 5 business days | Escalate to Stock Admin lead same day. |
| `dbt run` fails (not `dbt test` - an actual model build error) | Data pipeline issue, likely a schema change in a source CSV. Fix before continuing. |
| Any script raises `ANTHROPIC_API_KEY is not set` | Stage 5 only; export the key and re-run. Stages 1-4 and 6 don't need it. |
