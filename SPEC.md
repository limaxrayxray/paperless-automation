# SPEC — Automatisation Paperless Rapidetech

## Vision

Pipeline d'ingestion Paperless-ngx qui, à chaque document consommé, le classe et
en extrait les données financières (type, fournisseur, date, total, TPS, TVQ,
items de ligne), puis réécrit ces informations dans Paperless. L'analyse se fait
**une seule fois** (Claude CLI, abonnement — pas l'API au token), et le résultat
structuré est exposé à l'application comptable `compta-rapidetech` via un
**contrat stable** : aucune ré-extraction côté compta.

But de cette passe : rendre le pipeline **robuste et testé** (il n'a aujourd'hui
aucun test automatisé) et **persister les items de ligne** pour unifier les deux
systèmes autour d'une source d'extraction unique.

## Principes non négociables

1. **Jamais de secret en dur.** Token Paperless et tout autre secret via `.env`
   (gitignoré) ou l'environnement. `.env.example` documente les variables.
2. **Tests hermétiques.** Le gate de tests (`pytest`) **mocke tous les appels
   externes** : jamais d'appel réel à Claude CLI ni à l'API Paperless pendant les
   tests. Aucun test ne doit dépendre d'un serveur Paperless en marche.
3. **Aucun déploiement automatique.** Le loop modifie le code du repo et le
   commite; il ne copie **jamais** rien vers `/opt/paperless/scripts` ni ne
   redémarre de service. Le déploiement reste une action humaine.
4. **Tags protégés intouchables.** Les tags de `PROTECTED_TAG_IDS` ne sont jamais
   ajoutés ni retirés automatiquement (cf. `config.py`).
5. **Idempotence.** Re-traiter le même document ne duplique rien et converge vers
   le même résultat (tags, champs, titre).
6. **Montants : cents (integer) dans le contrat.** Le payload `compta_json` exporte
   tous les montants en **cents entiers** (jamais de float pour l'argent), même si
   les champs Paperless hérités (Total/TPS/TVQ) restent en chaîne décimale pour
   l'affichage humain.
7. **Sécurité documentaire.** `delete_document` n'est appelé que pour les images
   inline d'email vides (règle existante) — jamais élargi sans validation humaine.

## Contrat d'unification — champ `compta_json`

Le seam entre `paperless-automation` (producteur) et `compta-rapidetech`
(consommateur) est **un champ personnalisé Paperless de type texte long**, nommé
`compta_json`, contenant un objet JSON unique et auto-suffisant :

```json
{
  "version": 3,
  "doc_type": "facture | recu | releve | contrat | assurance | rapport | certificat | gouvernement | medical | impots | autre",
  "fournisseur": "string | null",
  "supplier_foreign": false,
  "date": "YYYY-MM-DD | null",
  "currency": "CAD",
  "total_cents": 0,
  "tps_cents": 0,
  "tvq_cents": 0,
  "items": [
    { "description": "string", "amount_cents": 0, "taxable": true,
      "sku": "string | null", "qty": 1, "unit_price_cents": 0 }
  ],
  "needs_review": false,
  "review_reason": "string | null",
  "source_method": "ocr_text | vision_fallback | vision_primary | ..."
}
```

Règles du contrat :

- **Tous les montants en cents entiers.** `8000` = 80,00 $.
- **`items[].amount_cents`** sont des montants **avant taxes**.
- **v3 — `items[].sku` / `qty` / `unit_price_cents`** (additifs, non destructifs) :
  `sku` = code produit du fournisseur tel qu'affiché (UPC, ASIN Amazon, n° d'article
  Canadian Tire, réf. DigitalOcean…) — **jamais deviné**. But : pouvoir ré-identifier
  un item d'un achat à l'autre, sans imposer de format. Garde-fou qualité : un code en
  12 chiffres purs est traité comme un UPC-A et son check digit validé (checksum KO →
  repli, anti-erreur OCR) ; tout autre format passe tel quel. **Repli sans code** : si
  aucun code produit n'est lisible (ex. facture Claude « Claude Pro »), `sku` reprend
  la **description** (`sku == description`) ; `null` seulement si ni code ni description.
  `qty` = entier >= 1 (défaut 1) ; `unit_price_cents` en cents avant taxes. Invariant :
  `amount_cents == qty × unit_price_cents` (au cent près). Sans qté → `qty=1`,
  `unit_price_cents = amount_cents`.
- **Sources à prix taxes-incluses (SAQ)** : quand l'analyse pose
  `line_amounts_include_tax`, les lignes taxables (prix TTC affichés) sont ramenées
  en HT au prorata, à partir des totaux TPS/TVQ du document ; la consigne
  (`taxable:false`) est laissée intacte. `items[].amount_cents` est donc toujours HT,
  et la cohérence `somme(items) + tps + tvq == total` reste vérifiée.
- **`currency`** : code de devise du document (« CAD » par défaut). Les montants
  restent **dans cette devise, sans conversion** — c'est au consommateur de décider
  quoi faire d'une devise ≠ CAD (ex. brouillon `needs_review`, conversion manuelle).
- **`doc_type`** : type classé par l'analyse. Le consommateur peut ignorer ce qui
  n'est ni `facture` ni `recu` (un relevé ou un contrat n'est pas une dépense).
