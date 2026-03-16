#!/usr/bin/env bash
# Post-consumption hook Paperless-ngx
# Configuré via PAPERLESS_POST_CONSUME_SCRIPT dans paperless.conf

set -euo pipefail

SCRIPTS_DIR="/opt/paperless/scripts"
LOG_FILE="${SCRIPTS_DIR}/logs/processor.log"
PYTHON="/usr/bin/python3"

mkdir -p "${SCRIPTS_DIR}/logs"

echo "$(date '+%Y-%m-%d %H:%M:%S') [post_consume] Document ID=${DOCUMENT_ID} reçu" >> "${LOG_FILE}"

exec "${PYTHON}" "${SCRIPTS_DIR}/doc_processor.py"
