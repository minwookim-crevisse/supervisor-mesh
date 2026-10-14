#!/usr/bin/env bash
# Project audit gate (release blocker). Exit 0 = invariants hold; anything else blocks ship.
# Part B: replace the body with the project's full-chain invariant checks (reconciliation, counts, integrity).
# why: completion is stamped by this script, not self-declared.
set -euo pipefail
echo "audit: NOT CONFIGURED — fill scripts/audit.sh with the project's invariant checks (Part B)" >&2
exit 2
