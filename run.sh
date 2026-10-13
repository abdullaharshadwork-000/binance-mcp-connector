#!/usr/bin/env bash
set -e

if [ ! -f .env ]; then
  echo "Missing .env file. Copy .env.example to .env and fill it in."
  exit 1
fi

set -a
. ./.env
set +a

python server.py &
SERVER_PID=$!
trap "kill $SERVER_PID" EXIT

sleep 2
cloudflared tunnel --url "http://localhost:${PORT:-8000}"
