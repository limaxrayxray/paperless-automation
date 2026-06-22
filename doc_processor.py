#!/usr/bin/env python3
"""
Processeur automatique de documents Paperless-ngx.
Appelé par PAPERLESS_POST_CONSUME_SCRIPT après chaque consommation.

Variables d'environnement fournies par Paperless:
  DOCUMENT_ID, DOCUMENT_FILE_NAME, DOCUMENT_TAGS, DOCUMENT_CORRESPONDENT, etc.
"""

import json
import logging
import os
import sys
from datetime import date
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import claude_analyzer
import compta_payload
import paperless_client
from config import ALLOWED_TAGS
from config import CUSTOM_FIELD_IDS
from config import DATE_CONFIDENCE_THRESHOLD
from config import DATE_REVIEW_MAX_FUTURE_DAYS
from config import DATE_REVIEW_MAX_PAST_DAYS
from config import DOC_TYPE_IDS
from config import ERROR_TAG_ID
from config import GLOBAL_CONFIDENCE_THRESHOLD
from config import PROTECTED_TAG_IDS
from config import TAG_IDS
from config import TRIGGER_TAG_IDS
from config import YEAR_TAG_IDS

# ─── LOGGING ──────────────────────────────────────────────────────────────────
LOG_FILE = "/opt/paperless/scripts/logs/processor.log"
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def get_year_tag_id(year: int) -> int | None:
    """Retourne l'ID du tag année, ou None si l'année n'existe pas encore."""
    return YEAR_TAG_IDS.get(year)


def _ingestion_date(doc: dict) -> date:
    """Date d'ingestion du document (`added`), repli sur aujourd'hui."""
    raw = doc.get("added") or doc.get("created")
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return date.today()


