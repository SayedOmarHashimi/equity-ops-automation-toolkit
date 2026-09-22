#!/usr/bin/env python3
"""
dashboard.py - Stage 6 vest-event readiness dashboard
========================================================

A Streamlit dashboard for a Stock Administration team to check before an
upcoming vest event: how many shares/employees/dollars are coming due, the
current reconciliation exceptions backlog (Stage 2/3/5), and ESPP status.

Requires:
    pip install streamlit

Usage:
    streamlit run dashboard.py
"""

from __future__ import annotations

import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

REPO_URL = "https://github.com/SayedOmarHashimi/equity-ops-automation-toolkit"

# Status palette: severity is a state (bad -> good), not a category, so it's
# colored with fixed status hues rather than arbitrary categorical colors.
SEVERITY_COLORS = {"High": "#d03b3b", "Medium": "#fab219", "Low": "#0ca30c"}
SEVERITY_ORDER = ["High", "Medium", "Low"]

_ROOT = Path(__file__).resolve().parent
# data/ (Stage 1 output) is gitignored and local-only. When it's not present -
# e.g. on Streamlit Community Cloud, which only has what's committed - fall
# back to the committed sample_data/ snapshot so the deployed app still works.
DATA_DIR = _ROOT / "data" if (_ROOT / "data").exists() else _ROOT / "sample_data"

st.set_page_config(page_title="Vest-Event Readiness", layout="wide")


@st.cache_data
def load_data():
    vest = pd.read_csv(DATA_DIR / "vest_schedule_truth.csv", parse_dates=["vest_date"])
    hris = pd.read_csv(DATA_DIR / "hris_employees.csv")
    prices = pd.read_csv(DATA_DIR / "prices.csv", parse_dates=["date"]).set_index("date")["close"]

    triaged_path = DATA_DIR / "exceptions_triaged.csv"
    exceptions_path = DATA_DIR / "exceptions.csv"
    if triaged_path.exists():
        exceptions = pd.read_csv(triaged_path)
    elif exceptions_path.exists():
        exceptions = pd.read_csv(exceptions_path)
    else:
        exceptions = pd.DataFrame()

    espp_path = DATA_DIR / "espp_purchases.csv"
    espp = pd.read_csv(espp_path, parse_dates=["offering_start", "purchase_date"]) if espp_path.exists() else pd.DataFrame()

    meta_path = DATA_DIR / "run_metadata.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    return vest, hris, prices, exceptions, espp, meta


vest, hris, prices, exceptions, espp, meta = load_data()

st.title("Vest-Event Readiness Dashboard")

st.markdown(
    "This dashboard simulates what a **Stock Administration** team checks before an RSU "
    "vest date. Every quarter, employee stock grants vest on a schedule, and three separate "
    "systems - HR, the stock plan's broker, and payroll - each record their own version of "
    "who got shares and how much tax was withheld. When those systems disagree, it's usually "
    "found by hand, under deadline pressure. This tool automates that check: it shows how much "
    "is coming due at the next vest date, flags disagreements between the three systems as "
    "**exceptions** (each ranked by an AI model into a severity), and tracks Employee Stock "
    "Purchase Plan (ESPP) activity."
)
st.info(
    "**All data on this page is synthetic** - employee names, dollar amounts, and "
    "exceptions are all generated for demonstration, not a real company. "
    f"[Full source, pipeline, and docs on GitHub]({REPO_URL}).",
    icon="ℹ️",
)
if meta.get("as_of_date"):
    st.caption(f"Data as of {meta['as_of_date']} (Stage 1 generator run date)")

st.divider()

# ---------------------------------------------------------------------------
# Next vest event
# ---------------------------------------------------------------------------

st.header("Next vest event")
st.caption("Headcount, shares, and dollar exposure for the next scheduled RSU vesting date, "
          "so the team can size the work ahead of time instead of finding out on the day.")

upcoming = vest[vest["status"] == "UNVESTED"]
if upcoming.empty:
    st.info("No upcoming vest events in the data.")
else:
    next_date = upcoming["vest_date"].min()
    due = upcoming[upcoming["vest_date"] == next_date]
    latest_fmv = float(prices.iloc[-1])
    total_shares = int(due["shares"].sum())
    total_value = total_shares * latest_fmv
    n_employees = due["employee_id"].nunique()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Next vest date", next_date.strftime("%Y-%m-%d"))
    c2.metric("Employees due", f"{n_employees:,}")
    c3.metric("Shares due", f"{total_shares:,}")
    c4.metric("Est. value (latest FMV)", f"${total_value:,.0f}")

    with st.expander(f"Tranches vesting {next_date.strftime('%Y-%m-%d')} ({len(due)} rows)"):
        emp_cols = hris[["employee_id", "first_name", "last_name", "department", "status"]]
        emp_cols = emp_cols.rename(columns={"status": "employee_status"})
        detail = due.merge(emp_cols, on="employee_id", how="left")
        st.dataframe(
            detail[["employee_id", "first_name", "last_name", "department", "employee_status",
                    "grant_id", "tranche", "shares"]].sort_values("shares", ascending=False),
            use_container_width=True, hide_index=True,
        )

