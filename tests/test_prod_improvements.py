"""Tests des améliorations de production réintégrées (currency / supplier_foreign,
détection fiscale affinée, nettoyage trigger-tags, discard error-tag au succès).

Ces comportements viennent du code prod jamais commité; ils sont désormais
canoniques — couverts ici pour ne pas être fusionnés à l'aveugle.
"""

from claude_analyzer import _validate_and_clean
from doc_processor import build_tag_updates
from config import ERROR_TAG_ID, TAG_IDS, TRIGGER_TAG_IDS


def _doc(**over) -> dict:
    """Facture canadienne de base (montants déjà en chaîne décimale)."""
    base = {
        "doc_type": "facture",
        "total": "100.00",
        "tps": "0.00",
        "tvq": "0.00",
        "confidence": 0.9,
        "line_items": [],
    }
    base.update(over)
    return base


# ─── currency / supplier_foreign : normalisation ────────────────────────────────

def test_currency_defaut_cad():
    r = _validate_and_clean(_doc(total="10.00"))
    assert r["currency"] == "CAD"


def test_currency_normalisee_majuscule():
    r = _validate_and_clean(_doc(total="10.00", currency="usd"))
    assert r["currency"] == "USD"


def test_supplier_foreign_normalise_en_bool():
    assert _validate_and_clean(_doc(total="10.00"))["supplier_foreign"] is False
    assert _validate_and_clean(_doc(total="10.00", supplier_foreign="oui"))["supplier_foreign"] is True


# ─── Détection fiscale affinée ──────────────────────────────────────────────────

def test_fournisseur_canadien_sans_tvq_flague():
    """CAD, facture > 20 $, TVQ=0 → suspect (reçu thermique mal scanné)."""
    r = _validate_and_clean(_doc())
    assert r["confidence"] == 0.60
    assert "ATTENTION" in r["notes"]
    assert "canadien sans TVQ" in r["notes"]


def test_fournisseur_etranger_sans_taxes_non_flague():
    """Fournisseur hors Canada sans TPS/TVQ → NORMAL, pas d'alerte (régression évitée)."""
    r = _validate_and_clean(_doc(supplier_foreign=True))
    assert r["confidence"] == 0.9
    assert "ATTENTION" not in (r.get("notes") or "")


def test_devise_etrangere_vaut_etranger():
    """Devise non-CAD → traité comme étranger même sans supplier_foreign explicite."""
    r = _validate_and_clean(_doc(currency="USD"))
    assert r["confidence"] == 0.9
    assert "ATTENTION" not in (r.get("notes") or "")


def test_asymetrie_tps_sans_tvq_flague_meme_etranger():
    """TPS perçue mais TVQ=0 : impossible au QC → toujours suspect, même étranger."""
    r = _validate_and_clean(_doc(supplier_foreign=True, tps="5.00", tvq="0.00"))
    assert r["confidence"] == 0.60
    assert "TPS perçue mais TVQ" in r["notes"]


# ─── build_tag_updates : nettoyage trigger-tags + discard error-tag ─────────────

def _analysis(**over) -> dict:
    base = {"doc_type": "facture", "tags_to_add": [], "confidence": 0.9,
            "date": None, "date_confidence": 0.0}
    base.update(over)
    return base


def test_trigger_tags_retires_apres_traitement():
    trigger = sorted(TRIGGER_TAG_IDS)[0]
    result = build_tag_updates([trigger], _analysis())
    assert trigger not in result
    assert TAG_IDS["facture"] in result  # classification bien appliquée


def test_error_tag_efface_au_succes():
    """Une analyse réussie retire le tag erreur-traitement d'un passage précédent."""
    result = build_tag_updates([ERROR_TAG_ID], _analysis())
    assert ERROR_TAG_ID not in result
