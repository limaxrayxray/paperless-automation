"""Contrat d'unification `compta_json` — construit le payload exposé à compta-rapidetech.

Fonction pure `build_compta_payload(analysis) -> dict` : convertit le résultat
validé de `claude_analyzer` (montants en chaînes décimales / floats) en l'objet
JSON auto-suffisant décrit dans SPEC.md. Tous les montants sont en **cents entiers**
(jamais de float pour l'argent). Aucun effet de bord, aucun appel réseau.

Voir SPEC.md § « Contrat d'unification — champ `compta_json` ».
"""

import re
from decimal import ROUND_HALF_UP
from decimal import Decimal
from decimal import InvalidOperation

# Version du contrat. Incrémenter à chaque changement de format observable par le
# consommateur (compta-rapidetech). Un champ inconnu côté consommateur est ignoré.
# v2 (2026-06-14) : ajoute doc_type, currency, supplier_foreign au payload.
# v3 (2026-06-22) : ajoute upc, qty, unit_price_cents par ligne d'items.
COMPTA_CONTRACT_VERSION = 3


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


def _valid_upc(digits: str) -> str | None:
    """Valide un UPC-A (12 chiffres) via son check digit (mod-10).

    Renvoie les 12 chiffres si le checksum est cohérent, sinon None. Sert de
    garde-fou anti-OCR au sein de `_clean_sku` (un UPC mal lu est rejeté plutôt
    qu'émis faux). Checksum : (11 premiers chiffres, positions impaires x3 +
    positions paires x1) ; check = (10 - somme mod 10) mod 10.
    """
    if len(digits) != 12 or not digits.isdigit():
        return None
    odd = sum(int(d) for d in digits[0:11:2])   # positions 1,3,5,7,9,11
    even = sum(int(d) for d in digits[1:11:2])   # positions 2,4,6,8,10
    check = (10 - ((odd * 3 + even) % 10)) % 10
    return digits if check == int(digits[11]) else None


def _clean_sku(value) -> str | None:
    """Normalise un code produit fournisseur (SKU) — flexible, non destructif.

    Le code peut être un UPC, un ASIN Amazon, un n° d'article Canadian Tire, une
    référence DigitalOcean, etc. On émet ce qu'on lit : le but est de pouvoir
    ré-identifier un item d'un achat à l'autre, pas d'imposer un format.

    Seul garde-fou : si le code se présente comme un UPC-A (12 chiffres purs,
    séparateurs tolérés), on valide son check digit et on rejette (→ None) un
    checksum incohérent (lecture OCR erronée). Tout autre format est conservé tel
    quel. L'aval matche de toute façon surtout sur fournisseur+description.
    """
    if value is None:
        return None
    sku = str(value).strip()
    if not sku or sku.lower() == "null":
        return None
    compact = re.sub(r"[\s-]", "", sku)
    if len(compact) == 12 and compact.isdigit():
        return _valid_upc(compact)   # ressemble à un UPC-A → garde-fou checksum
    return sku


def _line_qty_and_unit(item: dict, amount_cents: int) -> tuple[int, int]:
    """Déduit (qty, unit_price_cents) en respectant l'invariant v3.

    Invariant : `amount_cents == qty * unit_price_cents` (arrondi au cent).
    `amount_cents` est autoritatif (montant HT, jamais recalculé). Règles :
    - qty entier >= 1 (défaut 1) ;
    - si un unit_price exact est fourni (qty*unit == amount) → on le garde ;
    - sinon on conserve la qté connue et on déduit le prix unitaire (arrondi au
      cent ; l'invariant peut alors être exact « au cent près ») ;
    - pas de qté claire (qty=1) → unit_price = amount.

    On préserve la quantité même quand `amount` n'est pas divisible : la fidélité
    au document (ex. reçus SAQ dé-taxés où amount_HT n'est pas un multiple net)
    prime sur une égalité au cent exacte. amount_cents reste la vérité de la ligne.
    """
    try:
        qty = int(item.get("qty", 1))
    except (TypeError, ValueError):
        qty = 1
    if qty < 1:
        qty = 1

    if qty == 1:
        return 1, amount_cents

    unit_price_cents = _to_cents(item.get("unit_price"))
    if (unit_price_cents is not None and unit_price_cents > 0
            and qty * unit_price_cents == amount_cents):
        return qty, unit_price_cents

    # Prix unitaire absent/incohérent → déduire de amount/qty (arrondi au cent).
    return qty, (amount_cents + qty // 2) // qty


def _detax_lines(lines: list[dict], tax_total_cents: int) -> None:
    """Convertit en HT les lignes dont le prix affiché inclut déjà les taxes.

    Cas SAQ : chaque ligne produit est en TTC, la consigne (taxable=False) ne l'est
    pas, et seuls les totaux TPS/TVQ figurent au bas. On répartit la base HT
    (= somme TTC taxable - taxes) au prorata du TTC de chaque ligne taxable ; le
    résidu d'arrondi est absorbé par la dernière ligne taxable. Ainsi
    `somme(amount HT) + tps + tvq == total` reste exact, sans supposer de taux.

    Mute `lines` en place (chaque dict porte `amount_cents` et `taxable`). Les
    lignes non taxables (consigne) sont laissées telles quelles.
    """
    taxable = [l for l in lines if l["taxable"] and l["amount_cents"] > 0]
    taxable_ttc = sum(l["amount_cents"] for l in taxable)
    target_ht = taxable_ttc - tax_total_cents
    if not taxable or taxable_ttc <= 0 or tax_total_cents <= 0 or target_ht <= 0:
        return  # rien à dé-taxer (taxes nulles, fournisseur étranger, données KO)

    running = 0
    for line in taxable[:-1]:
        ht = round(line["amount_cents"] * target_ht / taxable_ttc)
        line["amount_cents"] = ht
        running += ht
    taxable[-1]["amount_cents"] = target_ht - running


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

    # Lignes brutes (montant tel que lu : HT en temps normal, TTC si la source
    # affiche des prix taxes-incluses comme la SAQ — cf. line_amounts_include_tax).
    lines = []
    for item in analysis.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        amount_cents = _to_cents(item.get("amount"))
        description = str(item.get("description", "")).strip()
        # Repli : sans code produit explicite (ex. facture Claude « Claude Pro »),
        # la description SERT de sku pour pouvoir ré-identifier l'item. sku == desc
        # dans ce cas. None seulement si ni code ni description.
        sku = _clean_sku(item.get("sku")) or description or None
        lines.append({
            "item": item,
            "description": description,
            "amount_cents": amount_cents if amount_cents is not None else 0,
            "taxable": bool(item.get("taxable", True)),
            "sku": sku,
        })

    # Source à prix taxes-incluses (SAQ…) : ramener les lignes taxables en HT pour
    # harmoniser avec les autres factures avant tout calcul qty/unit_price.
    if analysis.get("line_amounts_include_tax"):
        _detax_lines(lines, tps_cents + tvq_cents)

    items = []
    for line in lines:
        qty, unit_price_cents = _line_qty_and_unit(line["item"], line["amount_cents"])
        items.append({
            "description": line["description"],
            "amount_cents": line["amount_cents"],
            "taxable": line["taxable"],
            "sku": line["sku"],
            "qty": qty,
            "unit_price_cents": unit_price_cents,
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
