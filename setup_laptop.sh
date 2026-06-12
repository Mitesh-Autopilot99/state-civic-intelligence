#!/bin/bash
# State Civic Intelligence — laptop setup (macOS). Run from the project folder:
#   bash setup_laptop.sh
set -e
cd "$(dirname "$0")"

echo "==> 1/5 Python environment"
python3 -m venv .venv
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "    OK ($(python3 --version))"

echo "==> 2/5 .env file"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    Created .env — EDIT IT NOW with your Reddit + OpenRouter keys (see README §2-3)."
else
  echo "    .env already exists, leaving it alone."
fi

echo "==> 3/5 Database"
python3 scripts/db.py

echo "==> 4/5 Hermes files"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HERMES_HOME/skills/state"
cp -R hermes/skills/* "$HERMES_HOME/skills/state/"
cp hermes/AGENTS.md ./AGENTS.md
if [ -f "$HERMES_HOME/SOUL.md" ]; then
  cp "$HERMES_HOME/SOUL.md" "$HERMES_HOME/SOUL.md.backup"
  echo "    Existing SOUL.md backed up to SOUL.md.backup"
fi
cp hermes/SOUL.md "$HERMES_HOME/SOUL.md"
echo "    Skills installed to $HERMES_HOME/skills/state/, SOUL.md installed, AGENTS.md placed in project root."

echo "==> 5/5 Done. Next steps (full detail in README):"
echo "    a) Fill in .env (Reddit + OpenRouter keys)"
echo "    b) Verify targets:   source .venv/bin/activate && python scripts/verify_targets.py"
echo "    c) First manual run: python scripts/run_pipeline.py"
echo "    d) Set up Telegram + the Hermes cron job (README §4-5)"
