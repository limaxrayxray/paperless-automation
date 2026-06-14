# PROGRESS — Automatisation Paperless Rapidetech

(Le loop écrit ici une entrée datée par tâche. La première ligne devient `DONE`
quand toutes les tâches hors Phase 3 sont cochées ou BLOQUÉES.)

## Décisions à valider

- Contrat d'unification : un seul champ Paperless `compta_json` (texte long, JSON),
  montants en cents entiers, plutôt que d'éparpiller les items dans plusieurs champs.
  Les champs hérités Total/TPS/TVQ restent pour l'affichage humain.
- Le loop ne fait aucun appel réseau réel ni déploiement : `ensure_compta_field.py`
  est écrit par le loop mais exécuté manuellement (création du champ via l'API).
