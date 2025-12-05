"""
Tests for SQL query validation security features.

Tests that LOAD_FILE() and SELECT ... INTO ... are properly blocked,
including in subqueries, while allowing them in string literals.
"""

import unittest
import asyncio
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from server import MariaDBServer


class TestQueryValidation(unittest.TestCase):
    """Test SQL query validation for security."""
    
    def setUp(self):
        """Set up test server."""
        self.server = MariaDBServer(server_name="TestServer")
        # Force read-only mode to enable validation
        self.server.is_read_only = True
        self.loop = asyncio.get_event_loop()
    
    def tearDown(self):
        """Clean up after tests."""
        if self.server.pool:
            self.loop.run_until_complete(self.server.close_pool())
    
    async def _test_query_blocked(self, query: str, expected_error_msg: str):
        """Helper to test that a query is blocked."""
        await self.server.initialize_pool()
        
        with self.assertRaises(PermissionError) as context:
            await self.server._execute_query(query)
        
        self.assertIn(expected_error_msg, str(context.exception))
    
    async def _test_query_allowed(self, query: str):
        """Helper to test that a query is allowed (doesn't raise PermissionError for validation)."""
        await self.server.initialize_pool()
        
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


if __name__ == '__main__':
    print("=" * 70)
    print("Testing SQL Query Validation Security Features")
    print("=" * 70)
    print()
    
    # Run tests
    unittest.main(verbosity=2)
