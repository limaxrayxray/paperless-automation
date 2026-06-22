"""Tests de compta_payload.build_compta_payload (Phase 2, tâche 2).

Couvre :
- conversion des montants décimaux (chaîne ou float) en cents entiers ;
- cohérence somme(items) + tps + tvq == total → pas de needs_review ;
- needs_review + raison quand écart, items vides, ou total manquant ;
- assemblage du contrat SPEC (version, fournisseur, date, source_method).

Fonction pure : aucun mock nécessaire.
"""

from compta_payload import COMPTA_CONTRACT_VERSION
from compta_payload import _clean_sku
from compta_payload import _to_cents
from compta_payload import _valid_upc
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

def test_version_contrat_est_3():
    assert COMPTA_CONTRACT_VERSION == 3
    payload = build_compta_payload(_facture())
    assert payload["version"] == 3


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


# ─── Contrat v3 : sku, qty, unit_price_cents ─────────────────────────────────

def test_valid_upc_checksum_ok():
    # UPC-A valide (check digit correct). Exemple Wikipedia : 036000291452.
    assert _valid_upc("036000291452") == "036000291452"


def test_valid_upc_checksum_ko_renvoie_none():
    # Dernier chiffre faux → checksum KO → None (on ne devine pas).
    assert _valid_upc("036000291453") is None


def test_valid_upc_longueur_invalide_renvoie_none():
    assert _valid_upc("12345") is None          # trop court
    assert _valid_upc("0123456789012") is None  # 13 chiffres (EAN-13)


def test_clean_sku_upc_valide_normalise():
    # UPC-A pur (séparateurs tolérés) → validé par checksum, renvoyé compact.
    assert _clean_sku("036000291452") == "036000291452"
    assert _clean_sku("  0 36000-29145 2 ") == "036000291452"


def test_clean_sku_upc_invalide_devient_null():
    # 12 chiffres mais checksum KO → lecture OCR douteuse → None.
    assert _clean_sku("036000291453") is None
    assert _clean_sku("111111111111") is None


def test_clean_sku_format_non_upc_passe_tel_quel():
    # Codes non-UPC : conservés tels quels (flexibilité fournisseurs).
    assert _clean_sku("B07XYZ1234") == "B07XYZ1234"        # ASIN Amazon
    assert _clean_sku("087-1234-6") == "087-1234-6"        # n° Canadian Tire
    assert _clean_sku("do-droplet-s-1vcpu") == "do-droplet-s-1vcpu"  # DigitalOcean
    assert _clean_sku("12345") == "12345"                  # code court numérique


def test_clean_sku_vide_ou_null_devient_none():
    assert _clean_sku(None) is None
    assert _clean_sku("") is None
    assert _clean_sku("   ") is None
    assert _clean_sku("null") is None


def test_item_v3_champs_par_defaut_retrocompat():
    # Document sans sku/qty/unit_price → JSON v3 valide. Sans code, sku=description.
    payload = build_compta_payload(_facture(line_items=[
        {"description": "Vis", "amount": 100.00, "taxable": True},
    ]))
    item = payload["items"][0]
    assert item["sku"] == "Vis"   # repli sur la description
    assert item["qty"] == 1
    assert item["unit_price_cents"] == item["amount_cents"] == 10000


def test_item_v3_sku_repli_sur_description():
    # Facture Claude : pas de code produit, juste « Claude Pro » → sku = desc.
    payload = build_compta_payload(_facture(line_items=[
        {"description": "Claude Pro", "amount": 100.00, "taxable": True},
    ]))
    item = payload["items"][0]
    assert item["sku"] == "Claude Pro"
    assert item["description"] == "Claude Pro"


def test_item_v3_sku_none_si_ni_code_ni_description():
    payload = build_compta_payload(_facture(line_items=[
        {"description": "", "amount": 100.00, "taxable": True},
    ]))
    assert payload["items"][0]["sku"] is None


def test_item_v3_sku_code_prime_sur_description():
    # Quand un vrai code est présent, il l'emporte sur la description.
    payload = build_compta_payload(_facture(line_items=[
        {"description": "Eau", "amount": 100.00, "taxable": True, "sku": "B07XYZ1234"},
    ]))
    assert payload["items"][0]["sku"] == "B07XYZ1234"


def test_item_v3_qty_et_unit_price_coherents():
    # 2 x 6.17 = 12.34 → invariant respecté, valeurs conservées.
    payload = build_compta_payload(_facture(
        total="14.20", tps="0.62", tvq="1.24",
        line_items=[{
            "description": "Eau", "amount": 12.34, "taxable": True,
            "sku": "036000291452", "qty": 2, "unit_price": 6.17,
        }],
    ))
    item = payload["items"][0]
    assert item["sku"] == "036000291452"
    assert item["qty"] == 2
    assert item["unit_price_cents"] == 617
    assert item["amount_cents"] == 1234
    assert item["qty"] * item["unit_price_cents"] == item["amount_cents"]


def test_item_v3_sku_asin_conserve():
    # Code produit non-UPC (ASIN Amazon) conservé tel quel sur la ligne.
    payload = build_compta_payload(_facture(line_items=[
        {"description": "Cable USB", "amount": 100.00, "taxable": True,
         "sku": "B07XYZ1234"},
    ]))
    assert payload["items"][0]["sku"] == "B07XYZ1234"


def test_item_v3_invariant_repli_unit_price_incoherent():
    # unit_price fourni incohérent (qty*unit != amount) → on déduit de amount.
    payload = build_compta_payload(_facture(line_items=[
        {"description": "X", "amount": 12.34, "taxable": True,
         "qty": 2, "unit_price": 99.99},
    ]))
    item = payload["items"][0]
    assert item["qty"] == 2
    assert item["unit_price_cents"] == 617
    assert item["qty"] * item["unit_price_cents"] == item["amount_cents"] == 1234