def check_date_plausibility(
    date_val: str | None,
    ingestion_date: date,
) -> tuple[bool, str | None]:
    """Détecte une date extraite incohérente avec la date d'ingestion.

    Un document est normalement scanné peu après son émission. Une date trop
    antérieure (souvent une confusion d'année : OCR 2026→2025, biais LLM) ou dans
    le futur est jugée suspecte. Retourne (suspect, raison). Objectif : ne jamais
    classer en silence dans la mauvaise année fiscale — on flag pour vérification.
    """
    if not date_val:
        return False, None
    try:
        d = datetime.strptime(date_val, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False, None
    delta = (ingestion_date - d).days
    if delta > DATE_REVIEW_MAX_PAST_DAYS:
        return True, (
            f"date extraite {date_val} = {delta} j avant l'ingestion "
            f"({ingestion_date}) — probable confusion d'année"
        )
    if delta < -DATE_REVIEW_MAX_FUTURE_DAYS:
        return True, (
            f"date extraite {date_val} dans le futur vs ingestion ({ingestion_date})"
        )
    return False, None


def build_tag_updates(
    current_tag_ids: list[int],
    analysis: dict,
    date_suspect: bool = False,
) -> list[int]:
    """
    Calcule la liste finale des tags en appliquant les résultats de l'analyse.
    Ne touche jamais aux tags protégés. Si `date_suspect`, le tag année est ignoré
    et a-verifier est forcé (date probablement dans la mauvaise année).
    """
    tags = set(current_tag_ids)

    # Retirer les anciens tags de classification (sauf protégés et personnel)
    # personnel n'est jamais retiré automatiquement — Alexandre peut l'avoir ajouté manuellement
    personnel_id = TAG_IDS["personnel"]
    classification_tag_ids = {
        tid for name, tid in TAG_IDS.items()
        if name in ALLOWED_TAGS
    }
    removable = classification_tag_ids - PROTECTED_TAG_IDS - {personnel_id}
    tags -= removable

    # Ajouter le tag de type de document principal
    doc_type = analysis.get("doc_type")
    if doc_type and doc_type in TAG_IDS:
        tags.add(TAG_IDS[doc_type])

    # Ajouter les tags supplémentaires suggérés par Claude
    for tag_name in analysis.get("tags_to_add", []):
        if tag_name in TAG_IDS and tag_name in ALLOWED_TAGS:
            tags.add(TAG_IDS[tag_name])

    # Règle métier: medical → toujours ajouter personnel aussi
    if TAG_IDS["medical"] in tags:
        tags.add(personnel_id)

    # Tag année (seulement si confiance suffisante ET date non suspecte)
    date_str = analysis.get("date")
    date_conf = analysis.get("date_confidence", 0.0)
    if not date_suspect and date_str and date_conf >= DATE_CONFIDENCE_THRESHOLD:
        try:
            year = int(date_str[:4])
            year_tag = get_year_tag_id(year)
            if year_tag:
                # Retirer les autres tags année d'abord
                tags -= set(YEAR_TAG_IDS.values())
                tags.add(year_tag)
        except (ValueError, IndexError):
            pass

    # Tag a-verifier si confiance basse OU date suspecte
    low_conf = analysis.get("confidence", 0) < GLOBAL_CONFIDENCE_THRESHOLD
    if low_conf or date_suspect:
        tags.add(TAG_IDS["a-verifier"])
    else:
        tags.discard(TAG_IDS["a-verifier"])

    # Une analyse réussie efface le tag d'erreur (le doc a enfin été traité)
    tags.discard(ERROR_TAG_ID)

    # Garantie finale: jamais de tags protégés dans les changements qu'on fait
    # (on les garde s'ils étaient là, on n'en ajoute pas)
    protected_that_were_there = PROTECTED_TAG_IDS & set(current_tag_ids)
    tags = (tags - PROTECTED_TAG_IDS) | protected_that_were_there

    # Retirer les tags de trigger (paperless-gpt-auto, etc.) — ils ont rempli
    # leur rôle de déclencher le traitement, plus besoin de les garder.
    tags -= TRIGGER_TAG_IDS

    return sorted(tags)


def build_custom_fields(
    existing_custom_fields: list[dict],
    analysis: dict,
) -> list[dict]:
    """
    Construit la mise à jour des custom fields.
    Extrait toujours TPS/TVQ/Total/Facture quand disponibles — Alexandre décide
    du contexte personnel/professionnel lui-même via les tags.

    Sérialise aussi le contrat d'unification `compta_json` (cf. SPEC.md) quand le
    champ est configuré dans `CUSTOM_FIELD_IDS`. Tant que l'id réel n'y est pas
    inscrit (création manuelle via `ensure_compta_field.py`), le contrat n'est
    simplement pas écrit — aucune erreur.
    """
    updates = {}

    if analysis.get("doc_type") in ("facture", "recu", "releve", "contrat", "assurance", "autre"):
        if analysis.get("tps") is not None:
            updates["TPS"] = analysis["tps"]
        if analysis.get("tvq") is not None:
            updates["TVQ"] = analysis["tvq"]
        if analysis.get("total") is not None:
            updates["Total"] = analysis["total"]
        if analysis.get("invoice_number"):
            updates["Facture"] = analysis["invoice_number"]

    # Contrat compta_json — pour tous les types de document, sérialisé en JSON
    # stable (clés triées → idempotence). Écrit seulement si le champ existe.
    if "compta_json" in CUSTOM_FIELD_IDS:
        payload = compta_payload.build_compta_payload(analysis)
        updates["compta_json"] = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
        )

    return paperless_client.build_custom_fields_payload(
        existing_custom_fields, updates, CUSTOM_FIELD_IDS,
    )


def _is_inline_email_image(title: str, content: str) -> bool:
    """Détecte les images inline d'email (ex: image001, image002) — à supprimer."""
    import re
    return bool(re.match(r"^image\d+$", title.strip(), re.I)) and len(content.strip()) == 0


