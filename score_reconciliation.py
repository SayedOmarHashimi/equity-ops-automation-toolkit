#!/usr/bin/env python3
"""
score_reconciliation.py - Score the reconciliation engine
===========================================================

Compares data/exceptions.csv (found by reconcile.py) against
data/_answer_key_injected_errors.csv (the ground truth injected by
generate_data.py) and reports precision/recall/F1, overall and per
error type. A match requires the same (grant_id, tranche, error_type).

Usage:
    python score_reconciliation.py
    python score_reconciliation.py --exceptions data/exceptions.csv --answer-key data/_answer_key_injected_errors.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

MATCH_COLS = ["grant_id", "tranche", "error_type"]


def keys(df: pd.DataFrame) -> set[tuple]:
    return set(df[MATCH_COLS].itertuples(index=False, name=None))


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (tp and (precision + recall)) else float("nan")
    return precision, recall, f1


def score(found: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    error_types = sorted(set(truth["error_type"]) | set(found["error_type"]))
    rows = []
    for etype in error_types:
        found_keys = keys(found[found["error_type"] == etype])
        truth_keys = keys(truth[truth["error_type"] == etype])
        tp, fp, fn = len(found_keys & truth_keys), len(found_keys - truth_keys), len(truth_keys - found_keys)
        precision, recall, f1 = prf(tp, fp, fn)
        rows.append({"error_type": etype, "true_positives": tp, "false_positives": fp,
                     "false_negatives": fn, "precision": round(precision, 3),
                     "recall": round(recall, 3), "f1": round(f1, 3)})

    overall = pd.DataFrame(rows)[["true_positives", "false_positives", "false_negatives"]].sum()
    precision, recall, f1 = prf(*overall)
    rows.append({"error_type": "OVERALL", "true_positives": overall["true_positives"],
                "false_positives": overall["false_positives"], "false_negatives": overall["false_negatives"],
                "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Score reconciliation exceptions against the answer key.")
    parser.add_argument("--exceptions", type=Path, default=Path("data/exceptions.csv"))
    parser.add_argument("--answer-key", type=Path, default=Path("data/_answer_key_injected_errors.csv"))
    args = parser.parse_args()

    found = pd.read_csv(args.exceptions)
    truth = pd.read_csv(args.answer_key)

    report = score(found, truth)
    print(report.to_string(index=False))


if __name__ == "__main__":
    main()
