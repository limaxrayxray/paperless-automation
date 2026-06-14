"""Tests du branchement de `compta_json` dans doc_processor.build_custom_fields
(Phase 2, tâche 3).

Couvre :
- absence du champ `compta_json` dans `CUSTOM_FIELD_IDS` → rien écrit (tolérance,
  l'id réel n'est connu qu'après exécution manuelle d'`ensure_compta_field.py`) ;
- champ configuré → un JSON valide et conforme au contrat est écrit ;
- le JSON contient les montants en cents et reflète `needs_review` ;
- coexistence avec les champs hérités (TPS/TVQ/Total/Facture) ;
- écrit pour tous les types de document, y compris non pertinents ;
- stabilité/idempotence : même analyse → même chaîne JSON (clés triées).

`CUSTOM_FIELD_IDS` est le même dict importé par `config` et `doc_processor` ;
on l'augmente via `monkeypatch.setitem` (restauré automatiquement).
"""

import json

import doc_processor
import pytest
from compta_payload import COMPTA_CONTRACT_VERSION
from config import CUSTOM_FIELD_IDS
from doc_processor import build_custom_fields

COMPTA_ID = 99  # id fictif injecté dans CUSTOM_FIELD_IDS pour les tests
TPS_ID = CUSTOM_FIELD_IDS["TPS"]
TOTAL_ID = CUSTOM_FIELD_IDS["Total"]


def _by_id(payload: list[dict]) -> dict:
    return {cf["field"]: cf["value"] for cf in payload}


@pytest.fixture
def compta_field(monkeypatch):
    """Inscrit temporairement `compta_json` dans CUSTOM_FIELD_IDS."""
    monkeypatch.setitem(doc_processor.CUSTOM_FIELD_IDS, "compta_json", COMPTA_ID)
    return COMPTA_ID


# ─── Tolérance : champ non configuré ─────────────────────────────────────────

def test_sans_champ_compta_rien_ecrit(monkeypatch):
    # Si compta_json n'est PAS configuré, le contrat n'est pas écrit (tolérance).
    # Le champ est désormais déployé (id réel dans config) → on simule l'absence
    # en le retirant temporairement, ce qui exerce le garde-fou du code.
    monkeypatch.delitem(doc_processor.CUSTOM_FIELD_IDS, "compta_json", raising=False)
    fields = _by_id(build_custom_fields([], {"doc_type": "facture", "total": "10.00"}))
    assert set(fields) == {TOTAL_ID}  # uniquement le champ hérité, aucun compta_json


# ─── Champ configuré : JSON valide et conforme ───────────────────────────────

def test_compta_json_ecrit_quand_champ_configure(compta_field):
    analysis = {
        "doc_type": "facture",
        "total": "114.98",
        "tps": "5.00",
        "tvq": "9.98",
        "line_items": [{"description": "Service", "amount": "100.00", "taxable": True}],
        "correspondent": "Fournisseur X",
        "date": "2026-01-15",
        "_method": "ocr_text",
    }
    fields = _by_id(build_custom_fields([], analysis))
    assert compta_field in fields
    payload = json.loads(fields[compta_field])
    assert payload["version"] == COMPTA_CONTRACT_VERSION
    assert payload["total_cents"] == 11498
    assert payload["tps_cents"] == 500
    assert payload["tvq_cents"] == 998
    assert payload["items"] == [
        {"description": "Service", "amount_cents": 10000, "taxable": True},
    ]
    assert payload["fournisseur"] == "Fournisseur X"
    assert payload["date"] == "2026-01-15"
    assert payload["source_method"] == "ocr_text"
    assert payload["needs_review"] is False
    assert payload["review_reason"] is None


def test_compta_json_needs_review_incoherent(compta_field):
    # somme items (100$) + taxes (0) ≠ total (50$) → needs_review.
    analysis = {
        "doc_type": "facture",
        "total": "50.00",
        "line_items": [{"description": "X", "amount": "100.00"}],
    }
    payload = json.loads(_by_id(build_custom_fields([], analysis))[compta_field])
    assert payload["needs_review"] is True
    assert payload["review_reason"]


def test_compta_json_items_vides_needs_review(compta_field):
    analysis = {"doc_type": "recu", "total": "20.00"}
    payload = json.loads(_by_id(build_custom_fields([], analysis))[compta_field])
    assert payload["needs_review"] is True
    assert "items vides" in payload["review_reason"]


# ─── Coexistence avec les champs hérités ─────────────────────────────────────

def test_compta_json_coexiste_avec_champs_herites(compta_field):
    analysis = {
        "doc_type": "facture",
        "total": "10.00",
        "tps": "5.00",
    }
    fields = _by_id(build_custom_fields([], analysis))
    assert fields[TOTAL_ID] == "10.00"
    assert fields[TPS_ID] == "5.00"
    assert compta_field in fields


# ─── Écrit pour tous les types, y compris non pertinents ─────────────────────

def test_compta_json_ecrit_meme_type_non_pertinent(compta_field):
    # Un rapport n'a pas de champs hérités, mais le contrat compta_json est posé.
    fields = _by_id(build_custom_fields([], {"doc_type": "rapport", "total": "10.00"}))
    assert TOTAL_ID not in fields  # champ hérité non écrit pour ce type
    assert compta_field in fields
    payload = json.loads(fields[compta_field])
    assert payload["version"] == COMPTA_CONTRACT_VERSION


# ─── Stabilité / idempotence du JSON sérialisé ───────────────────────────────

def test_compta_json_serialisation_stable(compta_field):
    analysis = {
        "doc_type": "facture",
        "total": "114.98",
        "tps": "5.00",
        "tvq": "9.98",
        "line_items": [{"description": "Service", "amount": "100.00", "taxable": True}],
        "correspondent": "Z",
        "date": "2026-01-15",
        "_method": "ocr_text",
    }
    a = _by_id(build_custom_fields([], analysis))[compta_field]
    b = _by_id(build_custom_fields([], analysis))[compta_field]
    assert a == b


def test_compta_json_existant_ecrase_par_nouvelle_valeur(compta_field):
    existing = [{"field": compta_field, "value": '{"version": 0}'}]
    fields = _by_id(build_custom_fields(existing, {"doc_type": "autre", "total": "1.00"}))
    payload = json.loads(fields[compta_field])
    assert payload["version"] == COMPTA_CONTRACT_VERSION
