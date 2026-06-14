"""Tests de compta_payload.build_compta_payload (Phase 2, tâche 2).

Couvre :
- conversion des montants décimaux (chaîne ou float) en cents entiers ;
- cohérence somme(items) + tps + tvq == total → pas de needs_review ;
- needs_review + raison quand écart, items vides, ou total manquant ;
- assemblage du contrat SPEC (version, fournisseur, date, source_method).

Fonction pure : aucun mock nécessaire.
"""

from compta_payload import COMPTA_CONTRACT_VERSION
from compta_payload import _to_cents
from compta_payload import build_compta_payload


def _facture(**overrides) -> dict:
    """Analyse cohérente de base (facture équilibrée) ; overrides au besoin."""
    base = {
        "doc_type": "facture",
        "correspondent": "RONA",
        "date": "2026-03-15",
        "total": "114.98",
        "tps": "5.00",
        "tvq": "9.98",
        "line_items": [
            {"description": "Vis", "amount": 100.00, "taxable": True},
        ],
        "_method": "ocr_text",
    }
    base.update(overrides)
    return base


# ─── Conversion en cents ──────────────────────────────────────────────────────

def test_conversion_chaine_decimale():
    assert _to_cents("66.81") == 6681
    assert _to_cents("80.00") == 8000
    assert _to_cents("0.00") == 0


def test_conversion_float():
    assert _to_cents(100.0) == 10000
    assert _to_cents(66.81) == 6681


def test_conversion_arrondi_half_up():
    # Pas d'erreur de virgule flottante : Decimal + HALF_UP.
    assert _to_cents("1.005") == 101
    assert _to_cents("2.994") == 299


def test_conversion_none_et_illisible():
    assert _to_cents(None) is None
    assert _to_cents("abc") is None
    assert _to_cents("") is None


def test_montants_payload_en_cents():
    payload = build_compta_payload(_facture())
    assert payload["total_cents"] == 11498
    assert payload["tps_cents"] == 500
    assert payload["tvq_cents"] == 998
    assert payload["items"][0]["amount_cents"] == 10000


# ─── Cohérence : pas de needs_review quand somme + taxes == total ────────────

def test_coherence_equilibree_pas_de_review():
    payload = build_compta_payload(_facture())
    # 10000 + 500 + 998 == 11498
    assert payload["needs_review"] is False
    assert payload["review_reason"] is None


def test_coherence_plusieurs_items():
    analysis = _facture(
        total="230.00",
        tps="10.00",
        tvq="20.00",
        line_items=[
            {"description": "A", "amount": 120.00, "taxable": True},
            {"description": "B", "amount": 80.00, "taxable": False},
        ],
    )
    payload = build_compta_payload(analysis)
    # (12000 + 8000) + 1000 + 2000 == 23000
    assert payload["needs_review"] is False
    assert payload["review_reason"] is None


# ─── needs_review : écart, items vides, total manquant ───────────────────────

def test_ecart_declenche_review():
    # somme items (10000) + taxes (1498) = 11498 ≠ total 12000
    payload = build_compta_payload(_facture(total="120.00"))
    assert payload["needs_review"] is True
    assert "incohérence" in payload["review_reason"]
    assert "11498" in payload["review_reason"]
    assert "12000" in payload["review_reason"]


def test_items_vides_declenche_review():
    payload = build_compta_payload(_facture(line_items=[]))
    assert payload["items"] == []
    assert payload["needs_review"] is True
    assert "items vides" in payload["review_reason"]


def test_items_absents_declenche_review():
    analysis = _facture()
    del analysis["line_items"]
    payload = build_compta_payload(analysis)
    assert payload["items"] == []
    assert payload["needs_review"] is True
    assert "items vides" in payload["review_reason"]


def test_total_manquant_declenche_review():
    payload = build_compta_payload(_facture(total=None))
    assert payload["total_cents"] == 0
    assert payload["needs_review"] is True
    assert "total manquant" in payload["review_reason"]


def test_total_manquant_pas_de_controle_de_coherence():
    # Avec total manquant on signale "total manquant" mais pas d'écart
    # (on ne peut pas comparer à un total inexistant).
    payload = build_compta_payload(_facture(total=None))
    assert "total manquant" in payload["review_reason"]
    assert "incohérence" not in payload["review_reason"]


def test_raisons_cumulees_total_manquant_et_items_vides():
    payload = build_compta_payload(_facture(total=None, line_items=[]))
    assert payload["needs_review"] is True
    assert "total manquant" in payload["review_reason"]
    assert "items vides" in payload["review_reason"]


