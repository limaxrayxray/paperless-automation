"""Exerce les fixtures de mock : faux Claude CLI et faux client Paperless.
Garantit qu'aucun appel externe réel n'est nécessaire pour tester le pipeline."""

import claude_analyzer
import paperless_client


def test_fake_claude_alimente_analyze_document(fake_claude):
    # « Le modèle » renvoie un JSON de facture; analyze_document doit le parser
    # et le nettoyer sans aucun appel réseau.
    fake_claude('{"doc_type": "facture", "correspondent": "RONA", '
                '"total": "11.49", "tps": "0.50", "tvq": "0.99", '
                '"line_items": [], "tags_to_add": ["facture"], "confidence": 0.9}')

    result = claude_analyzer.analyze_document("titre", "contenu OCR de la facture")

    assert result["doc_type"] == "facture"
    assert result["correspondent"] == "RONA"
    assert result["total"] == "11.49"
    assert result["confidence"] == 0.9


def test_fake_claude_erreur_cli_leve(fake_claude):
    fake_claude("", returncode=1, stderr="boom")
    try:
        claude_analyzer.analyze_document("t", "c" * 200)
    except RuntimeError:
        pass
    else:
        raise AssertionError("une erreur CLI devait lever RuntimeError")


def test_fake_paperless_get_patch_delete(fake_paperless):
    fake_paperless.add_document(42, title="Avant", tags=[1, 2])

    assert paperless_client.get_document(42)["title"] == "Avant"

    paperless_client.patch_document(42, {"title": "Après", "tags": [3]})
    doc = paperless_client.get_document(42)
    assert doc["title"] == "Après"
    assert doc["tags"] == [3]

    paperless_client.delete_document(42)
    assert 42 in fake_paperless.deleted


def test_fake_paperless_correspondent_idempotent(fake_paperless):
    cid = paperless_client.find_or_create_correspondent("Bell Canada")
    # Même nom (casse différente) → même id, pas de doublon.
    assert paperless_client.find_or_create_correspondent("bell canada") == cid
    assert len(fake_paperless.get_all_correspondents()) == 1