# ---------------------------------------------------------------------------
# Exceptions backlog
# ---------------------------------------------------------------------------

st.header("Exceptions backlog")
st.caption("Automated checks compare the HR, broker, and payroll extracts and flag every "
          "disagreement. Each one is triaged by Claude into a severity, so the team knows "
          "what to work first - High means real financial/compliance risk, Low is usually "
          "cent-level rounding noise.")

if exceptions.empty:
    st.info("No exceptions.csv found. Run reconcile.py (and optionally triage_exceptions.py) first.")
else:
    has_severity = "severity" in exceptions.columns

    if has_severity:
        c1, c2, c3, c4 = st.columns(4)
        counts = exceptions["severity"].value_counts()
        c1.metric("Total exceptions", f"{len(exceptions):,}")
        c2.metric("High severity", f"{int(counts.get('High', 0)):,}")
        c3.metric("Medium severity", f"{int(counts.get('Medium', 0)):,}")
        c4.metric("Low severity", f"{int(counts.get('Low', 0)):,}")

        chart_data = exceptions.groupby(["error_type", "severity"]).size().reset_index(name="count")
        order = (exceptions["error_type"].value_counts().index.tolist())
        chart = (
            alt.Chart(chart_data)
            .mark_bar()
            .encode(
                y=alt.Y("error_type:N", sort=order, title=None,
                       axis=alt.Axis(labelLimit=200, labelFontSize=12)),
                x=alt.X("count:Q", title="Exceptions"),
                color=alt.Color("severity:N", title="Severity",
                                scale=alt.Scale(domain=SEVERITY_ORDER,
                                                range=[SEVERITY_COLORS[s] for s in SEVERITY_ORDER])),
                order=alt.Order("severity:N", sort="ascending"),
                tooltip=["error_type:N", "severity:N", "count:Q"],
            )
            .properties(height=32 * chart_data["error_type"].nunique() + 40)
        )
        st.altair_chart(chart, use_container_width=True)
    else:
        st.metric("Total exceptions", f"{len(exceptions):,}")
        st.caption("Run triage_exceptions.py for severity classification and root-cause detail.")
        counts = exceptions["error_type"].value_counts().reset_index()
        counts.columns = ["error_type", "count"]
        chart = (
            alt.Chart(counts)
            .mark_bar(color=SEVERITY_COLORS["Medium"])
            .encode(
                y=alt.Y("error_type:N", sort="-x", title=None,
                       axis=alt.Axis(labelLimit=200, labelFontSize=12)),
                x=alt.X("count:Q", title="Exceptions"),
                tooltip=["error_type:N", "count:Q"],
            )
            .properties(height=32 * len(counts) + 40)
        )
        st.altair_chart(chart, use_container_width=True)

    st.subheader("Filter")
    col1, col2 = st.columns(2)
    error_types = sorted(exceptions["error_type"].unique())
    selected_types = col1.multiselect("Error type", error_types, default=error_types)
    if has_severity:
        severities = sorted(exceptions["severity"].dropna().unique())
        selected_sev = col2.multiselect("Severity", severities, default=severities)
    else:
        selected_sev = None

    filtered = exceptions[exceptions["error_type"].isin(selected_types)]
    if selected_sev is not None:
        filtered = filtered[filtered["severity"].isin(selected_sev)]

    st.dataframe(filtered, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# ESPP snapshot
# ---------------------------------------------------------------------------

st.header("ESPP snapshot")
st.caption("Employee Stock Purchase Plan activity: employees buy company stock at a 15% "
          "discount off the lower of two prices (a \"lookback\"), capped at $25k/year by IRS rule.")

if espp.empty:
    st.info("No espp_purchases.csv found. Run espp_calculator.py first.")
else:
    last_date = espp["purchase_date"].max()
    last_period = espp[espp["purchase_date"] == last_date]
    next_expected = last_date + pd.offsets.MonthEnd(6)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Last purchase period", last_date.strftime("%Y-%m-%d"))
    c2.metric("Participants (last period)", f"{last_period['employee_id'].nunique():,}")
    c3.metric("Shares purchased (last period)", f"{int(last_period['shares_purchased'].sum()):,}")
    c4.metric("Next expected purchase date", next_expected.strftime("%Y-%m-%d"))

    st.caption(
        f"Lifetime: {espp['employee_id'].nunique():,} unique participants, "
        f"{int(espp['shares_purchased'].sum()):,} shares purchased, "
        f"\\${espp['discount_value'].sum():,.0f} total discount value, "
        f"{int(espp['irs_limit_capped'].sum())} purchase events capped by the \\$25k IRS limit."
    )
