#!/usr/bin/env python3
"""Crée (idempotent) le champ personnalisé `compta_json` dans Paperless et inscrit
son id dans `CUSTOM_FIELD_IDS` (config.py).

Le champ `compta_json` est le seam d'unification avec `compta-rapidetech` : un
champ texte long contenant le contrat JSON décrit dans SPEC.md.

⚠️  Ce script fait un APPEL RÉSEAU RÉEL à l'API Paperless. Il n'est JAMAIS exécuté
par le loop d'automatisation (cf. SPEC.md, principe « aucun appel réseau réel
depuis le loop »). À lancer manuellement, une seule fois, après déploiement :

    python ensure_compta_field.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import paperless_client

COMPTA_FIELD_NAME = "compta_json"
# Paperless-ngx expose un type « longtext » (texte long) — idéal pour y sérialiser
# le contrat JSON `compta_json` (cf. SPEC.md). Plus adapté que `string` (mono-ligne).
COMPTA_FIELD_DATA_TYPE = "longtext"
CONFIG_PATH = Path(__file__).parent / "config.py"


def inject_field_id_into_config(source: str, name: str, field_id: int) -> str:
    """Insère ou met à jour `"<name>": <field_id>` dans le bloc CUSTOM_FIELD_IDS
    du texte source de config.py.

    Idempotent : ré-appliquer avec le même id ne change rien. Lève ValueError si le
    bloc CUSTOM_FIELD_IDS est introuvable.
    """
    block_re = re.search(r"CUSTOM_FIELD_IDS\s*=\s*\{(.*?)\n\}", source, re.DOTALL)
    if not block_re:
        raise ValueError("Bloc CUSTOM_FIELD_IDS introuvable dans config.py")

    block = block_re.group(1)
    entry_re = re.compile(rf'(\n\s*"{re.escape(name)}"\s*:\s*)(\d+)')
    if entry_re.search(block):
        new_block = entry_re.sub(rf"\g<1>{field_id}", block)
    else:
        new_block = block.rstrip() + f'\n    "{name}": {field_id},'

    return source[: block_re.start(1)] + new_block + source[block_re.end(1) :]


def main() -> int:
    field = paperless_client.find_or_create_custom_field(
        COMPTA_FIELD_NAME, COMPTA_FIELD_DATA_TYPE,
    )
    field_id = field["id"]
    print(
        f"Champ '{COMPTA_FIELD_NAME}' : id={field_id} "
        f"(data_type={field.get('data_type')})",
    )

    source = CONFIG_PATH.read_text(encoding="utf-8")
    updated = inject_field_id_into_config(source, COMPTA_FIELD_NAME, field_id)
    if updated != source:
        CONFIG_PATH.write_text(updated, encoding="utf-8")
        print(
            f"config.py mis à jour : "
            f"CUSTOM_FIELD_IDS['{COMPTA_FIELD_NAME}'] = {field_id}",
        )
    else:
        print("config.py déjà à jour (aucune modification).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