def test_item_v3_qty_preservee_amount_non_divisible():
    # amount non divisible par qty → qté conservée, unit_price arrondi au cent
    # (invariant exact « au cent près »), amount reste autoritatif.
    payload = build_compta_payload(_facture(line_items=[
        {"description": "X", "amount": 10.00, "taxable": True, "qty": 3},
    ]))
    item = payload["items"][0]
    assert item["qty"] == 3
    assert item["unit_price_cents"] == 333   # round(1000/3)
    assert item["amount_cents"] == 1000


def test_item_v3_qty_illisible_ou_negative_defaut_1():
    payload = build_compta_payload(_facture(line_items=[
        {"description": "A", "amount": 50.00, "taxable": True, "qty": "abc"},
        {"description": "B", "amount": 50.00, "taxable": True, "qty": 0},
    ]))
    assert payload["items"][0]["qty"] == 1
    assert payload["items"][1]["qty"] == 1


def test_v3_n_affecte_pas_la_coherence_des_taxes():
    # L'ajout des champs v3 ne change pas amount_cents → contrôle total intact.
    payload = build_compta_payload(_facture(
        total="14.20", tps="0.62", tvq="1.24",
        line_items=[{
            "description": "Eau", "amount": 12.34, "taxable": True,
            "sku": "036000291452", "qty": 2, "unit_price": 6.17,
        }],
    ))
    # 1234 + 62 + 124 == 1420
    assert payload["needs_review"] is False
    assert payload["review_reason"] is None


# ─── v3 — prix taxes-incluses (SAQ) : dé-taxe des lignes ─────────────────────

def _saq(**overrides) -> dict:
    """Reçu SAQ : prix par ligne TTC, consigne non taxable, taxes au bas.

    2 vins taxables (TTC 30.00 + 20.00 = 50.00) + consigne 0.20.
    Base HT = 50.00 - (tps 2.17 + tvq 4.34) = 43.49. Total = 50.20.
    """
    base = {
        "doc_type": "recu",
        "correspondent": "SAQ",
        "date": "2026-05-17",
        "currency": "CAD",
        "total": "50.20",
        "tps": "2.17",
        "tvq": "4.34",
        "line_amounts_include_tax": True,
        "line_items": [
            {"description": "Vin A", "amount": 30.00, "taxable": True, "qty": 2},
            {"description": "Vin B", "amount": 20.00, "taxable": True, "qty": 1},
            {"description": "Consigne", "amount": 0.20, "taxable": False, "qty": 1},
        ],
        "_method": "vision_primary",
    }
    base.update(overrides)
    return base


def test_saq_detax_invariant_total():
    payload = build_compta_payload(_saq())
    items = payload["items"]
    # Lignes taxables ramenées en HT : 2609 + 1740 = 4349 ; consigne inchangée 20.
    assert items[0]["amount_cents"] == 2609   # round(3000 * 4349/5000)
    assert items[1]["amount_cents"] == 1740   # résidu absorbé par la dernière
    assert items[2]["amount_cents"] == 20     # consigne intacte
    # somme(HT) + tps + tvq == total
    somme = sum(i["amount_cents"] for i in items)
    assert somme + payload["tps_cents"] + payload["tvq_cents"] == payload["total_cents"]
    assert payload["needs_review"] is False
    assert payload["review_reason"] is None


def test_saq_consigne_reste_non_taxable_et_intacte():
    payload = build_compta_payload(_saq())
    consigne = payload["items"][2]
    assert consigne["taxable"] is False
    assert consigne["amount_cents"] == 20
    assert consigne["qty"] == 1
    assert consigne["unit_price_cents"] == 20


def test_saq_qty_preservee_apres_detax():
    payload = build_compta_payload(_saq())
    vin_a = payload["items"][0]
    assert vin_a["qty"] == 2
    # unit_price déduit du HT (arrondi au cent) : round(2609/2) -> 1305
    assert vin_a["unit_price_cents"] == 1305


def test_sans_flag_les_montants_restent_ttc():
    # Même reçu mais sans line_amounts_include_tax : pas de dé-taxe → incohérent.
    payload = build_compta_payload(_saq(line_amounts_include_tax=False))
    assert payload["items"][0]["amount_cents"] == 3000  # TTC inchangé
    assert payload["needs_review"] is True
    assert "incohérence" in payload["review_reason"]


def test_detax_ignore_si_taxes_nulles():
    # Flag posé mais aucune taxe (ex. exonéré) → rien à dé-taxer, montants intacts.
    analysis = _saq(tps="0.00", tvq="0.00", total="50.20")
    payload = build_compta_payload(analysis)
    assert payload["items"][0]["amount_cents"] == 3000
    assert payload["items"][1]["amount_cents"] == 2000


def test_detax_une_seule_ligne_taxable():
    analysis = _saq(
        total="20.20", tps="0.87", tvq="1.74",
        line_items=[
            {"description": "Vin", "amount": 20.00, "taxable": True, "qty": 1},
            {"description": "Consigne", "amount": 0.20, "taxable": False, "qty": 1},
        ],
    )
    payload = build_compta_payload(analysis)
    # HT = 2000 - (87+174) = 1739 sur l'unique ligne taxable.
    assert payload["items"][0]["amount_cents"] == 1739
    assert payload["items"][1]["amount_cents"] == 20
    somme = sum(i["amount_cents"] for i in payload["items"])
    assert somme + payload["tps_cents"] + payload["tvq_cents"] == payload["total_cents"]
    assert payload["needs_review"] is False


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
