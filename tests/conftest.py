"""Configuration pytest partagée.

Hermétisme : on injecte un PAPERLESS_TOKEN bidon AVANT tout import des modules du
projet (config.py lève si le token manque). Les tests ne dépendent donc jamais
d'un fichier .env réel ni d'un serveur en marche. Les fixtures de mock (faux
Claude CLI, faux client Paperless) sont ajoutées par une tâche ultérieure.
"""

import os

# Doit s'exécuter avant que tout test n'importe `config` (ou un module qui en
# dépend). setdefault : ne masque pas une valeur déjà fournie par l'environnement.
os.environ.setdefault("PAPERLESS_TOKEN", "test-token-bidon")
os.environ.setdefault("PAPERLESS_URL", "http://paperless.invalid/api")
