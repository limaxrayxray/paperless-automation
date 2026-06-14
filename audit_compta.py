#!/usr/bin/env python3
"""Audit LECTURE SEULE de l'extraction `compta_json` sur les N derniers documents.

Pour chaque document récent : relance l'analyse Claude (OCR-first), construit le
payload `compta_json` et affiche un résumé (type, devise, total/taxes, items,
cohérence, needs_review). **N'écrit RIEN dans Paperless** — aucun patch, aucun tag.
À utiliser pour juger la qualité de l'extraction sur du réel avant de s'y fier.

⚠️ Appels réels : API Paperless (lecture) + Claude CLI (analyse, sur abonnement).
Jamais exécuté par le loop. Usage :

    python3 audit_compta.py [N]      # N = nombre de documents (défaut 10)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import claude_analyzer
import paperless_client
from compta_payload import build_compta_payload


def summarize(doc: dict, analysis: dict) -> dict:
    """Résumé pur (sans I/O) d'un document + son analyse, pour l'audit.

    `coherent` vaut True/False quand il y a des items (somme items + taxes ==
    total), ou None s'il n'y en a pas (rien à vérifier — repli ligne unique)."""
    payload = build_compta_payload(analysis)
    items_sum = sum(i["amount_cents"] for i in payload["items"])
    coherent = None
    if payload["items"]:
        coherent = (
            items_sum + payload["tps_cents"] + payload["tvq_cents"]
            == payload["total_cents"]
        )
    return {
        "id": doc.get("id"),
        "title": doc.get("title", ""),
        "doc_type": analysis.get("doc_type"),
        "currency": analysis.get("currency"),
        "supplier_foreign": analysis.get("supplier_foreign"),
        "total_cents": payload["total_cents"],
        "tps_cents": payload["tps_cents"],
        "tvq_cents": payload["tvq_cents"],
        "n_items": len(payload["items"]),
        "items_sum_cents": items_sum,
        "coherent": coherent,
        "needs_review": payload["needs_review"],
        "review_reason": payload["review_reason"],
    }


def _money(cents: int) -> str:
    return f"{cents / 100:.2f}$"


def _format_row(s: dict) -> str:
    if s["coherent"] is True:
        coh = "✓"
    elif s["coherent"] is False:
        coh = "✗ INCOHÉRENT"
    else:
        coh = "—"
    flags = []
    if s["currency"] and s["currency"] != "CAD":
        flags.append(f"⚠ {s['currency']}")
    if s["supplier_foreign"]:
        flags.append("étranger")
    if s["needs_review"]:
        flags.append(f"review: {s['review_reason']}")
    flag_str = ("  | " + " ; ".join(flags)) if flags else ""
    return (
        f"#{s['id']:<5} {str(s['doc_type'] or '?'):<12} "
        f"{(s['currency'] or '?'):<4} total={_money(s['total_cents']):>10} "
        f"tps={_money(s['tps_cents'])} tvq={_money(s['tvq_cents'])} "
        f"items={s['n_items']:<2} somme={coh}{flag_str}\n"
        f"       « {s['title']} »"
    )


def main(count: int = 10) -> int:
    docs = paperless_client.get_recent_documents(count)
    print(f"=== Audit compta_json — {len(docs)} documents les plus récents (lecture seule) ===\n")
    factures = 0
    for doc in docs:
        doc_id = doc.get("id")
        title = doc.get("title", "") or ""
        content = doc.get("content", "") or ""
        try:
            analysis = claude_analyzer.analyze_document_smart(doc_id, title, content)
        except Exception as e:  # noqa: BLE001 — audit : on continue sur le doc suivant
            print(f"#{doc_id:<5} ERREUR analyse: {e}\n")
            continue
        s = summarize(doc, analysis)
        if s["doc_type"] in ("facture", "recu"):
            factures += 1
        print(_format_row(s) + "\n")
    print(f"=== {factures}/{len(docs)} sont des factures/reçus ===")
    return 0


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    sys.exit(main(n))
