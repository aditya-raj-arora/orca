#!/usr/bin/env bash
# Quick local dev bootstrap. Owner: P6 (QA/Integration Lead) maintains this as
# the project grows — keep it in sync with README.md "Getting started".
set -euo pipefail

echo "== ORCA backend setup =="
cd "$(dirname "$0")/../src/backend"
[ -f .env ] || cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
echo "Backend deps installed. Fill in src/backend/.env before running the server."

echo "== ORCA frontend setup =="
cd ../frontend
npm install
echo "Frontend deps installed."

echo "Done. Run 'docker compose up --build' from the repo root, or start each
service manually per README.md."