def process_document(doc_id: int) -> None:
    log.info(f"=== Traitement document ID={doc_id} ===")

    # 1. Récupérer le document
    doc = paperless_client.get_document(doc_id)
    title = doc.get("title", f"Document {doc_id}")
    content = doc.get("content", "") or ""
    current_tags = doc.get("tags", [])
    current_custom_fields = doc.get("custom_fields", [])
    current_correspondent = doc.get("correspondent")

    log.info(f"Titre: {title}")
    log.info(f"Tags actuels: {current_tags}")
    log.info(f"Contenu OCR: {len(content)} caractères")

    # Supprimer les images inline d'email (image001, image002, etc.)
    if _is_inline_email_image(title, content):
        log.info(f"Doc {doc_id} ignoré: image inline email ('{title}') — suppression")
        try:
            paperless_client.delete_document(doc_id)
            log.info(f"Doc {doc_id} supprimé")
        except Exception as e:
            log.warning(f"Doc {doc_id}: impossible de supprimer: {e}")
        return

    # 2. Analyse — OCR texte d'abord, vision en fallback si confiance basse
    log.info("Analyse via Claude (OCR-first)...")
    try:
        analysis = claude_analyzer.analyze_document_smart(doc_id, title, content)
        log.info(f"Claude succès (méthode: {analysis.get('_method', '?')})")
    except claude_analyzer.RateLimitError:
        # Remonte au caller — post_consume queue, retry_processor laisse dans remaining
        raise
    except Exception as e:
        log.error(f"Erreur analyse: {e}")
        patch = {"tags": sorted(set(current_tags) | {ERROR_TAG_ID})}
        paperless_client.patch_document(doc_id, patch)
        log.warning("Tag erreur-traitement ajouté (erreur Claude) — sera retenté la nuit")
        return

    log.info(
        f"Analyse: type={analysis['doc_type']} context={analysis['context']} "
        f"confidence={analysis['confidence']:.2f} date={analysis.get('date')} "
        f"correspondent={analysis.get('correspondent')}",
    )
    log.info(f"Notes Claude: {analysis.get('notes', '')}")

    # Garde-fou date : la date extraite est-elle cohérente avec l'ingestion ?
    ingestion_date = _ingestion_date(doc)
    date_suspect, date_reason = check_date_plausibility(
        analysis.get("date"), ingestion_date,
    )
    if date_suspect:
        log.warning(
            f"DATE SUSPECTE doc {doc_id}: {date_reason} → a-verifier, "
            f"tag année ignoré, date NON écrasée",
        )
        analysis["notes"] = (analysis.get("notes", "") or "") + \
            f" [DATE SUSPECTE: {date_reason}]"

    # 3. Construire le payload de mise à jour
    payload: dict = {}

    # Tags
    new_tags = build_tag_updates(current_tags, analysis, date_suspect=date_suspect)
    if set(new_tags) != set(current_tags):
        payload["tags"] = new_tags
        log.info(f"Tags: {current_tags} → {new_tags}")

    # Titre suggéré (seulement si le titre actuel est générique/auto-généré)
    suggested_title = analysis.get("suggested_title", "").strip()
    if suggested_title and suggested_title != title:
        payload["title"] = suggested_title
        log.info(f"Titre: '{title}' → '{suggested_title}'")

    # Date du document — jamais écrasée si jugée suspecte (mauvaise année probable) :
    # on laisse Paperless garder sa date d'ingestion (bonne année) plutôt que de
    # classer en silence dans la mauvaise. Le tag a-verifier signale la correction.
    date_val = analysis.get("date")
    date_conf = analysis.get("date_confidence", 0.0)
    if date_val and date_conf >= DATE_CONFIDENCE_THRESHOLD and not date_suspect:
        try:
            parsed_date = datetime.strptime(date_val, "%Y-%m-%d")
            payload["created"] = parsed_date.strftime("%Y-%m-%dT00:00:00Z")
            log.info(f"Date: {date_val} (confiance {date_conf:.2f})")
        except ValueError:
            log.warning(f"Date invalide ignorée: {date_val}")

    # Type de document
    doc_type_map = {
        "facture": "facture",
        "releve": "releve",
        "rapport": "rapport",
        "manuel": "manuel",
    }
    doc_type_key = doc_type_map.get(analysis.get("doc_type", ""))
    if doc_type_key and doc_type_key in DOC_TYPE_IDS:
        new_type_id = DOC_TYPE_IDS[doc_type_key]
        if doc.get("document_type") != new_type_id:
            payload["document_type"] = new_type_id

    # Correspondant — Claude écrase systématiquement au post_consume car
    # un correspondant déjà présent vient du matching auto Paperless (souvent
    # faux match, ex. "3CX" attrapé dans un PDF DigitalOcean).
    suggested_corr = analysis.get("correspondent")
    if suggested_corr:
        try:
            corr_id = paperless_client.find_or_create_correspondent(suggested_corr)
            if corr_id != current_correspondent:
                payload["correspondent"] = corr_id
                log.info(
                    f"Correspondant: '{suggested_corr}' (ID={corr_id})"
                    + (f" [écrase ancien ID={current_correspondent}]" if current_correspondent else ""),
                )
        except Exception as e:
            log.warning(f"Erreur correspondant: {e}")

    # Custom fields
    new_custom_fields = build_custom_fields(current_custom_fields, analysis)
    if new_custom_fields != current_custom_fields:
        payload["custom_fields"] = new_custom_fields

    # 5. Appliquer la mise à jour
    if payload:
        try:
            paperless_client.patch_document(doc_id, payload)
            log.info(f"Document ID={doc_id} mis à jour avec succès")
        except Exception as e:
            log.error(f"Erreur mise à jour document: {e}")
    else:
        log.info("Aucune modification nécessaire")

    # 6. Log résumé pour audit
    log.info(
        f"RÉSUMÉ | ID={doc_id} | type={analysis['doc_type']} | "
        f"context={analysis['context']} | confidence={analysis['confidence']:.2f} | "
        f"correspondant={suggested_corr} | date={date_val} | "
        f"total={analysis.get('total')} | tps={analysis.get('tps')} | tvq={analysis.get('tvq')}",
    )


