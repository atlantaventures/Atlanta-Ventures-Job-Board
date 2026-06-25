#!/bin/bash
set -e
curl -s -X POST "${WEBHOOK_BASE}/run" \
  -H "X-Secret: ${WEBHOOK_SECRET}" \
  -H "Content-Type: application/json"
