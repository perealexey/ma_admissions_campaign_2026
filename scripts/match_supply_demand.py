#!/usr/bin/env python
"""
match_supply_demand.py — сопоставление таблиц мест приёма (КЦП: бюджет /
целевая квота / платные / платные иностранные) со спросом
(metric_applications_by_program.csv) по названию программы.

Источник мест приёма — RTF-файлы (moscow_supply.rtf, stpeter_supply.rtf),
экспортированные из HSE. RTF-таблица построена иерархически: строки
дисциплин ("01.00.00 Математика и механика") и направлений ("Направление
подготовки 01.04.01 Математика") — это агрегаты (суммы по всем программам
внутри), а не сами программы; отличить их от настоящих программ по тексту
нельзя — только по признаку, что название настоящей программы обёрнуто в
гиперссылку на страницу конкретной программы. Поэтому:

  1. Конвертируем .rtf в .html через `textutil` (сохраняет <a href> и
     корректно декодирует юникод — надёжнее, чем ручной разбор RTF-эскейпов
     или разбор plain-текста по считанным заранее правилам).
  2. Строкой программы считаем только <tr>, где ячейка с названием
     содержит <a> (гиперссылку). Агрегатные строки (без ссылки) пропускаем.
  3. В строке 5 ячеек: название | бюджетные | целевая квота | платные |
     платные иностр. "-" -> 0.

Итог ручной вычитки расхождений (закодирован ниже, чтобы связка была
воспроизводимой и проверяемой, а не «поправлена руками в CSV»):

  * Программы-«близнецы». Четыре имени встречаются в supply дважды и в demand
    тоже имеют по ДВА разных program_id (два отдельных конкурсных списка —
    подтверждено по magstats.html). Это НЕ дубликат: сливать их в один
    program_id было бы ошибочным слиянием. Обе записи сохраняются. При этом
    budget_places в обеих supply-строках каждой группы одинаков, поэтому
    каждый из двух program_id получает однозначное значение мест.
  * Расхождения имён из-за суффикса "/ English", "(онлайн программа)",
    "(очно-заочное обучение)", регистра — снимаются normalize_name().
  * Опечатки в самом RTF и аббревиатура ЦПМ — снимаются карту псевдонимов
    SUPPLY_TO_DEMAND_ALIAS (каждая пара проверена как 1:1).
  * budget_places нигде не превышает 100 (максимум 65); «баг с 200» из
    прежнего парсинга не воспроизводится — колонки не съезжают, т.к. читаем
    таблицу по <tr>/<td>.
  * Три supply-строки остаются без пары в demand — все объяснены в
    KNOWN_SUPPLY_ONLY (нет опубликованных отчётов / очно-заочная форма).
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
from bs4 import BeautifulSoup

SUSPICIOUS_BUDGET_THRESHOLD = 100
FUZZY_MATCH_THRESHOLD = 0.75


def normalize_name(name: str) -> str:
    """Убирает типичные причины несовпадения: суффикс "/ English name",
    суффикс "(онлайн программа)"/"(очно-заочное обучение)" и т.п., регистр."""
    name = re.split(r"\s*/\s*", name)[0]  # "рус / eng" -> "рус"
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)  # убрать "(...)" в конце
    name = re.sub(r"\s+", " ", name).strip().lower()
    return name


def rtf_to_html(rtf_path: Path) -> Path:
    html_path = rtf_path.with_suffix(".html")
    subprocess.run(
        ["textutil", "-convert", "html", str(rtf_path), "-output", str(html_path)],
        check=True,
    )
    return html_path


def parse_supply_rtf(rtf_path: Path, campus: str) -> pd.DataFrame:
    html_path = rtf_to_html(rtf_path)
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")

    rows = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 5:
            continue  # не строка данных программы/агрегата (например, заголовок с объединёнными ячейками)

        link = tds[0].find("a")
        if link is None:
            continue  # агрегатная строка (дисциплина/направление) — не настоящая программа

        program_name = link.get_text(strip=True)
        program_url = link.get("href", "").strip()

        def cell_value(td) -> int:
            text = td.get_text(strip=True)
            if text in ("", "-", "–", "—"):
                return 0
            return int(re.sub(r"\D", "", text) or 0)

        rows.append(
            {
                "campus": campus,
                "program_name_supply": program_name,
                "program_url": program_url,
                "budget_places": cell_value(tds[1]),
                "target_quota_places": cell_value(tds[2]),
                "commercial_places": cell_value(tds[3]),
                "foreign_commercial_places": cell_value(tds[4]),
            }
        )

    return pd.DataFrame(rows)


# --- Курируемая карта псевдонимов -------------------------------------------
# Заполнена по итогам ручной вычитки расхождений. Ключ — нормализованное имя
# из supply (RTF), значение — нормализованное имя из demand. Нужна только там,
# где normalize_name() не устраняет расхождение: опечатки в самом RTF и
# аббревиатура. Каждая пара проверена как однозначная 1:1 (см. разбор ниже).
SUPPLY_TO_DEMAND_ALIAS = {
    # опечатки в исходном RTF ВШЭ:
    "технологии искусственного интеллекта в телекомуникациях":
        "технологии искусственного интеллекта в телекоммуникациях",
    "управление в креативных индсутриях":
        "управление в креативных индустриях",
    "экономика и бизнес консалтинг":
        "экономика и бизнес-консалтинг",
    # аббревиатура (ЦПМ = Центр педагогического мастерства):
    "совместная магистратура ниу вшэ и центра педагогического мастерства":
        "совместная магистратура ниу вшэ и цпм",
}

# --- Программы из supply, у которых заведомо НЕТ пары в demand ---------------
# Проверено по исходному magstats.html: у этих программ на странице статистики
# нет ни одной ссылки на xlsx-отчёт (Budget/Commercial/ForeignCommercial),
# либо это очно-заочная форма, которую пайплайн не забирает (берём только
# FullTime). Отсутствие пары здесь — норма, а не ошибка сопоставления.
KNOWN_SUPPLY_ONLY = {
    ("Москва", "управление в высшем образовании"):
        "в magstats.html есть в списке, но без ссылок на отчёты — заявок нет/не публикуются",
    ("Москва", "управление образованием"):
        "в magstats.html есть в списке, но без ссылок на отчёты — заявок нет/не публикуются",
    ("НИУ ВШЭ - Санкт-Петербург", "управление образованием"):
        "очно-заочная форма (out of scope: пайплайн берёт только FullTime)",
}


def supply_key(name: str) -> str:
    """Нормализованный ключ для supply-имени, с учётом карты псевдонимов."""
    norm = normalize_name(name)
    return SUPPLY_TO_DEMAND_ALIAS.get(norm, norm)


def match(supply: pd.DataFrame, demand: pd.DataFrame):
    """Сопоставляет supply и demand по нормализованному имени + кампусу.

    Возвращает (clean, residual, checks) — где clean это program_id ->
    budget_places для ВСЕХ программ спроса, residual — supply-строки без пары,
    checks — результаты санити-проверок для отчёта.
    """
    supply = supply.copy()
    demand = demand.copy()
    supply["key"] = supply["program_name_supply"].map(supply_key)
    demand["key"] = demand["program_name"].map(normalize_name)

    checks = {}

    # (1) Санити-чек величины: бюджетные места почти всегда < 100.
    checks["suspicious_budget"] = supply[supply["budget_places"] > SUSPICIOUS_BUDGET_THRESHOLD][
        ["campus", "program_name_supply", "budget_places", "commercial_places"]
    ]

    # (2) Одно supply-имя (в рамках кампуса) может встречаться дважды — это
    #     программы-«близнецы»: в demand им ТОЖЕ соответствуют два разных
    #     program_id (два отдельных конкурсных списка). Это не ошибка и не
    #     повод сливать. Схлопываем supply к одной строке на (campus, key),
    #     но budget_places и commercial_places — КАЖДЫЙ ОТДЕЛЬНО — берём
    #     только если он одинаков во всех строках группы; иначе NaN, а не
    #     "первое попавшееся" (найден случай: у «Международный спортивный
    #     менеджмент...» budget_places совпадает в обеих строках, а
    #     commercial_places — нет, 40 против 20, из какой строки какому
    #     program_id это относится — неизвестно).
    def collapse_if_unique(s: pd.Series):
        u = s.unique()
        return u[0] if len(u) == 1 else np.nan

    grp = supply.groupby(["campus", "key"])
    ambiguous_budget = grp["budget_places"].nunique()
    ambiguous_commercial = grp["commercial_places"].nunique()
    checks["ambiguous_budget_groups"] = ambiguous_budget[ambiguous_budget > 1]
    checks["ambiguous_commercial_groups"] = ambiguous_commercial[ambiguous_commercial > 1]
    supply_collapsed = grp.agg(
        budget_places=("budget_places", collapse_if_unique),
        commercial_places=("commercial_places", collapse_if_unique),
        n_supply_rows=("budget_places", "size"),
        program_name_supply=("program_name_supply", "first"),
        program_url=("program_url", "first"),
    ).reset_index()

    # (3) Кросс-кампусная защита: join строго по (campus, key), поэтому
    #     московская программа не может сцепиться с питерской.
    merged = demand.merge(
        supply_collapsed[["campus", "key", "budget_places", "commercial_places", "program_name_supply"]],
        on=["campus", "key"],
        how="left",
        indicator=True,
    )

    checks["unmatched_demand"] = merged[merged["_merge"] == "left_only"][
        ["program_id", "program_name", "campus", "budget", "commercial"]
    ]

    clean = merged[
        ["program_id", "program_name", "campus", "budget", "commercial", "budget_places", "commercial_places"]
    ].copy()
    clean = clean.rename(columns={"budget": "n_budget_applicants", "commercial": "n_commercial_applicants"})
    # Int64 (nullable), не float: budget_places всегда целые, но
    # commercial_places содержит NaN (см. ambiguous_commercial_groups выше) —
    # обычный int64 не умеет хранить NaN, из-за чего pandas молча приводит
    # всю колонку к float64 и в таблицах появляется "25.0" вместо "25".
    clean["budget_places"] = clean["budget_places"].astype("Int64")
    clean["commercial_places"] = clean["commercial_places"].astype("Int64")

    # (4) Остаток supply без пары в demand — и проверка, что каждый такой
    #     случай заранее известен и объяснён (KNOWN_SUPPLY_ONLY).
    matched_keys = set(zip(demand["campus"], demand["key"]))
    residual = supply_collapsed[
        ~supply_collapsed.apply(lambda r: (r["campus"], r["key"]) in matched_keys, axis=1)
    ].copy()
    residual["reason"] = residual.apply(
        lambda r: KNOWN_SUPPLY_ONLY.get((r["campus"], r["key"]), "НЕИЗВЕСТНАЯ причина — требует ручной проверки"),
        axis=1,
    )
    checks["unexpected_supply_only"] = residual[residual["reason"].str.startswith("НЕИЗВЕСТНАЯ")]

    return clean, residual, checks


def main():
    p = argparse.ArgumentParser(description="Сопоставление мест приёма (RTF) со спросом (metric_applications_by_program.csv)")
    p.add_argument("moscow_rtf", type=Path)
    p.add_argument("stpeter_rtf", type=Path)
    p.add_argument("demand_csv", type=Path)
    p.add_argument("-o", "--out-dir", type=Path, default=Path("data"))
    args = p.parse_args()

    supply_moscow = parse_supply_rtf(args.moscow_rtf, "Москва")
    supply_spb = parse_supply_rtf(args.stpeter_rtf, "НИУ ВШЭ - Санкт-Петербург")
    supply = pd.concat([supply_moscow, supply_spb], ignore_index=True)
    print(f"Строк-программ (с гиперссылкой) в supply: Москва={len(supply_moscow)}, СПб={len(supply_spb)}, всего={len(supply)}")

    demand = pd.read_csv(args.demand_csv)
    print(f"Программ в demand: {len(demand)}")

    clean, residual, checks = match(supply, demand)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # --- Санити-проверки: любая непустая = потенциальная ошибка слияния/разделения ---
    problems = 0
    if len(checks["suspicious_budget"]):
        problems += 1
        print(f"\n⚠ budget_places > {SUSPICIOUS_BUDGET_THRESHOLD} (подозрительно для бюджета):")
        print(checks["suspicious_budget"].to_string(index=False))
    if len(checks["ambiguous_budget_groups"]):
        problems += 1
        print("\n⚠ группы-«близнецы» с РАЗНЫМ budget_places (нельзя схлопнуть автоматически):")
        print(checks["ambiguous_budget_groups"].to_string())
    if len(checks["ambiguous_commercial_groups"]):
        problems += 1
        print("\n⚠ группы-«близнецы» с РАЗНЫМ commercial_places (budget_places решён, "
              "commercial_places -> NaN, т.к. неизвестно, какое значение к какому program_id относится):")
        print(checks["ambiguous_commercial_groups"].to_string())
    if len(checks["unmatched_demand"]):
        problems += 1
        print(f"\n⚠ программы спроса без пары в местах приёма: {len(checks['unmatched_demand'])}")
        print(checks["unmatched_demand"].to_string(index=False))
    if len(checks["unexpected_supply_only"]):
        problems += 1
        print("\n⚠ supply-строки без пары и БЕЗ известной причины (требуют вычитки):")
        print(checks["unexpected_supply_only"][["campus", "program_name_supply", "budget_places"]].to_string(index=False))

    # --- (а) чистая таблица: budget_places для всех программ спроса ---
    clean_path = args.out_dir / "supply_demand_clean.csv"
    clean.to_csv(clean_path, index=False, encoding="utf-8-sig")
    matched_n = clean["budget_places"].notna().sum()
    print(f"\n(а) Чистая таблица: {len(clean)} программ спроса, из них с budget_places: {matched_n} -> {clean_path}")

    # --- (б) остаток supply без пары (для сведения, с объяснением причины) ---
    residual_path = args.out_dir / "supply_demand_residual.csv"
    residual[["campus", "program_name_supply", "program_url", "budget_places", "commercial_places", "reason"]].to_csv(
        residual_path, index=False, encoding="utf-8-sig"
    )
    print(f"(б) Supply без пары (объяснённые): {len(residual)} -> {residual_path}")

    if problems == 0:
        print("\n✓ Все санити-проверки чисты: нет подозрительных величин, нет неоднозначных близнецов,")
        print("  все 178 программ спроса получили budget_places, все supply-остатки объяснены.")
    else:
        print(f"\n✗ Обнаружено проблемных проверок: {problems} — см. предупреждения выше.")


if __name__ == "__main__":
    main()
