#!/usr/bin/env python3
"""Get an MCP session ID from the local MariaDB MCP server.

Usage: SESSION_ID=$(python3 mcp_session.py)

Prints the session ID to stdout. Exits non-zero on failure.
"""
import http.client, json, sys

conn = http.client.HTTPConnection('127.0.0.1', 9001)
payload = json.dumps({
    'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
    'params': {
        'protocolVersion': '2024-11-05',
        'capabilities': {},
        'clientInfo': {'name': 'claude', 'version': '1.0'},
    }
})
conn.request('POST', '/mcp', body=payload, headers={
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream',
})
resp = conn.getresponse()
session_id = resp.getheader('mcp-session-id')
if not session_id:
    print('ERROR: no mcp-session-id in response', file=sys.stderr)
    sys.exit(1)
print(session_id)
