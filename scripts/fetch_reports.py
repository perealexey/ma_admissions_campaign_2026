#!/usr/bin/env python
"""
fetch_reports.py — Этап 1: скачивание xlsx-отчётов по списку из parse_toc.py.

Жёсткое ограничение (см. промпт задачи, раздел 1): заход на сайт priem47.hse.ru
не чаще одного раза в сутки, без циклов с повторными заходами и без параллельных
запросов. Этот скрипт запускается вручную и один раз выполняет один
последовательный проход по списку с паузами между запросами.

Отсутствие файла (HTTP 404) — норма: у программы может быть только один из
типов мест (Budget/Commercial). Такие случаи логируются и пропускаются,
скрипт не падает.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date
from pathlib import Path

import requests

BASE = "https://priem47.hse.ru"
PAUSE_SECONDS = 2.0


def load_targets(csv_path: Path) -> list[dict]:
    with csv_path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def fetch_one(session: requests.Session, url: str, dest: Path) -> str:
    """Возвращает 'ok' / 'missing' / 'error'."""
    try:
        resp = session.get(url, timeout=30)
    except requests.RequestException as e:
        return f"error ({e})"

    if resp.status_code == 404:
        return "missing"
    if resp.status_code != 200:
        return f"error (HTTP {resp.status_code})"

    dest.write_bytes(resp.content)
    return "ok"


def main():
    p = argparse.ArgumentParser(description="Этап 1: скачивание xlsx-отчётов")
    p.add_argument("targets_csv", type=Path, help="CSV со списком из parse_toc.py")
    p.add_argument(
        "--only",
        type=Path,
        default=None,
        help="файл со списком program_id (по одному в строке) — скачать только их; "
        "если не задан, скачиваются все строки targets_csv",
    )
    p.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("raw"),
        help="корневая папка для сырья (по умолчанию ./raw)",
    )
    args = p.parse_args()

    targets = load_targets(args.targets_csv)

    if args.only:
        wanted_ids = {line.strip() for line in args.only.read_text(encoding="utf-8").splitlines() if line.strip()}
        targets = [t for t in targets if t["program_id"] in wanted_ids]

    if not targets:
        print("Нет целей для скачивания — проверьте --only / targets_csv", file=sys.stderr)
        sys.exit(1)

    run_dir = args.raw_dir / date.today().isoformat()
    run_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (admissions-dashboard research tool; manual single run)"

    results = {"ok": 0, "missing": 0, "error": 0}
    log_rows = []

    print(f"Целей: {len(targets)}. Папка сырья: {run_dir}")
    for i, t in enumerate(targets, 1):
        url = t["url"]
        dest = run_dir / t["filename"]
        status = fetch_one(session, url, dest)
        results[status.split(" ")[0]] = results.get(status.split(" ")[0], 0) + 1
        print(f"[{i}/{len(targets)}] {t['program_name']} ({t['place_type']}): {status}")
        log_rows.append({**t, "status": status})

        if i < len(targets):
            time.sleep(PAUSE_SECONDS)

    log_path = run_dir / "_fetch_log.csv"
    with log_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(log_rows[0].keys()))
        writer.writeheader()
        writer.writerows(log_rows)

    print(f"\nГотово: ok={results.get('ok', 0)} missing={results.get('missing', 0)} error={results.get('error', 0)}")
    print(f"Лог сохранён: {log_path}")


if __name__ == "__main__":
    main()
