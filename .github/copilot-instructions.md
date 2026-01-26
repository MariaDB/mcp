## Quick orientation for AI coding agents

This repository implements a Model Context Protocol (MCP) server that exposes MariaDB-focused tools and optional vector/embedding features.

- Entry points & important files:
  - `src/server.py` — main MCP server implementation and tool definitions (list_databases, list_tables, execute_sql, vector-store tools, etc.). Read this first to understand available tools and their contracts.
  - `src/embeddings.py` — provider-agnostic EmbeddingService (OpenAI, Gemini, HuggingFace). Embedding clients are initialized at runtime based on env config.
  - `src/config.py` — loads `.env` and environment variables; contains defaults (notably `MCP_READ_ONLY` default=true) and validation that can raise on missing keys.
  - `src/tests/` — integration-style tests that demonstrate how the server is started and how the FastMCP client calls tools. Useful runnable examples.
  - `README.md` — installation, run commands and example tool payloads (useful to replicate CLI behavior).

## Big-picture architecture (short)

- FastMCP-based server: `MariaDBServer` builds a `FastMCP` instance and registers tools. Tools are asynchronous methods on `MariaDBServer`.
- Database access: Uses `asyncmy` connection pool. Pool is created by `MariaDBServer.initialize_pool()` and used by `_execute_query()` for all SQL operations.
- Embeddings: Optional feature toggled by `EMBEDDING_PROVIDER` in env. `EmbeddingService` supports OpenAI, Gemini, and HuggingFace. When disabled, all vector-store tools should be treated as unavailable.
- Vector-store implementation: persisted in MariaDB tables (VECTOR column + VECTOR INDEX). The server uses information_schema queries to validate existence and structure of vector stores.

Why certain choices matter for edits:
- `config.py` reads env at import time and will raise if required embedding keys are missing — set env before importing modules in tests or scripts.
- `MCP_READ_ONLY` influences `self.autocommit` and `_execute_query` enforcement: code blocks non-read-only queries when read-only mode is enabled.

## Developer workflows and concrete commands

- Python version: 3.11 (see `pyproject.toml`).
- Dependency & environment setup (as in README):
  - Install `uv` and sync dependencies:
    ```bash
    pip install uv
    uv lock
    uv sync
    ```
- Run server (examples shown in README):
  - stdio (default): `uv run server.py`
  - SSE transport: `uv run server.py --transport sse --host 127.0.0.1 --port 9001`
  - HTTP transport: `uv run server.py --transport http --host 127.0.0.1 --port 9001 --path /mcp`
- Tests: tests live in `src/tests/` and use `unittest.IsolatedAsyncioTestCase` with `anyio` and `fastmcp.client.Client`. They start the server in-process by calling `MariaDBServer.run_async_server('stdio')` and then call tools through `Client(self.server.mcp)`. Run them with your preferred runner, e.g.:
  ```bash
  # With unittest discovery
  python -m unittest discover -s src/tests
  ```

## Project-specific patterns & gotchas for agents

- Environment-on-import: `config.py` performs validation and logs/raises if required env vars are not set (e.g., DB_USER/DB_PASSWORD, provider-specific API keys). Always ensure env is configured before importing modules.
- Read-only enforcement: `_execute_query()` strips comments and checks an allow-list of SQL prefixes (`SELECT`, `SHOW`, `DESC`, `DESCRIBE`, `USE`). Any mutation must either run with `MCP_READ_ONLY=false` or be explicitly implemented as a server tool that bypasses that check (not recommended).
- Validation via information_schema: many tools check existence and vector-store status using `information_schema` queries — prefer reproducing those queries when writing migrations or tools that manipulate schema.
- Embedding service lifecycle: `EmbeddingService` may try to import provider SDKs on init and raise if missing; tests and CI should supply the right env or mock the service.

## Integration & external dependencies

- DB: MariaDB reachable via `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`. `DB_NAME` is optional; many tools accept `database_name` parameter.
- Embedding providers:
  - `openai` (requires `OPENAI_API_KEY`) — uses `openai` AsyncOpenAI client when available.
  - `gemini` (requires `GEMINI_API_KEY`) — uses `google.genai` when present.
  - `huggingface` (requires `HF_MODEL`) — uses `sentence-transformers` and may dynamically load models.
- Logs: default file at `logs/mcp_server.log` (configurable via env). Use this for debugging server startup or tool call failures.

## Examples extracted from the codebase

- How tests start the server (see `src/tests/test_mcp_server.py`):
  - Instantiate server: `server = MariaDBServer(autocommit=False)`
  - Start background server task: `tg.start_soon(server.run_async_server, 'stdio')`
  - Create client: `client = fastmcp.client.Client(server.mcp)` and call `await client.call_tool('list_databases', {})`.

- Tool payload example (from README):
  ```json
  {"tool":"execute_sql","parameters":{"database_name":"test_db","sql_query":"SELECT * FROM users WHERE id = %s","parameters":[123]}}
  ```

## Short checklist for code changes

1. Ensure required env vars are set before imports (or mock config/EmbeddingService in tests).
2. If adding SQL tools, follow `_execute_query()`'s comment-stripping + prefix checks; avoid enabling writes unless intended.
3. If changing embedding behavior, reference `src/embeddings.py` model lists and `pyproject.toml` dependencies — CI must install required SDKs.
4. Run unit/integration tests in `src/tests/` using unittest discovery or pytest if present.

If anything in this document is unclear or you'd like more concrete examples (unit test scaffolds, CI matrix entries, or mock patterns for `EmbeddingService`), tell me which section to expand and I'll iterate.
