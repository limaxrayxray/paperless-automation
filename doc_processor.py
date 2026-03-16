#!/usr/bin/env python3
"""
Processeur automatique de documents Paperless-ngx.
Appelé par PAPERLESS_POST_CONSUME_SCRIPT après chaque consommation.

Variables d'environnement fournies par Paperless:
  DOCUMENT_ID, DOCUMENT_FILE_NAME, DOCUMENT_TAGS, DOCUMENT_CORRESPONDENT, etc.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import claude_analyzer
import paperless_client
from config import (
    ALLOWED_TAGS,
    CUSTOM_FIELD_IDS,
    DATE_CONFIDENCE_THRESHOLD,
    DOC_TYPE_IDS,
    GLOBAL_CONFIDENCE_THRESHOLD,
    PROTECTED_TAG_IDS,
    TAG_IDS,
    YEAR_TAG_IDS,
)

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


def build_tag_updates(
    current_tag_ids: list[int],
    analysis: dict,
) -> list[int]:
    """
    Calcule la liste finale des tags en appliquant les résultats de l'analyse.
    Ne touche jamais aux tags protégés.
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

    # Tag année (seulement si confiance suffisante)
    date_str = analysis.get("date")
    date_conf = analysis.get("date_confidence", 0.0)
    if date_str and date_conf >= DATE_CONFIDENCE_THRESHOLD:
        try:
            year = int(date_str[:4])
            year_tag = get_year_tag_id(year)
            if year_tag:
                # Retirer les autres tags année d'abord
                tags -= set(YEAR_TAG_IDS.values())
                tags.add(year_tag)
        except (ValueError, IndexError):
            pass

    # Tag a-verifier si confiance basse
    if analysis.get("confidence", 0) < GLOBAL_CONFIDENCE_THRESHOLD:
        tags.add(TAG_IDS["a-verifier"])
    else:
        tags.discard(TAG_IDS["a-verifier"])

    # Garantie finale: jamais de tags protégés dans les changements qu'on fait
    # (on les garde s'ils étaient là, on n'en ajoute pas)
    protected_that_were_there = PROTECTED_TAG_IDS & set(current_tag_ids)
    tags = (tags - PROTECTED_TAG_IDS) | protected_that_were_there

    return sorted(tags)


def build_custom_fields(
    existing_custom_fields: list[dict],
    analysis: dict,
) -> list[dict]:
    """
    Construit la mise à jour des custom fields.
    Extrait toujours TPS/TVQ/Total/Facture quand disponibles — Alexandre décide
    du contexte personnel/professionnel lui-même via les tags.
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

    return paperless_client.build_custom_fields_payload(
        existing_custom_fields, updates, CUSTOM_FIELD_IDS
    )


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

    # 2. Analyse — Claude vision en priorité, fallback texte OCR si échec
    log.info("Analyse via Claude vision (image directe)...")
    analysis = None
    try:
        analysis = claude_analyzer.analyze_document_vision(doc_id)
        log.info("Claude vision: succès")
    except Exception as e:
        log.warning(f"Vision échouée ({e}) — fallback texte OCR")

    if analysis is None:
        log.info("Fallback: analyse via texte OCR...")
        try:
            analysis = claude_analyzer.analyze_document(title, content)
            log.info("Fallback texte: succès")
        except Exception as e:
            log.error(f"Erreur analyse: {e}")
        # Fallback: tag a-verifier
        patch = {"tags": list(set(current_tags) | {TAG_IDS["a-verifier"]} - PROTECTED_TAG_IDS | (PROTECTED_TAG_IDS & set(current_tags)))}
        paperless_client.patch_document(doc_id, patch)
        log.warning(f"Tag a-verifier ajouté (erreur Claude)")
        return

    log.info(
        f"Analyse: type={analysis['doc_type']} context={analysis['context']} "
        f"confidence={analysis['confidence']:.2f} date={analysis.get('date')} "
        f"correspondent={analysis.get('correspondent')}"
    )
    log.info(f"Notes Claude: {analysis.get('notes', '')}")

    # 3. Construire le payload de mise à jour
    payload: dict = {}

    # Tags
    new_tags = build_tag_updates(current_tags, analysis)
    if set(new_tags) != set(current_tags):
        payload["tags"] = new_tags
        log.info(f"Tags: {current_tags} → {new_tags}")

    # Titre suggéré (seulement si le titre actuel est générique/auto-généré)
    suggested_title = analysis.get("suggested_title", "").strip()
    if suggested_title and suggested_title != title and _is_generic_title(title):
        payload["title"] = suggested_title
        log.info(f"Titre: '{title}' → '{suggested_title}'")

    # Date du document
    date_val = analysis.get("date")
    date_conf = analysis.get("date_confidence", 0.0)
    if date_val and date_conf >= DATE_CONFIDENCE_THRESHOLD:
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

    # Correspondant (seulement si pas déjà défini ou si Claude en trouve un meilleur)
    suggested_corr = analysis.get("correspondent")
    if suggested_corr and not current_correspondent:
        try:
            corr_id = paperless_client.find_or_create_correspondent(suggested_corr)
            payload["correspondent"] = corr_id
            log.info(f"Correspondant: '{suggested_corr}' (ID={corr_id})")
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
        f"total={analysis.get('total')} | tps={analysis.get('tps')} | tvq={analysis.get('tvq')}"
    )


def _is_generic_title(title: str) -> bool:
    """
    Retourne True si le titre semble auto-généré (nom de fichier, ID, date brute).
    Dans ce cas, on peut le remplacer par le titre suggéré par Claude.
    """
    import re
    t = title.strip()
    # Fichier: scan001.pdf, IMG_1234, page-001, Receipt_001441, Facture_001234
    if re.match(r'^(scan|img|page|doc|document|untitled|fichier|receipt|facture_\d|invoice)[\s_\-]?\d*', t, re.I):
        return True
    # Pattern scanner Brother/imprimante: Mot_Chiffres (ex: Receipt_001441, Facture_001439)
    if re.match(r'^[A-Za-z]+_\d{4,}$', t):
        return True
    # Seulement des chiffres, tirets, underscores (ex: "2024-01-15", "20240115")
    if re.match(r'^[\d\-_\.]+$', t):
        return True
    # Très court (moins de 5 chars) ou numérique seul
    if len(t) < 5:
        return True
    # Ressemble à un nom de fichier sans extension pertinente
    if re.match(r'^[A-Z0-9_\-]{3,20}$', t):
        return True
    return False


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
    except Exception as e:
        log.exception(f"Erreur fatale traitement document {doc_id}: {e}")
        sys.exit(1)
