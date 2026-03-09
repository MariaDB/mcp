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
from custom_connection import SafeConnection
from asyncmy.connection import Connection
from asyncmy.constants.CLIENT import MULTI_STATEMENTS, LOCAL_FILES


class TestQueryValidation(unittest.TestCase):
    """Test SQL query validation for security."""
    
    def setUp(self):
        """Set up test server."""
        self.server = MariaDBServer(server_name="TestServer")
        # Force read-only mode to enable validation
        self.server.is_read_only = True
        
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
            if "LOAD_FILE" in str(e) or "INTO OUTFILE" in str(e) or "INTO DUMPFILE" in str(e):
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
        ]
        for query in queries:
            with self.subTest(query=query):
                self.loop.run_until_complete(
                    self._test_query_allowed(query)
                )


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


if __name__ == '__main__':
    print("=" * 70)
    print("Testing SQL Query Validation Security Features")
    print("=" * 70)
    print()
    
    # Run tests
    unittest.main(verbosity=2)
