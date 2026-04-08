# DB Refresh Schema

Rebuild `~/.claude/db-schema.json` by querying `information_schema` for all user databases and tables.

## Steps

### 0. Start the MCP server

```bash
uv run --directory /Users/treeves/go/src/github.com/BidPal/maria-mcp src/server.py --transport http --host 127.0.0.1 --port 9001 --path /mcp &>/dev/null &
sleep 2
```

### 1. Query all tables

```bash
SESSION_ID=$(curl -s -D - http://127.0.0.1:9001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"claude","version":"1.0"}}}' \
  2>/dev/null | grep -i "mcp-session-id:" | cut -d' ' -f2 | tr -d '\r')

curl -s http://127.0.0.1:9001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: $SESSION_ID" \
  -d '{
    "jsonrpc":"2.0","id":2,"method":"tools/call","params":{
      "name":"execute_sql",
      "arguments":{
        "database_name":"information_schema",
        "sql_query":"SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema NOT IN ('"'"'information_schema'"'"','"'"'mysql'"'"','"'"'performance_schema'"'"','"'"'sys'"'"') ORDER BY table_schema, table_name"
      }
    }
  }' 2>/dev/null | python3 -c "
import sys, json
data = sys.stdin.read()
d = json.loads(data.split('data: ', 1)[1])
rows = d['result']['structuredContent']['result']
for r in rows: print(r)
"
```

### 2. Build and write the manifest

Read the existing `~/.claude/db-schema.json` to preserve any manually added `aliases` and `collision_defaults`. Then write the updated file.

**Strip the environment prefix** from each `table_schema` value before writing — remove everything up to and including the first `-` (e.g. `dev-phaas-ticket-api` → `phaas-ticket-api`). Database names are stored as bare base names so the schema works across all environments.

```json
{
  "refreshed_at": "<current ISO timestamp>",
  "tables": {
    "<table_name>": "<base_database_name>",
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
