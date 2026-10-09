#!/usr/bin/env bash
set -euo pipefail

if [[ ! -x .venv/bin/python ]]; then
  printf '%s\n' 'Run this command from the repository root after creating .venv.' >&2
  exit 1
fi

for variable in \
  SIGNAL_JEV_EGRESS_CONTEXT \
  SIGNAL_JEV_EGRESS_ADMISSION_DSN \
  SIGNAL_JEV_EGRESS_INGEST_DSN
do
  if [[ -z "${!variable:-}" ]]; then
    printf 'Jev live qualification failed (missing %s).\n' "${variable}" >&2
    exit 1
  fi
done

IFS= read -r -s -p 'TypeSafe API key: ' signal_jev_key
printf '\n' >&2
if [[ -z "${signal_jev_key}" ]]; then
  printf '%s\n' 'Jev live qualification failed (EMPTY_CREDENTIAL).' >&2
  exit 1
fi

printf '%s' "${signal_jev_key}" | .venv/bin/python scripts/qualify_jev_provider.py
unset signal_jev_key
