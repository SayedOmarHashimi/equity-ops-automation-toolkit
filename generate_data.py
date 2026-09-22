#!/usr/bin/env python3
"""
generate_data.py - Synthetic equity-plan data generator
=======================================================

Part of the Equity Ops Automation Toolkit.

Creates a realistic but fully synthetic population of employees, RSU grants,
and vesting events, then produces the three "source system" extracts a Stock
Administration team reconciles every quarter:

    1. HRIS extract      -> who works here, where, and when they left
    2. Broker file       -> shares released per vest (stand-in for a broker platform)
    3. Payroll file      -> taxable income and tax withheld per vest

The clean feeds agree with each other. A small, known set of errors is then
injected, and every one is logged in an answer key, so a reconciliation engine
can be scored on precision and recall.

Usage:
    python generate_data.py                     # default: 500 employees, real NVDA prices if available
    python generate_data.py --employees 2000 --seed 7
    python generate_data.py --offline           # skip yfinance, use simulated prices

All tax rates and plan rules are simplified and illustrative. Nothing here
reflects NVIDIA's actual plan terms or any real tax guidance.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------------------------


@dataclass
class Config:
    seed: int = 42
    n_employees: int = 500
    start_date: date = date(2021, 1, 1)      # earliest possible hire date
    as_of_date: date = date(2026, 9, 22)     # "today" for the dataset
    termination_rate: float = 0.15           # share of employees who leave
    transfer_rate: float = 0.06              # share who move countries
    vest_quarters: int = 16                  # 4 years of quarterly vesting
    vest_months: tuple = (3, 6, 9, 12)       # quarterly vest months
    vest_day: int = 15                       # fixed day of the vest month
    refresh_month: int = 3                   # annual refresh grant date
    refresh_day: int = 1
    error_rate: float = 0.03                 # share of vest events to corrupt
    ticker: str = "NVDA"


# Illustrative combined withholding rates (NOT real tax guidance).
WITHHOLDING_RATES = {"US": 0.40, "IN": 0.35, "IL": 0.47, "TW": 0.20, "DE": 0.45, "GB": 0.47}
COUNTRY_WEIGHTS = {"US": 0.62, "IN": 0.12, "IL": 0.10, "TW": 0.07, "DE": 0.05, "GB": 0.04}

# New-hire grant size (in split-adjusted shares) by job level.
LEVEL_BASE_SHARES = {"IC1": 400, "IC2": 800, "IC3": 1600, "IC4": 3200, "IC5": 6400}
LEVEL_WEIGHTS = [0.15, 0.35, 0.28, 0.15, 0.07]

DEPARTMENTS = [
    "GPU Architecture", "Deep Learning Software", "Networking", "Autonomous Vehicles",
    "Robotics", "Finance", "Legal", "HR", "Sales", "Marketing", "IT", "Operations",
]
FIRST_NAMES = [
    "Aarav", "Maya", "Daniel", "Priya", "Noah", "Leah", "Wei", "Sofia", "Omar", "Hannah",
    "Ravi", "Emma", "Yuki", "Lucas", "Fatima", "Ethan", "Chen", "Olivia", "Ari", "Grace",
]
LAST_NAMES = [
    "Patel", "Cohen", "Nguyen", "Garcia", "Kim", "Singh", "Muller", "Chen", "Levi", "Smith",
    "Lin", "Johnson", "Sharma", "Wang", "Rossi", "Brown", "Tanaka", "Haddad", "Lee", "Walker",
]

# Every injected error type, with a plain-English description for the answer key.
ERROR_TYPES = {
    "POST_TERM_RELEASE": "Broker released a tranche that vests after the employee's termination date (should be forfeited).",
    "DUPLICATE_RELEASE": "Broker file contains the same vest event twice under different release references.",
    "STALE_FMV_PAYROLL": "Payroll used the prior trading day's closing price instead of the vest-date FMV.",
    "WITHHOLDING_ROUNDING": "Broker rounded withheld shares down instead of up, releasing one extra share.",
    "MISSING_PAYROLL": "Vest event exists at the broker but was never reported to payroll.",
    "ID_FORMAT_MISMATCH": "Payroll employee ID is missing its 'E' prefix, so it won't join to HRIS/broker.",
    "STALE_COUNTRY": "Payroll taxed the vest in the employee's old country after an international transfer.",
}


# ---------------------------------------------------------------------------
# 2. Small helpers
# ---------------------------------------------------------------------------


def random_date(rng: np.random.Generator, lo: date, hi: date) -> date:
    """Uniform random date between lo and hi, inclusive."""
    return lo + timedelta(days=int(rng.integers(0, (hi - lo).days + 1)))


def as_date(value) -> date | None:
    """Normalize pandas missing values (None/NaN/NaT) to None."""
    return None if value is None or pd.isna(value) else value


def next_friday(d: date) -> date:
    """Payroll picks up vest income on the next pay date (modeled as next Friday)."""
    days_ahead = (4 - d.weekday()) % 7 or 7
    return d + timedelta(days=days_ahead)


def country_on(emp: pd.Series, d: date) -> str:
    """The employee's tax country on a given date, accounting for transfers."""
    transfer = as_date(emp["transfer_date"])
    if transfer is not None and d < transfer:
        return emp["prior_country"]
    return emp["country"]


