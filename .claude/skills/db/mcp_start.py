#!/usr/bin/env python3
"""Start the MariaDB MCP server and wait until it is ready.

Usage: python3 mcp_start.py [ENV]
  ENV: dev, staging, or production (default: omitted, server uses its own default)

Exits non-zero if the server fails to start within 15 seconds.
"""
import os, signal, subprocess, sys, tempfile, time

env_name = sys.argv[1] if len(sys.argv) > 1 else None
server_dir = '/Users/treeves/go/src/github.com/BidPal/maria-mcp'

log_fd, log_path = tempfile.mkstemp(prefix='maria-mcp-', suffix='.log')

try:
    proc_env = os.environ.copy()
    if env_name:
        proc_env['DB_ENV'] = env_name

    with os.fdopen(log_fd, 'w') as log_f:
        proc = subprocess.Popen(
            ['uv', 'run', '--directory', server_dir, 'src/server.py',
             '--transport', 'http', '--host', '127.0.0.1', '--port', '9001', '--path', '/mcp'],
            env=proc_env,
            stdout=log_f,
            stderr=log_f,
        )

    for _ in range(30):
        if proc.poll() is not None:
            with open(log_path) as f:
                print('MCP server died; log:', file=sys.stderr)
                print(f.read(), file=sys.stderr)
            sys.exit(1)
        with open(log_path) as f:
            if 'Application startup complete' in f.read():
                break
        time.sleep(0.5)
    else:
        print('MCP server did not start within 15 seconds', file=sys.stderr)
        proc.terminate()
        sys.exit(1)
finally:
    try:
        os.unlink(log_path)
    except OSError:
        pass
