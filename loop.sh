#!/bin/bash
# loop.sh — Loop autonome Claude Code pour l'automatisation Paperless.
# Usage : ./loop.sh   (depuis la racine du repo paperless-automation)
# Gate avant chaque commit : python -m pytest (tout mocké, aucun appel réel).
# Le loop NE DÉPLOIE JAMAIS vers /opt/paperless et ne fait aucun appel réseau réel.
# AVANT une session de nuit : snapshot du LXC/VM!

set -uo pipefail

MAX_ITER=40        # plafond d'itérations par lancement
STALL_LIMIT=3      # itérations consécutives sans commit avant arrêt
PAUSE_LIMITE=1800  # pause (s) quand le rate limit est atteint, puis reprise auto
MODEL="${LOOP_MODEL:-claude-fable-5}"

# Webhook de notification, optionnel — via .env / environnement, jamais en dur.
[ -f .env ] && set -a && . ./.env && set +a
HA_WEBHOOK="${LOOP_HA_WEBHOOK:-}"

notify() {
  [ -z "$HA_WEBHOOK" ] && return 0
  curl -s -m 10 -X POST "$HA_WEBHOOK" \
    -H "Content-Type: application/json" \
    -d "{\"message\": \"$1\"}" >/dev/null 2>&1 || true
}

stall=0
last_commit=$(git rev-parse HEAD)

for i in $(seq 1 "$MAX_ITER"); do
  echo ""
  echo "=== Itération $i/$MAX_ITER — $(date '+%Y-%m-%d %H:%M') ==="

  # Condition de fin : le loop a écrit DONE en première ligne de PROGRESS.md
  if head -n1 PROGRESS.md 2>/dev/null | grep -q "^DONE"; then
    echo ">>> Projet marqué DONE. Fin du loop."
    notify "✅ Loop paperless-automation : terminé (DONE)."
    break
  fi

  # Session Claude Code avec le prompt fixe — sortie à l'écran ET dans le log
  claude -p "$(cat PROMPT.md)" \
    --model "$MODEL" \
    --dangerously-skip-permissions 2>&1 | tee -a loop.log
  output=$(tail -n 30 loop.log)

  # Rate limit → pause silencieuse puis reprise (l'état vit dans les fichiers)
  if echo "$output" | grep -qiE "limit reached|rate.?limit|usage limit"; then
    echo ">>> Limite d'utilisation atteinte. Pause $((PAUSE_LIMITE/60)) min, reprise auto."
    sleep "$PAUSE_LIMITE"
    continue
  fi

  # Anti-enlisement : pas de nouveau commit = compteur
  new_commit=$(git rev-parse HEAD)
  if [ "$new_commit" = "$last_commit" ]; then
    stall=$((stall + 1))
    echo ">>> Aucun commit cette itération ($stall/$STALL_LIMIT)."
    if [ "$stall" -ge "$STALL_LIMIT" ]; then
      echo ">>> Loop bloqué : $STALL_LIMIT itérations sans progrès."
      echo ">>> Voir loop.log et la section BLOQUÉE de PLAN.md."
      notify "🛑 Loop paperless-automation bloqué après $i itérations sans commit."
      break
    fi
  else
    stall=0
    last_commit=$new_commit
    echo ">>> Commit : $(git log -1 --oneline)"
    # Décommenter pour pousser à chaque progrès (repo privé) :
    # git push >/dev/null 2>&1 || true
  fi

  sleep 10
done

echo ""
echo "=== Loop terminé. Derniers commits : ==="
git log --oneline -10
notify "ℹ️ Loop paperless-automation terminé ($(git log --oneline -1))."
