#!/usr/bin/env python3
"""
Test script to query the geography database via MCP server.
Runs the MCP server tools directly (not via network).
"""

import sys
import asyncio
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Patch environment for local testing
import os
# Detect if running inside Docker container
if not os.path.exists("/.dockerenv"):
    # Running on host - use localhost to connect via mapped port
    os.environ["DB_HOST"] = "127.0.0.1"
# Inside Docker, keep DB_HOST as-is (uses docker network hostname)

from server import MariaDBServer


async def main():
    print("=" * 70)
    print("MCP Geography Database Test")
    print("=" * 70)

    # Create server instance
    server = MariaDBServer("Geography_Test")

    try:
        # Initialize connection pool
        print("\n1. Initializing connection pool...")
        await server.initialize_pool()
        print("   ✓ Pool initialized")

        # Test list_databases
        print("\n2. Testing list_databases()...")
        databases = await server.list_databases()
        print(f"   ✓ Found {len(databases)} databases:")
        for db in databases:
            marker = " ← target" if db == "geography" else ""
            print(f"      - {db}{marker}")

        if "geography" not in databases:
            print("   ✗ 'geography' database not found!")
            return

        # Test list_tables
        print("\n3. Testing list_tables('geography')...")
        tables = await server.list_tables("geography")
        print(f"   ✓ Found {len(tables)} tables:")
        for table in tables:
            print(f"      - {table}")

        # Test get_table_schema
        print("\n4. Testing get_table_schema('geography', 'countries')...")
        schema = await server.get_table_schema("geography", "countries")
        print(f"   ✓ Countries table has {len(schema)} columns:")
        for col, info in list(schema.items())[:8]:
            print(f"      - {col}: {info['type']}")
        print(f"      ... and {len(schema) - 8} more columns")

        # Test execute_sql - List continents
        print("\n5. Testing execute_sql() - List all continents...")
        results = await server.execute_sql(
            "SELECT id, name FROM regions ORDER BY name",
            "geography"
        )
        print(f"   ✓ Found {len(results)} continents:")
        for row in results:
            print(f"      - {row['name']}")

        # Test execute_sql - Countries by continent
        print("\n6. Testing execute_sql() - Countries in Europe...")
        results = await server.execute_sql(
            """
            SELECT c.name as country, c.capital, c.population
            FROM countries c
            JOIN regions r ON c.region_id = r.id
            WHERE r.name = %s
            ORDER BY c.population DESC
            LIMIT 10
            """,
            "geography",
            parameters=["Europe"]
        )
        print(f"   ✓ Top 10 European countries by population:")
        for row in results:
            pop = f"{row['population']:,}" if row['population'] else "N/A"
            print(f"      - {row['country']} (capital: {row['capital']}, pop: {pop})")

        # Test execute_sql - Cities search
        print("\n7. Testing execute_sql() - Cities named 'Paris'...")
        results = await server.execute_sql(
            """
            SELECT ci.name as city, s.name as state, co.name as country
            FROM cities ci
            JOIN states s ON ci.state_id = s.id
            JOIN countries co ON ci.country_id = co.id
            WHERE ci.name = %s
            ORDER BY co.name
            """,
            "geography",
            parameters=["Paris"]
        )
        print(f"   ✓ Found {len(results)} cities named 'Paris':")
        for row in results:
            print(f"      - {row['city']}, {row['state']}, {row['country']}")

        # Test execute_sql - Large cities
        print("\n8. Testing execute_sql() - Largest cities worldwide...")
        results = await server.execute_sql(
            """
            SELECT ci.name as city, co.name as country, ci.population
            FROM cities ci
            JOIN countries co ON ci.country_id = co.id
            WHERE ci.population IS NOT NULL
            ORDER BY ci.population DESC
            LIMIT 10
            """,
            "geography"
        )
        print(f"   ✓ Top 10 largest cities:")
        for row in results:
            pop = f"{row['population']:,}" if row['population'] else "N/A"
            print(f"      - {row['city']}, {row['country']} (pop: {pop})")

        # Test get_table_schema_with_relations
        print("\n9. Testing get_table_schema_with_relations('geography', 'cities')...")
        schema = await server.get_table_schema_with_relations("geography", "cities")
        fk_cols = [col for col, info in schema['columns'].items() if info.get('foreign_key')]
        print(f"   ✓ Cities table foreign keys:")
        for col in fk_cols:
            fk = schema['columns'][col]['foreign_key']
            print(f"      - {col} → {fk['referenced_table']}.{fk['referenced_column']}")

        # Summary statistics
        print("\n10. Database summary statistics...")
        for table in ['regions', 'subregions', 'countries', 'states', 'cities']:
            results = await server.execute_sql(f"SELECT COUNT(*) as cnt FROM {table}", "geography")
            count = results[0]['cnt'] if results else 0
            print(f"    - {table}: {count:,} records")

        print("\n" + "=" * 70)
        print("All MCP tool tests passed successfully!")
        print("=" * 70)

    finally:
        await server.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
