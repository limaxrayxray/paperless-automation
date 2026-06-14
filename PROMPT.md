# Instructions de session

Tu travailles sur l'automatisation Paperless de Rapidetech (voir SPEC.md). Les
Phases 0 à 2 (durcissement + contrat `compta_json` v1) sont faites. Le but de
cette passe : la **Phase 4 — contrat v2** (porter `doc_type`, `currency`,
`supplier_foreign` dans le payload et fournir un script de backfill de l'historique),
motivée par l'audit réel du 2026-06-14. Cf. SPEC.md § contrat (version 2).

## Procédure — UNE seule tâche par session

1. Lis SPEC.md, PLAN.md et PROGRESS.md.
2. Choisis la PREMIÈRE tâche non cochée et non marquée `BLOQUÉE` dans PLAN.md
   (ignore la Phase 3 : elle est réalisée dans l'autre repo).
3. Implémente-la complètement, avec ses tests.
4. Roule `python -m pytest -q`. Corrige jusqu'à ce que tout passe.
5. Si tout passe :
   - `git add -A && git commit -m "feat|fix|test|chore|docs: description"`
   - Coche la tâche dans PLAN.md
   - Ajoute une entrée datée dans PROGRESS.md : tâche, décisions, fichiers touchés
6. Si après 2 tentatives sérieuses les tests échouent toujours :
   - Annule le code cassé (`git checkout -- <fichiers>`)
   - Marque la tâche `BLOQUÉE — raison` dans PLAN.md, détaille dans PROGRESS.md
   - Commit uniquement PLAN.md et PROGRESS.md, puis termine la session

## Règles de sécurité (impératives)

- **Jamais d'appel réel** à l'API Paperless ni à Claude CLI dans les tests : tout
  est mocké (faux `subprocess.run`, faux client Paperless). Aucun test ne dépend
  d'un serveur en marche.
- **Aucun déploiement** : ne copie rien vers `/opt/paperless/scripts`, ne redémarre
  aucun service, ne touche pas aux `*.service`/`*.socket`. Le déploiement est manuel.
- **Aucun appel réseau réel** depuis le loop, point. Les scripts qui en font un
  (ex. `ensure_compta_field.py`) sont écrits puis laissés à exécuter par l'humain.
- **Tags protégés** (`PROTECTED_TAG_IDS`) jamais modifiés.
- **Jamais de secret en dur** : tout via `.env` (voir `.env.example`).
- Montants du contrat `compta_json` en **cents entiers** (jamais de float).

## Règles générales

- Ne modifie JAMAIS SPEC.md.
- Tâche/problème découvert hors de la tâche courante → section `## Backlog` de PLAN.md,
  ne l'implémente pas.
- Choix ambigu → option la plus simple, note-la dans PROGRESS.md « Décisions à valider ».
- Ne touche pas aux fichiers hors scope listés dans SPEC.md (Dolibarr/Odoo/Wave).
- Si toutes les tâches (hors Phase 3) sont cochées ou BLOQUÉES : écris `DONE` sur la
  première ligne de PROGRESS.md et termine.
