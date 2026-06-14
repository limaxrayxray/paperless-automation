"""Chemin d'erreur de `doc_processor.process_document` (client Paperless mocké).

Propriété visée (SPEC §«Chemin d'erreur») : si l'analyse Claude échoue,
`process_document` retombe proprement — il appose le tag `a-verifier` et ne
laisse remonter AUCUNE exception, pour ne jamais planter le hook post-consume.

Seule exception : `RateLimitError` doit remonter telle quelle, car l'appelant
(post_consume / retry_processor) la traite pour mettre le document en queue et
le rejouer une fois le rate limit levé — il ne faut donc PAS l'avaler ni poser
`a-verifier` dans ce cas.

L'analyse est remplacée par un stub qui lève (monkeypatch de
`claude_analyzer.analyze_document_smart`) : aucun appel réel CLI ni vision.
"""

import pytest

import claude_analyzer
import doc_processor
from config import PROTECTED_TAG_IDS, TAG_IDS


@pytest.fixture
def patch_analysis_raises(monkeypatch):
    """Fige `analyze_document_smart` sur une fonction qui lève l'exception donnée."""
    def _apply(exc: Exception):
        def _raise(doc_id, title, content):
            raise exc
        monkeypatch.setattr(claude_analyzer, "analyze_document_smart", _raise)
    return _apply


# ─── Erreur générique → a-verifier, pas d'exception ─────────────────────────────

def test_generic_error_adds_a_verifier_no_raise(fake_paperless, patch_analysis_raises):
    """Une erreur d'analyse quelconque → tag a-verifier, aucune exception remontée."""
    patch_analysis_raises(RuntimeError("boom OCR"))
    fake_paperless.add_document(1, title="scan_001", content="x" * 500, tags=[])

    # Ne doit pas lever.
    doc_processor.process_document(1)

    tags = fake_paperless.get_document(1)["tags"]
    assert TAG_IDS["a-verifier"] in tags


def test_generic_error_preserves_existing_tags(fake_paperless, patch_analysis_raises):
    """a-verifier s'ajoute aux tags existants sans en retirer ni dupliquer."""
    patch_analysis_raises(ValueError("JSON illisible"))
    existing = [TAG_IDS["facture"]]
    fake_paperless.add_document(1, title="scan_001", content="abc", tags=existing)

    doc_processor.process_document(1)

    tags = fake_paperless.get_document(1)["tags"]
    assert TAG_IDS["a-verifier"] in tags
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
    assert TAG_IDS["a-verifier"] in tags


def test_generic_error_does_not_classify(fake_paperless, patch_analysis_raises):
    """En cas d'erreur, aucun titre/type/champ n'est posé — seul a-verifier change."""
    patch_analysis_raises(RuntimeError("boom"))
    fake_paperless.add_document(1, title="scan_001", content="abc", tags=[])

    doc_processor.process_document(1)

    doc = fake_paperless.get_document(1)
    assert doc["title"] == "scan_001"
    assert doc["document_type"] is None
    assert doc["custom_fields"] == []
    assert doc["correspondent"] is None
    assert doc["tags"] == [TAG_IDS["a-verifier"]]


def test_generic_error_is_idempotent(fake_paperless, patch_analysis_raises):
    """Deux échecs successifs → un seul a-verifier, état stable."""
    patch_analysis_raises(RuntimeError("boom"))
    fake_paperless.add_document(1, title="scan_001", content="abc", tags=[])

    doc_processor.process_document(1)
    after_first = fake_paperless.get_document(1)
    doc_processor.process_document(1)
    after_second = fake_paperless.get_document(1)

    assert after_first == after_second
    assert after_second["tags"].count(TAG_IDS["a-verifier"]) == 1


# ─── RateLimitError → remonte (pas d'a-verifier) ────────────────────────────────

def test_rate_limit_error_propagates(fake_paperless, patch_analysis_raises):
    """RateLimitError n'est PAS avalée : elle remonte au caller."""
    patch_analysis_raises(claude_analyzer.RateLimitError("429"))
    fake_paperless.add_document(1, title="scan_001", content="abc", tags=[])

    with pytest.raises(claude_analyzer.RateLimitError):
        doc_processor.process_document(1)


def test_rate_limit_error_does_not_tag_a_verifier(fake_paperless, patch_analysis_raises):
    """Sur rate limit, le document reste intact (pas d'a-verifier) — il sera rejoué."""
    patch_analysis_raises(claude_analyzer.RateLimitError("429"))
    fake_paperless.add_document(1, title="scan_001", content="abc", tags=[])

    with pytest.raises(claude_analyzer.RateLimitError):
        doc_processor.process_document(1)

    tags = fake_paperless.get_document(1)["tags"]
    assert TAG_IDS["a-verifier"] not in tags
    assert tags == []
