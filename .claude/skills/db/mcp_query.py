#!/usr/bin/env python3
"""Execute a SQL query against the local MariaDB MCP server.

Usage: python3 mcp_query.py <session_id> < sql_query.sql
       echo "SELECT 1" | python3 mcp_query.py <session_id>
       python3 mcp_query.py <session_id> <<'EOF'
       SELECT ...
       EOF

Reads SQL from stdin, prints result rows to stdout. Exits non-zero on error.
"""
import subprocess, json, sys

session_id = sys.argv[1]
sql = sys.stdin.read().strip()

payload = json.dumps({
    'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
    'params': {'name': 'execute_sql', 'arguments': {'database_name': 'information_schema', 'sql_query': sql}}
})

result = subprocess.check_output([
    'curl', '-s', 'http://127.0.0.1:9001/mcp',
    '-H', 'Content-Type: application/json',
    '-H', 'Accept: application/json, text/event-stream',
    '-H', f'mcp-session-id: {session_id}',
    '-d', payload
]).decode()

try:
    d = json.loads(result.split('data: ', 1)[1])
    if 'structuredContent' in d.get('result', {}):
        for r in d['result']['structuredContent']['result']:
            print(r)
    else:
        print('ERROR:', d['result']['content'][0]['text'], file=sys.stderr)
        sys.exit(1)
except Exception as e:
    print('PARSE ERROR:', e, result[:300], file=sys.stderr)
    sys.exit(1)
