#!/bin/bash
# fetch_data.sh — ЕДИНСТВЕННЫЙ скрипт, который обращается к priem47.hse.ru.
# Не чаще одного раза в сутки! Разведка списка (Этап 0) + скачивание xlsx
# (Этап 1). После него — scripts/run_process_and_sync.sh, сколько угодно раз,
# без повторного захода на сайт.
#
# Использование: scripts/fetch_data.sh magstat/magstat_ДД_ММ.html
set -euo pipefail

MAGSTAT_HTML="$1"
DATE=$(date +%Y-%m-%d)

echo "=== Этап 0: разведка списка (magstat: $MAGSTAT_HTML) ==="
python3 scripts/parse_toc.py "$MAGSTAT_HTML" -o "data/stage0_targets_${DATE}.csv"
echo ">>> Проверьте data/stage0_targets_${DATE}.csv глазами, прежде чем продолжать (Ctrl+C, если что-то не так)."
read -p "Нажмите Enter, чтобы продолжить сбор... "

echo "=== Этап 1: сбор xlsx (сегодняшний заход на сайт — раз в сутки, назад дороги нет) ==="
python3 scripts/fetch_reports.py "data/stage0_targets_${DATE}.csv"

echo "=== Готово: сырьё за $DATE в raw/${DATE}. Дальше — scripts/run_process_and_sync.sh ==="
