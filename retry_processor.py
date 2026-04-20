#!/usr/bin/env python3
"""
Retraite les documents en attente dans la retry queue (rate limit Claude).
Appelé par cron toutes les 2 heures.
"""

import fcntl
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import claude_analyzer
import doc_processor

RETRY_QUEUE_FILE = "/opt/paperless/scripts/retry_queue.json"
MAX_ATTEMPTS = 5
LOG_FILE = "/opt/paperless/scripts/logs/processor.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def load_queue() -> list:
    try:
        with open(RETRY_QUEUE_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_queue(queue: list) -> None:
    with open(RETRY_QUEUE_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(queue, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)


def main():
    queue = load_queue()
    if not queue:
        log.info("Retry queue vide — rien à faire")
        return

    log.info(f"Retry queue: {len(queue)} document(s) en attente")

    remaining = []
    rate_limited = False

    for entry in queue:
        doc_id = entry["doc_id"]
        attempts = entry.get("attempts", 0)

        if rate_limited:
            # Si on est déjà rate-limité dans ce run, on reporte tout le reste
            remaining.append(entry)
            continue

        if attempts >= MAX_ATTEMPTS:
            log.warning(f"Doc {doc_id}: {MAX_ATTEMPTS} tentatives épuisées — abandonné")
            continue

        log.info(f"Retry doc {doc_id} (tentative {attempts + 1}/{MAX_ATTEMPTS})...")
        entry["attempts"] = attempts + 1
        entry["last_attempt"] = datetime.now(timezone.utc).isoformat()

        try:
            doc_processor.process_document(doc_id)
            log.info(f"Doc {doc_id}: traité avec succès")
            # Ne pas remettre dans remaining = retiré de la queue
        except claude_analyzer.RateLimitError:
            log.warning(f"Doc {doc_id}: rate limit encore actif — reporte")
            remaining.append(entry)
            rate_limited = True
        except Exception as e:
            log.error(f"Doc {doc_id}: erreur inattendue: {e}")
            remaining.append(entry)

    save_queue(remaining)
    processed = len(queue) - len(remaining)
    log.info(f"Retry terminé: {processed} traité(s), {len(remaining)} en attente")


if __name__ == "__main__":
    main()
