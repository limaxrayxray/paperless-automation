"""Tests des fonctions pures de `backfill_compta_json` — sélection des documents à
backfiller et construction du patch. AUCUN réseau : on ne touche ni à l'API
Paperless ni à Claude CLI (`main` et `_fetch_candidate_documents` ne sont pas
testés ici, ce sont les seuls à faire des appels réels)."""

import json

import backfill_compta_json as bf
from compta_payload import COMPTA_CONTRACT_VERSION

COMPTA_FIELD_ID = 17
OTHER_FIELD_ID = 15  # Total (champ hérité)


def _doc(doc_id, custom_fields=None):
    return {
        "id": doc_id,
        "title": f"Doc {doc_id}",
        "content": "contenu",
        "custom_fields": custom_fields or [],
    }


def _compta_cf(version):
    payload = {"version": version, "total_cents": 0}
    return {"field": COMPTA_FIELD_ID, "value": json.dumps(payload)}


# ─── needs_backfill ───────────────────────────────────────────────────────────

def test_needs_backfill_champ_absent():
    assert bf.needs_backfill(_doc(1), COMPTA_FIELD_ID) is True


def test_needs_backfill_autres_champs_seulement():
    doc = _doc(1, [{"field": OTHER_FIELD_ID, "value": "80.00"}])
    assert bf.needs_backfill(doc, COMPTA_FIELD_ID) is True


def test_needs_backfill_version_anterieure():
    doc = _doc(1, [_compta_cf(1)])
    assert bf.needs_backfill(doc, COMPTA_FIELD_ID) is True


def test_needs_backfill_version_courante():
    doc = _doc(1, [_compta_cf(COMPTA_CONTRACT_VERSION)])
    assert bf.needs_backfill(doc, COMPTA_FIELD_ID) is False


def test_needs_backfill_version_superieure():
    doc = _doc(1, [_compta_cf(COMPTA_CONTRACT_VERSION + 1)])
    assert bf.needs_backfill(doc, COMPTA_FIELD_ID) is False


def test_needs_backfill_valeur_vide():
    doc = _doc(1, [{"field": COMPTA_FIELD_ID, "value": ""}])
    assert bf.needs_backfill(doc, COMPTA_FIELD_ID) is True


def test_needs_backfill_valeur_none():
    doc = _doc(1, [{"field": COMPTA_FIELD_ID, "value": None}])
    assert bf.needs_backfill(doc, COMPTA_FIELD_ID) is True


def test_needs_backfill_json_casse():
    doc = _doc(1, [{"field": COMPTA_FIELD_ID, "value": "{pas du json"}])
    assert bf.needs_backfill(doc, COMPTA_FIELD_ID) is True


def test_needs_backfill_version_non_int():
    doc = _doc(1, [{"field": COMPTA_FIELD_ID, "value": json.dumps({"version": "2"})}])
    assert bf.needs_backfill(doc, COMPTA_FIELD_ID) is True


def test_needs_backfill_target_version_explicite():
    doc = _doc(1, [_compta_cf(2)])
    # Cible v3 → la v2 doit être backfillée.
    assert bf.needs_backfill(doc, COMPTA_FIELD_ID, target_version=3) is True
    assert bf.needs_backfill(doc, COMPTA_FIELD_ID, target_version=2) is False


# ─── select_documents_to_backfill ─────────────────────────────────────────────

def test_select_filtre_les_a_jour():
    docs = [
        _doc(1),  # absent → à backfiller
        _doc(2, [_compta_cf(1)]),  # v1 → à backfiller
        _doc(3, [_compta_cf(COMPTA_CONTRACT_VERSION)]),  # à jour → ignoré
    ]
    selected = bf.select_documents_to_backfill(docs, COMPTA_FIELD_ID)
    assert [d["id"] for d in selected] == [1, 2]


def test_select_respecte_limit():
    docs = [_doc(i) for i in range(1, 6)]
    selected = bf.select_documents_to_backfill(docs, COMPTA_FIELD_ID, limit=2)
    assert [d["id"] for d in selected] == [1, 2]


def test_select_limit_none_prend_tout():
    docs = [_doc(i) for i in range(1, 4)]
    selected = bf.select_documents_to_backfill(docs, COMPTA_FIELD_ID, limit=None)
    assert len(selected) == 3


def test_select_limit_zero():
    docs = [_doc(1), _doc(2)]
    selected = bf.select_documents_to_backfill(docs, COMPTA_FIELD_ID, limit=0)
    assert selected == []


def test_select_liste_vide():
    assert bf.select_documents_to_backfill([], COMPTA_FIELD_ID) == []


