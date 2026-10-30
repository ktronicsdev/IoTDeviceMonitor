#!/usr/bin/env bash
################################################################################
# mask_credentials.sh
#
# Register every secret in a credentials file with the GitHub Actions log
# masker, so any later step that prints one shows *** instead.
#
# Usage:
#   bash mask_credentials.sh <credentials.json> [<more.json> ...]
#
# Each credentials file arrives as ONE GitHub secret holding a whole JSON blob,
# and GitHub masks a secret only as a whole. A single password printed on its
# own was therefore shown in clear: a failing live test had pytest print the
# command it ran, and a customer's ShineMonitor password reached 12 public
# workflow logs between 19 and 29 Oct 2026. Masking each value covers that
# path and any other that turns up later.
#
# Masked: every non-empty string whose key names a secret - password, pwd,
# secret, token, any *key* (company_key, key_id, key_secret) and access_id.
################################################################################

set -euo pipefail

for file in "$@"; do
  jq -r '.. | objects | to_entries[]
         | select((.key | test("pass|pwd|secret|token|key|access_id"; "i"))
                  and (.value | type) == "string" and (.value | length) > 0)
         | .value' "$file" |
    sort -u |
    while IFS= read -r value; do
      echo "::add-mask::${value}"
    done
done
