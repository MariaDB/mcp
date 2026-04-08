---
name: db-refresh-schema
description: Rebuild ~/.claude/db-schema.json by querying information_schema for all user databases and tables. Use when the user invokes /db-refresh-schema or when the schema manifest is missing or stale.
---

# DB Refresh Schema

Rebuild `~/.claude/db-schema.json` by querying `information_schema` for all user databases and tables.

## Steps

### 0. Start the MCP server

```bash
python3 ~/.claude/skills/db/mcp_start.py
```

### 1. Query all tables

```bash
SESSION_ID=$(python3 ~/.claude/skills/db/mcp_session.py)

python3 ~/.claude/skills/db/mcp_query.py "$SESSION_ID" <<'EOF'
SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys') ORDER BY table_schema, table_name
EOF
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
python3 ~/.claude/skills/db/mcp_stop.py
```

### 4. Report

Tell the user:
- How many databases were found
- How many tables were indexed
- Any table name collisions (same table name in multiple databases — list them so the user can add aliases if needed)
