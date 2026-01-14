---
name: mariadb-debug
description: Debug MariaDB MCP server issues, analyze connection pool problems, troubleshoot embedding service failures, diagnose vector store operations. Use when working with database connectivity, embedding errors, or MCP tool failures.
---

# MariaDB MCP Server Debugging

## Key Files to Check

1. **src/server.py** - Main MCP server and tool definitions
   - Connection pool initialization (`initialize_pool`)
   - Tool registration (`register_tools`)
   - Query execution (`_execute_query`)

2. **src/config.py** - Configuration loading
   - Environment variables validation
   - Logging setup
   - Embedding provider configuration

3. **src/embeddings.py** - Embedding service
   - Provider initialization (OpenAI, Gemini, HuggingFace)
   - Model dimension lookup
   - Embedding generation

4. **logs/mcp_server.log** - Server logs

## Common Issues & Solutions

### Connection Pool Exhaustion
- **Symptom**: "Database connection pool not available"
- **Check**: `MCP_MAX_POOL_SIZE` in .env (default: 10)
- **Solution**: Increase pool size or check for connection leaks

### Embedding Service Failures
- **Symptom**: "Embedding provider not configured" or API errors
- **Check**: `EMBEDDING_PROVIDER` must be: openai, gemini, or huggingface
- **Verify**: Corresponding API key is set (OPENAI_API_KEY, GEMINI_API_KEY, or HF_MODEL)

### Read-Only Mode Violations
- **Symptom**: "Operation forbidden: Server is in read-only mode"
- **Check**: `MCP_READ_ONLY` environment variable
- **Note**: Only SELECT, SHOW, DESCRIBE queries allowed when true

### Vector Store Creation Fails
- **Symptom**: "Failed to create vector store"
- **Check**:
  - Database exists and user has CREATE TABLE permission
  - Embedding dimension matches model (e.g., text-embedding-3-small = 1536)
  - MariaDB version supports VECTOR type

### Tool Not Registered
- **Symptom**: Tool not found errors
- **Check**: EMBEDDING_PROVIDER must be set for vector tools
- **Verify**: Pool initialized before tool registration

## Debugging Commands

```bash
# Check server logs
tail -f logs/mcp_server.log

# Test database connection
uv run python -c "from config import *; print(f'DB: {DB_HOST}:{DB_PORT}')"

# Verify environment
uv run python -c "from config import *; print(f'Provider: {EMBEDDING_PROVIDER}')"

# Run tests
uv run -m pytest src/tests/ -v
```

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| DB_HOST | Yes | localhost | MariaDB host |
| DB_PORT | No | 3306 | MariaDB port |
| DB_USER | Yes | - | Database username |
| DB_PASSWORD | Yes | - | Database password |
| MCP_READ_ONLY | No | true | Enforce read-only |
| EMBEDDING_PROVIDER | No | None | openai/gemini/huggingface |
