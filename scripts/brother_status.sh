#!/usr/bin/env bash
# Old name, kept for old shortcuts: the same as scripts/start.sh --status.
exec bash "$(dirname "${BASH_SOURCE[0]}")/start.sh" --status
