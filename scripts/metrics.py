#!/usr/bin/env python
"""
metrics.py — Этап 3 (часть 1): метрики, которые НЕ входят в зону обучения.

Считает из длинной таблицы (см. normalize.py):
  1. applications_by_program — заявления по программам: бюджет / коммерц / всего.
  2. program_intersections — матрица пересечений: сколько абитуриентов подались
     в обе программы одновременно (симметричная, по числу уникальных студентов).
  3. priority_distribution — распределение приоритетов по программам
     (по каждой программе: сколько раз она была приоритетом 1, 2, 3, ...).

"Индекс интересности" сюда сознательно не входит — см. промпт задачи,
раздел 5, зона обучения №2: формулу считает и пишет автор задачи, не этот
скрипт.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def applications_by_program(df: pd.DataFrame) -> pd.DataFrame:
    counts = (
        df.groupby(["program_id", "program_name", "campus", "place_type"])
        .size()
        .unstack("place_type", fill_value=0)
    )
    for col in ("budget", "commercial"):
        if col not in counts.columns:
            counts[col] = 0
    counts["total"] = counts["budget"] + counts["commercial"]
    return counts.reset_index().sort_values("total", ascending=False)


def program_intersections(df: pd.DataFrame, place_type: str) -> pd.DataFrame:
    """N x N матрица для одного типа места: [i, j] = число уникальных
    абитуриентов, подавших заявление этого типа и в программу i, и в
    программу j (диагональ = всего заявителей этого типа в программу)."""
    subset = df[df["place_type"] == place_type]
    incidence = (
        subset[["student_id", "program_id"]]
        .drop_duplicates()
        .assign(flag=1)
        .pivot_table(index="student_id", columns="program_id", values="flag", fill_value=0)
    )
    overlap = incidence.T.dot(incidence)  # program_id x program_id
    name_map = df.drop_duplicates("program_id").set_index("program_id")["program_name"]
    overlap.index = overlap.index.map(name_map)
    overlap.columns = overlap.columns.map(name_map)
    return overlap


def priority_distribution(df: pd.DataFrame) -> pd.DataFrame:
    dist = (
        df.dropna(subset=["priority"])
        .groupby(["program_id", "program_name", "campus", "priority", "place_type"])
        .size()
        .unstack("place_type", fill_value=0)
        .reset_index()
    )
    for col in ("budget", "commercial"):
        if col not in dist.columns:
            dist[col] = 0
    dist = dist.rename(columns={"budget": "n_budget_applicants", "commercial": "n_commercial_applicants"})
    dist = dist[["program_id", "program_name", "campus", "priority", "n_budget_applicants", "n_commercial_applicants"]]
    return dist.sort_values(["program_name", "priority"])


def main():
    p = argparse.ArgumentParser(description="Этап 3: базовые метрики (без индекса интересности)")
    p.add_argument("long_table", type=Path, help="parquet из normalize.py")
    p.add_argument("-o", "--out-dir", type=Path, default=Path("data"), help="куда сохранить метрики")
    args = p.parse_args()

    df = pd.read_parquet(args.long_table)

    apps = applications_by_program(df)
    apps_path = args.out_dir / "metric_applications_by_program.csv"
    apps.to_csv(apps_path, index=False, encoding="utf-8-sig")
    print(f"applications_by_program: {len(apps)} программ -> {apps_path}")

    for place_type in ("budget", "commercial"):
        overlap = program_intersections(df, place_type)
        overlap_path = args.out_dir / f"metric_program_intersections_{place_type}.csv"
        overlap.to_csv(overlap_path, encoding="utf-8-sig")
        print(f"program_intersections_{place_type}: {overlap.shape[0]}x{overlap.shape[1]} -> {overlap_path}")

    dist = priority_distribution(df)
    dist_path = args.out_dir / "metric_priority_distribution.csv"
    dist.to_csv(dist_path, index=False, encoding="utf-8-sig")
    print(f"priority_distribution: {len(dist)} строк -> {dist_path}")


if __name__ == "__main__":
    main()
