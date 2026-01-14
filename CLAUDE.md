# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MariaDB MCP Server - A Model Context Protocol (MCP) server providing an interface for AI assistants to interact with MariaDB databases. Supports standard SQL operations and optional vector/embedding-based semantic search.

## Development Commands

```bash
# Setup (requires Python 3.13+)
pip install uv && uv sync

# Run server (stdio - default for MCP clients)
uv run src/server.py

# Run server with SSE/HTTP transport
uv run src/server.py --transport sse --host 127.0.0.1 --port 9001
uv run src/server.py --transport http --host 127.0.0.1 --port 9001 --path /mcp

# Run tests (requires live MariaDB with configured .env)
uv run -m pytest src/tests/ -v
uv run -m pytest src/tests/test_mcp_server.py::TestMariaDBMCPTools::test_list_databases

# Docker
docker compose up --build
```

## Architecture

### Core Components

- **`src/server.py`**: `MariaDBServer` class using FastMCP. Contains all MCP tool definitions, connection pool management, and query execution. Entry point via `anyio.run()`.

- **`src/config.py`**: Loads environment/.env configuration. Sets up logging (console + rotating file at `logs/mcp_server.log`). Validates credentials and embedding provider at import time.

- **`src/embeddings.py`**: `EmbeddingService` class supporting OpenAI, Gemini, and HuggingFace providers. Model dimension lookup is async-capable.

### Key Design Patterns

1. **Connection Pooling**: Uses `asyncmy` pool. Supports multiple databases via comma-separated env vars:
   - `DB_HOSTS`, `DB_PORTS`, `DB_USERS`, `DB_PASSWORDS`, `DB_NAMES`, `DB_CHARSETS`
   - First connection becomes default pool; others stored in `self.pools` dict keyed by `host:port`

2. **Read-Only Mode**: `MCP_READ_ONLY=true` (default) allows only SELECT/SHOW/DESCRIBE/USE. SQL comments are stripped via regex in `_execute_query()` before checking query prefix.

3. **Conditional Tool Registration**: Vector store tools only registered when `EMBEDDING_PROVIDER` is set. Check at `register_tools()` in server.py:879 (`if EMBEDDING_PROVIDER is not None`).

4. **Async Architecture**: `anyio.run()` at entry point. All DB operations and tool handlers are async. Gemini embeddings use `asyncio.to_thread()` since SDK lacks async.

5. **Singleton EmbeddingService**: Created once at module load (`embedding_service = EmbeddingService()`) when provider is configured.

### MCP Tools

**Standard:** `list_databases`, `list_tables`, `get_table_schema`, `get_table_schema_with_relations`, `execute_sql`, `create_database`

**Vector Store (requires EMBEDDING_PROVIDER):** `create_vector_store`, `delete_vector_store`, `list_vector_stores`, `insert_docs_vector_store`, `search_vector_store`

## Configuration

**Required:** `DB_USER`, `DB_PASSWORD`

**Database:** `DB_HOST` (localhost), `DB_PORT` (3306), `DB_NAME`, `DB_CHARSET`

**MCP:** `MCP_READ_ONLY` (true), `MCP_MAX_POOL_SIZE` (10)

**Security:** `ALLOWED_ORIGINS`, `ALLOWED_HOSTS` (for HTTP/SSE transports)

**Embeddings:** `EMBEDDING_PROVIDER` (openai|gemini|huggingface), plus provider-specific key (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `HF_MODEL`)

## Code Quality Rules

- **CRITICAL:** Always use parameterized queries (`%s` placeholders) - never concatenate SQL strings
- **CRITICAL:** Validate database/table names with `isidentifier()` before use in SQL
- All database operations must be `async` with `await`
- Log tool calls: `logger.info(f"TOOL START: ...")` / `logger.info(f"TOOL END: ...")`
- Catch `AsyncMyError` for database errors, `PermissionError` for read-only violations
- Vector store tests require `EMBEDDING_PROVIDER` configured
- Use backtick quoting for identifiers in SQL: `` `database_name`.`table_name` ``

## Custom Commands

- `/project:fix-issue <number>` - Fix GitHub issue with full workflow
- `/project:review-pr <number>` - Review a pull request

## Skills

- `mariadb-debug` - Debug database connectivity, embedding errors, MCP tool failures
