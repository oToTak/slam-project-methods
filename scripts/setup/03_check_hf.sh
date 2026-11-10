#!/usr/bin/env bash
# Check HF login without printing the token.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
if hf auth whoami 2>&1 | head -3; then :; fi
