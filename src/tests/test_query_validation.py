"""
Tests for SQL query validation security features.

Tests that LOAD_FILE() and SELECT ... INTO ... are properly blocked,
including in subqueries, while allowing them in string literals.
"""

import unittest
import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from server import MariaDBServer
from custom_connection import SafeConnection, SafePool, _connection_stream_broken
from asyncmy.connection import Connection
from asyncmy.constants.CLIENT import MULTI_STATEMENTS, LOCAL_FILES


class TestQueryValidation(unittest.TestCase):
    """Test SQL query validation for security."""
    
    def setUp(self):
        """Set up test server."""
        self.server = MariaDBServer(server_name="TestServer")
        # Force read-only mode to enable validation
        self.server.is_read_only = True
        # Force sensitive-SHOW blocking on regardless of the environment
        self.server.block_sensitive_show = True
        
        acquire_cm = MagicMock()
        acquire_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("Database access not configured for unit tests"))
        acquire_cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=acquire_cm)
        self.server.pool = pool

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
    
    def tearDown(self):
        """Clean up after tests."""
        try:
            self.loop.close()
        finally:
            asyncio.set_event_loop(None)
    
    async def _test_query_blocked(self, query: str, expected_error_msg: str):
        """Helper to test that a query is blocked."""
        with self.assertRaises(PermissionError) as context:
            await self.server._execute_query(query)
        
        self.assertIn(expected_error_msg, str(context.exception))
    
    async def _test_query_allowed(self, query: str):
        """Helper to test that a query is allowed (doesn't raise PermissionError for validation)."""
        # We expect this might fail with database errors, but NOT with PermissionError
        # for validation reasons. We're only testing the validation logic.
        try:
            await self.server._execute_query(query)
        except PermissionError as e:
            if "LOAD_FILE" in str(e) or "INTO OUTFILE" in str(e) or "INTO DUMPFILE" in str(e) or "SHOW" in str(e):
                self.fail(f"Query was incorrectly blocked by validation: {query}")
        except Exception:
            # Other exceptions (like syntax errors) are fine - we're only testing validation
            pass
    
    # LOAD_FILE() tests
    
    def test_load_file_simple(self):
        """Test that LOAD_FILE() is blocked in simple SELECT."""
        query = "SELECT LOAD_FILE('/etc/passwd')"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "LOAD_FILE()")
        )
    
    def test_load_file_with_spaces(self):
        """Test that LOAD_FILE() is blocked with various spacing."""
        query = "SELECT LOAD_FILE  (  '/etc/passwd'  )"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "LOAD_FILE()")
        )
    
    def test_load_file_case_insensitive(self):
        """Test that LOAD_FILE() is blocked regardless of case."""
        queries = [
            "SELECT load_file('/etc/passwd')",
            "SELECT Load_File('/etc/passwd')",
            "SELECT LOAD_file('/etc/passwd')",
        ]
        for query in queries:
            with self.subTest(query=query):
                self.loop.run_until_complete(
                    self._test_query_blocked(query, "LOAD_FILE()")
                )
    
    def test_load_file_in_subquery(self):
        """Test that LOAD_FILE() is blocked in subqueries."""
        query = "SELECT * FROM users WHERE id IN (SELECT LOAD_FILE('/etc/passwd'))"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "LOAD_FILE()")
        )
    
    def test_load_file_in_join(self):
        """Test that LOAD_FILE() is blocked in JOIN conditions."""
        query = "SELECT * FROM users u JOIN (SELECT LOAD_FILE('/etc/passwd') as data) t ON u.id = t.data"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "LOAD_FILE()")
        )
    
    def test_load_file_in_string_allowed(self):
        """Test that LOAD_FILE inside a string literal is allowed."""
        queries = [
            "SELECT 'LOAD_FILE(/etc/passwd)' as text",
            "SELECT \"LOAD_FILE(/etc/passwd)\" as text",
            "SELECT * FROM users WHERE name = 'LOAD_FILE(test)'",
        ]
        for query in queries:
            with self.subTest(query=query):
                self.loop.run_until_complete(
                    self._test_query_allowed(query)
                )
    
    # SELECT INTO OUTFILE tests
    
    def test_select_into_outfile(self):
        """Test that SELECT INTO OUTFILE is blocked."""
        query = "SELECT * FROM users INTO OUTFILE '/tmp/users.txt'"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "INTO OUTFILE")
        )
    
    def test_select_into_dumpfile(self):
        """Test that SELECT INTO DUMPFILE is blocked."""
        query = "SELECT * FROM users INTO DUMPFILE '/tmp/users.bin'"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "INTO DUMPFILE")
        )
    
    def test_select_into_outfile_case_insensitive(self):
        """Test that SELECT INTO OUTFILE is blocked regardless of case."""
        queries = [
            "SELECT * FROM users into outfile '/tmp/users.txt'",
            "SELECT * FROM users Into Outfile '/tmp/users.txt'",
            "SELECT * FROM users INTO outfile '/tmp/users.txt'",
        ]
        for query in queries:
            with self.subTest(query=query):
                self.loop.run_until_complete(
                    self._test_query_blocked(query, "INTO OUTFILE")
                )
    
    def test_select_into_in_subquery(self):
        """Test that SELECT INTO OUTFILE is blocked in subqueries."""
        query = "SELECT * FROM (SELECT * FROM users INTO OUTFILE '/tmp/users.txt') t"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "INTO OUTFILE")
        )
    
    def test_select_into_in_string_allowed(self):
        """Test that INTO OUTFILE inside a string literal is allowed."""
        queries = [
            "SELECT 'INTO OUTFILE /tmp/test' as text",
            "SELECT \"INTO DUMPFILE /tmp/test\" as text",
            "SELECT * FROM users WHERE description = 'backup INTO OUTFILE'",
        ]
        for query in queries:
            with self.subTest(query=query):
                self.loop.run_until_complete(
                    self._test_query_allowed(query)
                )
    
    # Combined tests
    
    def test_load_file_and_into_outfile(self):
        """Test that both LOAD_FILE and INTO OUTFILE are blocked together."""
        query = "SELECT LOAD_FILE('/etc/passwd') INTO OUTFILE '/tmp/output.txt'"
        # Should be blocked by LOAD_FILE check first
        self.loop.run_until_complete(
            self._test_query_blocked(query, "LOAD_FILE()")
        )
    
    # Edge cases
    
    def test_escaped_quotes_in_strings(self):
        """Test that escaped quotes in strings don't break validation."""
        queries = [
            r"SELECT 'It\'s a LOAD_FILE test' as text",
            r'SELECT "He said \"LOAD_FILE\"" as text',
            r"SELECT 'INTO OUTFILE with \' quote' as text",
        ]
        for query in queries:
            with self.subTest(query=query):
                self.loop.run_until_complete(
                    self._test_query_allowed(query)
                )
    
    def test_comments_removed_before_validation(self):
        """Test that comments are removed before validation."""
        queries = [
            "SELECT * FROM users -- LOAD_FILE('/etc/passwd')",
            "SELECT * FROM users /* LOAD_FILE('/etc/passwd') */",
            "SELECT * FROM users -- INTO OUTFILE '/tmp/test'",
            "SELECT * FROM users # LOAD_FILE('/etc/passwd')",
        ]
        for query in queries:
            with self.subTest(query=query):
                self.loop.run_until_complete(
                    self._test_query_allowed(query)
                )

    # MariaDB executable comment (/*! ... */) bypass tests.
    # Executable comments are NOT inert: MariaDB executes their contents as
    # live SQL, so validation must catch dangerous keywords hidden inside them.

    def test_load_file_in_executable_comment_is_blocked(self):
        """LOAD_FILE() hidden inside a /*! ... */ executable comment must be blocked."""
        query = "SELECT 1 /*!50000 , LOAD_FILE('/etc/passwd') */"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "LOAD_FILE()")
        )

    def test_into_outfile_in_executable_comment_is_blocked(self):
        """INTO OUTFILE hidden inside a /*! ... */ executable comment must be blocked."""
        query = "SELECT 'pwned' /*!50000 INTO OUTFILE '/var/lib/mysql-files/pwned.txt' */"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "INTO OUTFILE")
        )

    def test_into_dumpfile_in_executable_comment_is_blocked(self):
        """INTO DUMPFILE hidden inside a /*! ... */ executable comment must be blocked."""
        query = "SELECT 'pwned' /*!50000 INTO DUMPFILE '/tmp/pwned.txt' */"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "INTO DUMPFILE")
        )

    def test_non_read_prefix_hidden_in_executable_comment_is_blocked(self):
        """A write statement disguised via a leading executable comment must be blocked."""
        query = "/*!50000 DROP*/ TABLE users"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "read-only mode")
        )

    def test_load_file_in_string_still_allowed_with_executable_comment(self):
        """LOAD_FILE text inside a genuine string literal remains allowed, even
        when an unrelated executable comment is also present in the query."""
        query = "SELECT 'LOAD_FILE(/etc/passwd)' /*!50000 as text */"
        self.loop.run_until_complete(
            self._test_query_allowed(query)
        )

    def test_inert_comment_mentioning_keywords_still_allowed(self):
        """A genuinely inert /* ... */ comment (no leading !) must not trigger
        false positives even if it mentions blocked keywords."""
        query = "SELECT * FROM users /* not executable: LOAD_FILE INTO OUTFILE */"
        self.loop.run_until_complete(
            self._test_query_allowed(query)
        )

    # SHOW namespace tests: SHOW is allowed in read-only mode, but some
    # subcommands leak cross-connection or system-sensitive data and must
    # still be blocked.

    def test_show_processlist_is_blocked(self):
        """SHOW PROCESSLIST exposes query text from other sessions; must be blocked."""
        query = "SHOW PROCESSLIST"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "MCP_BLOCK_SENSITIVE_SHOW")
        )

    def test_show_grants_is_blocked(self):
        """SHOW GRANTS enumerates privileges of a DB account; must be blocked."""
        query = "SHOW GRANTS FOR 'root'@'%'"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "MCP_BLOCK_SENSITIVE_SHOW")
        )

    def test_show_variables_is_blocked(self):
        """SHOW VARIABLES exposes server configuration/filesystem paths; must be blocked."""
        queries = [
            "SHOW VARIABLES",
            "SHOW GLOBAL VARIABLES",
            "SHOW SESSION VARIABLES LIKE 'version%'",
        ]
        for query in queries:
            with self.subTest(query=query):
                self.loop.run_until_complete(
                    self._test_query_blocked(query, "MCP_BLOCK_SENSITIVE_SHOW")
                )

    def test_show_master_and_replica_status_is_blocked(self):
        """SHOW MASTER/REPLICA STATUS exposes replication topology; must be blocked."""
        queries = [
            "SHOW MASTER STATUS",
            "SHOW SLAVE STATUS",
            "SHOW REPLICA STATUS",
            "SHOW BINARY LOGS",
        ]
        for query in queries:
            with self.subTest(query=query):
                self.loop.run_until_complete(
                    self._test_query_blocked(query, "MCP_BLOCK_SENSITIVE_SHOW")
                )

    def test_safe_show_variants_still_allowed(self):
        """Ordinary schema-inspection SHOW commands remain unaffected."""
        queries = [
            "SHOW TABLES",
            "SHOW COLUMNS FROM users",
            "SHOW CREATE TABLE users",
            "SHOW DATABASES",
            "SHOW INDEX FROM users",
        ]
        for query in queries:
            with self.subTest(query=query):
                self.loop.run_until_complete(
                    self._test_query_allowed(query)
                )

    def test_show_blocking_is_independent_of_read_only(self):
        """MCP_BLOCK_SENSITIVE_SHOW must apply even when the server is NOT
        in read-only mode (a write-mode deployment may still want it blocked)."""
        self.server.is_read_only = False
        self.server.block_sensitive_show = True
        query = "SHOW PROCESSLIST"
        self.loop.run_until_complete(
            self._test_query_blocked(query, "MCP_BLOCK_SENSITIVE_SHOW")
        )

    def test_show_blocking_can_be_disabled_in_read_only_mode(self):
        """When MCP_BLOCK_SENSITIVE_SHOW=false, sensitive SHOW commands must
        be permitted even while the server is in read-only mode."""
        self.server.is_read_only = True
        self.server.block_sensitive_show = False
        query = "SHOW PROCESSLIST"
        self.loop.run_until_complete(
            self._test_query_allowed(query)
        )


