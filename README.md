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
