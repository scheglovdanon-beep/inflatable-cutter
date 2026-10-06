#!/usr/bin/env bash
# Запуск тяжёлого прогона под cgroup-лимитом памяти, чтобы при пике OOM-killer убивал
# ТОЛЬКО этот скрипт (в своём scope), а не ронял весь сервер и remote-control чат.
# После оптимизаций памяти полный прогон ~0.5 ГБ, лимит 3 ГБ — с большим запасом.
#
# Использование:
#   ./run_capped.sh venv/bin/python texture_view.py <pdf> --config <cfg> -o out/x.png
#   ./run_capped.sh venv/bin/python export_svg.py   <pdf> --config <cfg> -o out/x.svg
exec systemd-run --scope -p MemoryMax=3G -p MemorySwapMax=2G --quiet "$@"
