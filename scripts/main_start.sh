#!/usr/bin/env bash
# Old name, kept so old shortcuts do not silently do something else: the same as scripts/start.sh (one start for both PCs;
# direction and data policy come from ~/.config/slam/runner.conf, not from the file name).
echo "(main_start.sh — стара назва, запускаю scripts/start.sh)"
exec bash "$(dirname "${BASH_SOURCE[0]}")/start.sh" "$@"
