"""Contrat d'unification `compta_json` — construit le payload exposé à compta-rapidetech.

Fonction pure `build_compta_payload(analysis) -> dict` : convertit le résultat
validé de `claude_analyzer` (montants en chaînes décimales / floats) en l'objet
JSON auto-suffisant décrit dans SPEC.md. Tous les montants sont en **cents entiers**
(jamais de float pour l'argent). Aucun effet de bord, aucun appel réseau.

Voir SPEC.md § « Contrat d'unification — champ `compta_json` ».
"""

from decimal import ROUND_HALF_UP
from decimal import Decimal
from decimal import InvalidOperation

# Version du contrat. Incrémenter à chaque changement de format observable par le
# consommateur (compta-rapidetech). Un champ inconnu côté consommateur est ignoré.
# v2 (2026-06-14) : ajoute doc_type, currency, supplier_foreign au payload.
COMPTA_CONTRACT_VERSION = 2


def _to_cents(value) -> int | None:
    """Convertit un montant décimal (chaîne « 66.81 » ou nombre) en cents entiers.

    Retourne None si la valeur est None ou illisible. Utilise Decimal pour éviter
    les erreurs de virgule flottante; arrondi au cent le plus proche (HALF_UP).
    """
    if value is None:
        return None
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    cents = (dec * 100).to_integral_value(rounding=ROUND_HALF_UP)
    return int(cents)


def build_compta_payload(analysis: dict) -> dict:
    """Assemble le contrat `compta_json` à partir d'une analyse validée.

    - Montants (`total`/`tps`/`tvq`, `line_items[].amount`) → cents entiers.
    - `tps_cents`/`tvq_cents` valent 0 si absents (taxe non perçue).
    - `needs_review` est posé (avec raison) si : total manquant, items vides, ou
      incohérence somme(items) + taxes ≠ total. Le producteur n'invente jamais de
      ligne pour forcer l'équilibre.
    """
    total_raw = _to_cents(analysis.get("total"))
    total_missing = total_raw is None
    total_cents = total_raw if total_raw is not None else 0

    tps_cents = _to_cents(analysis.get("tps")) or 0
    tvq_cents = _to_cents(analysis.get("tvq")) or 0

    items = []
    for item in analysis.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        amount_cents = _to_cents(item.get("amount"))
        items.append({
            "description": str(item.get("description", "")).strip(),
            "amount_cents": amount_cents if amount_cents is not None else 0,
            "taxable": bool(item.get("taxable", True)),
        })

    reasons: list[str] = []
    if total_missing:
        reasons.append("total manquant")
    if not items:
        reasons.append("items vides — repli ligne unique requis")
    elif not total_missing:
        items_sum = sum(i["amount_cents"] for i in items)
        expected = items_sum + tps_cents + tvq_cents
        if expected != total_cents:
            reasons.append(
                f"incohérence: somme items ({items_sum}) + TPS ({tps_cents}) "
                f"+ TVQ ({tvq_cents}) = {expected} ≠ total ({total_cents})",
            )

    needs_review = bool(reasons)
    review_reason = "; ".join(reasons) if reasons else None

    # v2 : type de document, devise et exemption fournisseur étranger voyagent dans
    # le payload. Une devise ≠ CAD n'est PAS marquée needs_review côté producteur —
    # c'est au consommateur (compta-rapidetech) de décider quoi en faire (cf. SPEC.md).
    raw_currency = analysis.get("currency")
    currency = str(raw_currency).strip().upper() if raw_currency else "CAD"

    return {
        "version": COMPTA_CONTRACT_VERSION,
        "doc_type": analysis.get("doc_type"),
        "fournisseur": analysis.get("correspondent"),
        "supplier_foreign": bool(analysis.get("supplier_foreign", False)),
        "date": analysis.get("date"),
        "currency": currency or "CAD",
        "total_cents": total_cents,
        "tps_cents": tps_cents,
        "tvq_cents": tvq_cents,
        "items": items,
        "needs_review": needs_review,
        "review_reason": review_reason,
        "source_method": analysis.get("_method") or "unknown",
    }