# ─── Exclusion du contexte personnel/médical ─────────────────────────────────

def _doc_tags(doc_id, tags):
    d = _doc(doc_id)
    d["tags"] = tags
    return d


def test_select_exclut_docs_personnels():
    from config import PERSONAL_CONTEXT_TAG_IDS
    perso = next(iter(PERSONAL_CONTEXT_TAG_IDS))  # ex. medical/personnel/Leticia…
    docs = [
        _doc_tags(1, [3]),         # facture pro → backfill
        _doc_tags(2, [9, perso]),  # reçu mais perso → exclu
    ]
    selected = bf.select_documents_to_backfill(docs, COMPTA_FIELD_ID)
    ids = [d["id"] for d in selected]
    assert ids == [1]


def test_select_sans_tags_inclus():
    # Doc sans tags (helper par défaut) → pas personnel → backfillé.
    assert len(bf.select_documents_to_backfill([_doc(1)], COMPTA_FIELD_ID)) == 1


# ─── build_backfill_patch ─────────────────────────────────────────────────────

def _analysis(**over):
    base = {
        "doc_type": "facture",
        "correspondent": "RONA",
        "date": "2026-03-01",
        "currency": "CAD",
        "supplier_foreign": False,
        "total": "80.00",
        "tps": "0.00",
        "tvq": "0.00",
        "line_items": [{"description": "Article", "amount": "80.00", "taxable": True}],
        "_method": "ocr_text",
    }
    base.update(over)
    return base


def _compta_value(patch):
    by_id = {cf["field"]: cf["value"] for cf in patch["custom_fields"]}
    return json.loads(by_id[COMPTA_FIELD_ID])


def test_build_patch_ecrit_compta_json_valide():
    patch = bf.build_backfill_patch([], _analysis(), COMPTA_FIELD_ID)
    payload = _compta_value(patch)
    assert payload["version"] == COMPTA_CONTRACT_VERSION
    assert payload["doc_type"] == "facture"
    assert payload["total_cents"] == 8000
    assert payload["fournisseur"] == "RONA"


def test_build_patch_porte_champs_v2():
    patch = bf.build_backfill_patch(
        [], _analysis(currency="USD", supplier_foreign=True, tps=None, tvq=None,
                      line_items=[{"description": "x", "amount": "80.00"}]),
        COMPTA_FIELD_ID,
    )
    payload = _compta_value(patch)
    assert payload["currency"] == "USD"
    assert payload["supplier_foreign"] is True
    # USD étranger sans taxe reste cohérent → pas de needs_review parasite.
    assert payload["needs_review"] is False


def test_build_patch_preserve_champs_existants():
    existing = [{"field": OTHER_FIELD_ID, "value": "80.00"}]
    patch = bf.build_backfill_patch(existing, _analysis(), COMPTA_FIELD_ID)
    by_id = {cf["field"]: cf["value"] for cf in patch["custom_fields"]}
    assert by_id[OTHER_FIELD_ID] == "80.00"  # champ hérité préservé
    assert COMPTA_FIELD_ID in by_id  # compta_json ajouté


def test_build_patch_ecrase_compta_json_v1():
    existing = [_compta_cf(1)]
    patch = bf.build_backfill_patch(existing, _analysis(), COMPTA_FIELD_ID)
    payload = _compta_value(patch)
    assert payload["version"] == COMPTA_CONTRACT_VERSION  # v1 remplacée
    # Un seul enregistrement pour le champ compta_json (pas de doublon).
    ids = [cf["field"] for cf in patch["custom_fields"]]
    assert ids.count(COMPTA_FIELD_ID) == 1


def test_build_patch_serialisation_stable():
    a = bf.build_backfill_patch([], _analysis(), COMPTA_FIELD_ID)
    b = bf.build_backfill_patch([], _analysis(), COMPTA_FIELD_ID)
    assert _compta_value(a) == _compta_value(b)
    # sort_keys → chaîne identique à chaque appel (idempotence d'écriture).
    av = {cf["field"]: cf["value"] for cf in a["custom_fields"]}[COMPTA_FIELD_ID]
    bv = {cf["field"]: cf["value"] for cf in b["custom_fields"]}[COMPTA_FIELD_ID]
    assert av == bv


def test_build_patch_accents_lisibles():
    # review_reason contient des accents → ensure_ascii=False les garde lisibles.
    patch = bf.build_backfill_patch([], _analysis(line_items=[]), COMPTA_FIELD_ID)
    raw = {cf["field"]: cf["value"] for cf in patch["custom_fields"]}[COMPTA_FIELD_ID]
    assert "\\u" not in raw
