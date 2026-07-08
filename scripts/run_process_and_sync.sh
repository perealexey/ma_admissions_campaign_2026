#!/bin/bash
# run_process_and_sync.sh — всё, что идёт ПОСЛЕ сбора с сайта: нормализация →
# метрики → заливка на Google Drive. НЕ обращается к priem47.hse.ru вообще —
# можно запускать сколько угодно раз за день (например, чтобы перезалить на
# Drive после правки скрипта), не нарушая ограничение "раз в сутки".
#
# Предполагает, что raw/ДАТА и data/stage0_targets_ДАТА.csv уже существуют
# (то есть fetch_reports.py на эту дату уже отработал).
#
# Использование: scripts/run_process_and_sync.sh [ДАТА]
#   ДАТА по умолчанию — сегодня (ГГГГ-ММ-ДД). Указывайте явно, если нужно
#   пересчитать не сегодняшний, а более ранний срез.
set -euo pipefail

DATE="${1:-$(date +%Y-%m-%d)}"

if [ ! -d "raw/${DATE}" ]; then
    echo "Не найдена raw/${DATE} — сначала нужен сбор данных (scripts/fetch_data.sh) на эту дату." >&2
    exit 1
fi
if [ ! -f "data/stage0_targets_${DATE}.csv" ]; then
    echo "Не найден data/stage0_targets_${DATE}.csv — сначала нужен Этап 0 (scripts/parse_toc.py) на эту дату." >&2
    exit 1
fi

echo "=== Этап 2: нормализация (срез ${DATE}) ==="
python3 scripts/normalize.py "raw/${DATE}" "data/stage0_targets_${DATE}.csv" -o "data/admissions_long_${DATE}.parquet"

echo "=== Этап 3: метрики ==="
python3 scripts/metrics.py "data/admissions_long_${DATE}.parquet"
python3 scripts/campaign_metrics.py "data/admissions_long_${DATE}.parquet" data/supply_demand_clean.csv --snapshot-date "$DATE"

echo "=== Заливка на Google Drive ==="
python3 scripts/sync_to_drive.py

echo "=== Готово: срез $DATE обработан и залит на Drive (сайт ВШЭ не трогали). ==="
