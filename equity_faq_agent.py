#!/usr/bin/env python3
"""
equity_faq_agent.py - Stage 5 employee equity FAQ agent
==========================================================

An interactive Claude-powered assistant that answers one employee's
questions about their own RSU grants, vesting schedule, and ESPP purchases.
Grounded entirely in that employee's rows from the Stage 1/4 data - the
model never sees other employees' data, and no data is queried by the model
live (everything is pre-fetched into the system prompt).

Requires:
    pip install anthropic
    export ANTHROPIC_API_KEY=...

Usage:
    python equity_faq_agent.py                          # prompts for an employee ID, then a REPL
    python equity_faq_agent.py --employee-id E00001
    python equity_faq_agent.py --employee-id E00001 --question "When do I next vest?"
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import anthropic
import pandas as pd

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are an equity compensation FAQ assistant for employees. Answer questions \
ONLY using the equity data provided below for this one employee. Be specific and cite exact grant \
IDs, tranche numbers, dates, share counts, and dollar amounts from the data when relevant. If a \
question can't be answered from the data provided, say so rather than guessing.

For any total or summary figure (e.g. "how many shares have I vested"), use the "Precomputed \
totals" lines given below verbatim - do not add up individual tranche or grant rows yourself, \
since that arithmetic is error-prone. If a precomputed total you need isn't listed, say so rather \
than calculating your own.

You are not a tax or legal advisor - for tax-filing or legal questions, tell the employee to \
consult a professional. All figures are illustrative/synthetic and do not reflect real company \
data or real tax guidance.

Employee equity data:
{context}
"""


def load_all(data_dir: Path) -> dict[str, pd.DataFrame]:
    data = {
        "hris": pd.read_csv(data_dir / "hris_employees.csv",
                            parse_dates=["hire_date", "termination_date", "transfer_date"]),
        "grants": pd.read_csv(data_dir / "grants.csv", parse_dates=["grant_date"]),
        "vest": pd.read_csv(data_dir / "vest_schedule_truth.csv", parse_dates=["vest_date"]),
        "broker": pd.read_csv(data_dir / "broker_releases.csv"),
        "payroll": pd.read_csv(data_dir / "payroll_rsu.csv"),
    }
    espp_path = data_dir / "espp_purchases.csv"
    data["espp"] = pd.read_csv(espp_path) if espp_path.exists() else pd.DataFrame()
    return data


