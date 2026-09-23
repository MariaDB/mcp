# server.py

# Import configuration settings
# Connection settings now live on DbProfile (see profiles.py); config keeps the
# process-wide knobs.
from config import (
    MCP_BLOCK_SENSITIVE_SHOW, EMBEDDING_PROVIDER,
    ALLOWED_ORIGINS, ALLOWED_HOSTS,
    logger
)

import asyncio
import argparse
import contextlib
import contextvars
import re
from typing import List, Dict, Any, Optional

from profiles import (
    DbProfile,
    ProfileConfigError,
    eager_profile_names,
    load_profiles,
    replace,
)

# Which connection profile the current tool call is bound to.
# Set by the tool wrappers in register_tools() so the ~20 internal helpers that funnel
# into _execute_query() need no signature change; contextvars propagate across awaits
# within a task, so concurrent tool calls cannot bleed into each other.
_current_profile: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "mariadb_mcp_current_profile", default=None
)

from functools import partial
import os
import ssl

import asyncmy
import anyio 
from fastmcp import FastMCP, Context

# Import custom connection pool that disables MULTI_STATEMENTS
from custom_connection import create_safe_pool

from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

# Import EmbeddingService for vector store creation
from embeddings import EmbeddingService

# Singleton instance for embedding service
embedding_service = None
if EMBEDDING_PROVIDER is not None:
    embedding_service = EmbeddingService()

from asyncmy.errors import Error as AsyncMyError

