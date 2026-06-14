"""Chemin d'erreur de `doc_processor.process_document` (client Paperless mocké).

Propriété visée (SPEC §«Chemin d'erreur») : si l'analyse Claude échoue,
`process_document` retombe proprement — il appose le tag `erreur-traitement`
(`ERROR_TAG_ID`, distinct de `a-verifier` qui signale une confiance basse) et ne
laisse remonter AUCUNE exception, pour ne jamais planter le hook post-consume.
Le document tagué `erreur-traitement` est rejoué chaque nuit par retry_errors.py.

Seule exception : `RateLimitError` doit remonter telle quelle, car l'appelant
(post_consume / retry_processor) la traite pour mettre le document en queue et
le rejouer une fois le rate limit levé — il ne faut donc PAS l'avaler ni poser
de tag dans ce cas.

L'analyse est remplacée par un stub qui lève (monkeypatch de
`claude_analyzer.analyze_document_smart`) : aucun appel réel CLI ni vision.
"""

import pytest

import claude_analyzer
import doc_processor
from config import ERROR_TAG_ID, PROTECTED_TAG_IDS, TAG_IDS


@pytest.fixture
def patch_analysis_raises(monkeypatch):
    """Fige `analyze_document_smart` sur une fonction qui lève l'exception donnée."""
    def _apply(exc: Exception):
        def _raise(doc_id, title, content):
            raise exc
        monkeypatch.setattr(claude_analyzer, "analyze_document_smart", _raise)
    return _apply


# ─── Erreur générique → erreur-traitement, pas d'exception ──────────────────────

def test_generic_error_adds_error_tag_no_raise(fake_paperless, patch_analysis_raises):
    """Une erreur d'analyse quelconque → tag erreur-traitement, aucune exception."""
    patch_analysis_raises(RuntimeError("boom OCR"))
    fake_paperless.add_document(1, title="scan_001", content="x" * 500, tags=[])

    # Ne doit pas lever.
    doc_processor.process_document(1)

    tags = fake_paperless.get_document(1)["tags"]
    assert ERROR_TAG_ID in tags


def test_generic_error_preserves_existing_tags(fake_paperless, patch_analysis_raises):
    """erreur-traitement s'ajoute aux tags existants sans en retirer ni dupliquer."""
    patch_analysis_raises(ValueError("JSON illisible"))
    existing = [TAG_IDS["facture"]]
    fake_paperless.add_document(1, title="scan_001", content="abc", tags=existing)

    doc_processor.process_document(1)

    tags = fake_paperless.get_document(1)["tags"]
    assert ERROR_TAG_ID in tags
    assert TAG_IDS["facture"] in tags
    assert len(tags) == len(set(tags)), "tags dupliqués"


def test_generic_error_keeps_protected_tags(fake_paperless, patch_analysis_raises):
    """Le chemin d'erreur ne retire jamais un tag protégé déjà présent."""
    patch_analysis_raises(RuntimeError("vision crash"))
    protected = next(iter(PROTECTED_TAG_IDS))
    fake_paperless.add_document(1, title="scan_001", content="abc", tags=[protected])

    doc_processor.process_document(1)

    tags = fake_paperless.get_document(1)["tags"]
    assert protected in tags
    assert ERROR_TAG_ID in tags


def test_generic_error_does_not_classify(fake_paperless, patch_analysis_raises):
    """En cas d'erreur, aucun titre/type/champ n'est posé — seul le tag erreur change."""
    patch_analysis_raises(RuntimeError("boom"))
    fake_paperless.add_document(1, title="scan_001", content="abc", tags=[])

    doc_processor.process_document(1)

    doc = fake_paperless.get_document(1)
    assert doc["title"] == "scan_001"
    assert doc["document_type"] is None
    assert doc["custom_fields"] == []
    assert doc["correspondent"] is None
    assert doc["tags"] == [ERROR_TAG_ID]


def test_generic_error_is_idempotent(fake_paperless, patch_analysis_raises):
    """Deux échecs successifs → un seul tag erreur-traitement, état stable."""
    patch_analysis_raises(RuntimeError("boom"))
    fake_paperless.add_document(1, title="scan_001", content="abc", tags=[])

    doc_processor.process_document(1)
    after_first = fake_paperless.get_document(1)
    doc_processor.process_document(1)
    after_second = fake_paperless.get_document(1)

    assert after_first == after_second
    assert after_second["tags"].count(ERROR_TAG_ID) == 1


# ─── RateLimitError → remonte (aucun tag posé) ──────────────────────────────────

def test_rate_limit_error_propagates(fake_paperless, patch_analysis_raises):
    """RateLimitError n'est PAS avalée : elle remonte au caller."""
    patch_analysis_raises(claude_analyzer.RateLimitError("429"))
    fake_paperless.add_document(1, title="scan_001", content="abc", tags=[])

    with pytest.raises(claude_analyzer.RateLimitError):
        doc_processor.process_document(1)


def test_rate_limit_error_does_not_tag(fake_paperless, patch_analysis_raises):
    """Sur rate limit, le document reste intact (aucun tag) — il sera rejoué."""
    patch_analysis_raises(claude_analyzer.RateLimitError("429"))
    fake_paperless.add_document(1, title="scan_001", content="abc", tags=[])

    with pytest.raises(claude_analyzer.RateLimitError):
        doc_processor.process_document(1)

    tags = fake_paperless.get_document(1)["tags"]
    assert ERROR_TAG_ID not in tags
    assert TAG_IDS["a-verifier"] not in tags
    assert tags == []
