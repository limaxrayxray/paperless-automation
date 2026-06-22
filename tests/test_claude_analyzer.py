"""Tests de claude_analyzer._extract_json : extraction tolérante du JSON renvoyé
par le modèle (nu, fencé Markdown, entouré de texte, nesté, illisible).

Couvre aussi la classification d'erreur de `_call_claude` quand le CLI sort en
code != 0 : motif de limite/surcharge (même sur stdout) → RateLimitError (file de
retry), sinon RuntimeError dont le message inclut stdout (diagnostic non perdu)."""

import subprocess

import claude_analyzer
import pytest
from claude_analyzer import RateLimitError
from claude_analyzer import _call_claude
from claude_analyzer import _extract_json


def _patch_run(monkeypatch, *, returncode, stdout="", stderr=""):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(claude_analyzer.subprocess, "run", fake_run)


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
    raw = '```\n{"a": 2}\n```'
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


# ─── _call_claude : classification d'erreur (code != 0) ──────────────────────

def test_call_claude_limite_sur_stdout_donne_ratelimit(monkeypatch):
    # Le CLI met son motif sur stdout (pas stderr) → doit être vu comme limite.
    _patch_run(monkeypatch, returncode=1,
               stdout='{"type":"result","is_error":true,"result":"usage limit reached"}')
    with pytest.raises(RateLimitError):
        _call_claude({"x": 1})


def test_call_claude_overloaded_donne_ratelimit(monkeypatch):
    _patch_run(monkeypatch, returncode=1, stderr="Error: Overloaded")
    with pytest.raises(RateLimitError):
        _call_claude({"x": 1})


def test_call_claude_erreur_generique_inclut_stdout(monkeypatch):
    # Erreur non liée à une limite : RuntimeError dont le message expose stdout
    # (avant : stderr vide → message opaque « code 1: »).
    _patch_run(monkeypatch, returncode=1, stdout="boom: quelque chose a planté", stderr="")
    with pytest.raises(RuntimeError) as exc:
        _call_claude({"x": 1})
    assert "boom" in str(exc.value)
    assert not isinstance(exc.value, RateLimitError)


def test_call_claude_code1_sans_sortie_reste_explicite(monkeypatch):
    _patch_run(monkeypatch, returncode=1, stdout="", stderr="")
    with pytest.raises(RuntimeError) as exc:
        _call_claude({"x": 1})
    assert "aucune sortie" in str(exc.value)