def build_employee_context(employee_id: str, data: dict[str, pd.DataFrame]) -> str | None:
    emp_rows = data["hris"][data["hris"]["employee_id"] == employee_id]
    if emp_rows.empty:
        return None
    emp = emp_rows.iloc[0]

    lines = [f"Employee: {emp['first_name']} {emp['last_name']} ({employee_id})",
             f"Department: {emp['department']}, Level: {emp['level']}",
             f"Hire date: {emp['hire_date'].date()}, Status: {emp['status']}"]
    if pd.notna(emp["termination_date"]):
        lines.append(f"Termination date: {emp['termination_date'].date()}")
    country_line = f"Country: {emp['country']}"
    if pd.notna(emp["transfer_date"]):
        country_line += f" (transferred from {emp['prior_country']} on {emp['transfer_date'].date()})"
    lines.append(country_line)

    grants = data["grants"][data["grants"]["employee_id"] == employee_id]
    lines.append("\nRSU Grants:")
    for g in grants.itertuples():
        lines.append(f"  {g.grant_id}: {g.grant_type}, granted {g.grant_date.date()}, {g.shares_granted} shares")

    vest = data["vest"][data["vest"]["employee_id"] == employee_id].sort_values(["grant_id", "tranche"])

    # Precomputed totals: the model should report these, not add up rows itself -
    # LLMs are unreliable at summing many numbers in context.
    by_status = vest.groupby("status")["shares"].sum()
    lines.append("\nPrecomputed totals (use these exact numbers for any summary/total question):")
    lines.append(f"  Total vested shares (all grants): {int(by_status.get('VESTED', 0))}")
    lines.append(f"  Total unvested shares (all grants): {int(by_status.get('UNVESTED', 0))}")
    lines.append(f"  Total forfeited shares (all grants): {int(by_status.get('FORFEITED', 0))}")
    for grant_id, grp in vest.groupby("grant_id"):
        gstatus = grp.groupby("status")["shares"].sum()
        lines.append(f"  {grant_id} vested: {int(gstatus.get('VESTED', 0))}, "
                     f"unvested: {int(gstatus.get('UNVESTED', 0))}, "
                     f"forfeited: {int(gstatus.get('FORFEITED', 0))}")
    upcoming = vest[vest["status"] == "UNVESTED"].sort_values("vest_date")
    if len(upcoming):
        nxt = upcoming.iloc[0]
        lines.append(f"  Next vest date: {nxt['vest_date'].date()} ({nxt['grant_id']} tranche "
                     f"{nxt['tranche']}, {nxt['shares']} shares)")
    broker, payroll = data["broker"], data["payroll"]
    lines.append("\nVesting schedule:")
    for v in vest.itertuples():
        detail = f"  {v.grant_id} tranche {v.tranche}: {v.shares} shares, vest date {v.vest_date.date()}, status {v.status}"
        if v.status == "VESTED":
            b = broker[(broker["grant_id"] == v.grant_id) & (broker["tranche"] == v.tranche)]
            p = payroll[(payroll["grant_id"] == v.grant_id) & (payroll["tranche"] == v.tranche)]
            if len(b):
                row = b.iloc[0]
                detail += (f"; released {row['shares_released']} shares after withholding "
                          f"{row['shares_withheld']} shares at FMV ${row['fmv_per_share']}")
            if len(p):
                row = p.iloc[0]
                detail += (f"; taxable income ${row['taxable_income']}, tax withheld "
                          f"${row['tax_withheld']} ({row['tax_country']})")
        lines.append(detail)

    espp = data["espp"]
    if len(espp) and "employee_id" in espp.columns:
        emp_espp = espp[espp["employee_id"] == employee_id]
        if len(emp_espp):
            lines.append("\nESPP purchases:")
            for e in emp_espp.itertuples():
                lines.append(f"  Offering {e.offering_start}, purchase {e.purchase_date}: "
                            f"{e.shares_purchased} shares at ${e.purchase_price} "
                            f"(market FMV ${e.purchase_date_fmv}), cost ${e.purchase_cost}, "
                            f"discount value ${e.discount_value}")
            lines.append(f"  Precomputed ESPP totals: {int(emp_espp['shares_purchased'].sum())} total shares "
                        f"purchased, ${emp_espp['purchase_cost'].sum():.2f} total cost, "
                        f"${emp_espp['discount_value'].sum():.2f} total discount value")

    return "\n".join(lines)


def ask(client: anthropic.Anthropic, model: str, system: str,
        messages: list[dict], question: str) -> str:
    messages.append({"role": "user", "content": question})
    response = client.messages.create(model=model, max_tokens=1024, system=system, messages=messages)
    answer = response.content[0].text
    messages.append({"role": "assistant", "content": answer})
    return answer


def main() -> None:
    parser = argparse.ArgumentParser(description="Employee equity FAQ agent.")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--employee-id", help="Skip the prompt and use this employee ID.")
    parser.add_argument("--question", help="Ask a single question and exit (no REPL).")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set.")

    data = load_all(args.data)

    employee_id = args.employee_id or input("Employee ID (e.g. E00001): ").strip()
    context = build_employee_context(employee_id, data)
    if context is None:
        raise SystemExit(f"No employee found with ID {employee_id}")

    system = SYSTEM_PROMPT.format(context=context)
    client = anthropic.Anthropic()
    messages: list[dict] = []

    if args.question:
        print(ask(client, args.model, system, messages, args.question))
        return

    print(f"Loaded equity data for {employee_id}. Ask a question, or type 'quit' to exit.\n")
    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if question.lower() in {"quit", "exit"}:
            break
        if not question:
            continue
        answer = ask(client, args.model, system, messages, question)
        print(f"\nAssistant: {answer}\n")


if __name__ == "__main__":
    main()
