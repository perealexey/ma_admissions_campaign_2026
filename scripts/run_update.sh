#!/bin/bash
# run_update.sh — вся цепочка сразу: сбор с сайта (раз в сутки!) + обработка +
# заливка на Drive. Тонкая обёртка над двумя независимыми скриптами:
#   scripts/fetch_data.sh          — трогает priem47.hse.ru (раз в сутки)
#   scripts/run_process_and_sync.sh — всё остальное, сайт не трогает,
#                                      можно перезапускать сколько угодно раз
#
# Использование: scripts/run_update.sh magstat/magstat_ДД_ММ.html
set -euo pipefail

MAGSTAT_HTML="$1"

scripts/fetch_data.sh "$MAGSTAT_HTML"
scripts/run_process_and_sync.sh