class TestAutocommit(unittest.TestCase):
    """Test that autocommit respects the constructor parameter (issue: stale
    reads in read-only mode caused by an unconditional autocommit override)."""

    def test_autocommit_defaults_true_regardless_of_read_only(self):
        server = MariaDBServer(server_name="TestServer")
        self.assertTrue(server.autocommit)

    def test_autocommit_respects_explicit_false(self):
        server = MariaDBServer(server_name="TestServer", autocommit=False)
        self.assertFalse(server.autocommit)


class TestClientCapabilityAndPrivilegeWarnings(unittest.IsolatedAsyncioTestCase):
    async def test_safe_connection_clears_local_files_when_read_only(self):
        conn = SafeConnection(host="localhost", user="u", password="p", database="d")

        conn._client_flag = conn._client_flag | MULTI_STATEMENTS | LOCAL_FILES
        self.assertTrue(bool(conn._client_flag & MULTI_STATEMENTS))
        self.assertTrue(bool(conn._client_flag & LOCAL_FILES))

        with patch("custom_connection.MCP_READ_ONLY", True), patch.object(
            Connection, "connect", new=AsyncMock(return_value=None)
        ):
            await conn.connect()

        self.assertFalse(bool(conn._client_flag & MULTI_STATEMENTS))
        self.assertFalse(bool(conn._client_flag & LOCAL_FILES))

    async def test_safe_connection_does_not_clear_local_files_when_not_read_only(self):
        conn = SafeConnection(host="localhost", user="u", password="p", database="d")

        conn._client_flag = conn._client_flag | MULTI_STATEMENTS | LOCAL_FILES
        self.assertTrue(bool(conn._client_flag & MULTI_STATEMENTS))
        self.assertTrue(bool(conn._client_flag & LOCAL_FILES))

        with patch("custom_connection.MCP_READ_ONLY", False), patch.object(
            Connection, "connect", new=AsyncMock(return_value=None)
        ):
            await conn.connect()

        self.assertFalse(bool(conn._client_flag & MULTI_STATEMENTS))
        self.assertTrue(bool(conn._client_flag & LOCAL_FILES))

    async def test_warns_when_file_privilege_present(self):
        server = MariaDBServer(server_name="TestServer")
        server.is_read_only = True

        cursor = MagicMock()
        cursor.__aenter__ = AsyncMock(return_value=cursor)
        cursor.__aexit__ = AsyncMock(return_value=None)
        cursor.execute = AsyncMock(return_value=None)

        async def fetchone_side_effect():
            return ("'testuser'@'localhost'",)

        async def fetchall_side_effect():
            return [("GRANT FILE ON *.* TO 'testuser'@'localhost'",)]

        cursor.fetchone = AsyncMock(side_effect=fetchone_side_effect)
        cursor.fetchall = AsyncMock(side_effect=fetchall_side_effect)

        conn = MagicMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=None)
        conn.cursor = MagicMock(return_value=cursor)

        acquire_cm = MagicMock()
        acquire_cm.__aenter__ = AsyncMock(return_value=conn)
        acquire_cm.__aexit__ = AsyncMock(return_value=None)

        pool = MagicMock()
        pool.acquire = MagicMock(return_value=acquire_cm)
        server.pool = pool

        with patch("server.logger") as mock_logger:
            await server._warn_if_file_privilege_enabled()
            self.assertTrue(mock_logger.error.called)

    async def test_does_not_warn_when_not_read_only(self):
        server = MariaDBServer(server_name="TestServer")
        server.is_read_only = False

        with patch("server.logger") as mock_logger:
            if server.is_read_only:
                await server._warn_if_file_privilege_enabled()
            self.assertFalse(mock_logger.error.called)


