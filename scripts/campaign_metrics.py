#!/usr/bin/env python
"""
campaign_metrics.py — слой метрик приёмной кампании (M1-M9) поверх
нормализованной длинной таблицы (admissions_long_*.parquet) и данных о
местах (supply_demand_clean.csv).

Модуль-дополнение к основному пайплайну сбора/обработки. Метрики
пересчитываются заново на каждом срезе, не кэшируются между днями.

Все метрики per-program. Там, где сказано "budget/commercial раздельно" —
две колонки, не сумма (приоритеты в budget и commercial нумеруются
независимо, см. §1(в) — смешивать их в одну шкалу нельзя).
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# Порог "серьёзной" заявки (приоритет <= K_SERIOUS). Захардкожено по опыту
# заказчика — перепроверить по баллам портфолио/вступительных испытаний,
# когда конкурс пройдёт и баллы появятся.
K_SERIOUS = 2

MIN_N_FOR_VARIANCE_ESTIMATION = 10  # k оценивается только на программах с n>=10,
                                     # но применяется потом ко всем budget-программам


def validate_inputs(df: pd.DataFrame, supply: pd.DataFrame) -> None:
    bad_types = set(df["place_type"].unique()) - {"budget", "commercial"}
    assert not bad_types, f"в длинной таблице найден place_type кроме budget/commercial: {bad_types}"

    bad_campuses = set(df["campus"].unique()) - {"Москва", "НИУ ВШЭ - Санкт-Петербург"}
    assert not bad_campuses, f"найден кампус кроме Москвы/СПб: {bad_campuses}"

    missing = set(df["program_id"].unique()) - set(supply["program_id"].astype(str))
    assert not missing, f"program_id из admissions_long отсутствуют в supply_demand_clean: {missing}"


def _program_base(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df[["program_id", "program_name", "campus"]]
        .drop_duplicates("program_id")
        .set_index("program_id")
    )


def compute_m1_m2_m3(df: pd.DataFrame) -> pd.DataFrame:
    """M1 (заявления) и M3 (серьёзный объём: priority <= K_SERIOUS) —
    budget/commercial раздельно, плюс total для M1.

    M2 (уникальные абитуриенты) считается ОДИН раз на программу, по обоим
    типам места вместе — не budget/commercial раздельно. Раздельный вариант
    (n_unique_applicants_budget/commercial) буквально дублировал
    n_applications_budget/commercial: в данных нет ни одной повторной строки
    (student_id, program_id, place_type), поэтому "число заявок" и "число
    уникальных абитуриентов" ВНУТРИ одного типа места — всегда одно и то же
    число. Различие появляется только на уровне программы в целом: человек
    может подать и на бюджет, и на коммерцию одной программы — тогда он
    учтён в обеих заявках (n_applications_total), но один раз в
    n_unique_applicants_total."""
    base = _program_base(df)

    parts = []
    for place_type in ("budget", "commercial"):
        sub = df[df["place_type"] == place_type]
        m1 = sub.groupby("program_id").size().rename(f"n_applications_{place_type}")
        m3 = (
            sub[sub["priority"] <= K_SERIOUS]
            .groupby("program_id")
            .size()
            .rename(f"n_serious_{place_type}")
        )
        parts.append(pd.concat([m1, m3], axis=1))

    m2_total = df.groupby("program_id")["student_id"].nunique().rename("n_unique_applicants_total")

    out = base.join(parts[0]).join(parts[1]).join(m2_total).fillna(0)
    count_cols = [c for c in out.columns if c not in ("program_name", "campus")]
    out[count_cols] = out[count_cols].astype(int)
    out["n_applications_total"] = out["n_applications_budget"] + out["n_applications_commercial"]
    return out.reset_index()


def compute_m5_m6(m1_m2_m3: pd.DataFrame, supply: pd.DataFrame) -> pd.DataFrame:
    """M5 (конкурс на место) и M6 (давление серьёзного спроса на бюджетные
    места). mest == 0 или неизвестно (NaN, см. match_supply_demand.py про
    программы-«близнецы» с неоднозначным commercial_places) -> NaN, не 0
    ("нет данных" != "нулевой интерес").

    M6 (давление серьёзного спроса, priority <= K_SERIOUS) считается только
    для бюджета — это в оригинале метрика риска недобора бюджетных мест,
    для платных мест такого институционального риска в том же смысле нет.
    M5 (простой конкурс: заявок / мест) считается для ОБОИХ типов места —
    для сравнения "плотности конкурса" между бюджетом и платными местами."""
    supply = supply.copy()
    supply["program_id"] = supply["program_id"].astype(str)
    merged = m1_m2_m3.merge(
        supply[["program_id", "budget_places", "commercial_places"]], on="program_id", how="left"
    )

    # .fillna(False): budget_places/commercial_places теперь Int64 (nullable) —
    # сравнение "> 0" с NA даёт pd.NA, а не False, и np.where падает на такой
    # булевой массиве ("boolean value of NA is ambiguous"). NA мест = мест
    # неизвестно => по смыслу то же самое, что и "мест нет", для целей этих
    # отношений (в обоих случаях результат должен быть NaN).
    has_budget_places = (merged["budget_places"] > 0).fillna(False)
    merged["competition_ratio_budget"] = np.where(
        has_budget_places, merged["n_applications_budget"] / merged["budget_places"], np.nan
    )
    merged["demand_pressure_budget"] = np.where(
        has_budget_places, merged["n_serious_budget"] / merged["budget_places"], np.nan
    )
    merged["no_budget_places"] = ~has_budget_places

    has_commercial_places = (merged["commercial_places"] > 0).fillna(False)
    merged["competition_ratio_commercial"] = np.where(
        has_commercial_places, merged["n_applications_commercial"] / merged["commercial_places"], np.nan
    )
    merged["no_commercial_places"] = ~has_commercial_places
    return merged


def compute_m4(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """M4: рейтинг желанности (только budget) со сжатием к общему среднему p0
    (эмпирический Байес, метод моментов для оценки силы сжатия k).
    Разобрано построчно и подтверждено заказчиком — см. docs/METRICS.md."""
    budget = df[df["place_type"] == "budget"]

    per_program = budget.groupby("program_id").agg(
        cnt_p1=("priority", lambda s: (s == 1).sum()),
        n=("priority", "size"),
    )

    p0 = per_program["cnt_p1"].sum() / per_program["n"].sum()

    share_p1 = per_program["cnt_p1"] / per_program["n"]
    big = per_program[per_program["n"] >= MIN_N_FOR_VARIANCE_ESTIMATION]
    share_p1_big = share_p1.loc[big.index]

    obs_var = share_p1_big.var(ddof=1)
    samp_var = (share_p1_big * (1 - share_p1_big) / big["n"]).mean()
    true_var = max(obs_var - samp_var, 1e-6)
    if obs_var <= samp_var:
        print(f"⚠ M4: вся наблюдаемая вариация — выборочный шум (obs_var={obs_var:.5f} <= samp_var={samp_var:.5f}); "
              f"true_var принудительно занижен до {true_var}, k уйдёт вверх — межпрограммного сигнала не обнаружено.")

    k = max(p0 * (1 - p0) / true_var - 1, 1.0)

    per_program["desirability"] = (per_program["cnt_p1"] + k * p0) / (per_program["n"] + k)
    per_program["low_data"] = per_program["n"] < MIN_N_FOR_VARIANCE_ESTIMATION

    base = _program_base(df)
    result = base.join(per_program, how="left")  # программы без budget-заявок -> NaN, не 0

    meta = {
        "k": k,
        "p0": p0,
        "obs_var": obs_var,
        "samp_var": samp_var,
        "true_var": true_var,
        "n_programs_used_for_variance": int(len(big)),
        "n_programs_with_budget_data": int(len(per_program)),
    }
    return result.reset_index(), meta


def compute_m9_diagnostic(df: pd.DataFrame) -> pd.DataFrame:
    """M9 (диагностика, не для основного дашборда): доля budget-заявок с
    priority > K_SERIOUS — "шумовая" доля неприоритетных заявок."""
    base = _program_base(df)
    sub = df[df["place_type"] == "budget"]
    g = sub.groupby("program_id").agg(
        n_budget=("student_id", "size"),
        n_noisy=("priority", lambda s: (s > K_SERIOUS).sum()),
    )
    g["noise_share_budget"] = np.where(g["n_budget"] > 0, g["n_noisy"] / g["n_budget"], np.nan)
    return base.join(g).dropna(subset=["n_budget"]).reset_index()


def main():
    p = argparse.ArgumentParser(description="Метрики приёмной кампании (M1-M6, M9)")
    p.add_argument("long_table", type=Path)
    p.add_argument("supply_demand_csv", type=Path)
    p.add_argument("--snapshot-date", type=str, default=None, help="дата среза (по умолчанию — сегодня)")
    p.add_argument("-o", "--out-dir", type=Path, default=Path("data"))
    args = p.parse_args()

    df = pd.read_parquet(args.long_table)
    # dtype Int64 (nullable) явно — иначе pd.read_csv увидит пропуски в
    # commercial_places (см. match_supply_demand.py) и молча приведёт всю
    # колонку к float64 ("25.0" вместо "25").
    supply = pd.read_csv(args.supply_demand_csv, dtype={"budget_places": "Int64", "commercial_places": "Int64"})

    validate_inputs(df, supply)
    print(f"Валидация §1(а-в) пройдена. Программ: {df['program_id'].nunique()}, заявлений: {len(df)}")

    m1_m2_m3 = compute_m1_m2_m3(df)
    m5_m6 = compute_m5_m6(m1_m2_m3, supply)
    m4, m4_meta = compute_m4(df)
    m9 = compute_m9_diagnostic(df)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    # --- основная таблица: M1-M3, M4, M5-M6, флаг "нет бюджетных мест" ---
    # m4["n"] не берём: это то же самое n_applications_budget из M1 (проверено —
    # совпадает на всех 125 программах с budget-заявками, расходится только
    # в способе показать "нет данных": 0 в M1 против NaN в M4 для программ
    # без единой budget-заявки). Дублировать под другим именем незачем.
    main_table = m5_m6.merge(
        m4[["program_id", "cnt_p1", "desirability", "low_data"]],
        on="program_id", how="left",
    ).rename(columns={"cnt_p1": "n_p1_budget"})

    # (б) отдельная отсортированная витрина желанности — ТОЛЬКО программы
    #     с бюджетными местами; без мест = "не определено", не ноль и не хвост рейтинга.
    showcase = main_table[~main_table["no_budget_places"]].copy()
    showcase = showcase.sort_values("desirability", ascending=False)
    showcase["desirability_rank"] = range(1, len(showcase) + 1)
    no_places_list = main_table[main_table["no_budget_places"]][["program_id", "program_name", "campus"]].copy()

    main_path = args.out_dir / "campaign_metrics_main.csv"
    main_table.to_csv(main_path, index=False, encoding="utf-8-sig")
    print(f"\n(main) M1-M6 по всем программам -> {main_path} ({len(main_table)} строк)")

    showcase_path = args.out_dir / "campaign_metrics_m4_desirability_ranked.csv"
    showcase[["desirability_rank", "program_id", "program_name", "campus",
              "n_p1_budget", "n_applications_budget", "desirability", "low_data"]].to_csv(
        showcase_path, index=False, encoding="utf-8-sig"
    )
    print(f"(б) Витрина рейтинга желанности (только с бюджетными местами) -> {showcase_path} ({len(showcase)} строк)")

    no_places_path = args.out_dir / "campaign_metrics_no_budget_places.csv"
    no_places_list.to_csv(no_places_path, index=False, encoding="utf-8-sig")
    print(f"    Отдельный список без бюджетных мест (вне рейтинга) -> {no_places_path} ({len(no_places_list)} программ)")

    m9_path = args.out_dir / "campaign_metrics_m9_diagnostic.csv"
    m9.to_csv(m9_path, index=False, encoding="utf-8-sig")
    print(f"(M9, диагностика, ПОСТ-КАМПАНИЯ, вне основного дашборда) -> {m9_path}")

    # --- метаданные в заголовок (JSON-компаньон к main_table, т.к. CSV не
    #     умеет хранить метаданные вперемешку с табличными данными) ---
    meta = {
        "snapshot_date": args.snapshot_date or date.today().isoformat(),
        "n_programs": int(df["program_id"].nunique()),
        "K_SERIOUS": K_SERIOUS,
        "M4_k": m4_meta["k"],
        "M4_p0": m4_meta["p0"],
        "M4_n_programs_used_for_variance": m4_meta["n_programs_used_for_variance"],
        "M4_n_programs_with_budget_data": m4_meta["n_programs_with_budget_data"],
    }
    meta_path = args.out_dir / "campaign_metrics_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nМетаданные -> {meta_path}")
    print(json.dumps(meta, ensure_ascii=False, indent=2))

    n_no_places = int(main_table["no_budget_places"].sum())
    print(f"\nПрограмм без бюджетных мест (M4/M5/M6 не определены, вынесены отдельно): {n_no_places}")
    print("M7 (распределение приоритетов) и M8 (матрицы пересечений budget/commercial) — см. существующие")
    print("  metric_priority_distribution.csv, metric_program_intersections_budget.csv, "
          "metric_program_intersections_commercial.csv (scripts/metrics.py).")


if __name__ == "__main__":
    main()
