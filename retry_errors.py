#!/usr/bin/env python3
"""
Retraite chaque nuit les documents marqués 'erreur-traitement' (échec analyse
Claude: CLI code 1, timeout, etc.). Distinct du retry rate-limit (retry_processor.py).

- Succès → process_document retire le tag erreur automatiquement.
- Échec → le tag erreur reste; on incrémente le compteur de tentatives.
- Après ERROR_MAX_ATTEMPTS échecs, on escalade vers a-verifier (intervention humaine).

Appelé par cron à 3h du matin.
"""

import fcntl
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import claude_analyzer
import doc_processor
import paperless_client
from config import ERROR_MAX_ATTEMPTS, ERROR_TAG_ID, TAG_IDS

STATE_FILE = "/opt/paperless/scripts/error_retry_state.json"
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


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
        return {int(k): v for k, v in data.items()}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump({str(k): v for k, v in state.items()}, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)


def find_error_docs() -> list[int]:
    """Tous les documents (non supprimés) portant le tag erreur-traitement."""
    return [d["id"] for d in paperless_client.get_all_documents_by_tag(ERROR_TAG_ID)]


def escalate(doc_id: int) -> None:
    """Remplace erreur-traitement par a-verifier après tentatives épuisées."""
    try:
        doc = paperless_client.get_document(doc_id)
        tags = set(doc.get("tags", []))
        tags.discard(ERROR_TAG_ID)
        tags.add(TAG_IDS["a-verifier"])
        paperless_client.patch_document(doc_id, {"tags": sorted(tags)})
        log.warning(
            f"Doc {doc_id}: {ERROR_MAX_ATTEMPTS} échecs — escalade vers a-verifier"
        )
    except Exception as e:
        log.error(f"Doc {doc_id}: échec escalade a-verifier: {e}")


def main():
    doc_ids = find_error_docs()
    if not doc_ids:
        log.info("Aucun document en erreur — rien à retenter")
        return

    log.info(f"Retry erreurs: {len(doc_ids)} document(s) à retenter")
    state = load_state()
    rate_limited = False

    for doc_id in doc_ids:
        attempts = state.get(doc_id, 0)

        if attempts >= ERROR_MAX_ATTEMPTS:
            escalate(doc_id)
            state.pop(doc_id, None)
            continue

        if rate_limited:
            continue  # inutile d'insister, on réessaiera la prochaine nuit

        log.info(f"Retry doc {doc_id} (tentative {attempts + 1}/{ERROR_MAX_ATTEMPTS})...")
        try:
            doc_processor.process_document(doc_id)
        except claude_analyzer.RateLimitError:
            log.warning(f"Doc {doc_id}: rate limit — reporté (tentative non comptée)")
            rate_limited = True
            continue
        except Exception as e:
            log.error(f"Doc {doc_id}: erreur inattendue: {e}")

        # process_document attrape les erreurs Claude en interne et re-pose le
        # tag erreur sans lever d'exception. On revérifie donc l'état réel.
        try:
            doc = paperless_client.get_document(doc_id)
            still_error = ERROR_TAG_ID in doc.get("tags", [])
        except Exception as e:
            log.error(f"Doc {doc_id}: impossible de revérifier l'état: {e}")
            still_error = True

        if still_error:
            state[doc_id] = attempts + 1
            log.warning(f"Doc {doc_id}: échec ({state[doc_id]}/{ERROR_MAX_ATTEMPTS})")
            if state[doc_id] >= ERROR_MAX_ATTEMPTS:
                escalate(doc_id)
                state.pop(doc_id, None)
        else:
            log.info(f"Doc {doc_id}: traité avec succès")
            state.pop(doc_id, None)

    save_state(state)
    log.info("Retry erreurs terminé")


if __name__ == "__main__":
    main()
