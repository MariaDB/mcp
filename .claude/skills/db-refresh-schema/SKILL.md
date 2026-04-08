---
name: db-refresh-schema
description: Rebuild ~/.claude/db-schema.json by querying information_schema for all user databases and tables. Use when the user invokes /db-refresh-schema or when the schema manifest is missing or stale.
---

# DB Refresh Schema

Rebuild `~/.claude/db-schema.json` by querying `information_schema` for all user databases and tables.

## Steps

### 0. Start the MCP server

```bash
_MCP_LOG=$(mktemp /tmp/maria-mcp-XXXXXX.log)
uv run --directory /Users/treeves/go/src/github.com/BidPal/maria-mcp src/server.py --transport http --host 127.0.0.1 --port 9001 --path /mcp &>"$_MCP_LOG" &
_MCP_PID=$!
for _i in $(seq 1 30); do
  grep -q "Application startup complete" "$_MCP_LOG" 2>/dev/null && break
  kill -0 "$_MCP_PID" 2>/dev/null || { echo "MCP server died; log:"; cat "$_MCP_LOG"; exit 1; }
  sleep 0.5
done
rm -f "$_MCP_LOG"
```

### 1. Query all tables

```bash
SESSION_ID=$(curl -s -D - http://127.0.0.1:9001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"claude","version":"1.0"}}}' \
  2>/dev/null | grep -i "mcp-session-id:" | cut -d' ' -f2 | tr -d '\r')

python3 -c "
import subprocess, json, sys

session_id = '$SESSION_ID'
sql = '''SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys') ORDER BY table_schema, table_name'''

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
        for r in d['result']['structuredContent']['result']: print(r)
    else:
        print('ERROR:', d['result']['content'][0]['text'], file=sys.stderr)
except Exception as e:
    print('PARSE ERROR:', e, result[:300], file=sys.stderr)
"
```

### 2. Build and write the manifest

Read the existing `~/.claude/db-schema.json` to preserve any manually added `aliases` and `collision_defaults`. Then process the rows:

1. Strip the environment prefix from each `table_schema` — remove everything up to and including the first `-` (e.g. `dev-phaas-ticket-api` → `phaas-ticket-api`).
2. Group rows by `table_name` → list of unique base database names.
3. Build `tables`: every table gets an entry. For tables in multiple databases, use `collision_defaults` if an entry exists for that table name, otherwise use the first database name alphabetically.
4. Build `collisions`: tables that appear in 2+ databases → sorted list of all their base database names. Also strip the env prefix from any database values in existing `collision_defaults` when preserving them.

```json
{
  "refreshed_at": "<current ISO timestamp>",
  "tables": {
    "<table_name>": "<base_database_name>",
    ...
  },
  "collisions": {
    "<table_name>": ["<base_db_1>", "<base_db_2>", ...],
    ...
  },
  "aliases": {
    // preserve existing aliases, do not overwrite
  },
  "collision_defaults": {
    // preserve existing collision_defaults, strip env prefix from any database values
  }
}
```

Write to `~/.claude/db-schema.json` using the Write tool.

### 3. Stop the MCP server

```bash
kill -INT $(lsof -ti:9001) 2>/dev/null || true
```

### 4. Report

Tell the user:
- How many databases were found
- How many tables were indexed
- Any table name collisions (same table name in multiple databases — list them so the user can add aliases if needed)
