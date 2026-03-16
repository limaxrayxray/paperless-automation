"""Configuration centrale pour le processeur de documents Rapidetech."""

PAPERLESS_URL = "http://localhost:8000/api"
PAPERLESS_TOKEN = "ca37cac25733d04b2ead0aaa9eeaf2da8f801239"

CLAUDE_BIN = "/root/.local/bin/claude"
LOG_FILE = "/opt/paperless/scripts/logs/processor.log"

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