RETRY_QUEUE_FILE = "/opt/paperless/scripts/retry_queue.json"


def _queue_for_retry(doc_id: int) -> None:
    """Ajoute un document à la queue de retry pour quand le rate limit sera levé."""
    import fcntl
    from datetime import datetime
    from datetime import timezone
    queue = []
    try:
        with open(RETRY_QUEUE_FILE) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            queue = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Éviter les doublons
    if not any(e["doc_id"] == doc_id for e in queue):
        queue.append({
            "doc_id": doc_id,
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "attempts": 0,
        })
        with open(RETRY_QUEUE_FILE, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(queue, f, indent=2)
            fcntl.flock(f, fcntl.LOCK_UN)
        log.info(f"Doc {doc_id} ajouté à la retry queue ({RETRY_QUEUE_FILE})")
    else:
        log.info(f"Doc {doc_id} déjà dans la retry queue")



# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Mode 1: appelé par post_consume.sh avec DOCUMENT_ID en env
    doc_id_str = os.environ.get("DOCUMENT_ID")

    # Mode 2: argument CLI pour tests manuels
    if not doc_id_str and len(sys.argv) > 1:
        doc_id_str = sys.argv[1]

    if not doc_id_str:
        log.error("DOCUMENT_ID non défini")
        sys.exit(1)

    try:
        doc_id = int(doc_id_str)
    except ValueError:
        log.error(f"DOCUMENT_ID invalide: {doc_id_str}")
        sys.exit(1)

    try:
        process_document(doc_id)
    except claude_analyzer.RateLimitError as e:
        log.warning(f"Rate limit Claude — document mis en queue: {e}")
        _queue_for_retry(doc_id)
    except Exception as e:
        log.exception(f"Erreur fatale traitement document {doc_id}: {e}")
        sys.exit(1)
