#!/usr/bin/env python3
"""
triage_exceptions.py - Stage 5 LLM exception triage
=====================================================

Uses Claude to triage the reconciliation exceptions found in Stage 2/3
(data/exceptions.csv): assigns a severity, a plain-English root cause, and a
recommended remediation action for each one, batched to control cost.

Requires:
    pip install anthropic
    export ANTHROPIC_API_KEY=...

Usage:
    python triage_exceptions.py
    python triage_exceptions.py --limit 10          # smoke test on a small sample
    python triage_exceptions.py --batch-size 25 --model claude-haiku-4-5-20251001
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import anthropic
import pandas as pd

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are a Stock Administration controls analyst triaging reconciliation \
exceptions between HRIS, broker, and payroll RSU feeds. For each exception you are given, \
assess:
- severity: "High", "Medium", or "Low" (High = wrong tax withholding, forfeited-share \
leakage, or payroll compliance risk; Medium = data-quality/matching issue with a clear fix; \
Low = cosmetic/reference-only discrepancy)
- root_cause: one plain-English sentence on the likely cause
- recommended_action: one plain-English sentence on what Stock Admin should do next

Respond with ONLY a JSON array, one object per exception, each with exactly these keys: \
exception_id, severity, root_cause, recommended_action. No other text, no markdown fences."""


def load_context(data_dir: Path) -> dict[str, pd.DataFrame]:
    return {
        "hris": pd.read_csv(data_dir / "hris_employees.csv"),
        "broker": pd.read_csv(data_dir / "broker_releases.csv"),
        "payroll": pd.read_csv(data_dir / "payroll_rsu.csv"),
    }


def describe(row: pd.Series, ctx: dict[str, pd.DataFrame]) -> str:
    """Just enough source-system detail for the LLM to reason about one exception."""
    hris, broker, payroll = ctx["hris"], ctx["broker"], ctx["payroll"]
    parts = [f"error_type={row['error_type']}", f"source_system={row['source_system']}",
             f"grant_id={row['grant_id']}", f"tranche={row['tranche']}"]

    emp = hris[hris["employee_id"] == row["employee_id"]]
    if len(emp):
        parts.append(f"employee_status={emp.iloc[0]['status']}")
        parts.append(f"department={emp.iloc[0]['department']}")

    b = broker[(broker["grant_id"] == row["grant_id"]) & (broker["tranche"] == row["tranche"])]
    if len(b):
        parts.append(f"broker_shares_vested={b.iloc[0]['shares_vested']}")
        parts.append(f"broker_fmv={b.iloc[0]['fmv_per_share']}")

    p = payroll[(payroll["grant_id"] == row["grant_id"]) & (payroll["tranche"] == row["tranche"])]
    if len(p):
        parts.append(f"payroll_taxable_income={p.iloc[0]['taxable_income']}")
        parts.append(f"payroll_tax_withheld={p.iloc[0]['tax_withheld']}")

    return "; ".join(parts)


def triage_batch(client: anthropic.Anthropic, model: str, batch: pd.DataFrame) -> list[dict]:
    items = "\n".join(f"- {row['exception_id']}: {row['context']}" for _, row in batch.iterrows())
    message = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Triage these {len(batch)} exceptions:\n{items}"}],
    )
    text = message.content[0].text.strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    return json.loads(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Triage reconciliation exceptions with Claude.")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--exceptions", type=Path, default=Path("data/exceptions.csv"))
    parser.add_argument("--out", type=Path, default=Path("data/exceptions_triaged.csv"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--limit", type=int, default=None,
                        help="Only triage the first N exceptions (cost control / smoke test).")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set.")

    exceptions = pd.read_csv(args.exceptions)
    if args.limit:
        exceptions = exceptions.head(args.limit)

    ctx = load_context(args.data)
    exceptions = exceptions.copy()
    exceptions["context"] = exceptions.apply(lambda row: describe(row, ctx), axis=1)

    client = anthropic.Anthropic()
    results: list[dict] = []
    for start in range(0, len(exceptions), args.batch_size):
        batch = exceptions.iloc[start:start + args.batch_size]
        print(f"Triaging {start + 1}-{start + len(batch)} of {len(exceptions)}...")
        results.extend(triage_batch(client, args.model, batch))

    triage_df = pd.DataFrame(results)
    output = exceptions.drop(columns=["context"]).merge(triage_df, on="exception_id", how="left")
    output.to_csv(args.out, index=False)

    print(f"Triaged {len(output)} exceptions -> {args.out}")
    print(output["severity"].value_counts().to_string())


if __name__ == "__main__":
    main()
