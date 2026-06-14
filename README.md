# Automatisation Paperless — Rapidetech

Pipeline d'ingestion Paperless-ngx : à chaque document consommé, classe et extrait
les données financières (type, fournisseur, date, total, TPS, TVQ, items), puis
réécrit tags/titre/date/champs dans Paperless. L'analyse se fait via **Claude CLI**
(abonnement). Le résultat structuré est exposé à `compta-rapidetech` via le champ
personnalisé `compta_json` (voir [SPEC.md](SPEC.md)).

Roadmap et avancement : [PLAN.md](PLAN.md), [PROGRESS.md](PROGRESS.md).

## Configuration

Les secrets vivent dans `.env` (gitignoré). Copier le modèle et remplir :

```bash
cp .env.example .env
# éditer .env : PAPERLESS_TOKEN (obligatoire), PAPERLESS_URL, CLAUDE_BIN
```

## Tests

Les scripts de production n'utilisent que la stdlib; les tests utilisent pytest et
**mockent tous les appels externes** (aucun appel réel à Claude ni à Paperless).

```bash
python -m venv .venv
. .venv/bin/activate            # Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q
```

## Contrat `compta_json` (unification avec compta-rapidetech)

Le seam entre ce repo (producteur) et `compta-rapidetech` (consommateur) est un
**champ personnalisé Paperless de type texte long** nommé `compta_json`, contenant
un objet JSON unique et auto-suffisant. L'analyse n'est faite **qu'une fois** ici;
compta ne ré-extrait jamais. Le contrat de référence est dans [SPEC.md](SPEC.md);
le producteur est `compta_payload.build_compta_payload`.

**Version courante du contrat : `2`** (`COMPTA_CONTRACT_VERSION` dans
`compta_payload.py`). Le champ `version` permet l'évolution : un champ inconnu du
consommateur est ignoré, jamais une erreur. Incrémenter à chaque changement de
format observable par le consommateur.

```json
{
  "version": 2,
  "doc_type": "facture | recu | releve | contrat | assurance | autre | ... | null",
  "fournisseur": "string | null",
  "supplier_foreign": false,
  "date": "YYYY-MM-DD | null",
  "currency": "CAD",
  "total_cents": 0,
  "tps_cents": 0,
  "tvq_cents": 0,
  "items": [
    { "description": "string", "amount_cents": 0, "taxable": true }
  ],
  "needs_review": false,
  "review_reason": "string | null",
  "source_method": "ocr_text | vision_fallback | vision_primary | unknown"
}
```

Règles :

- **Tous les montants en cents entiers** (jamais de float) : `8000` = 80,00 $.
- **`items[].amount_cents`** sont des montants **avant taxes**.
- **`doc_type`** : type classé par l'analyse (`null` si absent). Le consommateur
  peut ignorer ce qui n'est ni `facture` ni `recu` (un relevé ou un contrat n'est
  pas une dépense).
- **`currency`** : code de devise du document (« CAD » par défaut, normalisée en
  majuscules). Les montants restent **dans cette devise, sans conversion**. Une
  devise ≠ CAD **ne déclenche pas** `needs_review` côté producteur — c'est au
  consommateur de décider quoi en faire (brouillon `needs_review`, conversion
  manuelle, etc.).
- **`supplier_foreign`** : `true` si le fournisseur est hors Canada (`false` par
  défaut). Dans ce cas `tps_cents = tvq_cents = 0` est **normal** (pas une
  incohérence).
- **Cohérence** : `somme(items.amount_cents) + tps_cents + tvq_cents` doit égaler
  `total_cents`. Sinon `needs_review = true` et `review_reason` explique l'écart.
  Le producteur n'invente jamais de ligne pour forcer l'équilibre.
- **`items` peut être `[]`** (reçu global) — alors `needs_review = true` avec raison;
  le consommateur retombe sur une ligne unique (`total_cents − tps − tvq`).

**Compatibilité v1 ↔ v2.** Les champs `doc_type`, `currency` et `supplier_foreign`
sont apparus en version 2 (motivés par l'audit réel du 2026-06-14 : devises USD et
fournisseurs étrangers observés). Un consommateur v1 lit un payload v2 sans broncher
(champs en trop ignorés). Inversement, le consommateur v2 **tolère un payload v1** :
champs absents → défauts `doc_type = null`, `currency = "CAD"`,
`supplier_foreign = false`. L'historique en v1 peut être recalculé en v2 via
`backfill_compta_json.py` (appel réseau réel — jamais exécuté par le loop).

Le champ doit exister dans Paperless et son id figurer dans `CUSTOM_FIELD_IDS`
(`config.py`). Le créer une fois, manuellement, via `ensure_compta_field.py`
(appel réseau réel — jamais exécuté par le loop). Tant que l'id n'est pas inscrit,
`build_custom_fields` n'écrit simplement pas le contrat.

## Déploiement (manuel)

Les scripts se déploient à `/opt/paperless/scripts/` et sont déclenchés par le hook
post-consommation de Paperless (`PAPERLESS_POST_CONSUME_SCRIPT` → `post_consume.sh`).
Le déploiement n'est **jamais** automatisé par le loop (cf. PROMPT.md).

## Composants principaux

| Fichier | Rôle |
|---|---|
| `doc_processor.py` | Point d'entrée du hook : analyse + application des changements |
| `claude_analyzer.py` | Analyse OCR-first + fallback vision via Claude CLI |
| `paperless_client.py` | Client REST Paperless (stdlib) |
| `config.py` | Config centrale, secrets via `.env` |
| `retry_processor.py` | Reprise des documents en queue (rate limit) |

Hors scope (intégrations héritées, ne pas toucher) : `*dolibarr*`, `*odoo*`,
`wave_to_odoo.py`, `wave_export/`.