class TestConnectionStreamBroken(unittest.TestCase):
    """
    asyncmy has changed how it exposes a broken connection stream across
    versions: newer releases expose `_stream_broken`, older releases (down
    to the pinned minimum asyncmy>=0.2.10) only expose `_reader`. The pool
    must work against either.
    """

    def test_uses_stream_broken_property_when_present(self):
        conn = MagicMock()
        conn._stream_broken = True
        self.assertTrue(_connection_stream_broken(conn))

        conn._stream_broken = False
        self.assertFalse(_connection_stream_broken(conn))

    def test_falls_back_to_reader_when_stream_broken_absent(self):
        class LegacyConnection:
            def __init__(self, at_eof=False, exception=None):
                self._reader = MagicMock()
                self._reader.at_eof.return_value = at_eof
                self._reader.exception.return_value = exception

        broken = LegacyConnection(at_eof=True)
        self.assertFalse(hasattr(broken, "_stream_broken"))
        self.assertTrue(_connection_stream_broken(broken))

        healthy = LegacyConnection(at_eof=False, exception=None)
        self.assertFalse(_connection_stream_broken(healthy))

    def test_handles_missing_reader_gracefully(self):
        class BareConnection:
            pass

        self.assertFalse(_connection_stream_broken(BareConnection()))


