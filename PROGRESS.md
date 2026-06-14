# PROGRESS — Automatisation Paperless Rapidetech

(Le loop écrit ici une entrée datée par tâche. La première ligne devient `DONE`
quand toutes les tâches hors Phase 3 sont cochées ou BLOQUÉES.)

## 2026-06-13 — Phase 0 tâche 1 : harnais pytest

**Tâche** : `requirements-dev.txt` (pytest) + note README + `pytest.ini` ciblant les tests.

**Fait** :
- `requirements-dev.txt` : pytest 8.x (les scripts de prod restent stdlib).
- `pytest.ini` : `testpaths = tests` (le script manuel `test_ollama.py` à la racine est
  hors collecte), `pythonpath = .` (modules racine importables depuis `tests/`).
- `tests/conftest.py` : injecte un `PAPERLESS_TOKEN` bidon **avant** tout import des
  modules du projet → tests hermétiques (aucune dépendance au `.env` réel).
- `tests/test_sanity.py` : 2 tests verts (harnais + `config` importable sans `.env`).
- `README.md` : démarrage, configuration, tests, déploiement manuel, composants.

**Décisions** : tests sous `tests/` plutôt qu'à la racine, pour isoler du legacy
`test_ollama.py`. Le `conftest.py` minimal (env hermétique) sera enrichi des fixtures
de mock (faux Claude CLI, faux client Paperless) à la tâche suivante.

**Vérifications** : `python -m pytest -q` ✅ (2/2).

**Fichiers** : requirements-dev.txt, pytest.ini, tests/conftest.py, tests/test_sanity.py,
README.md, PLAN.md, PROGRESS.md.

## 2026-06-13 — Phase 0 tâche 2 : fixtures de mock

**Tâche** : fixtures `conftest.py` — faux Claude CLI + faux client Paperless, zéro réseau.

**Fait** :
- `fake_claude` : patche `claude_analyzer.subprocess.run` pour émettre le flux
  stream-json attendu par `_call_claude`. Retourne `set_result(text, returncode, stderr)`
  → le test fixe le JSON « du modèle » ou simule une erreur CLI.
- `FakePaperless` + fixture `fake_paperless` : client en mémoire (docs, correspondants,
  suppressions), branché sur le module `paperless_client` via monkeypatch — donc vu
  aussi par `doc_processor`. `build_custom_fields_payload` (pure) reste l'originale.
- `tests/test_fixtures.py` : 4 tests (analyze_document via faux CLI, erreur CLI → 
  RuntimeError, get/patch/delete en mémoire, correspondant idempotent insensible à la casse).

**Décisions** : monkeypatch des fonctions du module `paperless_client` plutôt qu'injection
de dépendance — le code de prod appelle `paperless_client.X` directement, on respecte
ce style sans le refactorer.

**Vérifications** : `python -m pytest -q` ✅ (6/6).

**Fichiers** : tests/conftest.py, tests/test_fixtures.py, PLAN.md, PROGRESS.md.

## Décisions à valider

- Contrat d'unification : un seul champ Paperless `compta_json` (texte long, JSON),
  montants en cents entiers, plutôt que d'éparpiller les items dans plusieurs champs.
  Les champs hérités Total/TPS/TVQ restent pour l'affichage humain.
- Le loop ne fait aucun appel réseau réel ni déploiement : `ensure_compta_field.py`
  est écrit par le loop mais exécuté manuellement (création du champ via l'API).