# ─── Taxes absentes → 0 cents ────────────────────────────────────────────────

def test_taxes_none_valent_zero():
    analysis = _facture(tps=None, tvq=None, total="100.00")
    payload = build_compta_payload(analysis)
    assert payload["tps_cents"] == 0
    assert payload["tvq_cents"] == 0
    # 10000 + 0 + 0 == 10000 → équilibré
    assert payload["needs_review"] is False


# ─── Assemblage du contrat ───────────────────────────────────────────────────

def test_contrat_champs_de_base():
    payload = build_compta_payload(_facture())
    assert payload["version"] == COMPTA_CONTRACT_VERSION
    assert payload["fournisseur"] == "RONA"
    assert payload["date"] == "2026-03-15"
    assert payload["source_method"] == "ocr_text"


def test_fournisseur_et_date_null():
    analysis = _facture(correspondent=None, date=None)
    payload = build_compta_payload(analysis)
    assert payload["fournisseur"] is None
    assert payload["date"] is None


def test_source_method_absent_defaut_unknown():
    analysis = _facture()
    del analysis["_method"]
    payload = build_compta_payload(analysis)
    assert payload["source_method"] == "unknown"


def test_item_description_et_taxable_normalises():
    analysis = _facture(line_items=[
        {"description": "  Service  ", "amount": 100.00},  # taxable absent → True
    ])
    payload = build_compta_payload(analysis)
    assert payload["items"][0]["description"] == "Service"
    assert payload["items"][0]["taxable"] is True


def test_item_amount_illisible_vaut_zero():
    analysis = _facture(line_items=[
        {"description": "X", "amount": "abc", "taxable": True},
    ])
    payload = build_compta_payload(analysis)
    assert payload["items"][0]["amount_cents"] == 0


# ─── Contrat v2 : doc_type, currency, supplier_foreign ───────────────────────

def test_version_contrat_est_2():
    assert COMPTA_CONTRACT_VERSION == 2
    payload = build_compta_payload(_facture())
    assert payload["version"] == 2


def test_v2_champs_presents():
    analysis = _facture(doc_type="facture", currency="CAD", supplier_foreign=False)
    payload = build_compta_payload(analysis)
    assert payload["doc_type"] == "facture"
    assert payload["currency"] == "CAD"
    assert payload["supplier_foreign"] is False


def test_doc_type_repris_de_lanalyse():
    payload = build_compta_payload(_facture(doc_type="recu"))
    assert payload["doc_type"] == "recu"


def test_doc_type_absent_reste_none():
    analysis = _facture()
    del analysis["doc_type"]
    payload = build_compta_payload(analysis)
    assert payload["doc_type"] is None


def test_currency_defaut_cad_si_absente():
    analysis = _facture()
    assert "currency" not in analysis
    payload = build_compta_payload(analysis)
    assert payload["currency"] == "CAD"


def test_currency_defaut_cad_si_vide():
    payload = build_compta_payload(_facture(currency=""))
    assert payload["currency"] == "CAD"
    payload = build_compta_payload(_facture(currency=None))
    assert payload["currency"] == "CAD"


def test_currency_normalisee_majuscules():
    payload = build_compta_payload(_facture(currency=" usd "))
    assert payload["currency"] == "USD"


def test_supplier_foreign_defaut_false():
    analysis = _facture()
    assert "supplier_foreign" not in analysis
    payload = build_compta_payload(analysis)
    assert payload["supplier_foreign"] is False


def test_supplier_foreign_true_conserve():
    payload = build_compta_payload(_facture(supplier_foreign=True))
    assert payload["supplier_foreign"] is True


def test_usd_etranger_sans_taxe_reste_coherent():
    # Fournisseur étranger payé en USD, sans TPS/TVQ : NORMAL, pas de needs_review
    # parasite tant que somme(items) + taxes == total.
    analysis = _facture(
        doc_type="facture",
        currency="USD",
        supplier_foreign=True,
        total="100.00",
        tps=None,
        tvq=None,
        line_items=[{"description": "Cloudflare", "amount": 100.00, "taxable": True}],
    )
    payload = build_compta_payload(analysis)
    assert payload["currency"] == "USD"
    assert payload["supplier_foreign"] is True
    assert payload["tps_cents"] == 0
    assert payload["tvq_cents"] == 0
    assert payload["needs_review"] is False
    assert payload["review_reason"] is None