# --- MariaDB MCP Server Class ---
class MariaDBServer:
    """
    MCP Server exposing tools to interact with a MariaDB database.
    Manages the database connection pool.
    """
    def __init__(self, server_name="MariaDB_Server", autocommit=True):
        self.mcp = FastMCP(server_name)
        self.autocommit = autocommit

        self.profiles: Dict[str, DbProfile] = load_profiles()
        self.default_profile: Optional[str] = (
            next(iter(self.profiles)) if len(self.profiles) == 1 else None
        )
        # One pool per profile, created on first use.
        self.pools: Dict[str, asyncmy.Pool] = {}
        self._pool_locks: Dict[str, asyncio.Lock] = {}

        logger.info(f"Initializing {server_name}...")
        for prof in self.profiles.values():
            logger.info(
                f"Profile '{prof.name}' -> {prof.host}:{prof.port}"
                f"/{prof.database or '(no default db)'} read_only={prof.read_only}"
            )
        if all(prof.read_only for prof in self.profiles.values()):
            logger.warning("All profiles are READ-ONLY. Write operations are disabled.")

        self.block_sensitive_show = MCP_BLOCK_SENSITIVE_SHOW
        if self.block_sensitive_show:
            logger.info("Sensitive SHOW commands (PROCESSLIST, GRANTS, VARIABLES, replication status, etc.) are blocked.")
        else:
            logger.warning("MCP_BLOCK_SENSITIVE_SHOW is disabled: sensitive SHOW commands are permitted.")

    async def _warn_if_file_privilege_enabled(self, prof: Optional[DbProfile] = None,
                                              pool: Optional[asyncmy.Pool] = None) -> None:
        """Warn when a read-only profile connects as a user holding global FILE.

        Pool creation passes the profile and its fresh pool explicitly, because pools are
        per profile and created lazily so there is no single self.pool to inspect. Called
        with no arguments it falls back to the profile bound to the current call.
        """
        if prof is None:
            prof = self.resolve_profile()
        if pool is None:
            pool = self.pools.get(prof.name)
        if pool is None:
            return

        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT CURRENT_USER()")
                    current_user_row = await cursor.fetchone()
                    if not current_user_row:
                        return

                    if isinstance(current_user_row, dict):
                        current_user = next(iter(current_user_row.values()))
                    else:
                        current_user = current_user_row[0]

                    if not current_user:
                        return

                    await cursor.execute(f"SHOW GRANTS FOR {current_user}")
                    grant_rows = await cursor.fetchall()

                    grants: List[str] = []
                    for row in grant_rows or []:
                        if isinstance(row, dict):
                            grants.append(str(next(iter(row.values()))))
                        else:
                            grants.append(str(row[0]))

                    has_file_priv = any(
                        re.search(r"\bFILE\b", grant, flags=re.IGNORECASE) and "ON *.*" in grant.upper()
                        for grant in grants
                    )

                    if has_file_priv:
                        logger.error(
                            f"Profile '{prof.name}' connects as a user with the global FILE privilege. "
                            "This means the profile is NOT in a fully read-only posture, because MariaDB/MySQL allow "
                            "filesystem read/write via SQL (e.g. SELECT ... INTO OUTFILE, LOAD DATA INFILE, LOAD_FILE()). "
                            "This cannot be fixed client-side; revoke FILE for that database user."
                        )
        except Exception as e:
            logger.debug(f"Unable to determine whether FILE privilege is enabled for '{prof.name}': {e}")

    # --- Profile / pool plumbing ---

    @property
    def is_read_only(self) -> bool:
        """Read-only state of the profile bound to the current call.

        Kept as an attribute-style property so existing call sites and tests keep working.
        """
        return self.resolve_profile().read_only

    @is_read_only.setter
    def is_read_only(self, value: bool) -> None:
        """Flip read-only on every profile.

        Before profiles this was one global flag, and callers (including the upstream
        test suite) assign to it directly. Applying it to all profiles keeps that
        meaning: there is no single profile to single out, and silently touching only
        one would leave the others contradicting the caller.
        """
        value = bool(value)
        self.profiles = {
            name: replace(prof, read_only=value) for name, prof in self.profiles.items()
        }

    @property
    def pool(self) -> Optional[asyncmy.Pool]:
        """Pool of the current (or single) profile. Back-compat with the pre-profile API."""
        try:
            name = self.resolve_profile().name
        except (ProfileConfigError, ValueError):
            return None
        return self.pools.get(name)

    @pool.setter
    def pool(self, value: Optional[asyncmy.Pool]) -> None:
        """Assign the current profile's pool.

        `pool` was a plain attribute before profiles and callers still assign to it.
        It targets the profile this call is bound to; with several profiles configured
        and none selected, resolve_profile() raises rather than guessing which endpoint
        the pool belongs to.
        """
        name = self.resolve_profile().name
        if value is None:
            self.pools.pop(name, None)
        else:
            self.pools[name] = value

    def resolve_profile(self, profile: Optional[str] = None) -> DbProfile:
        """Pick the profile for this call: explicit arg > context var > sole profile."""
        name = profile or _current_profile.get() or self.default_profile
        if name is None:
            raise ValueError(
                "No profile selected. Pass profile=<name>; available: "
                f"{sorted(self.profiles)}"
            )
        try:
            return self.profiles[name]
        except KeyError:
            raise ValueError(
                f"Unknown profile '{name}'. Available: {sorted(self.profiles)}"
            ) from None

    def list_profiles(self) -> List[Dict[str, Any]]:
        """Configured endpoints, without credentials."""
        return [prof.describe() for prof in self.profiles.values()]

    async def create_vector_store(self, database_name: str, vector_store_name: str, model_name: Optional[str] = None, distance_function: Optional[str] = None) -> dict:
        """
        This tool creates a table which stores embeddings.
        
        Creates a new vector store (table) with a predefined schema if it doesn't already exist.
        It first checks if the database exists, creating it if necessary.
        Then, it checks if the table exists; if so, it reports that.
        Otherwise, it creates the table with id, document, embedding (VECTOR type), and metadata (JSON) columns.
        A VECTOR INDEX is created on the embedding column.

        Parameters:
        - database_name (str): The target database.
        - vector_store_name (str): The name of the table to create.
        - embedding_service: An instance of EmbeddingService to get model details.
        - model_name (str, optional): The embedding model to use (defaults to service default).
        - distance_function (str, optional): 'euclidean' or 'cosine'. Defaults to 'cosine'.
        """
        return await self.create_vector_store_tool(database_name, vector_store_name, embedding_service, model_name, distance_function)

    async def get_pool(self, profile: Optional[str] = None) -> asyncmy.Pool:
        """Return the pool for a profile, creating it on first use."""
        prof = self.resolve_profile(profile)
        existing = self.pools.get(prof.name)
        if existing is not None:
            return existing

        lock = self._pool_locks.setdefault(prof.name, asyncio.Lock())
        async with lock:
            # Another task may have created it while we waited for the lock.
            existing = self.pools.get(prof.name)
            if existing is not None:
                return existing
            pool = await self._create_pool(prof)
            self.pools[prof.name] = pool
            return pool

    async def initialize_pool(self, profile: Optional[str] = None):
        """Eagerly create a pool. Retained for the pre-profile API and tests."""
        await self.get_pool(profile)

    async def _create_pool(self, prof: DbProfile) -> asyncmy.Pool:
        """Creates the asyncmy connection pool for one profile."""
        if not prof.user:
            logger.error(f"Cannot initialize pool for '{prof.name}': user is empty or missing")
            raise ConnectionError(f"Missing user for profile '{prof.name}'.")
        if prof.password is None:
            logger.error(f"Cannot initialize pool for '{prof.name}': password is missing")
            raise ConnectionError(f"Missing password for profile '{prof.name}'.")

        try:
            ssl_context = None
            if prof.ssl:
                ssl_context = ssl.create_default_context()
                if prof.ssl_ca:
                    ca_path = os.path.expanduser(prof.ssl_ca)
                    if os.path.exists(ca_path):
                        ssl_context.load_verify_locations(cafile=ca_path)
                        logger.info(f"Loaded SSL CA certificate: {ca_path}")
                    else:
                        logger.warning(f"SSL CA certificate file not found: {ca_path}")

                if prof.ssl_cert and prof.ssl_key:
                    cert_path = os.path.expanduser(prof.ssl_cert)
                    key_path = os.path.expanduser(prof.ssl_key)
                    if os.path.exists(cert_path) and os.path.exists(key_path):
                        ssl_context.load_cert_chain(cert_path, key_path)
                        logger.info(f"Loaded SSL client certificate: {cert_path}")
                    else:
                        logger.warning(f"SSL client certificate files not found: cert={cert_path}, key={key_path}")

                if not prof.ssl_verify_cert:
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                    logger.info("SSL certificate verification disabled")
                elif not prof.ssl_verify_identity:
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_REQUIRED
                    logger.info("SSL hostname verification disabled, certificate verification enabled")
                else:
                    logger.info("Full SSL verification enabled")

                logger.info("SSL enabled for database connection")
            else:
                logger.info("SSL disabled for database connection")

            pool_params = {
                "host": prof.host,
                "port": prof.port,
                "user": prof.user,
                "password": prof.password,
                "db": prof.database,
                "minsize": 1,
                "maxsize": prof.max_pool_size,
                "autocommit": self.autocommit,
                "pool_recycle": 3600
            }
            if prof.ssl and ssl_context is not None:
                pool_params["ssl"] = ssl_context

            if prof.charset:
                pool_params["charset"] = prof.charset
                logger.info(f"Creating connection pool [{prof.name}] for {prof.user}@{prof.host}:{prof.port}/{prof.database} (max size: {prof.max_pool_size}, charset: {prof.charset})")
            else:
                logger.info(f"Creating connection pool [{prof.name}] for {prof.user}@{prof.host}:{prof.port}/{prof.database} (max size: {prof.max_pool_size})")

            pool = await create_safe_pool(**pool_params)
            logger.info(f"Connection pool [{prof.name}] initialized successfully.")
            if prof.read_only:
                await self._warn_if_file_privilege_enabled(prof, pool)
            return pool
        except AsyncMyError as e:
            logger.error(f"Failed to initialize connection pool [{prof.name}]: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"An unexpected error occurred initializing pool [{prof.name}]: {e}", exc_info=True)
            raise

    async def close_pool(self):
        """Closes every open connection pool gracefully."""
        for name, pool in list(self.pools.items()):
            logger.info(f"Closing database connection pool [{name}]...")
            try:
                pool.close()
                await pool.wait_closed()
                logger.info(f"Database connection pool [{name}] closed.")
            except Exception as e:
                logger.error(f"Error closing connection pool [{name}]: {e}", exc_info=True)
            finally:
                self.pools.pop(name, None)

    # Kept for readability at call sites that close everything.
    close_pools = close_pool

    async def _execute_query(self, sql: str, params: Optional[tuple] = None, database: Optional[str] = None,
                             profile: Optional[str] = None) -> List[Dict[str, Any]]:
        """Helper function to execute SELECT queries using the pool.

        The profile comes from the explicit argument, otherwise from the context var the
        tool wrapper set for this call.
        """
        prof = self.resolve_profile(profile)

        # Build a normalized string for validation that reflects what MariaDB
        # will actually execute. This must never *remove* content that the
        # server treats as live SQL, or validation and execution diverge
        # (parser differential). In particular, MariaDB "executable comments"
        # (/*! ... */ and /*!VVVVV ... */) are NOT inert comments: their
        # contents run as normal SQL. Only genuinely inert comments and
        # string literal contents are safe to strip for validation purposes.

        # Mask string literals first, so comment-like sequences inside data
        # cannot be mistaken for actual comment syntax below.
        sql_normalized = re.sub(r"'(?:[^'\\]|\\.)*'", "''", sql)
        sql_normalized = re.sub(r'"(?:[^"\\]|\\.)*"', '""', sql_normalized)

        # Remove single-line comments (-- comment and # comment), which never execute.
        sql_normalized = re.sub(r'--.*?$', '', sql_normalized, flags=re.MULTILINE)
        sql_normalized = re.sub(r'#.*?$', '', sql_normalized, flags=re.MULTILINE)

        # Remove inert block comments: /* ... */ that are NOT executable
        # comments. Executable comments start with /*! and must be left
        # for the next step so their contents remain visible to validation.
        sql_normalized = re.sub(r'/\*(?!!)[\s\S]*?\*/', '', sql_normalized)

        # Unwrap executable comments: strip only the /*! or /*!VVVVV marker
        # and the closing */, keeping the inner SQL intact so it is subject
        # to the same checks below as ordinary SQL.
        sql_normalized = re.sub(r'/\*!\d*', ' ', sql_normalized)
        sql_normalized = sql_normalized.replace('*/', ' ')
        sql_normalized = sql_normalized.strip()

        allowed_prefixes = ('SELECT', 'SHOW', 'DESC', 'DESCRIBE', 'USE')

        query_upper = sql_normalized.upper()
        is_allowed_read_query = any(query_upper.startswith(prefix) for prefix in allowed_prefixes)

        if prof.read_only and not is_allowed_read_query:
             logger.warning(f"Blocked potentially non-read-only query on read-only profile '{prof.name}': {sql[:100]}...")
             raise PermissionError(f"Operation forbidden: profile '{prof.name}' is in read-only mode.")
        if prof.read_only:
            # Check for LOAD_FILE() function (case-insensitive, outside strings/inert comments)
            if re.search(r'\bLOAD_FILE\s*\(', query_upper):
                logger.warning(f"Blocked query containing LOAD_FILE(): {sql[:100]}...")
                raise PermissionError("Operation forbidden: LOAD_FILE() is not allowed for security reasons.")

            # Check for SELECT ... INTO OUTFILE/DUMPFILE (case-insensitive, outside strings/inert comments)
            if re.search(r'\bINTO\s+(OUTFILE|DUMPFILE)\b', query_upper):
                logger.warning(f"Blocked query containing SELECT INTO OUTFILE or DUMPFILE: {sql[:100]}...")
                raise PermissionError("Operation forbidden: SELECT INTO OUTFILE and SELECT INTO DUMPFILE are not allowed for security reasons.")

        # SHOW admits an entire subcommand namespace, some of which leak
        # cross-connection or system-sensitive data (e.g. SHOW PROCESSLIST
        # exposes query text/parameters from *other* active sessions).
        # This is gated independently of read-only mode via
        # MCP_BLOCK_SENSITIVE_SHOW: some deployments want it blocked even
        # when writes are allowed, others explicitly want it available in
        # read-only mode.
        if self.block_sensitive_show and query_upper.startswith('SHOW') and re.search(
            r'\bSHOW\s+(PROCESSLIST|(GLOBAL\s+|SESSION\s+)?VARIABLES|'
            r'MASTER\s+STATUS|BINARY\s+LOGS|BINLOG\s+EVENTS|'
            r'(SLAVE|REPLICA)\s+STATUS|SLAVE\s+HOSTS|'
            r'GRANTS|CREATE\s+USER|PRIVILEGES|ENGINE\s+STATUS)\b',
            query_upper,
        ):
            logger.warning(f"Blocked sensitive SHOW query: {sql[:100]}...")
            raise PermissionError("Operation forbidden: this SHOW variant is blocked by MCP_BLOCK_SENSITIVE_SHOW.")

        logger.info(f"Executing query [{prof.name}] (DB: {database or prof.database}): {sql[:100]}...")
        if params:
            logger.debug(f"Parameters: {params}")

        # Acquire only after the read-only gate: a blocked write must not open a connection.
        pool = await self.get_pool(prof.name)

        conn = None
        try:
            async with pool.acquire() as conn:
                async with conn.cursor(cursor=asyncmy.cursors.DictCursor) as cursor:
                    current_db_query = "SELECT DATABASE()"
                    await cursor.execute(current_db_query)
                    current_db_result = await cursor.fetchone()
                    current_db_name = current_db_result.get('DATABASE()') if current_db_result else None
                    pool_db_name = prof.database
                    actual_current_db = current_db_name or pool_db_name

                    if database and database != actual_current_db:
                        logger.info(f"Switching database context from '{actual_current_db}' to '{database}'")
                        await cursor.execute(f"USE `{database}`")

                    if params is None:
                        await cursor.execute(sql)
                    else:
                        await cursor.execute(sql, params)
                    results = await cursor.fetchall()
                    logger.info(f"Query executed successfully, {len(results)} rows returned.")
                    return results if results else []
        except AsyncMyError as e:
            conn_state = f"Connection: {'acquired' if conn else 'not acquired'}"
            logger.error(f"Database error executing query ({conn_state}): {e}", exc_info=True)
            # Check for specific connection-related errors if possible
            raise RuntimeError(f"Database error: {e}") from e
        except PermissionError as e:
             logger.warning(f"Permission denied: {e}")
             raise e
        except Exception as e:
            # Catch potential loop closed errors here too, although ideally fixed by structure change
            if isinstance(e, RuntimeError) and 'Event loop is closed' in str(e):
                 logger.critical("Detected closed event loop during query execution!", exc_info=True)
                 # This indicates a fundamental problem with loop management still exists
                 raise RuntimeError("Event loop closed unexpectedly during query.") from e
            conn_state = f"Connection: {'acquired' if conn else 'not acquired'}"
            logger.error(f"Unexpected error during query execution ({conn_state}): {e}", exc_info=True)
            raise RuntimeError(f"An unexpected error occurred: {e}") from e
            
    async def _database_exists(self, database_name: str) -> bool:
        """Checks if a database exists."""
        if not database_name or not database_name.isidentifier():
            logger.warning(f"_database_exists called with invalid database_name: {database_name}")
            return False 

        sql = "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s"
        try:
            results = await self._execute_query(sql, params=(database_name,), database='information_schema')
            return len(results) > 0
        except Exception as e:
            logger.error(f"Error checking if database '{database_name}' exists: {e}", exc_info=True)
            return False
        
    async def _table_exists(self, database_name: str, table_name: str) -> bool:
        """Checks if a table exists in the given database."""
        if not database_name or not database_name.isidentifier() or \
           not table_name or not table_name.isidentifier():
            logger.warning(f"_table_exists called with invalid names: db='{database_name}', table='{table_name}'")
            return False

        sql = "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s"
        try:
            results = await self._execute_query(sql, params=(database_name, table_name), database='information_schema')
            return len(results) > 0
        except Exception as e:
            logger.error(f"Error checking if table '{database_name}.{table_name}' exists: {e}", exc_info=True)
            return False

    async def _is_vector_store(self, database_name: str, table_name: str) -> bool:
        """
        Checks if the specified table in the given database is a vector store.
        A table is considered a vector store if it has an indexed column named 'embedding'
        with a data type of 'VECTOR'.

        Parameters:
        - database_name (str): The name of the database.
        - table_name (str): The name of the table to check.

        Returns:
        - bool: True if the table is a vector store, False otherwise.
        """
        logger.debug(f"Checking if '{database_name}.{table_name}' is a vector store.")

        if not database_name or not database_name.isidentifier() or \
           not table_name or not table_name.isidentifier():
            logger.warning(f"_is_vector_store called with invalid names: db='{database_name}', table='{table_name}'")
            return False

        # SQL query to verify vector store criteria
        sql_query = """
        SELECT COUNT(T1.TABLE_NAME) AS vector_store_count
        FROM information_schema.COLUMNS AS T1
        INNER JOIN information_schema.STATISTICS AS T2
            ON T1.TABLE_SCHEMA = T2.TABLE_SCHEMA
            AND T1.TABLE_NAME = T2.TABLE_NAME
            AND T1.COLUMN_NAME = T2.COLUMN_NAME
        WHERE T1.TABLE_SCHEMA = %s
          AND T1.TABLE_NAME = %s
          AND T1.COLUMN_NAME = 'embedding'
          AND UPPER(T1.DATA_TYPE) = 'VECTOR';
        """
        try:
            results = await self._execute_query(sql_query, params=(database_name, table_name), database='information_schema')
            if results and results[0].get('vector_store_count', 0) > 0:
                logger.debug(f"Confirmation: '{database_name}.{table_name}' is a vector store.")
                return True
            else:
                logger.debug(f"Confirmation: '{database_name}.{table_name}' is NOT a vector store.")
                return False
        except Exception as e:
            logger.error(f"Error checking if '{database_name}.{table_name}' is a vector store: {e}", exc_info=True)
            return False # Treat errors as "not a vector store" for safety in deletion context

    
    # --- MCP Tool Definitions ---

    async def list_databases(self) -> List[str]:
        """Lists all accessible databases on the connected MariaDB server."""
        logger.info("TOOL START: list_databases called.")
        sql = "SHOW DATABASES"
        try:
            results = await self._execute_query(sql)
            db_list = [row['Database'] for row in results if 'Database' in row]
            logger.info(f"TOOL END: list_databases completed. Databases found: {len(db_list)}.")
            return db_list
        except Exception as e:
            logger.error(f"TOOL ERROR: list_databases failed: {e}", exc_info=True)
            raise

    async def list_tables(self, database_name: str) -> List[str]:
        """Lists all tables within the specified database."""
        logger.info(f"TOOL START: list_tables called. database_name={database_name}")
        if not database_name or not database_name.isidentifier():
            logger.warning(f"TOOL WARNING: list_tables called with invalid database_name: {database_name}")
            raise ValueError(f"Invalid database name provided: {database_name}")
        sql = "SHOW TABLES"
        try:
            results = await self._execute_query(sql, database=database_name)
            table_list = [list(row.values())[0] for row in results if row]
            logger.info(f"TOOL END: list_tables completed. Tables found: {len(table_list)}.")
            return table_list
        except Exception as e:
            logger.error(f"TOOL ERROR: list_tables failed for database_name={database_name}: {e}", exc_info=True)
            raise

    async def get_table_schema(self, database_name: str, table_name: str) -> Dict[str, Any]:
        """
        Retrieves the schema (column names, types, nullability, keys, default values)
        for a specific table in a database.
        """
        logger.info(f"TOOL START: get_table_schema called. database_name={database_name}, table_name={table_name}")
        if not database_name or not database_name.isidentifier():
            logger.warning(f"TOOL WARNING: get_table_schema called with invalid database_name: {database_name}")
            raise ValueError(f"Invalid database name provided: {database_name}")
        if not table_name or not table_name.isidentifier():
            logger.warning(f"TOOL WARNING: get_table_schema called with invalid table_name: {table_name}")
            raise ValueError(f"Invalid table name provided: {table_name}")

        sql = f"DESCRIBE `{database_name}`.`{table_name}`"
        try:
            schema_results = await self._execute_query(sql)
            schema_info = {}
            if not schema_results:
                exists_sql = "SELECT COUNT(*) as count FROM information_schema.tables WHERE table_schema = %s AND table_name = %s"
                exists_result = await self._execute_query(exists_sql, params=(database_name, table_name))
                if not exists_result or exists_result[0]['count'] == 0:
                    logger.warning(f"TOOL WARNING: Table '{database_name}'.'{table_name}' not found or inaccessible.")
                    raise FileNotFoundError(f"Table '{database_name}'.'{table_name}' not found or inaccessible.")
                else:
                    logger.warning(f"Could not describe table '{database_name}'.'{table_name}'. It might be a view or lack permissions.")

            for row in schema_results:
                col_name = row.get('Field')
                if col_name:
                    schema_info[col_name] = {
                        'type': row.get('Type'),
                        'nullable': row.get('Null', '').upper() == 'YES',
                        'key': row.get('Key'),
                        'default': row.get('Default'),
                        'extra': row.get('Extra')
                    }
            logger.info(f"TOOL END: get_table_schema completed. Columns found: {len(schema_info)}. Keys: {list(schema_info.keys())}")
            return schema_info
        except FileNotFoundError as e:
            logger.warning(f"TOOL WARNING: get_table_schema table not found: {e}")
            raise e
        except Exception as e:
            logger.error(f"TOOL ERROR: get_table_schema failed for database_name={database_name}, table_name={table_name}: {e}", exc_info=True)
            raise RuntimeError(f"Could not retrieve schema for table '{database_name}.{table_name}'.")
        
    async def get_table_schema_with_relations(self, database_name: str, table_name: str) -> Dict[str, Any]:
        """
        Retrieves table schema with foreign key relationship information.
        Includes all basic schema info plus foreign key relationships and referenced tables.
        """
        logger.info(f"TOOL START: get_table_schema_with_relations called. database_name={database_name}, table_name={table_name}")
        if not database_name or not database_name.isidentifier():
            logger.warning(f"TOOL WARNING: get_table_schema_with_relations called with invalid database_name: {database_name}")
            raise ValueError(f"Invalid database name provided: {database_name}")
        if not table_name or not table_name.isidentifier():
            logger.warning(f"TOOL WARNING: get_table_schema_with_relations called with invalid table_name: {table_name}")
            raise ValueError(f"Invalid table name provided: {table_name}")

        try:
            # 1. Get basic schema information
            basic_schema = await self.get_table_schema(database_name, table_name)
            
            # 2. Retrieve foreign key information
            fk_sql = """
            SELECT 
                kcu.COLUMN_NAME as column_name,
                kcu.CONSTRAINT_NAME as constraint_name,
                kcu.REFERENCED_TABLE_NAME as referenced_table,
                kcu.REFERENCED_COLUMN_NAME as referenced_column,
                rc.UPDATE_RULE as on_update,
                rc.DELETE_RULE as on_delete
            FROM information_schema.KEY_COLUMN_USAGE kcu
            INNER JOIN information_schema.REFERENTIAL_CONSTRAINTS rc
                ON kcu.CONSTRAINT_NAME = rc.CONSTRAINT_NAME
                AND kcu.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA
            WHERE kcu.TABLE_SCHEMA = %s 
              AND kcu.TABLE_NAME = %s 
              AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
            ORDER BY kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION
            """
            
            fk_results = await self._execute_query(fk_sql, params=(database_name, table_name))
            
            # 3. Add foreign key information to the basic schema
            enhanced_schema = {}
            for col_name, col_info in basic_schema.items():
                enhanced_schema[col_name] = col_info.copy()
                enhanced_schema[col_name]['foreign_key'] = None
            
            # 4. Add foreign key information to the corresponding columns
            for fk_row in fk_results:
                column_name = fk_row['column_name']
                if column_name in enhanced_schema:
                    enhanced_schema[column_name]['foreign_key'] = {
                        'constraint_name': fk_row['constraint_name'],
                        'referenced_table': fk_row['referenced_table'],
                        'referenced_column': fk_row['referenced_column'],
                        'on_update': fk_row['on_update'],
                        'on_delete': fk_row['on_delete']
                    }
            
            # 5. Return the enhanced schema with foreign key relations
            result = {
                'table_name': table_name,
                'columns': enhanced_schema
            }
            
            logger.info(f"TOOL END: get_table_schema_with_relations completed. Columns: {len(enhanced_schema)}, Foreign keys: {len(fk_results)}")
            return result
            
        except Exception as e:
            logger.error(f"TOOL ERROR: get_table_schema_with_relations failed for database_name={database_name}, table_name={table_name}: {e}", exc_info=True)
            raise RuntimeError(f"Could not retrieve schema with relations for table '{database_name}.{table_name}': {str(e)}")


    async def execute_sql(self, sql_query: str, database_name: str, parameters: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        """
        Executes a read-only SQL query (primarily SELECT, EXPLAIN, SHOW, DESCRIBE) against a specified database
        and returns the results. Uses parameterized queries for safety.
        Example `parameters`: ["value1", 123] corresponding to %s placeholders in `sql_query`.
        """
        logger.info(f"TOOL START: execute_sql called. database_name={database_name}, sql_query={sql_query[:100]}, parameters={parameters}")
        if database_name and not database_name.isidentifier():
            logger.warning(f"TOOL WARNING: execute_sql called with invalid database_name: {database_name}")
            raise ValueError(f"Invalid database name provided: {database_name}")
        param_tuple = tuple(parameters) if parameters is not None else None
        try:
            results = await self._execute_query(sql_query, params=param_tuple, database=database_name)
            logger.info(f"TOOL END: execute_sql completed. Rows returned: {len(results)}.")
            return results
        except Exception as e:
            logger.error(f"TOOL ERROR: execute_sql failed for database_name={database_name}, sql_query={sql_query[:100]}, parameters={parameters}: {e}", exc_info=True)
            raise
            
    async def create_database(self, database_name: str) -> Dict[str, Any]:
        """
        Creates a new database if it doesn't exist.
        """
        logger.info(f"TOOL START: create_database called for database: '{database_name}'")
        if not database_name or not database_name.isidentifier():
            logger.error(f"Invalid database_name for creation: '{database_name}'. Must be a valid identifier.")
            raise ValueError(f"Invalid database_name for creation: '{database_name}'. Must be a valid identifier.")

        # Check existence first to provide a clear message, though CREATE DATABASE IF NOT EXISTS is idempotent
        if await self._database_exists(database_name):
            message = f"Database '{database_name}' already exists."
            logger.info(f"TOOL END: create_database. {message}")
            return {"status": "exists", "message": message, "database_name": database_name}

        sql = f"CREATE DATABASE IF NOT EXISTS `{database_name}`;"

        try:
            await self._execute_query(sql, database=None)

            message = f"Database '{database_name}' created successfully."
            logger.info(f"TOOL END: create_database. {message}")
            return {"status": "success", "message": message, "database_name": database_name}
        except Exception as e:
            error_message = f"Failed to create database '{database_name}'."
            logger.error(f"TOOL ERROR: create_database. {error_message} Error: {e}", exc_info=True)
            raise RuntimeError(f"{error_message} Reason: {str(e)}")

    async def create_vector_store_tool(self,
                                  database_name: str,
                                  vector_store_name: str,
                                  embedding_service: EmbeddingService,
                                  model_name: Optional[str] = None,
                                  distance_function: Optional[str] = None) -> Dict[str, Any]:
        """
        This tool creates a new table which stores embeddings.

        Creates a new vector store (table) with a predefined schema if it doesn't already exist.
        It first checks if the database exists, creating it if necessary.
        Then, it checks if the table exists; if so, it reports that.
        Otherwise, it creates the table with id, document, embedding (VECTOR type), and metadata (JSON) columns.
        A VECTOR INDEX is created on the embedding column.

        Parameters:
        - database_name (str): The target database.
        - vector_store_name (str): The name of the table to create.
        - embedding_service: An instance of EmbeddingService to get model details.
        - model_name (str, optional): The embedding model to use (defaults to service default).
        - distance_function (str, optional): 'euclidean' or 'cosine'. Defaults to 'cosine'.
        """
        embedding_length = await embedding_service.get_embedding_dimension(model_name)
        logger.info(f"TOOL START: create_vector_store called. DB: '{database_name}', Store: '{vector_store_name}', Model: '{model_name}', Embedding_Length: {embedding_length}, Distance_Requested: '{distance_function}'")

        # --- Input Validation ---
        if not database_name or not database_name.isidentifier():
            logger.error(f"Invalid database_name: '{database_name}'. Must be a valid identifier.")
            raise ValueError(f"Invalid database_name: '{database_name}'. Must be a valid identifier.")
        if not vector_store_name or not vector_store_name.isidentifier():
            logger.error(f"Invalid vector_store_name: '{vector_store_name}'. Must be a valid identifier.")
            raise ValueError(f"Invalid vector_store_name: '{vector_store_name}'. Must be a valid identifier.")

        if not isinstance(embedding_length, int) or embedding_length <= 0:
            logger.error(f"Invalid embedding_length: {embedding_length}. Must be a positive integer.")
            raise ValueError(f"Invalid embedding_length: {embedding_length}. Must be a positive integer.")

        # Validate and set distance_function
        valid_distance_functions_map = {"euclidean": "EUCLIDEAN", "cosine": "COSINE"}
        processed_distance_function_sql = valid_distance_functions_map["cosine"] # Default

        if distance_function:
            df_lower = distance_function.lower()
            if df_lower in valid_distance_functions_map:
                processed_distance_function_sql = valid_distance_functions_map[df_lower]
            else:
                logger.error(f"Invalid distance_function: '{distance_function}'. Must be one of {list(valid_distance_functions_map.keys())}.")
                raise ValueError(f"Invalid distance_function: '{distance_function}'. Must be one of {list(valid_distance_functions_map.keys())}.")
        else:
            logger.info(f"Distance function not provided, defaulting to '{processed_distance_function_sql}'.")
        
        logger.info(f"Using SQL distance function: '{processed_distance_function_sql}'.")

        # --- Database Existence Check ---
        if not await self._database_exists(database_name):
            logger.info(f"Database '{database_name}' does not exist. Attempting to create it.")
            try:
                await self.create_database(database_name) 
            except Exception as db_create_e:
                logger.error(f"Failed to ensure database '{database_name}' existence: {db_create_e}", exc_info=True)
                raise RuntimeError(f"Failed to ensure database '{database_name}' exists before creating vector store. Reason: {str(db_create_e)}")

        # --- Table Existence Check ---
        if await self._table_exists(database_name, vector_store_name):
            message = f"Vector store (table) '{vector_store_name}' already exists in database '{database_name}'. No action taken."
            logger.info(f"TOOL END: create_vector_store. {message}")
            return {
                "status": "exists",
                "message": message,
                "database_name": database_name,
                "vector_store_name": vector_store_name
            }

        # --- SQL Query for Vector Store Table Creation ---
        schema_query = f"""
        CREATE TABLE IF NOT EXISTS `{vector_store_name}` (
            id VARCHAR(36) NOT NULL DEFAULT UUID_v7() PRIMARY KEY,
            document TEXT NOT NULL,
            embedding VECTOR({embedding_length}) NOT NULL,
            metadata JSON NOT NULL,
            VECTOR INDEX (embedding) DISTANCE={processed_distance_function_sql}
        );
        """

        try:
            # --- Execute Query ---
            await self._execute_query(schema_query, database=database_name)
            
            success_message = f"Vector store '{vector_store_name}' created successfully in database '{database_name}' with {processed_distance_function_sql} distance."
            logger.info(f"TOOL END: create_vector_store completed. {success_message}")
            return {
                "status": "success",
                "message": success_message,
                "database_name": database_name,
                "vector_store_name": vector_store_name
            }
        except Exception as e:
            error_message = f"Failed to create vector store '{vector_store_name}' in database '{database_name}'."
            logger.error(f"TOOL ERROR: create_vector_store failed. {error_message} Error: {e}", exc_info=True)
            raise RuntimeError(f"{error_message} Reason: {str(e)}")

    async def list_vector_stores(self, database_name: str) -> List[str]:
        """
        Lists all tables within the specified database that are identified as vector stores.
        A table is considered a vector store if it contains an indexed column named 'embedding'
        with a data type of 'VECTOR'.

        Parameters:
        - database_name (str): The name of the database to scan.

        Returns:
        - List[str]: A list of table names that are identified as vector stores.
                     Returns an empty list if no such tables are found or if the database doesn't exist.
        
        Raises:
        - ValueError: If the database_name is invalid.
        - RuntimeError: For database errors during the operation.
        """
        logger.info(f"TOOL START: list_vector_stores called for database: '{database_name}'")

        # --- Input Validation ---
        if not database_name or not database_name.isidentifier():
            logger.error(f"Invalid database_name: '{database_name}'. Must be a valid identifier.")
            raise ValueError(f"Invalid database_name: '{database_name}'. Must be a valid identifier.")

        if not await self._database_exists(database_name):
            logger.warning(f"Database '{database_name}' does not exist. Cannot list vector stores.")
            return []

        # --- SQL Query ---
        # This query identifies tables that have:
        # 1. A column named 'embedding'.
        # 2. The data type of this 'embedding' column is 'VECTOR'.
        # 3. This 'embedding' column is part of an index (ensured by the JOIN with STATISTICS).
        sql_query = """
        SELECT DISTINCT T1.TABLE_NAME
        FROM information_schema.COLUMNS AS T1
        INNER JOIN information_schema.STATISTICS AS T2
            ON T1.TABLE_SCHEMA = T2.TABLE_SCHEMA
            AND T1.TABLE_NAME = T2.TABLE_NAME
            AND T1.COLUMN_NAME = T2.COLUMN_NAME
        WHERE T1.TABLE_SCHEMA = %s
          AND UPPER(T1.COLUMN_NAME) = 'EMBEDDING'
          AND UPPER(T1.DATA_TYPE) = 'VECTOR' 
        ORDER BY T1.TABLE_NAME;
        """

        try:
            results = await self._execute_query(sql_query, params=(database_name,), database='information_schema')
            
            store_list = [row['TABLE_NAME'] for row in results if 'TABLE_NAME' in row]
            
            if not store_list:
                logger.info(f"No vector stores found in database '{database_name}'.")
            else:
                logger.info(f"Found {len(store_list)} vector store(s) in database '{database_name}': {store_list}")
            
            logger.info(f"TOOL END: list_vector_stores completed for database '{database_name}'.")
            return store_list

        except Exception as e:
            error_message = f"Failed to list vector stores in database '{database_name}'."
            logger.error(f"TOOL ERROR: list_vector_stores. {error_message} Error: {e}", exc_info=True)
            raise RuntimeError(f"{error_message} Reason: {str(e)}")
            
    async def delete_vector_store(self,
                                  database_name: str,
                                  vector_store_name: str) -> Dict[str, Any]:
        """
        Deletes a vector store (table) from the specified database.
        It first verifies if the database and table exist, and if the table
        conforms to the definition of a vector store (contains an indexed 'embedding'
        column of type VECTOR).

        Parameters:
        - database_name (str): The name of the database.
        - vector_store_name (str): The name of the vector store table to delete.

        Returns:
        - Dict[str, Any]: A dictionary containing the status and a message.
                          Possible statuses: "success", "not_found", "not_vector_store", "error".
        """
        logger.info(f"TOOL START: delete_vector_store called for: '{database_name}.{vector_store_name}'")

        # --- Input Validation for names ---
        if not database_name or not database_name.isidentifier():
            logger.error(f"Invalid database_name: '{database_name}'. Must be a valid identifier.")
            raise ValueError(f"Invalid database_name: '{database_name}'. Must be a valid identifier.")
        if not vector_store_name or not vector_store_name.isidentifier():
            logger.error(f"Invalid vector_store_name: '{vector_store_name}'. Must be a valid identifier.")
            raise ValueError(f"Invalid vector_store_name: '{vector_store_name}'. Must be a valid identifier.")

        # --- Database Existence Check ---
        if not await self._database_exists(database_name):
            message = f"Database '{database_name}' does not exist. Cannot delete vector store."
            logger.warning(message)
            return {"status": "not_found", "message": message, "type": "database"}

        # --- Table Existence Check ---
        if not await self._table_exists(database_name, vector_store_name):
            message = f"Vector store (table) '{vector_store_name}' does not exist in database '{database_name}'."
            logger.warning(message)
            return {"status": "not_found", "message": message, "type": "table"}

        # --- Vector Store Verification ---
        if not await self._is_vector_store(database_name, vector_store_name):
            message = f"Table '{vector_store_name}' in database '{database_name}' is not a valid vector store (missing indexed 'embedding' column of type VECTOR). Deletion aborted."
            logger.warning(message)
            return {"status": "not_vector_store", "message": message}
            
        # --- SQL Query for Deletion ---
        drop_query = f"DROP TABLE IF EXISTS `{vector_store_name}`;"

        try:
            await self._execute_query(drop_query, database=database_name)
            
            success_message = f"Vector store '{vector_store_name}' deleted successfully from database '{database_name}'."
            logger.info(f"TOOL END: delete_vector_store. {success_message}")
            return {
                "status": "success",
                "message": success_message,
                "database_name": database_name,
                "vector_store_name": vector_store_name
            }
        except Exception as e:
            error_message = f"Failed to delete vector store '{vector_store_name}' from database '{database_name}'."
            logger.error(f"TOOL ERROR: delete_vector_store. {error_message} Error: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"{error_message} Reason: {str(e)}",
                "database_name": database_name,
                "vector_store_name": vector_store_name
            }
            
    async def insert_docs_vector_store(self, database_name: str, vector_store_name: str, documents: List[str], metadata: Optional[List[dict]] = None) -> dict:
        """
        Insert a batch of documents (with optional metadata) into a vector store.
        Documents must be a non-empty list of strings. Metadata, if provided, must be a list of dicts of the same length as documents.
        If metadata is not provided, an empty dict will be used for each document.
        """
        import json
        if not database_name or not database_name.isidentifier():
            logger.error(f"Invalid database_name: '{database_name}'")
            raise ValueError(f"Invalid database_name: '{database_name}'")
        if not vector_store_name or not vector_store_name.isidentifier():
            logger.error(f"Invalid vector_store_name: '{vector_store_name}'")
            raise ValueError(f"Invalid vector_store_name: '{vector_store_name}'")
        if not isinstance(documents, list) or not documents or not all(isinstance(doc, str) and doc for doc in documents):
            logger.error("'documents' must be a non-empty list of non-empty strings.")
            raise ValueError("'documents' must be a non-empty list of non-empty strings.")
        # Handle metadata: optional
        if metadata is None:
            metadata = [{} for _ in documents]
        if not isinstance(metadata, list) or len(metadata) != len(documents):
            logger.error("'metadata' must be a list of dicts, same length as documents (or omitted).")
            raise ValueError("'metadata' must be a list of dicts, same length as documents (or omitted).")
        # Generate embeddings
        embeddings = await embedding_service.embed(documents)
        # Prepare metadata JSON
        metadata_json = [json.dumps(m) for m in metadata]
        # Prepare values for batch insert
        insert_query = f"INSERT INTO `{database_name}`.`{vector_store_name}` (document, embedding, metadata) VALUES (%s, VEC_FromText(%s), %s)"
        inserted = 0
        errors = []
        for doc, emb, meta in zip(documents, embeddings, metadata_json):
            emb_str = json.dumps(emb)
            try:
                await self._execute_query(insert_query, params=(doc, emb_str, meta), database=database_name)
                inserted += 1
            except Exception as e:
                logger.error(f"Failed to insert doc into {database_name}.{vector_store_name}: {e}", exc_info=True)
                errors.append(str(e))
        logger.info(f"Inserted {inserted} documents into {database_name}.{vector_store_name} (errors: {len(errors)})")
        result = {"status": "success" if inserted == len(documents) else "partial", "inserted": inserted}
        if errors:
            result["errors"] = errors
        return result
        
    async def search_vector_store(self, user_query: str, database_name: str, vector_store_name: str, k: int = 7) -> list:
        """
        Search a vector store for the most similar documents to a query using semantic search.
        Parameters:
            user_query (str): The search query string.
            database_name (str): The database name.
            vector_store_name (str): The vector store (table) name.
            k (int, optional): Number of top results to retrieve (default 7).
        Returns:
            List of dicts with document, metadata, and distance.
        """
        import json
        # Input validation
        if not user_query or not isinstance(user_query, str):
            logger.error("user_query must be a non-empty string.")
            raise ValueError("user_query must be a non-empty string.")
        if not database_name or not database_name.isidentifier():
            logger.error(f"Invalid database_name: '{database_name}'")
            raise ValueError(f"Invalid database_name: '{database_name}'")
        if not vector_store_name or not vector_store_name.isidentifier():
            logger.error(f"Invalid vector_store_name: '{vector_store_name}'")
            raise ValueError(f"Invalid vector_store_name: '{vector_store_name}'")
        if not isinstance(k, int) or k <= 0:
            logger.error("k must be a positive integer.")
            raise ValueError("k must be a positive integer.")
        # Generate embedding for the query
        embedding = await embedding_service.embed(user_query)
        emb_str = json.dumps(embedding)
        # Prepare the search query
        search_query = f"""
            SELECT 
                document,
                metadata,
                VEC_DISTANCE_COSINE(embedding, VEC_FromText(%s)) AS distance
            FROM `{database_name}`.`{vector_store_name}`
            ORDER BY distance ASC
            LIMIT %s
        """
        try:
            results = await self._execute_query(search_query, params=(emb_str, k), database=database_name)
            for row in results:
                if isinstance(row.get('metadata'), str):
                    try:
                        row['metadata'] = json.loads(row['metadata'])
                    except Exception:
                        pass
            logger.info(f"Semantic search in {database_name}.{vector_store_name} returned {len(results)} results.")
            return results
        except Exception as e:
            logger.error(f"Failed to search vector store {database_name}.{vector_store_name}: {e}", exc_info=True)
            return []
            
    @contextlib.asynccontextmanager
    async def _profile_scope(self, profile: Optional[str]):
        """Bind one tool call to a connection profile.

        Validates eagerly so an unknown/missing name fails with the list of valid names
        instead of surfacing later as a confusing pool error.
        """
        prof = self.resolve_profile(profile)
        token = _current_profile.set(prof.name)
        try:
            yield prof
        finally:
            _current_profile.reset(token)

    # --- Tool Registration (Synchronous) ---
    def register_tools(self):
        """Registers the class methods as MCP tools using the instance. This is synchronous.

        Pools are created on first use, so tools can be registered before any connection
        exists. That is what lets one process carry many endpoints cheaply.
        """
        @self.mcp.tool
        async def list_profiles() -> List[Dict[str, Any]]:
            """Lists the configured connection profiles (host, port, database, read_only).

            Credentials are never returned. Use the 'profile' argument of the other tools
            to pick one.
            """
            return self.list_profiles()

        @self.mcp.tool
        async def list_databases(profile: Optional[str] = None) -> List[str]:
            """Lists all accessible databases on the connected MariaDB server.

            profile: which configured connection to use. Call list_profiles() to see the
            names; omit only when a single profile is configured.
            """
            async with self._profile_scope(profile):
                return await self.list_databases()

        @self.mcp.tool
        async def list_tables(database_name: str, profile: Optional[str] = None) -> List[str]:
            """Lists all tables within the specified database.

            profile: which configured connection to use. Call list_profiles() to see the
            names; omit only when a single profile is configured.
            """
            async with self._profile_scope(profile):
                return await self.list_tables(database_name)

        @self.mcp.tool
        async def get_table_schema(database_name: str, table_name: str, profile: Optional[str] = None) -> Dict[str, Any]:
            """Retrieves the schema for a specific table in a database.

            profile: which configured connection to use. Call list_profiles() to see the
            names; omit only when a single profile is configured.
            """
            async with self._profile_scope(profile):
                return await self.get_table_schema(database_name, table_name)

        @self.mcp.tool
        async def get_table_schema_with_relations(database_name: str, table_name: str, profile: Optional[str] = None) -> Dict[str, Any]:
            """Retrieves table schema with foreign key relationship information.

            profile: which configured connection to use. Call list_profiles() to see the
            names; omit only when a single profile is configured.
            """
            async with self._profile_scope(profile):
                return await self.get_table_schema_with_relations(database_name, table_name)

        @self.mcp.tool
        async def execute_sql(sql_query: str, database_name: str, parameters: Optional[List[Any]] = None,
                              profile: Optional[str] = None) -> List[Dict[str, Any]]:
            """Executes a read-only SQL query against a specified database.

            profile: which configured connection to use. Call list_profiles() to see the
            names; omit only when a single profile is configured.
            """
            async with self._profile_scope(profile):
                return await self.execute_sql(sql_query, database_name, parameters)

        @self.mcp.tool
        async def create_database(database_name: str, profile: Optional[str] = None) -> Dict[str, Any]:
            """Creates a new database if it doesn't exist.

            profile: which configured connection to use. Call list_profiles() to see the
            names; omit only when a single profile is configured.
            """
            async with self._profile_scope(profile):
                return await self.create_database(database_name)

        if EMBEDDING_PROVIDER is not None:
            @self.mcp.tool
            async def create_vector_store(database_name: str, vector_store_name: str, model_name: Optional[str] = None,
                                          distance_function: Optional[str] = None, profile: Optional[str] = None) -> dict:
                """Creates a table which stores embeddings."""
                async with self._profile_scope(profile):
                    return await self.create_vector_store(database_name, vector_store_name, model_name, distance_function)

            @self.mcp.tool
            async def list_vector_stores(database_name: str, profile: Optional[str] = None) -> List[str]:
                """Lists all vector stores in a database."""
                async with self._profile_scope(profile):
                    return await self.list_vector_stores(database_name)

            @self.mcp.tool
            async def delete_vector_store(database_name: str, vector_store_name: str, profile: Optional[str] = None) -> Dict[str, Any]:
                """Deletes a vector store from the specified database."""
                async with self._profile_scope(profile):
                    return await self.delete_vector_store(database_name, vector_store_name)

            @self.mcp.tool
            async def insert_docs_vector_store(database_name: str, vector_store_name: str, documents: List[str],
                                               metadata: Optional[List[dict]] = None, profile: Optional[str] = None) -> dict:
                """Insert a batch of documents into a vector store."""
                async with self._profile_scope(profile):
                    return await self.insert_docs_vector_store(database_name, vector_store_name, documents, metadata)

            @self.mcp.tool
            async def search_vector_store(user_query: str, database_name: str, vector_store_name: str, k: int = 7,
                                          profile: Optional[str] = None) -> list:
                """Search a vector store for similar documents."""
                async with self._profile_scope(profile):
                    return await self.search_vector_store(user_query, database_name, vector_store_name, k)

        logger.info(f"Registered MCP tools explicitly for {len(self.profiles)} profile(s).")

    # --- Async Main Server Logic ---
    async def run_async_server(self, transport="stdio", host="127.0.0.1", port=9001, path="/mcp"):
        """
        Initializes pool, registers tools, and runs the appropriate async MCP listener.
        This method should be the target for anyio.run().
        """
        try:
            # 1. Pools are lazy: carrying N endpoints must not mean opening N connections
            #    at startup. Connect eagerly only for the profiles asked for, so that a
            #    single-endpoint setup can still fail fast on bad credentials.
            for name in eager_profile_names(self.profiles):
                logger.info(f"Eagerly connecting profile '{name}'...")
                await self.get_pool(name)

            # 2. Register tools (synchronous part, but called from async context)
            self.register_tools()

            # 3. Prepare transport arguments
            transport_kwargs = {}
            if transport != "stdio":
                middleware = [
                    Middleware(
                        CORSMiddleware,
                        allow_origins=ALLOWED_ORIGINS,
                        allow_methods=["GET", "POST"],
                        allow_headers=["*"],
                    ),
                    Middleware(TrustedHostMiddleware, 
                               allowed_hosts=ALLOWED_HOSTS)
                ]
            if transport == "sse":
                transport_kwargs = {"host": host, "port": port, "middleware": middleware}
                logger.info(f"Starting MCP server via {transport} on {host}:{port}...")
            elif transport == "http":
                transport_kwargs = {"host": host, "port": port, "path": path, "middleware": middleware}
                logger.info(f"Starting MCP server via {transport} on {host}:{port}{path}...")
            elif transport == "stdio":
                 logger.info(f"Starting MCP server via {transport}...")
            else:
                 logger.error(f"Unsupported transport type: {transport}")
                 return 

            # 4. Run the appropriate async listener from FastMCP
            await self.mcp.run_async(transport=transport, **transport_kwargs)

        except (ConnectionError, AsyncMyError, RuntimeError) as e:
            logger.critical(f"Server setup failed: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.critical(f"Server execution failed with an unexpected error: {e}", exc_info=True)
            raise
        finally:
            await self.close_pool()


# --- Main Execution Block ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MariaDB MCP Server")
    parser.add_argument('--transport', type=str, default='stdio', choices=['stdio', 'sse', 'http'],
                        help='MCP transport protocol (stdio, sse, or http)')
    parser.add_argument('--host', type=str, default='127.0.0.1',
                        help='Host for SSE or HTTP transport')
    parser.add_argument('--port', type=int, default=9001,
                        help='Port for SSE or HTTP transport')
    parser.add_argument('--path', type=str, default='/mcp',
                        help='Path for HTTP transport (default: /mcp)')
    args = parser.parse_args()

    # 1. Create the server instance
    server = MariaDBServer()
    exit_code = 0

    try:
        # 2. Use anyio.run to manage the event loop and call the main async server logic
        anyio.run(
            partial(server.run_async_server, 
                    transport=args.transport, 
                    host=args.host, 
                    port=args.port, 
                    path=args.path)
        )
        logger.info("Server finished gracefully.")

    except KeyboardInterrupt:
         logger.info("Server execution interrupted by user.")
    except Exception as e:
         logger.critical(f"Server failed to start or crashed: {e}", exc_info=True)
         exit_code = 1
    finally:
        logger.info(f"Server exiting with code {exit_code}.")
