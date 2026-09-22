#!/usr/bin/env python3
"""
espp_calculator.py - Stage 4 ESPP purchase calculator
========================================================

Simulates a standard qualified Section 423 Employee Stock Purchase Plan on
top of the Stage 1 HRIS extract and real NVDA prices: a 24-month offering
period split into four 6-month purchase periods, a 15% lookback discount,
and the IRC Section 423(b)(8) $25,000-per-calendar-year accrual limit.

Since HRIS never carried salary data, this script also generates a synthetic
annual base salary per employee (by level, same pattern as Stage 1's grant
sizing) and a synthetic ESPP election (participate or not, contribution % of
pay) for each offering period.

All plan terms and salary figures are simplified and illustrative. Nothing
here reflects any company's actual ESPP plan or compensation.

Usage:
    python espp_calculator.py
    python espp_calculator.py --seed 7 --data data --out data/espp_purchases.csv
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class Config:
    seed: int = 42
    discount: float = 0.15                 # 15% lookback discount
    offering_years: int = 2                # 24-month offering period
    purchase_months: int = 6               # 4 purchase periods per offering
    irs_limit_usd: float = 25_000.0        # IRC 423(b)(8) accrual limit
    participation_rate: float = 0.55       # share of eligible employees who enroll each offering
    start_date: date = date(2022, 1, 1)    # first offering period start (after HRIS ramp-up)
    as_of_date: date = date(2026, 9, 22)   # "today"; purchase periods after this are excluded


# Illustrative annual base salary by level (NOT real compensation figures).
LEVEL_BASE_SALARY = {"IC1": 95_000, "IC2": 130_000, "IC3": 175_000, "IC4": 230_000, "IC5": 300_000}
CONTRIBUTION_CHOICES = [0.05, 0.10, 0.15]
CONTRIBUTION_WEIGHTS = [0.35, 0.40, 0.25]


def load_prices(data_dir: Path) -> pd.Series:
    prices = pd.read_csv(data_dir / "prices.csv", parse_dates=["date"])
    return prices.set_index("date")["close"]


def load_employees(data_dir: Path) -> pd.DataFrame:
    return pd.read_csv(data_dir / "hris_employees.csv",
                       parse_dates=["hire_date", "termination_date"])


def fmv_on(prices: pd.Series, d: date) -> float:
    """Closing price on d, or the last trading day before d (weekends/holidays)."""
    return float(prices.asof(pd.Timestamp(d)))


def build_offering_periods(cfg: Config) -> list[dict]:
    """Sequential, non-overlapping offering periods, each split into six-month
    purchase periods that land on calendar-year boundaries (Jun 30 / Dec 31),
    so the $25k accrual limit never has to be split across a purchase period."""
    periods = []
    year = cfg.start_date.year
    while date(year, 1, 1) <= cfg.as_of_date:
        offering_start = date(year, 1, 1)
        offering_end = date(year + cfg.offering_years, 1, 1) - pd.Timedelta(days=1)
        purchase_dates = []
        months_per_period = cfg.purchase_months
        n_periods = (cfg.offering_years * 12) // months_per_period
        for i in range(1, n_periods + 1):
            total_months = i * months_per_period
            py, pm = year + total_months // 12, total_months % 12
            if pm == 0:
                py, pm = py - 1, 12
            purchase_date = date(py, pm, 1) + pd.offsets.MonthEnd(0)
            purchase_date = purchase_date.date()
            if purchase_date <= cfg.as_of_date:
                purchase_dates.append(purchase_date)
        if purchase_dates:
            periods.append({"offering_start": offering_start, "offering_end": offering_end,
                            "purchase_dates": purchase_dates})
        year += cfg.offering_years
    return periods


def generate_salaries(employees: pd.DataFrame, rng: np.random.Generator) -> pd.Series:
    base = employees["level"].map(LEVEL_BASE_SALARY)
    noise = rng.lognormal(0, 0.12, len(employees))
    salary = (base * noise / 1000).round() * 1000
    return pd.Series(salary.values, index=employees["employee_id"], name="salary")


def eligible(emp: pd.Series, offering_start: date, purchase_date: date) -> bool:
    if emp["hire_date"].date() > offering_start:
        return False
    term = emp["termination_date"]
    if pd.notna(term) and term.date() <= purchase_date:
        return False
    return True


def compute_purchases(cfg: Config, employees: pd.DataFrame, salaries: pd.Series,
                      prices: pd.Series, offerings: list[dict],
                      rng: np.random.Generator) -> pd.DataFrame:
    emp_idx = employees.set_index("employee_id")
    rows = []

    for offering in offerings:
        offering_start = offering["offering_start"]
        offering_start_fmv = fmv_on(prices, offering_start)
        annual_share_limit = math.floor(cfg.irs_limit_usd / offering_start_fmv)

        eligible_ids = [e for e in emp_idx.index if eligible(emp_idx.loc[e], offering_start, offering_start)]
        participating = {
            e: float(rng.choice(CONTRIBUTION_CHOICES, p=CONTRIBUTION_WEIGHTS))
            for e in eligible_ids if rng.random() < cfg.participation_rate
        }

        shares_ytd: dict[tuple[str, int], int] = {}

        for purchase_date in offering["purchase_dates"]:
            purchase_fmv = fmv_on(prices, purchase_date)
            lookback_fmv = min(offering_start_fmv, purchase_fmv)
            purchase_price = round(lookback_fmv * (1 - cfg.discount), 4)

            for employee_id, contribution_pct in participating.items():
                emp = emp_idx.loc[employee_id]
                if not eligible(emp, offering_start, purchase_date):
                    continue

                contribution = round(float(salaries[employee_id]) * contribution_pct * (cfg.purchase_months / 12), 2)
                tentative_shares = math.floor(contribution / purchase_price)

                year_key = (employee_id, purchase_date.year)
                remaining_allowance = annual_share_limit - shares_ytd.get(year_key, 0)
                shares = max(0, min(tentative_shares, remaining_allowance))
                shares_ytd[year_key] = shares_ytd.get(year_key, 0) + shares

                purchase_cost = round(shares * purchase_price, 2)
                market_value = round(shares * purchase_fmv, 2)
                rows.append({
                    "employee_id": employee_id,
                    "offering_start": offering_start,
                    "purchase_date": purchase_date,
                    "salary": salaries[employee_id],
                    "contribution_pct": contribution_pct,
                    "contribution": contribution,
                    "offering_start_fmv": round(offering_start_fmv, 4),
                    "purchase_date_fmv": round(purchase_fmv, 4),
                    "lookback_fmv": round(lookback_fmv, 4),
                    "purchase_price": purchase_price,
                    "shares_purchased": shares,
                    "purchase_cost": purchase_cost,
                    "market_value_at_purchase": market_value,
                    "discount_value": round(market_value - purchase_cost, 2),
                    "cash_refunded": round(contribution - purchase_cost, 2),
                    "irs_limit_capped": tentative_shares > shares,
                })

    return pd.DataFrame(rows).sort_values(["purchase_date", "employee_id"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate ESPP purchases (lookback, discount, $25k limit).")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("data/espp_purchases.csv"))
    args = parser.parse_args()

    cfg = Config(seed=args.seed)
    rng = np.random.default_rng(cfg.seed)

    employees = load_employees(args.data)
    prices = load_prices(args.data)
    salaries = generate_salaries(employees, rng)
    offerings = build_offering_periods(cfg)
    purchases = compute_purchases(cfg, employees, salaries, prices, offerings, rng)

    purchases.to_csv(args.out, index=False)

    print(f"Offering periods: {len(offerings)}, purchase events: {sum(len(o['purchase_dates']) for o in offerings)}")
    print(f"Purchase rows: {len(purchases)} -> {args.out}")
    print(f"Participants (unique employees): {purchases['employee_id'].nunique()}")
    print(f"Total shares purchased: {purchases['shares_purchased'].sum():,}")
    print(f"Total purchase cost: ${purchases['purchase_cost'].sum():,.2f}")
    print(f"Total discount value: ${purchases['discount_value'].sum():,.2f}")
    print(f"Total cash refunded (IRS-limit / rounding leftover): ${purchases['cash_refunded'].sum():,.2f}")
    print(f"Purchase events capped by the $25k IRS limit: {int(purchases['irs_limit_capped'].sum())}")


if __name__ == "__main__":
    main()
