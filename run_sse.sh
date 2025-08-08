#!/usr/bin/env bash
set -euo pipefail
source /home/diae/mcp/mariadb-mcp-server/.venv/bin/activate
python /home/diae/mcp/mariadb-mcp-server/src/server.py --transport sse --host 127.0.0.1 --port 9101