# ---------------------------------------------------------------------------
# 3. Stock prices (fair market value on each vest date)
# ---------------------------------------------------------------------------


def load_prices(cfg: Config, offline: bool, rng: np.random.Generator) -> tuple[pd.Series, str]:
    """Daily closing prices. Tries real data via yfinance, falls back to a simulation."""
    start = cfg.start_date - timedelta(days=14)
    end = cfg.as_of_date + timedelta(days=1)

    if not offline:
        try:
            import yfinance as yf

            raw = yf.download(cfg.ticker, start=start.isoformat(), end=end.isoformat(),
                              progress=False, auto_adjust=False)
            close = raw["Close"]
            if isinstance(close, pd.DataFrame):          # newer yfinance returns a frame
                close = close.iloc[:, 0]
            close = close.dropna()
            if len(close) > 0:
                close.index = pd.to_datetime(close.index)
                if close.index.tz is not None:
                    close.index = close.index.tz_localize(None)
                return close.rename("close").round(4), f"yfinance ({cfg.ticker}, split-adjusted)"
        except Exception as exc:  # no internet, package missing, API change...
            print(f"[prices] yfinance unavailable ({exc}); using simulated prices.")

    # Geometric Brownian motion on business days: strong drift, high volatility.
    days = pd.bdate_range(start, end)
    mu, sigma = 0.45 / 252, 0.50 / math.sqrt(252)
    shocks = rng.normal(mu - 0.5 * sigma**2, sigma, len(days))
    close = 13.0 * np.exp(np.cumsum(shocks))
    return pd.Series(close.round(4), index=days, name="close"), "simulated (GBM)"


def fmv_on(prices: pd.Series, d: date) -> float:
    """Closing price on d, or the last trading day before d (weekends/holidays)."""
    return float(prices.asof(pd.Timestamp(d)))


def prior_trading_close(prices: pd.Series, d: date) -> float:
    """Closing price one trading day before the FMV date (used to fake a stale price)."""
    fmv_day = prices.index[prices.index <= pd.Timestamp(d)][-1]
    return float(prices[prices.index < fmv_day].iloc[-1])


# ---------------------------------------------------------------------------
# 4. Employees (HRIS)
# ---------------------------------------------------------------------------


