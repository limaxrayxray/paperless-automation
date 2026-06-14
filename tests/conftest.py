"""Configuration pytest partagée.

Hermétisme : on injecte un PAPERLESS_TOKEN bidon AVANT tout import des modules du
projet (config.py lève si le token manque). Les tests ne dépendent donc jamais
d'un fichier .env réel ni d'un serveur en marche.

Fixtures de mock — AUCUN appel externe réel :
  - `fake_claude`   : remplace l'appel au Claude CLI (subprocess) par une réponse
                      simulée; le test fixe le texte JSON que « le modèle » renvoie.
  - `fake_paperless`: client Paperless en mémoire (get/patch/delete/correspondants),
                      branché sur le module `paperless_client`.
"""

import json
import os
import subprocess

import pytest

# Doit s'exécuter avant que tout test n'importe `config` (ou un module qui en
# dépend). setdefault : ne masque pas une valeur déjà fournie par l'environnement.
os.environ.setdefault("PAPERLESS_TOKEN", "test-token-bidon")
os.environ.setdefault("PAPERLESS_URL", "http://paperless.invalid/api")


@pytest.fixture
def fake_claude(monkeypatch):
    """Remplace l'appel au Claude CLI par une réponse simulée.

    Retourne une fonction `set_result(text)` : le `text` devient ce que renvoie
    le « modèle » (le code de production le passe ensuite dans _extract_json /
    _validate_and_clean). Le faux `subprocess.run` émet le flux stream-json qu'attend
    `claude_analyzer._call_claude` ({"type":"result","result": ...}).
    """
    import claude_analyzer

    state = {"result_text": "{}", "returncode": 0, "stderr": ""}

    def fake_run(cmd, **kwargs):
        if state["returncode"] != 0:
            return subprocess.CompletedProcess(cmd, state["returncode"], stdout="",
                                               stderr=state["stderr"])
        line = json.dumps({"type": "result", "result": state["result_text"]})
        return subprocess.CompletedProcess(cmd, 0, stdout=line + "\n", stderr="")

    monkeypatch.setattr(claude_analyzer.subprocess, "run", fake_run)

    def set_result(text, *, returncode=0, stderr=""):
        state["result_text"] = text
        state["returncode"] = returncode
        state["stderr"] = stderr

    return set_result


class FakePaperless:
    """Client Paperless en mémoire. Reproduit le contrat des fonctions de
    `paperless_client` utilisées par `doc_processor`, sans aucun réseau."""

    def __init__(self):
        self.docs: dict[int, dict] = {}
        self.correspondents: dict[str, int] = {}
        self._next_corr_id = 1000
        self.deleted: list[int] = []

    def add_document(self, doc_id: int, **fields) -> dict:
        doc = {
            "id": doc_id,
            "title": fields.get("title", f"Document {doc_id}"),
            "content": fields.get("content", ""),
            "tags": list(fields.get("tags", [])),
            "custom_fields": list(fields.get("custom_fields", [])),
            "correspondent": fields.get("correspondent"),
            "document_type": fields.get("document_type"),
            "created": fields.get("created"),
        }
        self.docs[doc_id] = doc
        return doc

    # ─── API reproduite ────────────────────────────────────────────────────────
    def get_document(self, doc_id: int) -> dict:
        return dict(self.docs[doc_id])

    def patch_document(self, doc_id: int, payload: dict) -> dict:
        self.docs[doc_id].update(payload)
        return dict(self.docs[doc_id])

    def delete_document(self, doc_id: int) -> None:
        self.docs.pop(doc_id, None)
        self.deleted.append(doc_id)

    def get_all_correspondents(self) -> dict[str, int]:
        return dict(self.correspondents)

    def find_or_create_correspondent(self, name: str) -> int:
        for existing, cid in self.correspondents.items():
            if existing.lower() == name.lower():
                return cid
        cid = self._next_corr_id
        self._next_corr_id += 1
        self.correspondents[name] = cid
        return cid


@pytest.fixture
def fake_paperless(monkeypatch):
    """Branche un FakePaperless en mémoire sur le module `paperless_client` (donc
    aussi vu par `doc_processor`). `build_custom_fields_payload` reste l'originale
    (fonction pure, sans réseau)."""
    import paperless_client

    fake = FakePaperless()
    for name in ("get_document", "patch_document", "delete_document",
                 "get_all_correspondents", "find_or_create_correspondent"):
        monkeypatch.setattr(paperless_client, name, getattr(fake, name))
    return fake
