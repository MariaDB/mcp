import unittest
import anyio
import fastmcp
from fastmcp.client import Client
from server import MariaDBServer
import json

# This file contains integration tests for the MCP server
# It tests the server's tools using the FastMCP client

class TestMariaDBMCPTools(unittest.IsolatedAsyncioTestCase):
    # Test database name used for cleanup
    TEST_DB_NAME = "test_database"

    async def asyncSetUp(self):
        # Start the MariaDBServer in the background using stdio transport
        self.server = MariaDBServer()
    
    async def task_group_helper(self, tg):
        # Start the server as a background task
        tg.start_soon(self.server.run_async_server, 'stdio')
        # Connect the FastMCP client using stdio
        self.client = Client(self.server.mcp)
        await anyio.sleep(2)

    async def asyncTearDown(self):
        pass

    async def test_list_databases(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                result = await self.client.call_tool('list_databases', {})
                result = result.text
                result = json.loads(result)
                self.assertIsInstance(result, list)
                self.assertTrue(all(isinstance(db, str) for db in result))
                # Optionally, check for system databases
                for sys_db in ["information_schema", "mysql", "performance_schema", "sys"]:
                    self.assertIn(sys_db, result)
            tg.cancel_scope.cancel()
    
    async def test_list_tables_valid_db(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                result = await self.client.call_tool('list_tables', {'database_name': 'information_schema'})
                result = result.text
                result = json.loads(result)
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
                result = result.text
                result = json.loads(result)
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
                result = result.text
                result = json.loads(result)
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

    async def test_execute_sql_parameterized(self):
        # Call the tool via the client
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                result = await self.client.call_tool('execute_sql', {'database_name': 'information_schema', 'sql_query': 'SELECT * FROM information_schema.tables WHERE TABLE_SCHEMA = %s', 'parameters': ['information_schema']})
                result = result.text
                result = json.loads(result)
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
                result = result[0].text
                result = json.loads(result)
                self.assertIsInstance(result, dict)
                self.assertTrue(all(isinstance(value, str) for value in result.values()))
            tg.cancel_scope.cancel()
        
    async def test_create_vector_store(self):
        async with anyio.create_task_group() as tg:
            await self.task_group_helper(tg)
            async with self.client:
                await self.client.call_tool('create_database', {'database_name': 'test_database'})
                result = await self.client.call_tool('create_vector_store', {'database_name': 'test_database', 'vector_store_name': 'test_vector_store'})
                result = result[0].text
                result = json.loads(result)
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
                result = result[0].text
                result = json.loads(result)
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
                result = result[0].text
                result = json.loads(result)
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
                result = result[0].text
                result = json.loads(result)
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
                result = await self.client.call_tool('search_vector_store', {'database_name': 'test_database', 'vector_store_name': 'test_vector_store', 'user_query': 'test_query'})
                result = result[0].text
                result = json.loads(result)
                self.assertIsInstance(result, list)
                self.assertGreater(len(result), 0)
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