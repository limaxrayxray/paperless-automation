# PLAN — Durcissement + unification de l'automatisation Paperless

Objectif : rendre le pipeline robuste (tests) et persister les items de ligne dans
le champ `compta_json` pour unifier avec `compta-rapidetech` (cf. SPEC.md).

Règles : une tâche = un commit. Tâches dans l'ordre. Tâche infaisable après 2
tentatives = `BLOQUÉE — raison`. Gate avant commit : `python -m pytest -q` (tout
mocké, jamais d'appel réel Paperless/Claude). Jamais de déploiement automatique.

## Phase 0 — Fondation de tests

- [x] `requirements-dev.txt` (pytest) + note dans README : créer un venv, installer,
      lancer `python -m pytest`. `pytest.ini` (ou `pyproject`) ciblant `test_*.py`.
- [x] `conftest.py` : fixtures de mock — un faux `subprocess.run` pour Claude CLI
      (réponses simulées, pas de réseau) et un faux client Paperless (get/patch/delete
      en mémoire). Aucun appel externe réel.
- [x] Premier test vert : `_extract_json` (claude_analyzer) — JSON nu, fencé ```json,
      entouré de texte, et cas illisible (lève ValueError).

## Phase 1 — Durcir l'extraction existante (tests sur le code en place)

- [x] Tests `_validate_and_clean` : normalisation des montants (« 66,81 $ » → « 66.81 »,
      valeurs nulles → None), `doc_type` invalide → « autre », filtrage des tags hors
      `ALLOWED_TAGS`, bornage des confiances [0,1].
- [x] Tests `_validate_and_clean` — règles fiscales : facture/recu force tps/tvq à
      « 0.00 » si None; drapeau d'incohérence (total > 20 $ et tvq = 0.00) abaisse la
      confiance et ajoute la note d'attention.
- [x] Tests `build_tag_updates` : tags protégés jamais retirés ni ajoutés; un seul tag
      année à la fois (selon `DATE_CONFIDENCE_THRESHOLD`); `a-verifier` ajouté/retiré
      selon `GLOBAL_CONFIDENCE_THRESHOLD`; règle medical → personnel.
- [x] Tests `build_custom_fields` : TPS/TVQ/Total/Facture écrits seulement pour les
      types pertinents; valeurs existantes préservées; aucune valeur None écrite.
- [x] Test d'idempotence de `process_document` (client mocké) : deux passages
      successifs sur le même document convergent (mêmes tags/champs finaux, aucun
      doublon de tag).
- [x] Test du chemin d'erreur : si l'analyse Claude lève, `process_document` ajoute
      `a-verifier` et n'élève aucune exception (sauf RateLimitError, qui remonte).

## Phase 2 — Contrat d'unification `compta_json`

- [x] Script `ensure_compta_field.py` : crée (idempotent) le champ personnalisé
      `compta_json` (type texte long) via l'API Paperless s'il n'existe pas, et
      affiche son id; ajouter l'id à `CUSTOM_FIELD_IDS` dans `config.py`. Script à
      exécuter manuellement une fois (jamais par le loop — appel réseau réel).
- [x] Module `compta_payload.py` : `build_compta_payload(analysis) -> dict` pur —
      convertit les montants décimaux en cents entiers, assemble le contrat SPEC
      (version, fournisseur, date, total/tps/tvq_cents, items[], needs_review,
      review_reason, source_method). Tests : conversion cents, cohérence
      somme(items)+taxes == total, `needs_review` + raison quand écart ou items vides.
- [x] Brancher dans `doc_processor.build_custom_fields` : sérialiser le payload en
      JSON dans le champ `compta_json` (en plus des champs hérités). Tests sur la
      présence et la validité du JSON écrit (via client mocké).
- [x] `docs: contrat compta_json` — documenter le format dans README (déjà dans
      SPEC.md) et noter la version du contrat.

## Phase 4 — Contrat v2 : devise, type, fournisseur étranger + backfill

Motivé par l'audit du 2026-06-14 (10 docs réels) : 2/10 en USD, le type
facture/autre est nécessaire au consommateur, et l'exemption fournisseur étranger
doit voyager dans le payload. Cf. SPEC.md § contrat (version 2).

- [x] `compta_payload.py` : passer `COMPTA_CONTRACT_VERSION` à 2 et ajouter au
      payload `doc_type` (depuis `analysis["doc_type"]`), `currency` (défaut « CAD »
      si absent/vide) et `supplier_foreign` (bool, défaut `false`). Ne PAS marquer
      `needs_review` du seul fait d'une devise ≠ CAD (c'est au consommateur de
      décider) — mais documenter ce choix. Tests : présence des 3 champs, défauts
      (currency manquante → « CAD », supplier_foreign manquant → false), un cas USD
      étranger sans taxe reste cohérent (pas de `needs_review` parasite).
- [x] Vérifier `doc_processor.build_custom_fields` : le payload v2 est sérialisé tel
      quel dans `compta_json` (sort_keys, ensure_ascii=False) — aucun champ v2 perdu.
      Adapter/compléter les tests existants pour asserter les 3 nouveaux champs dans
      le JSON écrit (client mocké).
- [ ] Script `backfill_compta_json.py` : (re)calcule et écrit `compta_json` sur les
      documents **déjà tagués** facture/recu qui n'ont pas encore le champ (ou l'ont
      en v1), pour donner un historique au consommateur. Idempotent, paginé, **lecture
      d'analyse via Claude CLI + écriture Paperless réelles** → comme
      `ensure_compta_field.py`, écrit par le loop mais **exécuté à la main**, jamais
      par le loop. Option `--dry-run` (défaut) qui n'écrit rien et `--limit N`.
      Tests : la fonction pure de sélection (« quels docs backfiller ») et la
      construction du patch, tout mocké — aucun réseau dans les tests.
- [ ] `docs: contrat compta_json v2` — mettre à jour README (les 3 champs, la règle
      devise, la compat v1↔v2) et noter la bascule de version.

## Phase 3 — Côté consommateur (réalisé dans le repo compta-rapidetech)

Hors de ce loop (autre repo). Listé ici pour la traçabilité de l'unification :

- [ ] (compta) `syncPaperless` lit `compta_json` du document, construit le brouillon
      via `createDraftFromExtraction` (une écriture par item, taxes séparées).
- [ ] (compta) Repli défensif : `compta_json` absent/mal formé/incohérent → brouillon
      à une ligne (`total_cents − taxes`), `needs_review`, jamais de plantage.
- [ ] (compta) Idempotence d'import sur `paperless_doc_id` conservée; tests.

## Backlog

(Le loop ajoute ici les tâches découvertes — ne pas implémenter sans validation humaine)

- ~~Audit qualité réelle des `items` sur de vraies factures~~ — FAIT 2026-06-14
  (`audit_compta.py`, 10 docs). Conclusions reportées en Phase 4 (devise USD réelle,
  besoin du type, exemption étranger). Cohérence et exemption validées sur du réel.
- `ollama_analyzer.py` comme repli local hors-ligne (gratuit) si Claude indisponible.
- Durcir `retry_processor.py` (verrou de fichier, backoff) + tests.
- Micro-opti `process_document` : ne pas ré-émettre `created` dans le payload si la
  date courante du doc est déjà identique (patch redondant aujourd'hui — sans effet
  de bord, idempotence OK, mais une écriture inutile par re-traitement).
