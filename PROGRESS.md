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

## 2026-06-13 — Phase 0 tâche 3 : tests _extract_json (Phase 0 terminée)

**Tâche** : tests de `claude_analyzer._extract_json` (extraction tolérante du JSON).

**Fait** : `tests/test_claude_analyzer.py` — 7 cas : JSON nu, espaces autour, fence
```json, fence sans langage, entouré de texte, objet nesté (récupéré par la branche
gloutonne `\{.*\}` quand la branche fencée non-gloutonne échoue), illisible → ValueError.

**Vérifications** : `python -m pytest -q` ✅ (13/13). **Phase 0 complète** (harnais +
mocks + premiers tests). Prochaine : Phase 1 (durcir l'existant) — `_validate_and_clean`.

**Fichiers** : tests/test_claude_analyzer.py, PLAN.md, PROGRESS.md.

## 2026-06-13 — Phase 1 tâche 1 : tests _validate_and_clean (base)

**Tâche** : tests de `claude_analyzer._validate_and_clean` — normalisation des
montants, `doc_type`/`context` invalides → défaut, filtrage des tags hors
`ALLOWED_TAGS`, bornage des confiances [0,1].

**Fait** : `tests/test_validate_and_clean.py` — 25 cas :
- Montants : « 66,81 $ » → « 66.81 », point décimal conservé, nombre natif → « 5.00 »,
  None reste None, chaîne illisible → None, normalisation simultanée total/tps/tvq.
- doc_type : invalide/absent → « autre », valide conservé. context : invalide →
  « rapidetech », « personnel » conservé.
- Tags : filtrage hors `ALLOWED_TAGS` (personnel/impots/inconnu retirés), tous valides
  conservés, non-liste → [], absent → [].
- Confiances : >1 → 1.0, <0 → 0.0, illisible/None → 0.5, valide conservée.
- invoice_number/date/correspondent : « null » → None, normalisation (strip), date
  invalide → None + date_confidence 0.0, date valide conservée.

**Décisions** : montants testés avec `doc_type="autre"` pour éviter le forçage tps/tvq
et le drapeau d'incohérence fiscale, qui relèvent de la tâche 2 (périmètre séparé).

**Vérifications** : `python -m pytest -q` ✅ (38/38).

**Fichiers** : tests/test_validate_and_clean.py, PLAN.md, PROGRESS.md.

## 2026-06-13 — Phase 1 tâche 2 : tests _validate_and_clean (règles fiscales)

**Tâche** : tester le forçage tps/tvq à « 0.00 » pour facture/recu et le drapeau
d'incohérence fiscale (total > 20 $ et tvq = 0.00 → confiance abaissée + note).

**Fait** : `tests/test_validate_fiscal.py` — 16 cas :
- Forçage : facture/recu avec tps/tvq None → « 0.00 » (un seul None forcé,
  l'autre conservé) ; valeurs fournies non écrasées ; releve/autre laissent
  tps/tvq à None.
- Drapeau d'incohérence : facture/recu total > 20 $ et tvq = 0.00 → confidence
  bornée à 0.60 + note « ATTENTION » ; min() ne remonte pas une confiance déjà
  < 0.60 ; note existante préservée. Non-déclenchement : total ≤ 20 $, borne
  exacte 20.00 (non > 20.0), juste au-dessus 20.01 (déclenche), tvq ≠ 0.00,
  total None, et releve (tvq non forcé donc condition fausse).

**Décisions** : borne testée explicitement (20.00 vs 20.01) pour figer la
sémantique strict `> 20.0` du code. Le cas releve confirme que le drapeau dépend
du forçage tps/tvq (réservé à facture/recu).

**Vérifications** : `python -m pytest -q` ✅ (54/54).

**Fichiers** : tests/test_validate_fiscal.py, PLAN.md, PROGRESS.md.

## Décisions à valider

- Contrat d'unification : un seul champ Paperless `compta_json` (texte long, JSON),
  montants en cents entiers, plutôt que d'éparpiller les items dans plusieurs champs.
  Les champs hérités Total/TPS/TVQ restent pour l'affichage humain.
- Le loop ne fait aucun appel réseau réel ni déploiement : `ensure_compta_field.py`
  est écrit par le loop mais exécuté manuellement (création du champ via l'API).