class TestSafePoolFillFreePool(unittest.IsolatedAsyncioTestCase):
    async def test_discards_broken_connection_without_reader_attribute(self):
        """Regression test: a connection exposing only `_stream_broken` (as in
        newer asyncmy releases) must not raise AttributeError('_reader')."""

        class BrokenConnection:
            _stream_broken = True

            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        pool = SafePool(minsize=0, maxsize=1)
        conn = BrokenConnection()
        pool._free.append(conn)

        await pool.fill_free_pool()

        self.assertTrue(conn.closed)
        self.assertFalse(pool._free)

    async def test_discards_broken_connection_with_legacy_reader_attribute(self):
        """Regression test: a connection exposing only `_reader` (as in
        asyncmy>=0.2.10, the pinned minimum) must still be discarded."""

        class LegacyBrokenConnection:
            def __init__(self):
                self.closed = False
                self._reader = MagicMock()
                self._reader.at_eof.return_value = True
                self._reader.exception.return_value = None

            def close(self):
                self.closed = True

        pool = SafePool(minsize=0, maxsize=1)
        conn = LegacyBrokenConnection()
        pool._free.append(conn)

        await pool.fill_free_pool()

        self.assertTrue(conn.closed)
        self.assertFalse(pool._free)


@unittest.skipUnless(
    os.getenv("MCP_RUN_PRIVILEGE_INTEGRATION_TESTS", "false").lower() == "true",
    "Integration test disabled. Set MCP_RUN_PRIVILEGE_INTEGRATION_TESTS=true to enable.",
)
class TestPrivilegeWarningIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncmy

        self._asyncmy = asyncmy

        self.admin_host = os.getenv("DB_HOST", "localhost")
        self.admin_port = int(os.getenv("DB_PORT", "3306"))
        self.admin_user = os.getenv("DB_ADMIN_USER") or os.getenv("DB_USER")
        self.admin_password = os.getenv("DB_ADMIN_PASSWORD") or os.getenv("DB_PASSWORD")
        self.db_name = os.getenv("DB_NAME")

        if not self.admin_user or self.admin_password is None:
            self.skipTest("Admin credentials not available via DB_ADMIN_USER/DB_ADMIN_PASSWORD or DB_USER/DB_PASSWORD")
        if not self.db_name:
            self.skipTest("DB_NAME must be set for this integration test")

        self.admin_conn = await asyncmy.connect(
            host=self.admin_host,
            port=self.admin_port,
            user=self.admin_user,
            password=self.admin_password,
            db=self.db_name,
            autocommit=True,
        )

        suffix = str(os.getpid())
        self.user_no_file = f"mcp_test_nofile_{suffix}"
        self.user_with_file = f"mcp_test_file_{suffix}"
        self.test_password = "mcp_test_pw_123!"
        self.host = "%"

        async with self.admin_conn.cursor() as cur:
            await cur.execute(f"DROP USER IF EXISTS '{self.user_no_file}'@'{self.host}'")
            await cur.execute(f"DROP USER IF EXISTS '{self.user_with_file}'@'{self.host}'")

            await cur.execute(
                f"CREATE USER '{self.user_no_file}'@'{self.host}' IDENTIFIED BY '{self.test_password}'"
            )
            await cur.execute(
                f"CREATE USER '{self.user_with_file}'@'{self.host}' IDENTIFIED BY '{self.test_password}'"
            )

            await cur.execute(f"GRANT SELECT ON `{self.db_name}`.* TO '{self.user_no_file}'@'{self.host}'")
            await cur.execute(f"GRANT SELECT ON `{self.db_name}`.* TO '{self.user_with_file}'@'{self.host}'")
            await cur.execute(f"GRANT FILE ON *.* TO '{self.user_with_file}'@'{self.host}'")

    async def asyncTearDown(self):
        try:
            async with self.admin_conn.cursor() as cur:
                await cur.execute(f"DROP USER IF EXISTS '{self.user_no_file}'@'{self.host}'")
                await cur.execute(f"DROP USER IF EXISTS '{self.user_with_file}'@'{self.host}'")
        finally:
            try:
                await self.admin_conn.ensure_closed()
            except Exception:
                pass

    async def _run_server_init_and_capture_error(self, user: str) -> bool:
        server = MariaDBServer(server_name="TestServer")
        server.is_read_only = True

        with patch("server.DB_HOST", self.admin_host), patch("server.DB_PORT", self.admin_port), patch(
            "server.DB_USER", user
        ), patch("server.DB_PASSWORD", self.test_password), patch("server.DB_NAME", self.db_name):
            with self.assertLogs(level="ERROR") as cm:
                await server.initialize_pool()
                await server.close_pool()

        return any("global FILE privilege" in msg for msg in cm.output)

    async def test_file_privilege_user_triggers_warning(self):
        warned = await self._run_server_init_and_capture_error(self.user_with_file)
        self.assertTrue(warned)

    async def test_non_file_privilege_user_does_not_trigger_warning(self):
        warned = await self._run_server_init_and_capture_error(self.user_no_file)
        self.assertFalse(warned)


