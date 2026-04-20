#!/usr/bin/env python3
"""
Envoie les documents Paperless vers Dolibarr comme factures fournisseurs.

Deux modes:
  1. Appelé par le cron: traite tous les docs tagués 'dolibarr-queue'
  2. Appelé avec un doc_id: envoie ce document spécifiquement

Usage:
    python3 push_to_dolibarr.py            # mode cron (dolibarr-queue)
    python3 push_to_dolibarr.py <doc_id>   # envoi forcé d'un doc
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import claude_analyzer
import dolibarr_client
import paperless_client
from config import CUSTOM_FIELD_IDS, PROTECTED_TAG_IDS, TAG_IDS

LOG_FILE = "/opt/paperless/scripts/logs/dolibarr.log"
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

TAG_QUEUE = TAG_IDS["dolibarr-queue"]
TAG_SENT  = TAG_IDS["dolibarr-sent"]

# Docs rapidetech éligibles (facture ou recu)
ELIGIBLE_DOC_TAGS = {TAG_IDS["facture"], TAG_IDS["recu"]}


def _get_custom_field(doc: dict, field_name: str) -> str | None:
    field_id = CUSTOM_FIELD_IDS[field_name]
    for f in doc.get("custom_fields", []):
        if f["field"] == field_id:
            return f["value"] or None
    return None


def _tag_ids(doc: dict) -> set[int]:
    return set(doc.get("tags", []))


def _mark_sent(doc_id: int, current_tags: set[int]) -> None:
    """Retire dolibarr-queue, ajoute dolibarr-sent."""
    new_tags = (current_tags - {TAG_QUEUE}) | {TAG_SENT}
    paperless_client.patch_document(doc_id, {"tags": sorted(new_tags)})


def push_document(doc_id: int, force: bool = False, prefetched_line_items: list | None = None) -> bool:
    """
    Envoie un document vers Dolibarr.
    force=True ignore le tag dolibarr-sent (re-envoi manuel).
    prefetched_line_items: si fourni, évite un double appel Claude (utilisé depuis doc_processor).
    Retourne True si envoyé avec succès.
    """
    doc = paperless_client.get_document(doc_id)
    tags = _tag_ids(doc)
    title = doc.get("title", f"Document {doc_id}")

    # Déjà envoyé?
    if TAG_SENT in tags and not force:
        log.info(f"Doc {doc_id} déjà envoyé (tag dolibarr-sent) — skip")
        return False

    # Données extraites par Claude
    import re as _re
    def _clean_amount(v):
        if v is None:
            return None
        clean = _re.sub(r"[^\d.,]", "", str(v)).replace(",", ".")
        try:
            return f"{float(clean):.2f}" if clean else None
        except ValueError:
            return None

    total        = _clean_amount(_get_custom_field(doc, "Total"))
    tps          = _clean_amount(_get_custom_field(doc, "TPS"))
    tvq          = _clean_amount(_get_custom_field(doc, "TVQ"))
    invoice_ref  = _get_custom_field(doc, "Facture")
    date_str     = (doc.get("created") or "")[:10]

    if not total:
        log.warning(f"Doc {doc_id} ({title}): pas de montant Total — skip")
        return False

    if not date_str:
        log.warning(f"Doc {doc_id} ({title}): pas de date — skip")
        return False

    # Correspondant
    corr_id = doc.get("correspondent")
    if corr_id:
        corr_data = paperless_client.get_correspondent(corr_id)
        supplier_name = corr_data.get("name", title)
    else:
        supplier_name = title.split(" ")[0]  # fallback: premier mot du titre

    # Tags Paperless pour la catégorie
    tag_names = [
        name for name, tid in TAG_IDS.items()
        if tid in tags and name not in ("dolibarr-queue", "dolibarr-sent", "a-verifier")
    ]

    log.info(f"Doc {doc_id} | {supplier_name} | {date_str} | Total={total} TPS={tps} TVQ={tvq} | ref={invoice_ref}")

    # Vérification doublon dans Dolibarr (toujours, même en force)
    if invoice_ref:
        if dolibarr_client.invoice_exists(supplier_name, invoice_ref, date_str):
            log.warning(f"Doc {doc_id}: doublon Dolibarr (ref={invoice_ref}) — marque sent sans créer")
            _mark_sent(doc_id, tags)
            return False

    # Création dans Dolibarr
    # Utiliser les line_items pré-calculés si fournis (évite double appel Claude)
    if prefetched_line_items is not None:
        line_items = prefetched_line_items
        log.info(f"  → {len(line_items)} ligne(s) pré-calculée(s)")
    else:
        # Re-analyser le document pour extraire les line_items
        line_items = []
        try:
            analysis = claude_analyzer.analyze_document_vision(doc_id)
            line_items = analysis.get("line_items", [])
            log.info(f"  → {len(line_items)} ligne(s) extraite(s) par Claude")
        except Exception as e:
            log.warning(f"  → Analyse Claude échouée ({e}), fallback ligne générique")

    try:
        inv_id = dolibarr_client.create_supplier_invoice(
            supplier_name    = supplier_name,
            date             = date_str,
            invoice_ref      = invoice_ref,
            total            = total,
            tps              = tps,
            tvq              = tvq,
            line_items       = line_items,
            tags             = tag_names,
            doc_title        = title,
            paperless_doc_id = doc_id,
        )
        log.info(f"✓ Facture Dolibarr créée ID={inv_id} pour doc {doc_id} ({supplier_name})")
        _mark_sent(doc_id, tags)
        return True

    except Exception as e:
        log.error(f"✗ Erreur création facture Dolibarr pour doc {doc_id}: {e}")
        return False


def process_queue() -> None:
    """Traite tous les documents avec le tag dolibarr-queue."""
    results = paperless_client.get_documents_by_tag(TAG_QUEUE)

    if not results:
        log.info("Queue vide — rien à envoyer")
        return

    log.info(f"=== {len(results)} document(s) en queue Dolibarr ===")
    sent, skipped, errors = 0, 0, 0
    for doc in results:
        ok = push_document(doc["id"])
        if ok:
            sent += 1
        else:
            skipped += 1

    log.info(f"Résultat: {sent} envoyés | {skipped} ignorés | {errors} erreurs")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            doc_id = int(sys.argv[1])
            force = "--force" in sys.argv
            log.info(f"=== Envoi manuel doc {doc_id} (force={force}) ===")
            push_document(doc_id, force=force)
        except ValueError:
            log.error(f"ID invalide: {sys.argv[1]}")
            sys.exit(1)
    else:
        process_queue()
