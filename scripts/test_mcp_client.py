#!/usr/bin/env python3
"""
Test MCP server via SSE transport (connects to running container).
"""

import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client

MCP_URL = "http://localhost:9001/sse"


async def main():
    print("=" * 70)
    print("MCP Client Test - Connecting to Container")
    print("=" * 70)
    print(f"URL: {MCP_URL}\n")

    async with sse_client(MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize the connection
            await session.initialize()
            print("✓ Connected to MCP server\n")

            # List available tools
            print("1. Listing available tools...")
            tools = await session.list_tools()
            print(f"   ✓ Found {len(tools.tools)} tools:")
            for tool in tools.tools:
                print(f"      - {tool.name}")

            # Test list_databases
            print("\n2. Calling list_databases()...")
            result = await session.call_tool("list_databases", {})
            databases = result.content[0].text if result.content else "[]"
            print(f"   ✓ Result: {databases}")

            # Test list_tables on demo
            print("\n3. Calling list_tables('demo')...")
            result = await session.call_tool("list_tables", {"database_name": "demo"})
            tables = result.content[0].text if result.content else "[]"
            print(f"   ✓ Result: {tables}")

            # Test execute_sql - List continents
            print("\n4. Querying continents from demo.regions...")
            result = await session.call_tool("execute_sql", {
                "sql_query": "SELECT name FROM regions ORDER BY name",
                "database_name": "demo"
            })
            print(f"   ✓ Result: {result.content[0].text if result.content else 'empty'}")

            # Test execute_sql - Country count by continent
            print("\n5. Querying country counts by continent...")
            result = await session.call_tool("execute_sql", {
                "sql_query": """
                    SELECT r.name as continent, COUNT(c.id) as country_count
                    FROM regions r
                    LEFT JOIN countries c ON r.id = c.region_id
                    GROUP BY r.id, r.name
                    ORDER BY country_count DESC
                """,
                "database_name": "demo"
            })
            print(f"   ✓ Result: {result.content[0].text if result.content else 'empty'}")

            # Test execute_sql - Cities in a specific country
            print("\n6. Querying cities in Switzerland...")
            result = await session.call_tool("execute_sql", {
                "sql_query": """
                    SELECT ci.name as city, s.name as state, ci.population
                    FROM cities ci
                    JOIN states s ON ci.state_id = s.id
                    JOIN countries co ON ci.country_id = co.id
                    WHERE co.name = %s
                    ORDER BY ci.population DESC
                    LIMIT 10
                """,
                "database_name": "demo",
                "parameters": ["Switzerland"]
            })
            print(f"   ✓ Result: {result.content[0].text if result.content else 'empty'}")

            # Test get_table_schema
            print("\n7. Getting schema for demo.countries...")
            result = await session.call_tool("get_table_schema", {
                "database_name": "demo",
                "table_name": "countries"
            })
            # Just show column count
            import json
            schema = json.loads(result.content[0].text) if result.content else {}
            print(f"   ✓ Found {len(schema)} columns")
            print(f"   ✓ Sample columns: {list(schema.keys())[:5]}")

            print("\n" + "=" * 70)
            print("All MCP client tests passed!")
            print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