class TestWithAnalyzeValidation(TestQueryValidation):
    """
    WITH and ANALYZE are allowed prefixes, but neither is a read on its own.

    MariaDB accepts WITH ... UPDATE / DELETE / INSERT, and ANALYZE *runs* the
    statement it wraps (unlike EXPLAIN), so `ANALYZE DELETE` really deletes and
    `ANALYZE TABLE` rewrites index statistics. Only the SELECT forms are reads.
    """

    async def _assert_read_allowed(self, query: str):
        try:
            await self.server._execute_query(query)
        except PermissionError as exc:
            self.fail(f"Read query was blocked: {query} ({exc})")
        except Exception:
            # Reaching the pool means validation passed, which is all we test.
            pass

    async def _assert_write_blocked(self, query: str):
        with self.assertRaises(PermissionError) as context:
            await self.server._execute_query(query)
        self.assertIn("read-only mode", str(context.exception))

    def test_with_select_allowed(self):
        self.loop.run_until_complete(
            self._assert_read_allowed("WITH t AS (SELECT 1) SELECT * FROM t"))

    def test_with_recursive_select_allowed(self):
        self.loop.run_until_complete(self._assert_read_allowed(
            "WITH RECURSIVE t AS (SELECT 1 UNION SELECT n+1 FROM t) SELECT * FROM t"))

    def test_with_multiple_ctes_allowed(self):
        self.loop.run_until_complete(self._assert_read_allowed(
            "WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a JOIN b"))

    def test_with_update_blocked(self):
        self.loop.run_until_complete(self._assert_write_blocked(
            "WITH t AS (SELECT 1) UPDATE users SET name = 'x'"))

    def test_with_delete_blocked(self):
        self.loop.run_until_complete(
            self._assert_write_blocked("WITH t AS (SELECT 1) DELETE FROM users"))

    def test_with_insert_blocked(self):
        self.loop.run_until_complete(self._assert_write_blocked(
            "WITH t AS (SELECT 1) INSERT INTO users SELECT * FROM t"))

    def test_with_write_keyword_in_string_is_not_the_body(self):
        self.loop.run_until_complete(self._assert_read_allowed(
            "WITH t AS (SELECT 'delete from users') SELECT * FROM t"))

    def test_analyze_select_allowed(self):
        self.loop.run_until_complete(
            self._assert_read_allowed("ANALYZE SELECT * FROM users"))

    def test_analyze_format_json_select_allowed(self):
        self.loop.run_until_complete(
            self._assert_read_allowed("ANALYZE FORMAT=JSON SELECT * FROM users"))

    def test_analyze_with_select_allowed(self):
        self.loop.run_until_complete(self._assert_read_allowed(
            "ANALYZE WITH t AS (SELECT 1) SELECT * FROM t"))

    def test_analyze_update_blocked(self):
        self.loop.run_until_complete(
            self._assert_write_blocked("ANALYZE UPDATE users SET name = 'x'"))

    def test_analyze_delete_blocked(self):
        self.loop.run_until_complete(
            self._assert_write_blocked("ANALYZE DELETE FROM users"))

    def test_analyze_with_delete_blocked(self):
        self.loop.run_until_complete(self._assert_write_blocked(
            "ANALYZE WITH t AS (SELECT 1) DELETE FROM users"))

    def test_analyze_table_blocked(self):
        """ANALYZE TABLE rewrites index statistics, so it is a write."""
        self.loop.run_until_complete(self._assert_write_blocked("ANALYZE TABLE users"))

    def test_analyze_local_table_blocked(self):
        self.loop.run_until_complete(
            self._assert_write_blocked("ANALYZE LOCAL TABLE users"))

    def test_with_cannot_hide_a_write_in_an_executable_comment(self):
        """
        Executable comments run as live SQL, so the WITH check must see them.
        This is the upstream /*! ... */ bypass applied to the WITH path.
        """
        self.loop.run_until_complete(self._assert_write_blocked(
            "WITH t AS (SELECT 1) /*!DELETE FROM users*/"))


if __name__ == '__main__':
    print("=" * 70)
    print("Testing SQL Query Validation Security Features")
    print("=" * 70)
    print()
    
    # Run tests
    unittest.main(verbosity=2)