def generate_employees(cfg: Config, rng: np.random.Generator) -> pd.DataFrame:
    countries = list(COUNTRY_WEIGHTS)
    weights = list(COUNTRY_WEIGHTS.values())
    rows = []

    for i in range(1, cfg.n_employees + 1):
        hire = random_date(rng, cfg.start_date, cfg.as_of_date - timedelta(days=90))
        country = str(rng.choice(countries, p=weights))

        # Some employees leave, but only after at least ~6 months.
        term = None
        earliest_term = hire + timedelta(days=180)
        if rng.random() < cfg.termination_rate and earliest_term < cfg.as_of_date:
            term = random_date(rng, earliest_term, cfg.as_of_date)

        # Some employees transfer to another country while employed.
        transfer, prior_country = None, country
        last_day = term or cfg.as_of_date
        earliest_transfer = hire + timedelta(days=120)
        if rng.random() < cfg.transfer_rate and earliest_transfer < last_day:
            transfer = random_date(rng, earliest_transfer, last_day)
            prior_country = country
            country = str(rng.choice([c for c in countries if c != country]))

        rows.append({
            "employee_id": f"E{i:05d}",
            "first_name": str(rng.choice(FIRST_NAMES)),
            "last_name": str(rng.choice(LAST_NAMES)),
            "department": str(rng.choice(DEPARTMENTS)),
            "level": str(rng.choice(list(LEVEL_BASE_SHARES), p=LEVEL_WEIGHTS)),
            "hire_date": hire,
            "termination_date": term,
            "status": "Terminated" if term else "Active",
            "country": country,                # current country
            "prior_country": prior_country,    # same as country unless transferred
            "transfer_date": transfer,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 5. Grants
# ---------------------------------------------------------------------------


def generate_grants(cfg: Config, employees: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    rows = []

    def add_grant(emp, grant_date: date, grant_type: str, shares: int) -> None:
        rows.append({
            "grant_id": f"G{len(rows) + 1:06d}",
            "employee_id": emp.employee_id,
            "grant_type": grant_type,
            "grant_date": grant_date,
            "shares_granted": max(shares, cfg.vest_quarters),  # at least 1 share per tranche
        })

    for emp in employees.itertuples(index=False):
        base = LEVEL_BASE_SHARES[emp.level]
        last_day = as_date(emp.termination_date) or cfg.as_of_date

        # New-hire grant on the hire date, with some negotiation noise.
        new_hire_shares = int(round(base * rng.lognormal(0, 0.25) / 10) * 10)
        add_grant(emp, emp.hire_date, "NEW_HIRE", new_hire_shares)

        # Annual refresh grants, once the employee has ~6 months of tenure.
        for year in range(emp.hire_date.year + 1, last_day.year + 1):
            refresh_date = date(year, cfg.refresh_month, cfg.refresh_day)
            if refresh_date <= last_day and (refresh_date - emp.hire_date).days >= 180:
                refresh_shares = int(round(base * rng.uniform(0.25, 0.5) / 10) * 10)
                add_grant(emp, refresh_date, "REFRESH", refresh_shares)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 6. Vesting schedule (the ground truth)
# ---------------------------------------------------------------------------


def vest_dates_after(cfg: Config, grant_date: date) -> list[date]:
    """The next N fixed quarterly vest dates strictly after the grant date."""
    out, year = [], grant_date.year
    while len(out) < cfg.vest_quarters:
        for month in cfg.vest_months:
            d = date(year, month, cfg.vest_day)
            if d > grant_date and len(out) < cfg.vest_quarters:
                out.append(d)
        year += 1
    return out


def build_vest_schedule(cfg: Config, grants: pd.DataFrame, employees: pd.DataFrame) -> pd.DataFrame:
    emp = employees.set_index("employee_id")
    rows = []

    for g in grants.itertuples(index=False):
        term = as_date(emp.at[g.employee_id, "termination_date"])
        per_tranche, remainder = divmod(g.shares_granted, cfg.vest_quarters)

        for tranche, vest_date in enumerate(vest_dates_after(cfg, g.grant_date), start=1):
            # Leftover shares from the division land in the final tranche.
            shares = per_tranche + (remainder if tranche == cfg.vest_quarters else 0)

            if term is not None and vest_date > term:
                status = "FORFEITED"
            elif vest_date <= cfg.as_of_date:
                status = "VESTED"
            else:
                status = "UNVESTED"

            rows.append({
                "grant_id": g.grant_id,
                "employee_id": g.employee_id,
                "tranche": tranche,
                "vest_date": vest_date,
                "shares": shares,
                "status": status,
            })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 7. Clean broker + payroll feeds
# ---------------------------------------------------------------------------


def event_rows(v, emp: pd.Series, prices: pd.Series, seq: int) -> tuple[dict, dict]:
    """Build the matching broker row and payroll row for one vest event."""
    fmv = fmv_on(prices, v.vest_date)
    country = country_on(emp, v.vest_date)
    rate = WITHHOLDING_RATES[country]

    # Net share settlement: withhold enough whole shares to cover tax (round UP).
    shares_withheld = math.ceil(v.shares * rate)

    broker = {
        "release_ref": f"R{seq:07d}",
        "participant_id": v.employee_id,
        "grant_id": v.grant_id,
        "tranche": v.tranche,
        "release_date": v.vest_date,
        "shares_vested": v.shares,
        "fmv_per_share": round(fmv, 4),
        "shares_withheld": shares_withheld,
        "shares_released": v.shares - shares_withheld,
    }

    taxable_income = round(v.shares * fmv, 2)
    tax_withheld = round(taxable_income * rate, 2)
    payroll = {
        "payroll_line_id": f"P{seq:07d}",
        "emp_id": v.employee_id,
        "grant_id": v.grant_id,
        "tranche": v.tranche,
        "vest_date": v.vest_date,
        "pay_date": next_friday(v.vest_date),
        "earning_code": "RSU",
        "fmv_per_share": round(fmv, 4),
        "taxable_income": taxable_income,
        "tax_country": country,
        "tax_rate": rate,
        "tax_withheld": tax_withheld,
        # Value of rounded-up withheld shares beyond the actual tax owed, refunded in payroll.
        "withholding_refund": round(shares_withheld * fmv - tax_withheld, 2),
    }
    return broker, payroll


def build_clean_feeds(schedule: pd.DataFrame, employees: pd.DataFrame,
                      prices: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    emp = employees.set_index("employee_id")
    vested = schedule[schedule["status"] == "VESTED"]
    broker_rows, payroll_rows = [], []

    for seq, v in enumerate(vested.itertuples(index=False), start=1):
        b, p = event_rows(v, emp.loc[v.employee_id], prices, seq)
        broker_rows.append(b)
        payroll_rows.append(p)

    return pd.DataFrame(broker_rows), pd.DataFrame(payroll_rows)


def sanity_check_clean(broker: pd.DataFrame, payroll: pd.DataFrame) -> None:
    """Invariants that must hold BEFORE errors are injected. Fail loudly if not."""
    assert (broker["shares_withheld"] + broker["shares_released"] == broker["shares_vested"]).all()
    assert (broker["shares_released"] >= 0).all()
    assert len(broker) == len(payroll)
    assert (broker["fmv_per_share"].values == payroll["fmv_per_share"].values).all()
    # Refund is always less than the value of one share (rounding-up leftover).
    assert (payroll["withholding_refund"] >= -0.01).all()
    assert (payroll["withholding_refund"] < payroll["fmv_per_share"] + 0.01).all()


# ---------------------------------------------------------------------------
# 8. Error injection + answer key
# ---------------------------------------------------------------------------


def inject_errors(cfg: Config, broker: pd.DataFrame, payroll: pd.DataFrame,
                  schedule: pd.DataFrame, employees: pd.DataFrame, prices: pd.Series,
                  rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    broker, payroll = broker.copy(), payroll.copy()
    emp = employees.set_index("employee_id")
    key_rows: list[dict] = []
    used: set[tuple[str, int]] = set()   # each vest event gets at most one error
    seq = len(broker) + 1                # continue reference numbering for new rows

    per_type = max(1, int(len(broker) * cfg.error_rate) // len(ERROR_TYPES))

    def pick(df: pd.DataFrame, n: int) -> pd.DataFrame:
        """Sample up to n rows whose vest event hasn't been corrupted yet."""
        mask = [(g, t) not in used for g, t in zip(df["grant_id"], df["tranche"])]
        candidates = df[mask]
        return candidates.sample(min(n, len(candidates)), random_state=int(rng.integers(1_000_000_000)))

    def log(error_type: str, system: str, row, extra: str = "") -> None:
        used.add((row["grant_id"], int(row["tranche"])))
        key_rows.append({
            "error_id": f"ERR{len(key_rows) + 1:04d}",
            "error_type": error_type,
            "source_system": system,
            "employee_id": row.get("employee_id", row.get("participant_id", row.get("emp_id"))),
            "grant_id": row["grant_id"],
            "tranche": int(row["tranche"]),
            "description": ERROR_TYPES[error_type] + (f" {extra}" if extra else ""),
        })

    # A. POST_TERM_RELEASE: broker (and payroll) process the first forfeited tranche.
    forfeited = schedule[(schedule["status"] == "FORFEITED") & (schedule["vest_date"] <= cfg.as_of_date)]
    first_forfeit = forfeited.sort_values("tranche").groupby("grant_id", as_index=False).first()
    new_b, new_p = [], []
    for _, row in pick(first_forfeit, per_type).iterrows():
        v = pd.Series(row)
        b, p = event_rows(v, emp.loc[row["employee_id"]], prices, seq)
        seq += 1
        new_b.append(b)
        new_p.append(p)
        term = emp.at[row["employee_id"], "termination_date"]
        log("POST_TERM_RELEASE", "BROKER", row, f"Terminated {term}, vest {row['vest_date']}.")
    broker = pd.concat([broker, pd.DataFrame(new_b)], ignore_index=True)
    payroll = pd.concat([payroll, pd.DataFrame(new_p)], ignore_index=True)

    # B. DUPLICATE_RELEASE: same event appears twice in the broker file.
    dupes = []
    for _, row in pick(broker, per_type).iterrows():
        dup = row.copy()
        dup["release_ref"] = f"R{seq:07d}"
        seq += 1
        dupes.append(dup)
        log("DUPLICATE_RELEASE", "BROKER", row, f"Original {row['release_ref']}, duplicate {dup['release_ref']}.")
    broker = pd.concat([broker, pd.DataFrame(dupes)], ignore_index=True)

    # C. STALE_FMV_PAYROLL: payroll priced the vest one trading day early.
    for idx, row in pick(payroll, per_type).iterrows():
        stale = round(prior_trading_close(prices, row["vest_date"]), 4)
        shares = schedule.loc[(schedule["grant_id"] == row["grant_id"]) &
                              (schedule["tranche"] == row["tranche"]), "shares"].iloc[0]
        income = round(shares * stale, 2)
        payroll.loc[idx, ["fmv_per_share", "taxable_income", "tax_withheld"]] = [
            stale, income, round(income * row["tax_rate"], 2)]
        log("STALE_FMV_PAYROLL", "PAYROLL", row, f"Used {stale} instead of {row['fmv_per_share']}.")

    # D. WITHHOLDING_ROUNDING: broker floors instead of ceils (only where it matters).
    rates = payroll.drop_duplicates(["grant_id", "tranche"]).set_index(["grant_id", "tranche"])["tax_rate"]
    broker_rate = [rates.get((g, t)) for g, t in zip(broker["grant_id"], broker["tranche"])]
    fractional = [r is not None and (s * r) % 1 > 1e-9 for s, r in zip(broker["shares_vested"], broker_rate)]
    for idx, row in pick(broker[fractional], per_type).iterrows():
        broker.loc[idx, "shares_withheld"] = row["shares_withheld"] - 1
        broker.loc[idx, "shares_released"] = row["shares_released"] + 1
        log("WITHHOLDING_ROUNDING", "BROKER", row)

    # E. MISSING_PAYROLL: vest never reaches payroll.
    missing = pick(payroll, per_type)
    for _, row in missing.iterrows():
        log("MISSING_PAYROLL", "PAYROLL", row)
    payroll = payroll.drop(index=missing.index)

    # F. ID_FORMAT_MISMATCH: "E00123" becomes "00123".
    for idx, row in pick(payroll, per_type).iterrows():
        payroll.loc[idx, "emp_id"] = row["emp_id"].lstrip("E")
        log("ID_FORMAT_MISMATCH", "PAYROLL", row)

    # G. STALE_COUNTRY: payroll ignores an international transfer.
    transferred = employees.dropna(subset=["transfer_date"]).set_index("employee_id")["transfer_date"]
    after_transfer = [
        e in transferred.index and d >= transferred[e]
        for e, d in zip(payroll["emp_id"], payroll["vest_date"])
    ]
    for idx, row in pick(payroll[after_transfer], per_type).iterrows():
        old = emp.at[row["emp_id"], "prior_country"]
        rate = WITHHOLDING_RATES[old]
        payroll.loc[idx, ["tax_country", "tax_rate", "tax_withheld"]] = [
            old, rate, round(row["taxable_income"] * rate, 2)]
        log("STALE_COUNTRY", "PAYROLL", row, f"Taxed as {old}, should be {row['tax_country']}.")

    broker = broker.sort_values(["release_date", "release_ref"]).reset_index(drop=True)
    payroll = payroll.sort_values(["pay_date", "payroll_line_id"]).reset_index(drop=True)
    return broker, payroll, pd.DataFrame(key_rows)


# ---------------------------------------------------------------------------
# 9. Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic equity-plan data.")
    parser.add_argument("--employees", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--error-rate", type=float, default=0.03)
    parser.add_argument("--offline", action="store_true", help="Skip yfinance; simulate prices.")
    parser.add_argument("--out", type=Path, default=Path("data"))
    args = parser.parse_args()

    cfg = Config(seed=args.seed, n_employees=args.employees, error_rate=args.error_rate)
    rng = np.random.default_rng(cfg.seed)
    args.out.mkdir(parents=True, exist_ok=True)

    prices, price_source = load_prices(cfg, args.offline, rng)
    employees = generate_employees(cfg, rng)
    grants = generate_grants(cfg, employees, rng)
    schedule = build_vest_schedule(cfg, grants, employees)
    broker, payroll = build_clean_feeds(schedule, employees, prices)
    sanity_check_clean(broker, payroll)
    broker, payroll, answer_key = inject_errors(cfg, broker, payroll, schedule, employees, prices, rng)

    outputs = {
        "hris_employees.csv": employees,
        "grants.csv": grants,
        "vest_schedule_truth.csv": schedule,
        "broker_releases.csv": broker,
        "payroll_rsu.csv": payroll,
        "prices.csv": prices.rename_axis("date").reset_index(),
        "_answer_key_injected_errors.csv": answer_key,
    }
    for name, df in outputs.items():
        df.to_csv(args.out / name, index=False)

    meta = {**asdict(cfg), "price_source": price_source,
            "row_counts": {k: len(v) for k, v in outputs.items()},
            "errors_by_type": answer_key["error_type"].value_counts().to_dict()}
    (args.out / "run_metadata.json").write_text(json.dumps(meta, indent=2, default=str))

    print(f"Prices: {price_source}")
    for name, df in outputs.items():
        print(f"  {name:<34} {len(df):>7,} rows")
    print("Injected errors:")
    for etype, n in meta["errors_by_type"].items():
        print(f"  {etype:<22} {n}")


if __name__ == "__main__":
    main()
