# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MariaDB MCP Server - A Model Context Protocol (MCP) server providing an interface for AI assistants to interact with MariaDB databases. Supports standard SQL operations and optional vector/embedding-based semantic search.

**Requirements:** Python 3.13+, MariaDB 11.7+ (for vector store features with `UUID_v7()`)

## Development Commands

```bash
# Setup (uses uv package manager)
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
docker compose logs -f mariadb-mcp

# Check server logs
tail -f logs/mcp_server.log
```

## Architecture

### Core Components

- **`src/server.py`**: `MariaDBServer` class using FastMCP. Contains all MCP tool definitions, connection pool management, and query execution. Entry point via `anyio.run()` with `functools.partial`.

- **`src/config.py`**: Loads environment/.env configuration at import time. Sets up logging (console + rotating file at `logs/mcp_server.log`). Validates credentials and embedding provider, raising `ValueError` if required keys are missing.

- **`src/embeddings.py`**: `EmbeddingService` class supporting OpenAI, Gemini, and HuggingFace providers. HuggingFace models are pre-loaded at init; Gemini uses `asyncio.to_thread()` since SDK lacks async.

### Key Design Patterns

1. **Connection Pooling**: Uses `asyncmy` pool. Supports multiple databases via comma-separated env vars:
   - `DB_HOSTS`, `DB_PORTS`, `DB_USERS`, `DB_PASSWORDS`, `DB_NAMES`, `DB_CHARSETS`
   - First connection becomes default pool; others stored in `self.pools` dict keyed by `host:port`

2. **Read-Only Mode**: `MCP_READ_ONLY=true` (default) allows only SELECT/SHOW/DESCRIBE/USE. SQL comments (`--` and `/* */`) are stripped via regex in `_execute_query()` before checking query prefix to prevent bypass attempts.

3. **Conditional Tool Registration**: Vector store tools only registered when `EMBEDDING_PROVIDER` is set. Check in `register_tools()` method (`if EMBEDDING_PROVIDER is not None`).

4. **Singleton EmbeddingService**: Created at module load only when `EMBEDDING_PROVIDER` is configured. Used by all vector store tools. Embedding dimensions vary by model: OpenAI text-embedding-3-small=1536, large=3072; Gemini=768; HuggingFace varies by model (e.g., BGE-M3=1024).

5. **Middleware Stack**: HTTP/SSE transports use Starlette middleware for CORS (`ALLOWED_ORIGINS`) and trusted host filtering (`ALLOWED_HOSTS`).

### MCP Tools

**Standard:** `list_databases`, `list_tables`, `get_table_schema`, `get_table_schema_with_relations`, `execute_sql`, `create_database`

**Vector Store (requires EMBEDDING_PROVIDER):** `create_vector_store`, `delete_vector_store`, `list_vector_stores`, `insert_docs_vector_store`, `search_vector_store`

### Vector Store Table Schema

```sql
-- Requires MariaDB 11.7+ for UUID_v7() and VECTOR type
CREATE TABLE vector_store_name (
    id VARCHAR(36) NOT NULL DEFAULT UUID_v7() PRIMARY KEY,
    document TEXT NOT NULL,
    embedding VECTOR(dimension) NOT NULL,
    metadata JSON NOT NULL,
    VECTOR INDEX (embedding) DISTANCE=COSINE
);
```

## Configuration

**Required:** `DB_USER`, `DB_PASSWORD`

**Database:** `DB_HOST` (localhost), `DB_PORT` (3306), `DB_NAME`, `DB_CHARSET`

**Timeouts:** `DB_CONNECT_TIMEOUT` (10s), `DB_READ_TIMEOUT` (30s), `DB_WRITE_TIMEOUT` (30s)

**MCP:** `MCP_READ_ONLY` (true), `MCP_MAX_POOL_SIZE` (10), `MCP_MAX_RESULTS` (10000)

**Security:** `ALLOWED_ORIGINS`, `ALLOWED_HOSTS` (for HTTP/SSE transports)

**Embeddings:** `EMBEDDING_PROVIDER` (openai|gemini|huggingface), `EMBEDDING_MAX_CONCURRENT` (5), plus provider-specific key (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `HF_MODEL`)

**Logging:** `LOG_LEVEL` (INFO), `LOG_FILE` (logs/mcp_server.log), `LOG_MAX_BYTES` (10MB), `LOG_BACKUP_COUNT` (5)

## Docker Health Checks

Both containers have health checks configured in `docker-compose.yml`:
- **mariadb**: Uses `mariadb-admin ping` (note: MariaDB 11+ renamed `mysqladmin` to `mariadb-admin`)
- **mariadb-mcp**: Uses TCP socket connection check on port 9001

## Health Check & Metrics

HTTP/SSE transports expose `/health` endpoint returning:
- `status`: "healthy" or "unhealthy"
- `uptime_seconds`: Server uptime
- `pool_status`: "connected" or "disconnected"
- `metrics`: Query counts, error rates, average query time, embedding counts

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

## Testing Notes

- Tests in `src/tests/` use unittest framework with pytest runner
- Integration tests require live MariaDB with configured `.env`
- Tests start server with stdio transport using FastMCP test client
- Vector store tests require `EMBEDDING_PROVIDER` to be configured
- Run single test: `uv run -m pytest src/tests/test_mcp_server.py::TestClass::test_method -v`
