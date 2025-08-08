#!/usr/bin/env bash
set -euo pipefail
cd /home/diae/mcp/mariadb-mcp-server
source .venv/bin/activate
python src/server.py --transport stdio
