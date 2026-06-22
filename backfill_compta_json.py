#!/usr/bin/env python3
"""(Re)calcule et écrit le contrat `compta_json` sur les documents déjà tagués
facture/recu qui ne l'ont pas encore — ou qui l'ont dans une version antérieure —
afin de donner un historique au consommateur `compta-rapidetech`.

Idempotent : un document déjà à la version courante du contrat est ignoré. Paginé
via `paperless_client.get_all_documents_by_tag`.

⚠️  APPELS RÉSEAU RÉELS : lecture API Paperless + analyse Claude CLI (abonnement) +
écriture Paperless. Comme `ensure_compta_field.py`, ce script est écrit par le loop
mais **jamais exécuté par lui** (cf. SPEC.md, « aucun appel réseau réel depuis le
loop »). À lancer manuellement :

    python backfill_compta_json.py                  # DRY-RUN : n'écrit rien
    python backfill_compta_json.py --limit 5        # se limite à 5 documents
    python backfill_compta_json.py --no-dry-run     # applique réellement

Les fonctions pures (`needs_backfill`, `select_documents_to_backfill`,
`build_backfill_patch`) sont testées sans aucun réseau.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import claude_analyzer
import paperless_client
from compta_payload import COMPTA_CONTRACT_VERSION
from compta_payload import build_compta_payload
from config import CUSTOM_FIELD_IDS
from config import PERSONAL_CONTEXT_TAG_IDS
from config import TAG_IDS

# Documents éligibles au backfill : ceux porteurs d'un de ces tags de classification.
BACKFILL_TAG_NAMES = ("facture", "recu")


def _compta_version(doc: dict, compta_field_id: int) -> int | None:
    """Retourne la version du contrat `compta_json` présent sur le document, ou
    None si le champ est absent, vide ou illisible (JSON cassé / version non int)."""
    for cf in doc.get("custom_fields") or []:
        if cf.get("field") == compta_field_id:
            value = cf.get("value")
            if not value:
                return None
            try:
                data = json.loads(value)
            except (ValueError, TypeError):
                return None
            version = data.get("version") if isinstance(data, dict) else None
            return version if isinstance(version, int) else None
    return None


def needs_backfill(
    doc: dict,
    compta_field_id: int,
    target_version: int = COMPTA_CONTRACT_VERSION,
) -> bool:
    """True si le document doit être (re)calculé : `compta_json` absent, illisible,
    ou dans une version antérieure à `target_version`. Idempotent : un document déjà
    à la version courante renvoie False."""
    version = _compta_version(doc, compta_field_id)
    return version is None or version < target_version


def _is_personal_context(doc: dict) -> bool:
    """True si le document porte un tag de contexte personnel (hors compta entreprise)."""
    return bool(set(doc.get("tags") or []) & PERSONAL_CONTEXT_TAG_IDS)


def select_documents_to_backfill(
    docs: list[dict],
    compta_field_id: int,
    limit: int | None = None,
) -> list[dict]:
    """Filtre les documents à backfiller : besoin de (re)calcul ET hors contexte
    personnel (un reçu médical/perso ne doit pas recevoir de compta_json). Applique
    éventuellement une limite. Fonction pure — aucun réseau."""
    selected = [
        d for d in docs
        if needs_backfill(d, compta_field_id) and not _is_personal_context(d)
    ]
    if limit is not None:
        selected = selected[:limit]
    return selected


def build_backfill_patch(
    existing_custom_fields: list[dict],
    analysis: dict,
    compta_field_id: int,
) -> dict:
    """Construit le patch Paperless qui (sur)écrit `compta_json` à partir d'une
    analyse, en préservant les autres champs personnalisés existants. Fonction pure.

    Sérialisation identique à `doc_processor.build_custom_fields` (sort_keys → chaîne
    stable / idempotente, ensure_ascii=False → accents lisibles)."""
    payload = build_compta_payload(analysis)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    custom_fields = paperless_client.build_custom_fields_payload(
        existing_custom_fields,
        {"compta_json": serialized},
        {"compta_json": compta_field_id},
    )
    return {"custom_fields": custom_fields}


def _fetch_candidate_documents() -> list[dict]:
    """Récupère (réseau) tous les documents tagués facture/recu, dédupliqués par id."""
    docs: dict[int, dict] = {}
    for tag_name in BACKFILL_TAG_NAMES:
        tag_id = TAG_IDS[tag_name]
        for doc in paperless_client.get_all_documents_by_tag(tag_id):
            docs[doc["id"]] = doc
    return list(docs.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="N'écrit rien dans Paperless (défaut). Utiliser --no-dry-run pour appliquer.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Nombre maximum de documents à (re)calculer.",
    )
    args = parser.parse_args(argv)

    compta_field_id = CUSTOM_FIELD_IDS.get("compta_json")
    if compta_field_id is None:
        print(
            "compta_json absent de CUSTOM_FIELD_IDS — exécuter d'abord "
            "ensure_compta_field.py pour créer le champ.",
        )
        return 1

    docs = _fetch_candidate_documents()
    to_backfill = select_documents_to_backfill(docs, compta_field_id, args.limit)
    mode = "DRY-RUN (aucune écriture)" if args.dry_run else "APPLY"
    print(
        f"=== Backfill compta_json (v{COMPTA_CONTRACT_VERSION}) — mode {mode} ===\n"
        f"{len(to_backfill)}/{len(docs)} documents facture/recu à (re)calculer\n",
    )

    written = 0
    for doc in to_backfill:
        doc_id = doc.get("id")
        title = doc.get("title", "") or ""
        # Dry-run : on ne dépense AUCUN appel Claude, on liste seulement les
        # candidats (le calcul réel exige une ré-analyse, faite en mode --no-dry-run).
        if args.dry_run:
            print(f"#{doc_id} [dry-run] à (re)calculer — « {title} »")
            continue
        content = doc.get("content", "") or ""
        try:
            analysis = claude_analyzer.analyze_document_smart(doc_id, title, content)
        except claude_analyzer.RateLimitError as e:
            # Inutile d'insister : on s'arrête. La reprise est idempotente (les docs
            # non écrits restent < version courante et seront repris au relancement).
            print(f"#{doc_id} RATE LIMIT — arrêt. Relancer plus tard pour reprendre: {e}")
            break
        except Exception as e:
            print(f"#{doc_id} ERREUR analyse: {e}")
            continue
        patch = build_backfill_patch(
            doc.get("custom_fields") or [], analysis, compta_field_id,
        )
        paperless_client.patch_document(doc_id, patch)
        written += 1
        print(f"#{doc_id} compta_json écrit — « {title} »")

    if args.dry_run:
        print(f"\n=== DRY-RUN terminé : {len(to_backfill)} candidats, rien écrit ===")
    else:
        print(f"\n=== {written} documents mis à jour ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
