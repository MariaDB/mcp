---
name: db
description: Execute a natural-language query against the local MariaDB MCP server. Use when the user invokes /db with an optional environment prefix (dev/staging/production) followed by a natural-language query.
argument-hint: "[dev|staging|production] <natural language query>"
---

# DB Query

Execute a natural-language query against the local MariaDB MCP server, automatically resolving which database(s) each table lives in.

## Environment

The default environment is **`dev`**. To use a different environment, begin the args with the environment name:

```
/db staging show all guests
/db production count active campaigns
/db how many users are there   ← uses dev
```

Valid values: `dev`, `staging`, `production`.

**At the start of every invocation:**
1. Check if the first word of the args is a valid environment name. If so, extract it and use the rest as the query. Otherwise, use `dev` and treat the full args as the query.
2. Remember the resolved `ENV` for all steps below.

When resolving database names from `db-schema.json`, the stored names are bare base names (e.g. `phaas-ticket-api`). Prepend `{ENV}-` before using any database name. Example: `phaas-ticket-api` → `staging-phaas-ticket-api` when `ENV=staging`, or `dev-phaas-ticket-api` when `ENV=dev`.

## MCP Server

Runs at `http://127.0.0.1:9001/mcp`. Start it at the beginning of every `/db` invocation and stop it when done. Stopping and restarting is also how you refresh an expired token.

**Start** (pass `DB_ENV` so the server connects to the right environment — env vars take precedence over `.env`):
```bash
_MCP_LOG=$(mktemp /tmp/maria-mcp-XXXXXX.log)
DB_ENV={ENV} uv run --directory /Users/treeves/go/src/github.com/BidPal/maria-mcp src/server.py --transport http --host 127.0.0.1 --port 9001 --path /mcp &>"$_MCP_LOG" &
_MCP_PID=$!
for _i in $(seq 1 30); do
  grep -q "Application startup complete" "$_MCP_LOG" 2>/dev/null && break
  kill -0 "$_MCP_PID" 2>/dev/null || { echo "MCP server died; log:"; cat "$_MCP_LOG"; exit 1; }
  sleep 0.5
done
rm -f "$_MCP_LOG"
```

**Stop (after all queries are complete):**
```bash
kill -INT $(lsof -ti:9001) 2>/dev/null || true
```

If any query returns a 1045 Access Denied error mid-session, stop the server and start it again before retrying.

## Bash pattern

Use this for every query. `cut` is used instead of `awk` to extract the session ID (avoid `$N`-style variable substitution issues). Build the JSON payload in Python to safely handle arbitrary SQL strings.

```bash
SESSION_ID=$(curl -s -D - http://127.0.0.1:9001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"claude","version":"1.0"}}}' \
  2>/dev/null | grep -i "mcp-session-id:" | cut -d' ' -f2 | tr -d '\r')

python3 -c "
import subprocess, json, sys

session_id = '$SESSION_ID'
sql = '''TARGET_SQL'''

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

Reuse the same `$SESSION_ID` for multiple queries in one invocation — no need to re-handshake between queries.

## Step 1 — Resolve tables to databases

Read `~/.claude/db-schema.json`. Structure:

```json
{
  "refreshed_at": "2026-01-01T00:00:00Z",
  "tables": { "guest": "phaas-virtualevent-api", "ticket": "phaas-ticket-api" },
  "collisions": { "ticket": ["phaas-event-api", "phaas-giving-api", "phaas-raffle-api", "phaas-ticket-api"] },
  "aliases": { "event": "virtual_event" },
  "collision_defaults": { "ticket": "phaas-ticket-api", "raffle ticket": { "table": "ticket", "database": "phaas-raffle-api" } }
}
```

The schema stores bare base names (no environment prefix). After resolving a database name, prepend `{ENV}-` to get the fully-qualified name used in SQL.

Resolution order:
1. **Apply `aliases` first — always, unless the user explicitly names a table.** If the user's term appears in `aliases`, replace it with the aliased name before doing anything else. **A term may exist in both `aliases` and `tables`; aliases always win — never look up the original name in `tables` or `collisions` if it has an alias.** (e.g. "event" → "virtual_event"; even though `event` exists in `tables`, use `virtual_event` instead.) **Exception: if the user explicitly specifies a fully-qualified reference such as `` `phaas-event-api`.`event` `` or otherwise directly names a specific table, respect that exactly — do not apply aliases.**
2. Check `collision_defaults` for the user's phrase (e.g. "raffle ticket" → `dev-phaas-raffle-api`.`ticket`). Entries can be either a database string (same table name) or an object with `table` and `database` keys.
3. Look up in `tables` to get the database
4. If the table is also in `collisions`, check context to pick the right one (e.g. if the query joins `guest` which has a `ticket_id` FK, use `dev-phaas-ticket-api`). If ambiguous, ask the user which database they mean.
5. Prepend `{ENV}-` to every resolved database name.

**If a table is missing from the manifest** — batch-discover all needed missing tables in one query:

```sql
SELECT table_schema, table_name, column_name
FROM information_schema.columns
WHERE table_name IN ('missing_table_1', 'missing_table_2')
  AND table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
ORDER BY table_schema, table_name, ordinal_position
```

Use `database = 'information_schema'` for this query. Add any found tables to `~/.claude/db-schema.json` (stripping the env prefix from `table_schema` before writing, e.g. `dev-phaas-foo` → `phaas-foo`) and bump `refreshed_at`.

**If the manifest doesn't exist** — run `/db-refresh-schema` first to bootstrap it, then continue.

**If `refreshed_at` is older than 7 days** — warn the user and suggest `/db-refresh-schema`, but proceed.

## Step 2 — Write the SQL

Use fully-qualified `` `database`.`table` `` notation. MariaDB resolves cross-database references natively — always pass `information_schema` as `database_name` (the connection context) so hyphenated database names don't fail server-side validation.

Example — "guests without non-deleted tickets" (with `ENV=staging`):
```sql
SELECT g.id, g.first_name, g.last_name, g.email, g.created
FROM `staging-phaas-virtualevent-api`.`guest` g
LEFT JOIN `staging-phaas-ticket-api`.`ticket` t
  ON t.ticket_id = g.ticket_id AND t.deleted = 0
WHERE g.organization_id = 'ORG_ID'
  AND g.deleted = 0
  AND t.ticket_id IS NULL
ORDER BY g.created DESC
LIMIT 5
```

## Step 3 — Confirm with the user

**Every SQL execution requires user confirmation — no exceptions.** This includes internal/discovery queries (e.g. `information_schema` lookups), follow-up queries, and any retry after an error. Never run SQL silently.

Before executing any SQL, show the SQL and the target database(s), then ask the user to confirm. Do not run the query until they approve. If they request changes, revise and show the updated SQL before asking again.

If multiple queries are needed in sequence (e.g. a discovery query followed by the main query), present each one individually and wait for approval before running it. Do not batch them into a single confirmation.

## Step 4 — Display results

Print results clearly. If the query spanned multiple databases, note which ones were involved.


ARGUMENTS: $ARGUMENTS
