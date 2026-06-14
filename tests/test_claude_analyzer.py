"""Tests de claude_analyzer._extract_json : extraction tolérante du JSON renvoyé
par le modèle (nu, fencé Markdown, entouré de texte, nesté, illisible)."""

import pytest

from claude_analyzer import _extract_json


def test_json_nu():
    assert _extract_json('{"doc_type": "facture", "total": "10.00"}') == {
        "doc_type": "facture",
        "total": "10.00",
    }


def test_json_avec_espaces_autour():
    assert _extract_json('  \n {"a": 1} \n ') == {"a": 1}


def test_json_fence_markdown():
    raw = '```json\n{"doc_type": "recu", "tps": "0.50"}\n```'
    assert _extract_json(raw) == {"doc_type": "recu", "tps": "0.50"}


def test_json_fence_sans_langage():
    raw = "```\n{\"a\": 2}\n```"
    assert _extract_json(raw) == {"a": 2}


def test_json_entoure_de_texte():
    raw = 'Voici le résultat : {"a": 1, "b": 2} — fin.'
    assert _extract_json(raw) == {"a": 1, "b": 2}


def test_json_neste_via_branche_gloutonne():
    # Objet avec sous-objets/tableau : la branche fencée non-gloutonne échoue,
    # mais la branche \{.*\} gloutonne récupère l'objet complet.
    raw = ('```json\n{"items": [{"description": "X", "amount": 1}], '
           '"total": "1.00"}\n```')
    assert _extract_json(raw) == {
        "items": [{"description": "X", "amount": 1}],
        "total": "1.00",
    }


def test_illisible_leve_valueerror():
    with pytest.raises(ValueError):
        _extract_json("aucun json ici, juste du texte")
