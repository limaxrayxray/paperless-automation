DONE

# PROGRESS — Automatisation Paperless Rapidetech

(Le loop écrit ici une entrée datée par tâche. La première ligne devient `DONE`
quand toutes les tâches hors Phase 3 sont cochées ou BLOQUÉES.)

## 2026-06-14 — Phase 4 tâche 4 : docs contrat compta_json v2 (Phase 4 terminée)

**Tâche** : mettre à jour le README pour le contrat v2 — les 3 nouveaux champs
(`doc_type`, `currency`, `supplier_foreign`), la règle devise (≠ CAD ne déclenche
pas `needs_review` côté producteur), la compat v1↔v2, et la bascule de version.

**Fait** :
- `README.md`, section « Contrat `compta_json` » : version courante passée de `1` à
  `2`; schéma JSON enrichi des 3 champs v2 (avec leurs positions et valeurs par
  défaut). Nouvelles puces de règles : `doc_type` (relayé, `null` si absent;
  consommateur ignore non-facture/recu), `currency` (défaut CAD, normalisée
  majuscules, sans conversion, ≠ CAD ne déclenche pas `needs_review` — le
  consommateur tranche), `supplier_foreign` (défaut false; taxes nulles normales).
  Nouveau paragraphe « Compatibilité v1 ↔ v2 » : champs apparus en v2 (motivés par
  l'audit du 2026-06-14), consommateur v1 ignore les champs en trop, consommateur
  v2 tolère un payload v1 (défauts), recalcul de l'historique via
  `backfill_compta_json.py` (appel réseau réel, jamais par le loop).

**Décisions** : tâche purement documentaire — aucun code touché, gate inchangé. Doc
gardée dans le README (renvoi à SPEC.md comme source de vérité, sans la dupliquer).

**Vérifications** : `python -m pytest -q` ✅ (183/183). **Phase 4 complète.** Toutes
les tâches hors Phase 3 sont cochées → `DONE` en première ligne. Phase 3 est réalisée
dans le repo `compta-rapidetech`.

**Fichiers** : README.md, PLAN.md, PROGRESS.md.

## 2026-06-14 — Phase 4 tâche 3 : backfill_compta_json.py

**Tâche** : script (re)calculant et écrivant `compta_json` sur les documents déjà
tagués facture/recu sans le champ (ou en v1), pour donner un historique au
consommateur. Idempotent, paginé, dry-run par défaut, `--limit N`. Appels réels
(Claude CLI + Paperless) → écrit par le loop, exécuté à la main. Tests : fonction
pure de sélection + construction du patch, tout mocké, zéro réseau.

**Fait** :
- `backfill_compta_json.py` :
  - `_compta_version(doc, field_id)` : lit la version du `compta_json` présent (None
    si absent/vide/JSON cassé/version non-int).
  - `needs_backfill(doc, field_id, target_version=COMPTA_CONTRACT_VERSION)` : True si
    absent/illisible ou version < cible → idempotence (doc déjà à jour ignoré).
  - `select_documents_to_backfill(docs, field_id, limit)` : filtre + `--limit`. Pures.
  - `build_backfill_patch(existing_cf, analysis, field_id)` : sérialise le payload v2
    (sort_keys, ensure_ascii=False, identique à doc_processor) et le fusionne via
    `paperless_client.build_custom_fields_payload` → préserve les champs hérités,
    écrase une éventuelle v1, jamais de doublon. Pure.
  - `_fetch_candidate_documents()` (réseau) : docs tagués facture+recu dédupliqués via
    `get_all_documents_by_tag`. `main()` : argparse `--dry-run`
    (BooleanOptionalAction, défaut True → `--no-dry-run` pour appliquer) + `--limit`,
    en-tête d'avertissement « APPELS RÉSEAU RÉELS — jamais par le loop », garde si
    `compta_json` absent de `CUSTOM_FIELD_IDS`.
- `tests/test_backfill_compta_json.py` — 21 cas : needs_backfill (absent, autres
  champs, v1, version courante/supérieure, valeur vide/None, JSON cassé, version
  non-int, target_version explicite) ; sélection (filtre les à-jour, limit, limit
  None/0, liste vide) ; patch (compta_json valide v2, champs v2 USD étranger sans
  needs_review parasite, préservation des champs existants, écrasement v1 sans
  doublon, sérialisation stable, accents lisibles).

**Décisions** : critère de backfill = `version < COMPTA_CONTRACT_VERSION` (et
absent/illisible) plutôt que « champ absent uniquement » — un doc en v1 doit gagner
les champs v2. `main`/`_fetch_candidate_documents` non testés (seuls à faire du
réseau), conforme à la consigne. Réutilise `build_custom_fields_payload` (pure) pour
rester aligné sur `doc_processor.build_custom_fields`. Ruff a auto-fixé `audit_compta.py`
(fichier hors scope) → reverté pour garder le commit borné ; T201/EXE001 restants sur
le script suivent la convention des scripts manuels du repo (idem ensure_compta_field.py).

**Vérifications** : `python -m pytest -q` ✅ (183/183).

**Fichiers** : backfill_compta_json.py, tests/test_backfill_compta_json.py, PLAN.md,
PROGRESS.md.

## 2026-06-14 — Phase 4 tâche 2 : v2 sérialisé tel quel dans compta_json

**Tâche** : vérifier que `doc_processor.build_custom_fields` sérialise le payload v2
intégralement dans `compta_json` (sort_keys, ensure_ascii=False) — aucun champ v2
perdu — et compléter les tests pour asserter `doc_type`/`currency`/`supplier_foreign`
dans le JSON écrit (client mocké).

**Fait** :
- Vérification : `build_custom_fields` (doc_processor.py:152-156) appelle
  `compta_payload.build_compta_payload(analysis)` et sérialise le dict **entier** via
  `json.dumps(payload, ensure_ascii=False, sort_keys=True)`. Comme la sérialisation
  porte sur le dict complet, les 3 champs v2 ajoutés en tâche 1 voyagent sans
  modification — aucun code à changer.
- `tests/test_build_custom_fields_compta.py` — 3 nouveaux cas : `test_compta_json_
  porte_champs_v2` (doc_type/currency=USD/supplier_foreign=True présents dans le
  JSON), `test_compta_json_v2_defauts` (currency absente → « CAD », supplier_foreign
  absent → False, doc_type relayé), `test_compta_json_v2_usd_etranger_sans_taxe_
  coherent` (cas réel audit : USD étranger sans taxe → pas de needs_review parasite).

**Décisions** : aucune modification de code nécessaire — la tâche était une
vérification + complétion de tests. Les assertions confirment via le client mocké
que le seam (doc_processor → compta_json) n'altère pas le contrat v2.

**Vérifications** : `python -m pytest -q` ✅ (162/162).

**Fichiers** : tests/test_build_custom_fields_compta.py, PLAN.md, PROGRESS.md.

## 2026-06-14 — Phase 4 tâche 1 : compta_payload v2 (doc_type, currency, supplier_foreign)

**Tâche** : passer `COMPTA_CONTRACT_VERSION` à 2 et porter `doc_type`, `currency`
(défaut « CAD »), `supplier_foreign` (bool, défaut false) dans le payload.

**Fait** :
- `compta_payload.py` : `COMPTA_CONTRACT_VERSION = 2`. Ajout au dict retourné de
  `doc_type` (`analysis.get("doc_type")`, None si absent), `supplier_foreign`
  (`bool(analysis.get("supplier_foreign", False))`) et `currency` (normalisée
  `.strip().upper()`, défaut « CAD » si absente/vide/None). Aucune logique de
  `needs_review` ajoutée pour une devise ≠ CAD — choix documenté en commentaire :
  le consommateur tranche (conforme à SPEC.md § contrat v2).
- `tests/test_compta_payload.py` — 10 nouveaux cas : version == 2, présence des 3
  champs, doc_type repris/absent→None, currency défaut CAD (absente/vide/None),
  normalisation majuscules, supplier_foreign défaut false / true conservé, et le cas
  réel USD fournisseur étranger sans taxe qui reste cohérent (pas de needs_review).

**Décisions** : `doc_type` absent → None (pas « autre ») au niveau du payload —
`claude_analyzer._validate_and_clean` force déjà « autre » en amont ; le payload se
contente de relayer ce qu'il reçoit, sans re-valider. Normalisation `currency`
dupliquée volontairement ici (le payload doit rester robuste même si l'analyse
n'est pas passée par le validateur, ex. backfill).

**Vérifications** : `python -m pytest -q` ✅ (159/159).

**Fichiers** : compta_payload.py, tests/test_compta_payload.py, PLAN.md, PROGRESS.md.

## 2026-06-14 — Ouverture Phase 4 (contrat v2)

**Contexte** : Phases 0-2 closes (le `DONE` précédent a été retiré pour rouvrir le
loop). L'audit réel de 10 documents a montré que la v1 perd des infos utiles au
consommateur. Nouvelle passe : porter `doc_type`, `currency`, `supplier_foreign`
dans `compta_json` (version 2) + script de backfill de l'historique.

**Décisions à valider (humain)** :
- Périmètre v2 = `doc_type` + `currency` + `supplier_foreign` (les 3 champs déjà
  produits par `claude_analyzer`, simplement non exportés en v1). Pas de champ
  `confidence` ni de conversion de devise dans cette passe — devise ≠ CAD est juste
  signalée, le consommateur tranche. Ajuster PLAN.md / SPEC.md avant de lancer si ce
  périmètre ne convient pas.
- Une devise ≠ CAD **ne déclenche pas** `needs_review` côté producteur (choix : le
  consommateur décide). À confirmer.

## 2026-06-14 — Réintégration des améliorations prod (hors loop, fusion humaine)

**Contexte** : le code live (`/opt/paperless/scripts`) portait des retouches faites
directement en prod, jamais commitées : currency/supplier_foreign, détection fiscale
affinée, tag `erreur-traitement` rejoué la nuit, nettoyage des trigger-tags,
correspondant écrasant le faux match auto, pagination, `retry_errors.py`. Le loop
avait écrit ses tests contre l'ancienne version GitHub → conflit.

**Fait** : modifs prod sauvegardées dans la branche `prod-uncommitted`, puis
cherry-pick dans `main` (commit `487d434`). Conflit `config.py` résolu en gardant le
token via `.env` + les nouveaux IDs (PROTECTED réorganisé, TRIGGER_TAG_IDS,
erreur-traitement 81, ERROR_TAG_ID/ERROR_MAX_ATTEMPTS). Tests réalignés
(error-path → `ERROR_TAG_ID`) + nouveau `tests/test_prod_improvements.py` couvrant
l'exemption fournisseur étranger, l'asymétrie TPS/TVQ, la normalisation devise et le
nettoyage trigger/error-tag.

**Vérifications** : `python -m pytest -q` ✅ (145/145).

**Reste** : déployer `main` vers `/opt/paperless/scripts` (checkout main + pull),
créer le champ Paperless via `ensure_compta_field.py`, puis valider `compta_json` sur
de vraies factures avant d'attaquer le côté consommateur (compta). Branche temporaire
`prod-uncommitted` à supprimer après confirmation du déploiement.

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

## 2026-06-13 — Phase 1 tâche 3 : tests build_tag_updates

**Tâche** : tester `doc_processor.build_tag_updates` — tags protégés intouchés,
un seul tag année à la fois (seuil `DATE_CONFIDENCE_THRESHOLD`), `a-verifier`
selon `GLOBAL_CONFIDENCE_THRESHOLD`, règle medical → personnel.

**Fait** : `tests/test_build_tag_updates.py` — 24 cas :
- Protégés : conservés s'ils étaient là, jamais ajoutés (disjoint quand absents),
  cohabitent avec le remplacement de classification.
- Classification : ancien type remplacé par le nouveau ; `doc_type` invalide
  n'ajoute rien ; doc_type valide ajouté.
- `tags_to_add` : autorisé ajouté ; hors `ALLOWED_TAGS` (impots) ignoré ;
  inconnu ignoré.
- medical → personnel (via `tags_to_add` et via `doc_type`) ; personnel existant
  jamais retiré.
- Tag année : ajouté si conf haute, pas si basse, borne seuil incluse (>=),
  absent si `date_confidence` non fourni, un seul à la fois (remplacement),
  année inconnue / date malformée → aucun tag ni crash.
- `a-verifier` : ajouté si conf basse / absente, retiré si conf haute, borne
  seuil exclue (condition `< seuil`).
- Sortie triée et sans doublon.

**Décisions** : `confidence` fourni explicitement (>= seuil) dans les cas ne
portant pas sur `a-verifier`, car son défaut (0) déclencherait le tag et
brouillerait les assertions. IDs et seuils importés de `config` plutôt
qu'écrits en dur.

**Vérifications** : `python -m pytest -q` ✅ (78/78).

**Fichiers** : tests/test_build_tag_updates.py, PLAN.md, PROGRESS.md.

## 2026-06-13 — Phase 1 tâche 4 : tests build_custom_fields

**Tâche** : tester `doc_processor.build_custom_fields` — TPS/TVQ/Total/Facture
écrits seulement pour les `doc_type` pertinents, valeurs existantes préservées,
aucune valeur None écrite.

**Fait** : `tests/test_build_custom_fields.py` — 10 cas :
- Types pertinents (facture/recu/releve/contrat/assurance/autre) : facture écrit
  les 4 champs ; chaque type pertinent écrit bien le Total.
- Types non pertinents (rapport/manuel/personnel/medical/""/None) : aucun champ
  écrit ; un type non pertinent préserve les champs existants sans les détruire.
- None : montants None / clés absentes non écrits ; `invoice_number` "" (falsy)
  ignoré — le code teste la véracité, pas `is None`.
- Existant : valeur existante non touchée conservée ; nouvelle valeur écrase
  l'ancienne ; un None de l'analyse n'efface jamais une valeur existante.

**Décisions** : résultat réindexé par field id (`_by_id`) car l'ordre de la liste
`custom_fields` n'est pas garanti. IDs importés de `config.CUSTOM_FIELD_IDS`.

**Vérifications** : `python -m pytest -q` ✅ (88/88).

**Fichiers** : tests/test_build_custom_fields.py, PLAN.md, PROGRESS.md.

## 2026-06-13 — Phase 1 tâche 5 : test d'idempotence de process_document

**Tâche** : vérifier que deux passages successifs de `process_document` sur le
même document convergent (mêmes tags/champs/titre/date finaux, aucun doublon).

**Fait** : `tests/test_process_document_idempotent.py` — 6 cas. Stub déterministe
de `claude_analyzer.analyze_document_smart` (aucun appel CLI/vision), client
`fake_paperless` en mémoire ; on traite deux fois et on compare les snapshots :
- État final strictement identique d'un passage à l'autre (cas facture fiable).
- Tags : aucun doublon, tag protégé conservé, classification facture + année 2026
  appliquées, pas de `a-verifier` (confiance haute).
- Custom fields : un seul enregistrement par field id, valeurs Total/Facture
  correctes et stables.
- Correspondant créé une seule fois (second passage : `current_correspondent`
  déjà posé → pas de re-création).
- Confiance basse : `a-verifier` ajouté exactement une fois, pas de tag année,
  état stable.
- Document déjà traité : `document_type`/titre posés au 1er passage, inchangés
  ensuite.

**Décisions** : assertion d'idempotence portée sur l'**égalité des snapshots du
document** (état final) plutôt que sur un payload vide au 2e passage, car
`process_document` ré-émet toujours `created` (valeur identique) sans comparer la
date courante — patch redondant mais sans effet de bord (état convergé, zéro
doublon). Noté en backlog comme micro-optimisation (ne pas patcher si `created`
inchangé), hors périmètre de cette tâche.

**Vérifications** : `python -m pytest -q` ✅ (94/94).

**Fichiers** : tests/test_process_document_idempotent.py, PLAN.md, PROGRESS.md.

## 2026-06-13 — Phase 1 tâche 6 : test du chemin d'erreur (Phase 1 terminée)

**Tâche** : vérifier que si `analyze_document_smart` lève, `process_document`
appose `a-verifier` et ne remonte aucune exception — sauf `RateLimitError`, qui
doit remonter telle quelle (gérée par l'appelant pour mise en queue / rejeu).

**Fait** : `tests/test_process_document_error_path.py` — 7 cas. Stub qui lève
(monkeypatch de `claude_analyzer.analyze_document_smart`), client `fake_paperless`
en mémoire :
- Erreur générique (RuntimeError/ValueError) → `a-verifier` ajouté, aucune
  exception ; tags existants préservés (union, sans doublon) ; tag protégé
  conservé ; aucune classification posée (titre/type/champs/correspondant
  inchangés, seul `a-verifier` change) ; deux échecs → un seul `a-verifier`,
  état stable (idempotence du repli).
- `RateLimitError` : remonte (`pytest.raises`) et ne pose PAS `a-verifier` — le
  document reste intact pour être rejoué.

**Décisions** : assertions sur l'état final du `fake_paperless` (cohérent avec le
style de la tâche 5). Le cas rate limit vérifie l'absence d'effet de bord (tags
vides), garantissant qu'un rejeu repart d'un document propre.

**Vérifications** : `python -m pytest -q` ✅ (101/101). **Phase 1 complète.**
Prochaine : Phase 2 (contrat `compta_json`) — `ensure_compta_field.py`.

**Fichiers** : tests/test_process_document_error_path.py, PLAN.md, PROGRESS.md.

## 2026-06-14 — Phase 2 tâche 1 : ensure_compta_field.py

**Tâche** : script idempotent qui crée le champ personnalisé `compta_json` (texte
long) via l'API Paperless et inscrit son id dans `CUSTOM_FIELD_IDS` (config.py).
Exécuté manuellement (appel réseau réel — jamais par le loop).

**Fait** :
- `paperless_client.py` : ajout de `get_custom_fields`, `create_custom_field` et
  `find_or_create_custom_field` (recherche insensible à la casse, idempotente —
  aucun doublon). Mêmes conventions stdlib/urllib que le reste du client.
- `ensure_compta_field.py` : entête d'avertissement « APPEL RÉSEAU RÉEL — jamais
  par le loop ». `main()` ensure le champ puis met à jour config.py. Type de champ
  `longtext` (Paperless-ngx expose bien un type « Long Text », plus adapté que
  `string` mono-ligne) — cf. `FieldDataType.LONG_TEXT` dans le modèle paperless.
  `inject_field_id_into_config(source, name, id)` : transformation de texte pure
  (regex sur le bloc `CUSTOM_FIELD_IDS = {…}`), insère ou met à jour l'entrée,
  idempotente, lève `ValueError` si le bloc est absent.
- `tests/test_ensure_compta_field.py` — 8 cas : idempotence de
  `find_or_create_custom_field` (existant insensible à la casse → jamais créé ;
  absent → créé une fois ; second appel ne recrée pas) ; injection (ajout si
  absent, mise à jour de valeur sans doublon, idempotence même id, `ValueError`
  sans bloc, et application au **vrai** config.py qui reste un Python valide avec
  `compta_json` posé et entrées héritées préservées).

**Décisions** : config.py mis à jour par réécriture de texte (regex) plutôt que par
une clé lue depuis l'env — respecte la lettre du PLAN (« ajouter l'id à
CUSTOM_FIELD_IDS dans config.py ») et garde la config auto-suffisante. Le loop ne
modifie PAS config.py lui-même : `compta_json` n'est pas encore dans
`CUSTOM_FIELD_IDS` (l'id réel sera connu à l'exécution manuelle du script). La
tâche 3 (branchement dans `build_custom_fields`) devra donc tolérer son absence.

**Vérifications** : `python -m pytest -q` ✅ (109/109).

**Fichiers** : ensure_compta_field.py, paperless_client.py,
tests/test_ensure_compta_field.py, PLAN.md, PROGRESS.md.

## 2026-06-14 — Phase 2 tâche 2 : compta_payload.py

**Tâche** : module pur `build_compta_payload(analysis) -> dict` — convertit les
montants décimaux en cents entiers et assemble le contrat `compta_json` (SPEC.md).

**Fait** :
- `compta_payload.py` : `_to_cents(value)` (Decimal + ROUND_HALF_UP, jamais de
  float pour l'argent ; None/illisible → None) et `build_compta_payload`.
  Montants `total/tps/tvq` et `line_items[].amount` → cents. `tps/tvq` absents → 0.
  `needs_review` + `review_reason` cumulés si : total manquant, items vides
  (repli ligne unique requis côté compta), ou incohérence
  somme(items) + tps + tvq ≠ total. Le producteur n'invente jamais de ligne.
  Champs du contrat : version, fournisseur, date, *_cents, items[], needs_review,
  review_reason, source_method (`_method` de l'analyse, défaut « unknown »).
  `COMPTA_CONTRACT_VERSION = 1`.
- `tests/test_compta_payload.py` — 19 cas : conversion (chaîne, float, arrondi
  HALF_UP, None/illisible/""), montants en cents, cohérence équilibrée (1 et
  plusieurs items), écart/items vides/total manquant → review (+ raisons cumulées),
  total manquant n'émet pas d'écart, taxes None → 0, assemblage du contrat,
  fournisseur/date null, source_method défaut, description/taxable normalisés,
  amount illisible → 0.

**Décisions** : clés d'analyse confirmées contre `claude_analyzer._validate_and_clean`
(`correspondent`, `date`, `total/tps/tvq`, `line_items[description/amount/taxable]`,
`_method`). `items` non équilibrés ne sont jamais « corrigés » — on signale via
`needs_review`, conforme au contrat SPEC. Ruff a reformaté l'ordre des imports
`decimal` (force-single-line). INP001 (pas d'`__init__.py`) ignoré : convention du
repo (modules à la racine). Prochaine : tâche 3 (brancher dans
`build_custom_fields` — tolérer l'absence de `compta_json` dans `CUSTOM_FIELD_IDS`).

**Vérifications** : `python -m pytest -q` ✅ (128/128).

**Fichiers** : compta_payload.py, tests/test_compta_payload.py, PLAN.md, PROGRESS.md.

## 2026-06-14 — Phase 2 tâche 3 : branchement compta_json dans build_custom_fields

**Tâche** : sérialiser le payload `compta_payload.build_compta_payload` en JSON dans
le champ `compta_json` depuis `doc_processor.build_custom_fields` (en plus des champs
hérités), avec tests sur la présence et la validité du JSON écrit.

**Fait** :
- `doc_processor.build_custom_fields` : si `"compta_json" in CUSTOM_FIELD_IDS`,
  construit le payload et l'ajoute à `updates` via `json.dumps(payload,
  ensure_ascii=False, sort_keys=True)`. `sort_keys` garantit une chaîne stable
  (idempotence) ; `ensure_ascii=False` garde les accents lisibles (`review_reason`).
  Import du module `compta_payload`.
- Tolérance d'absence : tant que l'id réel n'est pas inscrit dans `CUSTOM_FIELD_IDS`
  (création manuelle via `ensure_compta_field.py`), le contrat n'est pas écrit et
  `build_custom_fields_payload` l'ignore (field_id_map.get → None). Aucun test
  existant cassé.
- `tests/test_build_custom_fields_compta.py` — 8 cas : champ non configuré → rien
  écrit ; champ configuré (monkeypatch.setitem) → JSON valide et conforme (cents,
  items, fournisseur, date, source_method, version) ; needs_review (incohérence /
  items vides) ; coexistence avec champs hérités ; écrit même pour type non
  pertinent ; sérialisation stable ; écrasement d'une valeur existante.

**Décisions** : `compta_json` est écrit **pour tous les types de document** (pas
seulement les types « pertinents » des champs hérités), conforme à la lettre de
SPEC.md (« chaque document consommé produit un champ compta_json »). Choix simple
noté ci-dessous. Gate explicite `if "compta_json" in CUSTOM_FIELD_IDS` plutôt que
de toujours sérialiser et laisser `build_custom_fields_payload` filtrer — évite le
travail inutile et rend l'intention claire.

**Vérifications** : `python -m pytest -q` ✅ (136/136). Prochaine : tâche 4
(`docs: contrat compta_json` — README).

**Fichiers** : doc_processor.py, tests/test_build_custom_fields_compta.py,
PLAN.md, PROGRESS.md.

## 2026-06-14 — Phase 2 tâche 4 : docs contrat compta_json (Phase 2 terminée)

**Tâche** : documenter le format du contrat `compta_json` dans le README (référence
SPEC.md) et noter la version du contrat.

**Fait** :
- `README.md` : nouvelle section « Contrat `compta_json` (unification avec
  compta-rapidetech) » avant le déploiement. Décrit le seam (champ texte long,
  analyse unique côté producteur), la **version courante `1`**
  (`COMPTA_CONTRACT_VERSION` dans `compta_payload.py`), le schéma JSON, les règles
  (cents entiers, items avant taxes, cohérence somme+taxes == total → `needs_review`,
  items vides → repli ligne unique), et le rappel que le champ se crée manuellement
  via `ensure_compta_field.py` (jamais par le loop) avec id dans `CUSTOM_FIELD_IDS`.

**Décisions** : doc placée dans README (pas de nouveau fichier) pour rester proche
du point d'entrée du repo ; le schéma reprend celui de SPEC.md sans le dupliquer
comme source de vérité (renvoi explicite à SPEC.md). Tâche purement documentaire :
aucun code touché, gate inchangé.

**Vérifications** : `python -m pytest -q` ✅ (136/136). **Phase 2 complète.** Toutes
les tâches hors Phase 3 sont cochées → `DONE` en première ligne. Phase 3 est réalisée
dans le repo `compta-rapidetech`.

**Fichiers** : README.md, PLAN.md, PROGRESS.md.

## Décisions à valider

- Contrat d'unification : un seul champ Paperless `compta_json` (texte long, JSON),
  montants en cents entiers, plutôt que d'éparpiller les items dans plusieurs champs.
  Les champs hérités Total/TPS/TVQ restent pour l'affichage humain.
- Le loop ne fait aucun appel réseau réel ni déploiement : `ensure_compta_field.py`
  est écrit par le loop mais exécuté manuellement (création du champ via l'API).
- `compta_json` écrit pour TOUS les types de document (y compris rapport/manuel/
  personnel/medical), pas seulement les types financiers. Aligné sur SPEC.md mais à
  confirmer : Alexandre veut-il un contrat compta_json sur les documents non
  financiers (où il sera surtout `needs_review` avec items vides) ?
