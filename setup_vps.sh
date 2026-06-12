#!/bin/bash
# State Civic Intelligence — fresh Ubuntu VPS setup (e.g. Hostinger, Ubuntu 22.04+).
# For the POST-PILOT migration only. Copy the whole project folder up first:
#   scp -r state-civic-intelligence/ user@your-vps:~/
#   ssh user@your-vps && cd state-civic-intelligence && bash setup_vps.sh
set -e
cd "$(dirname "$0")"

echo "==> System packages"
sudo apt-get update -y && sudo apt-get install -y python3 python3-venv python3-pip git curl

echo "==> Python env + deps"
python3 -m venv .venv && source .venv/bin/activate
pip install --quiet --upgrade pip && pip install --quiet -r requirements.txt

echo "==> .env"
[ -f .env ] || { cp .env.example .env; echo "    EDIT .env with your keys before continuing."; }

echo "==> Database"
python3 scripts/db.py

echo "==> Hermes Agent"
if ! command -v hermes >/dev/null 2>&1; then
  echo "    Install Hermes per https://hermes-agent.nousresearch.com/docs (Linux script),"
  echo "    then re-run this script."
  exit 0
fi
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HERMES_HOME/skills/state"
cp -R hermes/skills/* "$HERMES_HOME/skills/state/"
cp hermes/AGENTS.md ./AGENTS.md
cp hermes/SOUL.md "$HERMES_HOME/SOUL.md"

echo "==> Gateway as boot service (24/7 operation)"
sudo hermes gateway install --system || hermes gateway install

echo "==> Done. Recreate the cron job in chat (README §5), copy data/state_intel.db"
echo "    from the laptop if you want history, and switch the schedule to 0 7 * * *."
