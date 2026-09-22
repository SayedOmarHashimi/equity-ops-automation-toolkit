#!/usr/bin/env python3
"""
reconcile.py - Stage 2 reconciliation engine
=============================================

Cross-checks the HRIS, broker, and payroll extracts produced by
generate_data.py (Stage 1) and flags exceptions: the same seven error
types the generator can inject, detected purely from the source data
(no access to the answer key).

Usage:
    python reconcile.py                       # reads data/, writes data/exceptions.csv
    python reconcile.py --data data --out data/exceptions.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def load_data(data_dir: Path) -> dict[str, pd.DataFrame]:
    hris = pd.read_csv(data_dir / "hris_employees.csv",
                       parse_dates=["hire_date", "termination_date", "transfer_date"])
    broker = pd.read_csv(data_dir / "broker_releases.csv", parse_dates=["release_date"])
    payroll = pd.read_csv(data_dir / "payroll_rsu.csv", parse_dates=["vest_date", "pay_date"])
    return {"hris": hris, "broker": broker, "payroll": payroll}


def flag(df: pd.DataFrame, error_type: str, system: str, id_col: str) -> pd.DataFrame:
    """Build a standard exception row set from a filtered dataframe."""
    return pd.DataFrame({
        "error_type": error_type,
        "source_system": system,
        "employee_id": df[id_col],
        "grant_id": df["grant_id"],
        "tranche": df["tranche"],
    })


def check_post_term_release(broker: pd.DataFrame, hris: pd.DataFrame) -> pd.DataFrame:
    """A broker release dated after the employee's HRIS termination date."""
    merged = broker.merge(hris[["employee_id", "termination_date"]],
                          left_on="participant_id", right_on="employee_id", how="left")
    bad = merged[merged["termination_date"].notna() & (merged["release_date"] > merged["termination_date"])]
    return flag(bad, "POST_TERM_RELEASE", "BROKER", "participant_id")


def check_duplicate_release(broker: pd.DataFrame) -> pd.DataFrame:
    """Two broker releases for the same grant and tranche."""
    dupes = broker[broker.duplicated(["grant_id", "tranche"], keep=False)]
    dupes = dupes.drop_duplicates(["grant_id", "tranche"], keep="first")
    return flag(dupes, "DUPLICATE_RELEASE", "BROKER", "participant_id")


def check_stale_fmv(broker: pd.DataFrame, payroll: pd.DataFrame) -> pd.DataFrame:
    """Payroll FMV doesn't match the broker's FMV for the same vest event."""
    broker_fmv = broker.drop_duplicates(["grant_id", "tranche"])[["grant_id", "tranche", "fmv_per_share"]]
    merged = payroll.merge(broker_fmv, on=["grant_id", "tranche"], suffixes=("_payroll", "_broker"))
    bad = merged[~np.isclose(merged["fmv_per_share_payroll"], merged["fmv_per_share_broker"], atol=0.001)]
    return flag(bad, "STALE_FMV_PAYROLL", "PAYROLL", "emp_id")


def check_withholding_rounding(broker: pd.DataFrame, payroll: pd.DataFrame,
                               stale_country: pd.DataFrame) -> pd.DataFrame:
    """Broker withheld fewer shares than ceil(shares_vested x tax_rate) implies.

    Events already flagged as STALE_COUNTRY are excluded: payroll's tax_rate
    there reflects the wrong country, so it can't be trusted to compute the
    expected withholding without producing false positives.
    """
    rates = payroll.drop_duplicates(["grant_id", "tranche"])[["grant_id", "tranche", "tax_rate"]]
    merged = broker.merge(rates, on=["grant_id", "tranche"], how="inner")
    merged = merged.merge(stale_country[["grant_id", "tranche"]], on=["grant_id", "tranche"],
                          how="left", indicator=True)
    merged = merged[merged["_merge"] == "left_only"]
    expected_withheld = np.ceil(merged["shares_vested"] * merged["tax_rate"])
    bad = merged[merged["shares_withheld"] < expected_withheld]
    return flag(bad, "WITHHOLDING_ROUNDING", "BROKER", "participant_id")


def check_missing_payroll(broker: pd.DataFrame, payroll: pd.DataFrame) -> pd.DataFrame:
    """A broker release with no matching payroll line."""
    payroll_keys = payroll[["grant_id", "tranche"]].drop_duplicates()
    merged = broker.merge(payroll_keys, on=["grant_id", "tranche"], how="left", indicator=True)
    missing = merged[merged["_merge"] == "left_only"]
    return flag(missing, "MISSING_PAYROLL", "PAYROLL", "participant_id")


def check_id_format_mismatch(payroll: pd.DataFrame, hris: pd.DataFrame) -> pd.DataFrame:
    """A payroll employee ID that doesn't exist in HRIS."""
    known_ids = set(hris["employee_id"])
    bad = payroll[~payroll["emp_id"].isin(known_ids)]
    return flag(bad, "ID_FORMAT_MISMATCH", "PAYROLL", "emp_id")


def _expected_country(row: pd.Series) -> str:
    """The employee's HRIS country on the vest date, accounting for transfers."""
    transfer = row["transfer_date"]
    if pd.notna(transfer) and row["vest_date"] < transfer:
        return row["prior_country"]
    return row["country"]


def check_stale_country(payroll: pd.DataFrame, hris: pd.DataFrame) -> pd.DataFrame:
    """Payroll taxed a vest in a country that doesn't match HRIS on the vest date."""
    merged = payroll.merge(hris[["employee_id", "country", "prior_country", "transfer_date"]],
                          left_on="emp_id", right_on="employee_id", how="inner")
    merged["expected_country"] = merged.apply(_expected_country, axis=1)
    bad = merged[merged["tax_country"] != merged["expected_country"]]
    return flag(bad, "STALE_COUNTRY", "PAYROLL", "emp_id")


def run_reconciliation(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    hris, broker, payroll = data["hris"], data["broker"], data["payroll"]

    stale_country = check_stale_country(payroll, hris)
    checks = [
        check_post_term_release(broker, hris),
        check_duplicate_release(broker),
        check_stale_fmv(broker, payroll),
        check_withholding_rounding(broker, payroll, stale_country),
        check_missing_payroll(broker, payroll),
        check_id_format_mismatch(payroll, hris),
        stale_country,
    ]
    exceptions = pd.concat(checks, ignore_index=True)
    exceptions = exceptions.sort_values(["grant_id", "tranche"]).reset_index(drop=True)
    exceptions.insert(0, "exception_id", [f"EXC{i + 1:04d}" for i in range(len(exceptions))])
    return exceptions


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile HRIS, broker, and payroll feeds.")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("data/exceptions.csv"))
    args = parser.parse_args()

    data = load_data(args.data)
    exceptions = run_reconciliation(data)
    exceptions.to_csv(args.out, index=False)

    print(f"Flagged {len(exceptions)} exceptions -> {args.out}")
    print(exceptions["error_type"].value_counts().to_string())


if __name__ == "__main__":
    main()
