#!/usr/bin/env python3
"""Stop the MariaDB MCP server running on port 9001."""
import os, signal, subprocess, sys

result = subprocess.run(['lsof', '-ti:9001'], capture_output=True, text=True)
for pid in result.stdout.split():
    try:
        os.kill(int(pid), signal.SIGINT)
    except (ProcessLookupError, ValueError):
        pass
