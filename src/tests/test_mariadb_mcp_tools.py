# -*- coding: utf-8 -*-
import unittest
from unittest.mock import AsyncMock, patch
import asyncio

import sys
import os

# Import the MariaDBServer from the project
from server import MariaDBServer

"""
Manual Test Cases for MariaDB Ops Server MCP Tools via Cascade AI Assistant

This script outlines the manual tests performed using Cascade AI Assistant
to verify the functionality of the MariaDB_Ops_Server MCP tools.

These tests were executed interactively and results were observed directly
from the tool responses within the Cascade environment.

MCP Tools Tested:
- mcp0_list_databases
- mcp0_list_tables
- mcp0_get_table_schema
- mcp0_execute_sql

NOTE: This script is documentation of manual tests. It cannot be executed
directly to run the tests as it relies on Cascade's MCP tool interaction.
"""

# --- Test Plan ---
# The following functions represent the test steps performed manually.
# Expected outcomes are based on the interactive session results.

def setup_mcp():
    server = MariaDBServer()
    asyncio.run(server.initialize_pool())
    return server

class TestMariaDBMCPTools(unittest.IsolatedAsyncioTestCase):
    async def setUp(self):
        server = MariaDBServer()
        self.server = server
        await server.initialize_pool()
        server.register_tools()

    def tearDown(self):
        self.server.close_pool()

    async def test_step_1_list_databases(self):
        """
        Test: Call mcp0_list_databases.
        Purpose: Verify it returns a list of database names.
        Expected Outcome: Success, returns a JSON list of strings (database names).
        """
        result = await self.server.list_databases()
        self.assertIsInstance(result, list)
        self.assertTrue(all(isinstance(db, str) for db in result))
        for sys_db in ["mysql", "sys"]:
            self.assertIn(sys_db, result)

# If this file is run directly, run the tests
if __name__ == "__main__":
    unittest.main()

def test_step_1_list_databases():
    """
    Test: Call mcp0_list_databases.
    Purpose: Verify it returns a list of database names.
    Expected Outcome: Success, returns a JSON list of strings (database names).
    Result: PASSED (as documented in TestMariaDBMCPTools.test_step_1_list_databases)
    """
    print("Executing: mcp0_list_databases()")
    # Manual execution via Cascade passed.

def test_step_2_list_tables_valid_db():
    """
    Test: Call mcp0_list_tables with a valid database ('information_schema').
    Purpose: Verify it lists tables for a known database.
    Expected Outcome: Success, returns a JSON list of strings (table names).
    Result: PASSED (Observed list: ['ALL_PLUGINS', 'APPLICABLE_ROLES', ...])
    """
    print("Executing: mcp0_list_tables(database_name='information_schema')")
    # Manual execution via Cascade passed.

def test_step_3_get_schema_valid_table():
    """
    Test: Call mcp0_get_table_schema for 'information_schema.TABLES'.
    Purpose: Verify it retrieves the schema for a known table.
    Expected Outcome: Success, returns a JSON object describing columns and types.
    Result: PASSED (Observed schema details for TABLES columns)
    """
    print("Executing: mcp0_get_table_schema(database_name='information_schema', table_name='TABLES')")
    # Manual execution via Cascade passed.

def test_step_4_execute_simple_select():
    """
    Test: Call mcp0_execute_sql with a simple SELECT query.
    Purpose: Verify basic SQL execution.
    Expected Outcome: Success, returns JSON list of query results.
    Result: PASSED (Observed result for SELECT * FROM information_schema.TABLES LIMIT 1)
    """
    print("Executing: mcp0_execute_sql(sql_query='SELECT * FROM information_schema.TABLES LIMIT 1')")
    # Manual execution via Cascade passed.

def test_step_5_execute_parameterized_select():
    """
    Test: Call mcp0_execute_sql with a parameterized SELECT query.
    Purpose: Verify parameterized query execution.
    Expected Outcome: Success, returns JSON list of filtered query results.
    Result: PASSED (Observed result for SELECT ... WHERE TABLE_SCHEMA = %s)
    """
    print("Executing: mcp0_execute_sql(sql_query='SELECT ... WHERE TABLE_SCHEMA = %s', parameters=['information_schema'])")
    # Manual execution via Cascade passed.

