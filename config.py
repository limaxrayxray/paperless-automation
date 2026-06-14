"""Configuration centrale pour le processeur de documents Rapidetech."""

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Charge un fichier .env situé à côté de ce module (lignes `clé=valeur`),
    sans dépendance externe (stdlib uniquement). Les variables déjà présentes
    dans l'environnement ne sont jamais écrasées — priorité au shell/systemd."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

PAPERLESS_URL = os.environ.get("PAPERLESS_URL", "http://localhost:8000/api")

# Secret : jamais en dur. Fourni par l'environnement ou un fichier .env (gitignoré).
PAPERLESS_TOKEN = os.environ.get("PAPERLESS_TOKEN")
if not PAPERLESS_TOKEN:
    raise RuntimeError(
        "PAPERLESS_TOKEN manquant : définissez-le dans l'environnement ou dans un "
        "fichier .env à côté de config.py (voir .env.example). Jamais de secret en dur."
    )

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/root/.local/bin/claude")
LOG_FILE = os.environ.get("LOG_FILE", "/opt/paperless/scripts/logs/processor.log")

# ─── TAGS PROTÉGÉS ────────────────────────────────────────────────────────────
# NE JAMAIS assigner, modifier ou retirer ces tags automatiquement
PROTECTED_TAG_IDS = {
    53,  # rapidetech_checked
    46,  # paperless-gpt-auto
    50,  # paperless-gpt
    54,  # Gestion_ALX_Checked
    55,  # paperless-gpt-ocr-auto
    68,  # Facture Rapidetech (factures ÉMISES par Rapidetech — jamais auto-assigné)
}

# ─── TAGS DE CLASSIFICATION ───────────────────────────────────────────────────
TAG_IDS = {
    # Type de document
    "facture": 3,
    "recu": 9,
    "releve": 10,
    "rapport": 64,
    "certificat": 70,
    "gouvernement": 66,
    "contrat": 73,
    "assurance": 74,
    "autre": 75,
    "a-verifier": 76,
    "dolibarr-queue": 79,
    "dolibarr-sent": 80,
    # Contexte
    "personnel": 56,
    "medical": 13,
    "impots": 5,
    "transport": 18,
    "internet": 15,
    "telephone": 16,
    # Personnes
    "Leticia": 65,
    "Olivia": 69,
}

# Tags autorisés que Claude peut suggérer automatiquement
# personnel n'est PAS ici — il est forcé par la logique (ex: medical → personnel auto)
# mais jamais retiré s'il est déjà présent
ALLOWED_TAGS = {
    "facture", "recu", "releve", "rapport", "certificat",
    "gouvernement", "contrat", "assurance", "autre",
    "transport", "internet", "telephone",
    "medical",   # auto: toujours accompagné de personnel (logique dans doc_processor)
    "Olivia",    # auto: si document concerne Olivia
    "Leticia",   # auto: si document concerne Leticia
}

# ─── TAGS ANNÉE ───────────────────────────────────────────────────────────────
YEAR_TAG_IDS = {
    2010: 71,
    2014: 72,
    2016: 63,
    2017: 62,
    2018: 61,
    2019: 60,
    2020: 59,
    2021: 58,
    2022: 57,
    2023: 27,
    2024: 26,
    2025: 25,
    2026: 67,
}

# ─── CUSTOM FIELDS ────────────────────────────────────────────────────────────
CUSTOM_FIELD_IDS = {
    "TPS": 13,
    "TVQ": 14,
    "Total": 15,
    "Facture": 16,  # Numéro de facture
}

# ─── TYPES DE DOCUMENT ────────────────────────────────────────────────────────
DOC_TYPE_IDS = {
    "facture": 5,
    "releve": 6,
    "rapport": 8,
    "manuel": 7,
}

# ─── SEUILS ───────────────────────────────────────────────────────────────────
# Confiance minimale pour assigner le tag année
DATE_CONFIDENCE_THRESHOLD = 0.85
# Confiance minimale globale; en dessous → tag a-verifier
GLOBAL_CONFIDENCE_THRESHOLD = 0.65
# Longueur max du contenu OCR envoyé à Claude (caractères)
MAX_CONTENT_LENGTH = 6000