- **`supplier_foreign`** : `true` si le fournisseur est hors Canada. Dans ce cas
  `tps_cents = tvq_cents = 0` est **normal** (pas une incohérence).
- **Cohérence** : `somme(items.amount_cents) + tps_cents + tvq_cents` doit égaler
  `total_cents`. Sinon `needs_review = true` et `review_reason` explique l'écart.
  Le producteur n'« invente » jamais de ligne pour forcer l'équilibre.
- **`items` peut être `[]`** (reçu global, montant unique) — alors `needs_review`
  est `true` avec une raison; le consommateur retombe sur une ligne unique.
- **Stabilité / compat** : `version` permet l'évolution. Un champ inconnu du
  consommateur est ignoré, jamais une erreur (un consommateur v1 lit un payload v2
  sans broncher). Inversement le consommateur v2 **tolère un payload v1** : champs
  absents → défauts `currency = "CAD"`, `supplier_foreign = false`, `doc_type = null`.

Côté consommateur (compta), responsabilité documentée ici pour mémoire (réalisée
dans le repo `compta-rapidetech`) : si `compta_json` est absent, mal formé, ou
incohérent, compta crée un brouillon à **une ligne** (`total_cents − tps − tvq`)
marqué `needs_review`, jamais un brouillon déséquilibré ni un plantage.

## Architecture (existant à préserver)

- `doc_processor.py` : point d'entrée du hook post-consommation (`DOCUMENT_ID` en
  env). Récupère le doc, l'analyse, construit tags/champs/titre/date, applique.
- `claude_analyzer.py` : analyse OCR-first + fallback vision via Claude CLI,
  validation et nettoyage du JSON (`_validate_and_clean`).
- `paperless_client.py` : client REST stdlib (urllib) — get/patch/delete document,
  correspondants, custom fields.
- `config.py` : config centrale, secrets via `.env`.
- `retry_processor.py` : reprise des documents mis en queue sur rate limit.

Hors scope (ignorés, ne pas toucher) : `dolibarr_client.py`, `push_to_dolibarr.py`,
`wave_to_odoo.py`, `pay_odoo_bills.py`, `fix_odoo_taxes.py`, `fix_remaining_bills.py`,
`create_akaunting_lxc.sh`, `wave_export/`.

## Tests (gates du loop)

- `python -m pytest -q` — obligatoire avant tout commit, **tout mocké**.
- Tests critiques minimaux :
  - `_extract_json` : JSON nu, fencé Markdown, entouré de texte, illisible.
  - `_validate_and_clean` : normalisation montants, filtrage des tags non
    autorisés, drapeau d'incohérence fiscale, validation de date.
  - `build_tag_updates` : tags protégés jamais touchés, tag année, `a-verifier`
    selon la confiance, idempotence.
  - Sérialisation `compta_json` : conversion en cents, validation de cohérence,
    `needs_review` quand somme ≠ total, items vides.
  - Chemin d'erreur : échec Claude → tag `a-verifier`, aucune exception remontée.

## Conventions pour le loop

- Une tâche = un commit, message `feat|fix|test|chore|docs: description`.
- Tâche découverte hors scope → section `## Backlog` de PLAN.md, ne pas l'implémenter.
- Ne jamais modifier ce SPEC.md sans instruction humaine.
- Choix ambigu → option la plus simple, notée dans PROGRESS.md « Décisions à valider ».
- Jamais d'appel réel Paperless/Claude dans les tests; jamais de déploiement.

## Définition de « terminé »

Le pipeline a un harnais de tests pytest vert couvrant les fonctions critiques;
chaque document consommé produit un champ `compta_json` conforme au contrat
ci-dessus (montants en cents, cohérence validée, `needs_review` posé au besoin);
et `compta-rapidetech` peut créer un brouillon comptable complet (une écriture par
item, taxes séparées, équilibré) **sans ré-extraction et sans appel API payant**.