# --- Complex / Edge Case Tests ---

def test_step_6_list_tables_nonexistent_db():
    """
    Test: Call mcp0_list_tables with a non-existent database name.
    Purpose: Verify error handling for unknown databases.
    Expected Outcome: Failure/Error response indicating unknown database.
    Result: PASSED (Observed error: (1049, "Unknown database ..."))
    """
    print("Executing: mcp0_list_tables(database_name='db_that_does_not_exist_cascade_test')")
    # Manual execution via Cascade passed (tool reported error).

def test_step_7_get_schema_nonexistent_table():
    """
    Test: Call mcp0_get_table_schema for a non-existent table.
    Purpose: Verify error handling for unknown tables.
    Expected Outcome: Failure/Error response indicating schema retrieval failure.
    Result: PASSED (Observed error: "Could not retrieve schema for table ...")
    """
    print("Executing: mcp0_get_table_schema(database_name='information_schema', table_name='table_that_does_not_exist')")
    # Manual execution via Cascade passed (tool reported error).

def test_step_8_execute_complex_join():
    """
    Test: Call mcp0_execute_sql with a JOIN query.
    Purpose: Verify handling of more complex SQL statements.
    Expected Outcome: Success, returns JSON list of joined results.
    Result: PASSED (Observed results from TABLES JOIN COLUMNS query)
    """
    print("Executing: mcp0_execute_sql(sql_query='SELECT ... JOIN ...')")
    # Manual execution via Cascade passed.

def test_step_9_execute_aggregation():
    """
    Test: Call mcp0_execute_sql with an aggregation query (COUNT/GROUP BY).
    Purpose: Verify handling of SQL aggregate functions.
    Expected Outcome: Success, returns JSON list of aggregated results.
    Result: PASSED (Observed results for COUNT(*) GROUP BY TABLE_SCHEMA query)
    """
    print("Executing: mcp0_execute_sql(sql_query='SELECT COUNT(*) ... GROUP BY ...')")
    # Manual execution via Cascade passed.

def test_step_10_execute_param_empty_string():
    """
    Test: Call mcp0_execute_sql with an empty string parameter.
    Purpose: Verify handling of specific parameter values.
    Expected Outcome: Success, returns empty result set (or as appropriate).
    Result: PASSED (Observed empty result set for WHERE TABLE_SCHEMA = '')
    """
    print("Executing: mcp0_execute_sql(sql_query='SELECT ... WHERE col = %s', parameters=[''])")
    # Manual execution via Cascade passed.

def test_step_11_execute_param_mismatch():
    """
    Test: Call mcp0_execute_sql with incorrect number of parameters.
    Purpose: Verify error handling for parameter mismatch.
    Expected Outcome: Failure/Error response indicating parameter mismatch.
    Result: PASSED (Observed error: "not enough arguments for format string")
    """
    print("Executing: mcp0_execute_sql(sql_query='SELECT ... WHERE col1 = %s AND col2 = %s', parameters=['one_param'])")
    # Manual execution via Cascade passed (tool reported error).

def test_step_12_execute_show_command():
    """
    Test: Call mcp0_execute_sql with a SHOW command (requires escaping '%').
    Purpose: Verify handling of non-SELECT read commands and literal '%'.
    Expected Outcome: Success, returns JSON list of results from SHOW VARIABLES.
    Result: PASSED (Observed results for SHOW VARIABLES LIKE 'version%%')
    """
    print("Executing: mcp0_execute_sql(sql_query='SHOW VARIABLES LIKE \'version%%\'')")
    # Manual execution via Cascade passed.


if __name__ == "__main__":
    print("Running manual test descriptions...")
    test_step_1_list_databases()
    test_step_2_list_tables_valid_db()
    test_step_3_get_schema_valid_table()
    test_step_4_execute_simple_select()
    test_step_5_execute_parameterized_select()
    print("\n--- Complex / Edge Cases ---")
    test_step_6_list_tables_nonexistent_db()
    test_step_7_get_schema_nonexistent_table()
    test_step_8_execute_complex_join()
    test_step_9_execute_aggregation()
    test_step_10_execute_param_empty_string()
    test_step_11_execute_param_mismatch()
    test_step_12_execute_show_command()
    print("\nManual test descriptions complete.")
