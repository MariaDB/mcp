import unittest
import anyio
import fastmcp
from fastmcp.client import Client
from server import MariaDBServer
import json
import random
from asyncmy.errors import OperationalError

# This file contains integration tests for the MCP server
# It tests the server's tools using the FastMCP client with HTTP transport

class TestMariaDBMCPTools(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Start the MariaDBServer in the background using HTTP transport
        self.server = MariaDBServer(autocommit=False)
        # Use a random port for each test to avoid conflicts
        self.port = random.randint(9000, 9999)
    
    async def task_group_helper(self, tg):
        # Start the server as a background task with HTTP transport
        async def run_server():
            await self.server.run_async_server('http', port=self.port)
        
        tg.start_soon(run_server)
        # Give the server time to start
        await anyio.sleep(2)
        # Connect the FastMCP client using HTTP
        self.client = Client(self.server.mcp)

    async def asyncTearDown(self):
        # Clean up the server
        if hasattr(self, 'server') and self.server.pool:
            await self.server.close_pool()
    
    def extract_result(self, result):
        """Helper to extract JSON content from MCP result."""
        content = result.content[0].text if result.content else ""
        return json.loads(content) if content else None

    async def test_list_databases(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                result = await self.client.call_tool('list_databases', {})
                # Extract the content from the result
                content = result.content[0].text if result.content else ""
                databases = json.loads(content)
                self.assertIsInstance(databases, list)
                self.assertTrue(all(isinstance(db, str) for db in databases))
                # Optionally, check for system databases
                for sys_db in ["information_schema", "mysql", "performance_schema", "sys"]:
                    self.assertIn(sys_db, databases)
            tg.cancel_scope.cancel()
    
    async def test_list_tables_valid_db(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                result = await self.client.call_tool('list_tables', {'database_name': 'information_schema'})
                result = self.extract_result(result)
                self.assertIsInstance(result, list)
                self.assertTrue(all(isinstance(table, str) for table in result))
            tg.cancel_scope.cancel()
            # Optionally, check for system tables
            for sys_table in ["ALL_PLUGINS", "APPLICABLE_ROLES"]:
                self.assertIn(sys_table, result)

    async def test_get_schema_valid_table(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                result = await self.client.call_tool('get_table_schema', {'database_name': 'information_schema', 'table_name': 'ALL_PLUGINS'})
                result = self.extract_result(result)
                self.assertIsInstance(result, dict)
                print(result)
                self.assertTrue(all(isinstance(value, dict) for value in result.values()))
            tg.cancel_scope.cancel()

    async def test_get_schema_invalid_table(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                try:
                    result = await self.client.call_tool('get_table_schema', {'database_name': 'information_schema', 'table_name': 'INVALID_TABLE'})
                    self.fail("Expected an exception, but none was raised")
                except fastmcp.exceptions.ToolError as e:
                    # unfortunately, ToolError does not have a message that we can check
                    pass
            tg.cancel_scope.cancel()

    async def test_execute_sql(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                result = await self.client.call_tool('execute_sql', {'database_name': 'information_schema', 'sql_query': 'SELECT 1'})
                result = self.extract_result(result)
                self.assertIsInstance(result, list)
                self.assertEqual(result[0]['1'], 1)
            tg.cancel_scope.cancel()

    async def test_execute_sql_invalid_query(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                try:
                    result = await self.client.call_tool('execute_sql', {'database_name': 'information_schema', 'sql_query': 'SELECT * FROM information_schema.INVALID_TABLE WHERE 1=1'})
                    self.fail("Expected an exception, but none was raised")
                except fastmcp.exceptions.ToolError as e:
                    # unfortunately, ToolError does not have a message that we can check
                    pass
            tg.cancel_scope.cancel()

    async def test_execute_query_literal_percent_no_params(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                result = await self.client.call_tool(
                    'execute_sql',
                    {
                        'database_name': 'information_schema',
                        'sql_query': "SHOW VARIABLES LIKE '%version%'",
                    },
                )
                result = self.extract_result(result)
                self.assertIsInstance(result, list)
                self.assertGreaterEqual(len(result), 1)
            tg.cancel_scope.cancel()

    async def test_execute_sql_parameterized(self):
        # Call the tool via the client
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                result = await self.client.call_tool('execute_sql', {'database_name': 'information_schema', 'sql_query': 'SELECT * FROM information_schema.tables WHERE TABLE_SCHEMA = %s', 'parameters': ['information_schema']})
                result = self.extract_result(result)
                self.assertIsInstance(result, list)
            self.assertTrue(all(isinstance(table, dict) for table in result))
            self.assertGreater(len(result), 1)
            tg.cancel_scope.cancel()

    async def test_execute_sql_parameterized_invalid(self):
        # Call the tool via the client
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                try:
                    result = await self.client.call_tool('execute_sql', {'database_name': 'information_schema_INVALID', 'sql_query': 'SELECT * FROM information_schema.tables WHERE TABLE_SCHEMA = %s', 'parameters': ['information_schema']})
                    self.fail("Expected an exception, but none was raised")
                except Exception as e:
                    pass
            tg.cancel_scope.cancel()

    async def test_execute_sql_parameterized_empty(self):
        # Call the tool via the client
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                try:
                    result = await self.client.call_tool('execute_sql', {'database_name': 'information_schema', 'sql_query': 'SELECT * FROM information_schema.tables WHERE TABLE_SCHEMA = %s', 'parameters': []})
                    self.fail("Expected an exception, but none was raised")
                except Exception as e:
                    pass
            tg.cancel_scope.cancel()

    async def test_execute_sql_parameterized_mismatch(self):
        # Call the tool via the client
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                try:
                    result = await self.client.call_tool('execute_sql', {'database_name': 'information_schema', 'sql_query': 'SELECT * FROM information_schema.tables WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s', 'parameters': ['information_schema']})
                    self.fail("Expected an exception, but none was raised")
                except Exception as e:
                    pass
            tg.cancel_scope.cancel()
    
    async def test_create_database(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                result = await self.client.call_tool('create_database', {'database_name': 'test_database'})
                result = self.extract_result(result)
                self.assertIsInstance(result, dict)
                self.assertTrue(all(isinstance(value, str) for value in result.values()))
            tg.cancel_scope.cancel()
        
    async def test_create_vector_store(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                await self.client.call_tool('create_database', {'database_name': 'test_database'})
                result = await self.client.call_tool('create_vector_store', {'database_name': 'test_database', 'vector_store_name': 'test_vector_store'})
                result = self.extract_result(result)
                self.assertIsInstance(result, dict)
                self.assertTrue(all(isinstance(value, str) for value in result.values()))
            tg.cancel_scope.cancel()
        
    async def test_list_vector_stores(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                await self.client.call_tool('create_database', {'database_name': 'test_database'})
                await self.client.call_tool('create_vector_store', {'database_name': 'test_database', 'vector_store_name': 'test_vector_store'})
                result = await self.client.call_tool('list_vector_stores', {'database_name': 'test_database'})
                result = self.extract_result(result)
                self.assertIsInstance(result, list)
                self.assertTrue(all(isinstance(store, str) for store in result))
            tg.cancel_scope.cancel()
        
    async def test_delete_vector_store(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                await self.client.call_tool('create_database', {'database_name': 'test_database'})
                await self.client.call_tool('create_vector_store', {'database_name': 'test_database', 'vector_store_name': 'test_vector_store'})
                result = await self.client.call_tool('delete_vector_store', {'database_name': 'test_database', 'vector_store_name': 'test_vector_store'})
                result = self.extract_result(result)
                self.assertIsInstance(result, dict)
                self.assertTrue(all(isinstance(value, str) for value in result.values()))
            tg.cancel_scope.cancel()
        
    async def test_insert_docs_vector_store(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                await self.client.call_tool('create_database', {'database_name': 'test_database'})
                await self.client.call_tool('create_vector_store', {'database_name': 'test_database', 'vector_store_name': 'test_vector_store'})
                result = await self.client.call_tool('insert_docs_vector_store', {'database_name': 'test_database', 'vector_store_name': 'test_vector_store', 'documents': ['test_document']})
                result = self.extract_result(result)
                print(result)
                self.assertIsInstance(result, dict)
                self.assertTrue(result['status'] == 'success')
                self.assertTrue(result['inserted'] == 1)
            tg.cancel_scope.cancel()
        
    async def test_search_vector_store(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                await self.client.call_tool('create_database', {'database_name': 'test_database'})
                await self.client.call_tool('create_vector_store', {'database_name': 'test_database', 'vector_store_name': 'test_vector_store'})
                await self.client.call_tool('insert_docs_vector_store', {'database_name': 'test_database', 'vector_store_name': 'test_vector_store', 'documents': ['test_document'], 'metadata': [{'test': 'test'}]})
                result = await self.client.call_tool('search_vector_store', {'database_name': 'test_database', 'vector_store_name': 'test_vector_store', 'query': 'test_query'})
                result = self.extract_result(result)
                self.assertIsInstance(result, dict)
                self.assertTrue(result['status'] == 'success')
            tg.cancel_scope.cancel()
    
    async def test_readonly_mode(self):
        self.server.is_read_only = True
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                try:
                    await self.client.call_tool('create_database', {'database_name': 'test_database2'})
                    self.fail("Expected an exception, but none was raised")
                except Exception as e:
                    pass
            tg.cancel_scope.cancel()

if __name__ == "__main__":
    unittest.main()