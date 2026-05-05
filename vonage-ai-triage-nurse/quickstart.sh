#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

# --- 1. Copy env files if not present ---
if [[ ! -f .env ]]; then
  cp env.example .env
  echo "Created .env from env.example — fill in your keys before continuing."
  exit 1
fi

if [[ ! -f n8n/.env ]]; then
  cp n8n/env.example n8n/.env
  echo "Created n8n/.env from n8n/env.example — fill in SMS credentials before continuing."
  exit 1
fi

# --- 2. Start N8N ---
echo "Starting N8N..."
docker compose -f n8n/docker-compose.yml --env-file n8n/.env up -d

echo ""
echo "N8N is running at http://localhost:5678"
echo ""
echo "MANUAL STEP REQUIRED (only once):"
echo "  1. Open http://localhost:5678"
echo "  2. Register a local account (first time only)."
echo "  3. Click + (top right) to create a new workflow."
echo "  4. Click ... menu (top right) -> Import from file."
echo "  5. Choose: n8n/workflows/vonage-ai-triage-nurse.workflow.json"
echo "  6. Click Publish."
echo ""
read -rp "Press Enter once you have published the workflow in N8N UI..."

# --- 3. Install Python dependencies ---
echo "Installing Python dependencies..."
uv sync

# --- 4. Start app ---
echo ""
echo "Starting app on http://localhost:8005 ..."
echo "After the app starts, trigger the voice bridge with:"
echo "  curl -X POST http://localhost:8005/connect -H 'Content-Type: application/json' -d '{}'"
echo ""
uv run server.py
