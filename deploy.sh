#!/usr/bin/env bash
# Deploy gpu-workbench to the inspur server (user-space only, no sudo).
#   ./deploy.sh          rsync code + restart tmux service
#   ./deploy.sh --init   first time: also create venv, install deps, install crontab entry
set -euo pipefail
REMOTE="${REMOTE:-inspur}"
DEST="~/gpu-workbench"

rsync -av --delete \
  --exclude .git --exclude .venv --exclude __pycache__ \
  --exclude workbench.db --exclude workbench.db-wal --exclude workbench.db-shm \
  --exclude logs --exclude config.json \
  ./ "$REMOTE:$DEST/"

if [ "${1:-}" = "--init" ]; then
  ssh "$REMOTE" bash -s <<'EOF'
cd ~/gpu-workbench
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt
# boot on server restart (tmux; kill old session if any)
tmux kill-session -t gpuwb 2>/dev/null || true
tmux new-session -d -s gpuwb 'cd ~/gpu-workbench && exec .venv/bin/python server.py'
echo "service started in tmux session 'gpuwb'"
EOF
  echo
  echo "== one-time: crontab @reboot (run this yourself) =="
  echo "   ssh $REMOTE crontab -l 2>/dev/null | { cat; echo '@reboot sleep 60 && tmux new-session -d -s gpuwb \"cd ~/gpu-workbench && exec .venv/bin/python server.py\"'; } | ssh $REMOTE crontab -"
else
  ssh "$REMOTE" bash -s <<'EOF'
cd ~/gpu-workbench
if tmux has-session -t gpuwb 2>/dev/null; then
  tmux kill-session -t gpuwb
fi
tmux new-session -d -s gpuwb 'cd ~/gpu-workbench && exec .venv/bin/python server.py'
echo "service restarted in tmux session 'gpuwb'"
EOF
fi

echo
echo "check:  ssh $REMOTE curl -s 127.0.0.1:8787/api/gpu"
