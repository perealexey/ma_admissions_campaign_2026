#!/bin/bash
# run_update.sh — вся цепочка после того, как magstats.html уже сохранён
# и сегодняшний заход на сайт (раз в сутки) осознанно разрешён.
#
# Использование: scripts/run_update.sh magstat/magstat_ДД_ММ.html
#
# Делает по порядку: разведка списка → сбор xlsx → нормализация → метрики →
# сопоставление с местами (если other_files/*.rtf не менялись — можно
# закомментировать этот шаг) → заливка в Google Drive.
set -euo pipefail

MAGSTAT_HTML="$1"
DATE=$(date +%Y-%m-%d)

echo "=== Этап 0: разведка списка (magstat: $MAGSTAT_HTML) ==="
python3 scripts/parse_toc.py "$MAGSTAT_HTML" -o "data/stage0_targets_${DATE}.csv"
echo ">>> Проверьте data/stage0_targets_${DATE}.csv глазами, прежде чем продолжать (Ctrl+C, если что-то не так)."
read -p "Нажмите Enter, чтобы продолжить сбор... "

echo "=== Этап 1: сбор xlsx (не чаще раза в сутки!) ==="
python3 scripts/fetch_reports.py "data/stage0_targets_${DATE}.csv"

echo "=== Этап 2: нормализация ==="
python3 scripts/normalize.py "raw/${DATE}" "data/stage0_targets_${DATE}.csv" -o "data/admissions_long_${DATE}.parquet"

echo "=== Этап 3: метрики ==="
python3 scripts/metrics.py "data/admissions_long_${DATE}.parquet"
python3 scripts/campaign_metrics.py "data/admissions_long_${DATE}.parquet" data/supply_demand_clean.csv --snapshot-date "$DATE"

echo "=== Заливка на Google Drive ==="
python3 scripts/sync_to_drive.py

echo "=== Готово: срез $DATE обработан и залит на Drive. ==="
