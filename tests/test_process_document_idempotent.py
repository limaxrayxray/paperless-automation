"""Idempotence de `doc_processor.process_document` (client Paperless mocké).

Propriété visée (SPEC §5) : re-traiter le même document ne duplique rien et
converge vers le même résultat (tags, champs, titre, date). On exécute donc
`process_document` DEUX fois de suite sur le même document du `fake_paperless`
et on vérifie que l'état final est strictement identique d'un passage à l'autre.

L'analyse Claude est ici remplacée par un stub déterministe (monkeypatch de
`claude_analyzer.analyze_document_smart`) : aucun appel réel CLI ni vision, et le
résultat d'analyse est figé pour isoler la logique d'application de `doc_processor`.
"""

import pytest

import claude_analyzer
import doc_processor
from config import (
    CUSTOM_FIELD_IDS,
    DOC_TYPE_IDS,
    PROTECTED_TAG_IDS,
    TAG_IDS,
    YEAR_TAG_IDS,
)


def _stub_analysis(**overrides) -> dict:
    """Analyse type telle que renvoyée par `_validate_and_clean` (facture fiable)."""
    analysis = {
        "doc_type": "facture",
        "context": "rapidetech",
        "confidence": 0.95,
        "date": "2026-03-15",
        "date_confidence": 0.95,
        "correspondent": "Hydro-Québec",
        "suggested_title": "Facture Hydro-Québec 2026-03",
        "tps": "5.00",
        "tvq": "9.98",
        "total": "114.98",
        "invoice_number": "INV-001",
        "tags_to_add": [],
        "notes": "",
        "_method": "ocr_text",
    }
    analysis.update(overrides)
    return analysis


@pytest.fixture
def patch_analysis(monkeypatch):
    """Fige `analyze_document_smart` sur une analyse donnée (copie défensive)."""
    def _apply(analysis: dict):
        monkeypatch.setattr(
            claude_analyzer,
            "analyze_document_smart",
            lambda doc_id, title, content: dict(analysis),
        )
    return _apply


def _run_twice(fake_paperless, doc_id):
    """Traite deux fois et retourne les deux snapshots successifs du document."""
    doc_processor.process_document(doc_id)
    after_first = fake_paperless.get_document(doc_id)
    doc_processor.process_document(doc_id)
    after_second = fake_paperless.get_document(doc_id)
    return after_first, after_second


def test_two_passes_converge_to_same_state(fake_paperless, patch_analysis):
    """Deux passages → état final identique (tags, champs, titre, date, type)."""
    patch_analysis(_stub_analysis())
    protected = next(iter(PROTECTED_TAG_IDS))
    fake_paperless.add_document(
        1, title="scan_001", content="x" * 500, tags=[protected]
    )

    after_first, after_second = _run_twice(fake_paperless, 1)

    assert after_first == after_second


def test_tags_have_no_duplicates_and_keep_protected(fake_paperless, patch_analysis):
    """Aucun doublon de tag et le tag protégé présent est conservé."""
    patch_analysis(_stub_analysis())
    protected = next(iter(PROTECTED_TAG_IDS))
    fake_paperless.add_document(
        1, title="scan_001", content="x" * 500, tags=[protected]
    )

    _run_twice(fake_paperless, 1)
    tags = fake_paperless.get_document(1)["tags"]

    assert len(tags) == len(set(tags)), "tags dupliqués"
    assert protected in tags
    # La classification facture a bien été appliquée et l'année 2026 ajoutée.
    assert TAG_IDS["facture"] in tags
    assert YEAR_TAG_IDS[2026] in tags
    # Confiance haute → pas de tag a-verifier.
    assert TAG_IDS["a-verifier"] not in tags


def test_custom_fields_not_duplicated_across_passes(fake_paperless, patch_analysis):
    """Les custom fields ne sont pas dupliqués (un seul enregistrement par field)."""
    patch_analysis(_stub_analysis())
    fake_paperless.add_document(1, title="scan_001", content="x" * 500)

    _run_twice(fake_paperless, 1)
    cfs = fake_paperless.get_document(1)["custom_fields"]

    field_ids = [cf["field"] for cf in cfs]
    assert len(field_ids) == len(set(field_ids)), "custom field dupliqué"
    by_id = {cf["field"]: cf["value"] for cf in cfs}
    assert by_id[CUSTOM_FIELD_IDS["Total"]] == "114.98"
    assert by_id[CUSTOM_FIELD_IDS["Facture"]] == "INV-001"


def test_correspondent_created_once(fake_paperless, patch_analysis):
    """Le correspondant n'est créé qu'au premier passage, pas re-créé au second."""
    patch_analysis(_stub_analysis())
    fake_paperless.add_document(1, title="scan_001", content="x" * 500)

    doc_processor.process_document(1)
    corr_after_first = fake_paperless.get_document(1)["correspondent"]
    corrs_after_first = dict(fake_paperless.correspondents)

    doc_processor.process_document(1)
    corr_after_second = fake_paperless.get_document(1)["correspondent"]

    assert corr_after_first is not None
    assert corr_after_second == corr_after_first
    # Aucun nouveau correspondant créé au second passage.
    assert fake_paperless.correspondents == corrs_after_first


def test_low_confidence_idempotent_keeps_single_a_verifier(fake_paperless, patch_analysis):
    """Confiance basse → a-verifier ajouté une seule fois, état stable."""
    patch_analysis(_stub_analysis(confidence=0.30, date_confidence=0.30))
    fake_paperless.add_document(1, title="scan_001", content="x" * 500)

    after_first, after_second = _run_twice(fake_paperless, 1)
    tags = after_second["tags"]

    assert after_first == after_second
    assert tags.count(TAG_IDS["a-verifier"]) == 1
    # Confiance de date basse → pas de tag année.
    assert YEAR_TAG_IDS[2026] not in tags


def test_already_processed_document_is_stable(fake_paperless, patch_analysis):
    """Un document déjà entièrement traité ne change plus au passage suivant."""
    patch_analysis(_stub_analysis())
    fake_paperless.add_document(1, title="scan_001", content="x" * 500)

    # Premier traitement → état « stabilisé ».
    doc_processor.process_document(1)
    stabilized = fake_paperless.get_document(1)

    # Type de document et titre ont bien été posés au premier passage.
    assert stabilized["document_type"] == DOC_TYPE_IDS["facture"]
    assert stabilized["title"] == "Facture Hydro-Québec 2026-03"

    # Tout passage ultérieur converge vers le même état.
    doc_processor.process_document(1)
    assert fake_paperless.get_document(1) == stabilized
