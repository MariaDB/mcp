#!/usr/bin/env python3
"""
Download and populate MariaDB with geographical data.
Data source: https://github.com/dr5hn/countries-states-cities-database

Tables: regions (continents), subregions, countries, states, cities
"""

import os
import sys
import gzip
import tempfile
import urllib.request
from pathlib import Path

# Add src to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import asyncio
import asyncmy

# Database config from environment
# Use localhost for host access (Docker maps port 3306)
_db_host = os.getenv("DB_HOST", "localhost")
DB_HOST = "127.0.0.1" if _db_host in ("mariadb", "mysql", "db") else _db_host
DB_PORT = int(os.getenv("DB_PORT", "3306"))
# Use root credentials for database creation (can override with env vars)
DB_USER = os.getenv("DB_ROOT_USER", "root")
DB_PASSWORD = os.getenv("DB_ROOT_PASSWORD", "rootpassword123")
DATABASE_NAME = "geography"

# GitHub raw URLs for SQL files
BASE_URL = "https://raw.githubusercontent.com/dr5hn/countries-states-cities-database/master/sql"
SQL_FILES = [
    ("schema.sql", False),
    ("regions.sql", False),
    ("subregions.sql", False),
    ("countries.sql", False),
    ("states.sql", False),
    ("cities.sql.gz", True),  # Compressed
]


def download_file(url: str, dest: Path, compressed: bool = False) -> Path:
    """Download a file from URL to destination."""
    print(f"Downloading {url}...")

    if compressed:
        # Download to temp file, decompress
        with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as tmp:
            urllib.request.urlretrieve(url, tmp.name)
            print(f"  Decompressing {dest.name}...")
            with gzip.open(tmp.name, 'rb') as f_in:
                with open(dest, 'wb') as f_out:
                    f_out.write(f_in.read())
            os.unlink(tmp.name)
    else:
        urllib.request.urlretrieve(url, dest)

    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"  Downloaded: {dest.name} ({size_mb:.2f} MB)")
    return dest


def fix_sql_for_mariadb(content: str) -> str:
    """Fix MySQL-specific syntax for MariaDB compatibility."""
    # Remove MySQL-specific SET statements that might cause issues
    lines = content.split('\n')
    filtered_lines = []

    for line in lines:
        # Skip problematic SET statements
        if line.strip().startswith('SET ') and any(x in line for x in [
            'GLOBAL', 'SESSION', 'sql_require_primary_key'
        ]):
            continue
        filtered_lines.append(line)

    return '\n'.join(filtered_lines)


async def execute_sql_file(conn, filepath: Path, description: str):
    """Execute SQL statements from a file."""
    print(f"\nImporting {description} from {filepath.name}...")

    content = filepath.read_text(encoding='utf-8')
    content = fix_sql_for_mariadb(content)

    # Split into individual statements
    statements = []
    current = []

    for line in content.split('\n'):
        stripped = line.strip()

        # Skip comments and empty lines
        if not stripped or stripped.startswith('--') or stripped.startswith('/*'):
            continue

        current.append(line)

        # Check for statement end
        if stripped.endswith(';'):
            stmt = '\n'.join(current).strip()
            if stmt and not stmt.startswith('--'):
                statements.append(stmt)
            current = []

    # Execute statements
    async with conn.cursor() as cursor:
        executed = 0
        errors = 0

        for i, stmt in enumerate(statements):
            try:
                await cursor.execute(stmt)
                executed += 1

                # Progress indicator for large files
                if (i + 1) % 1000 == 0:
                    print(f"  Progress: {i + 1}/{len(statements)} statements...")

            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  Warning: {str(e)[:100]}")
                elif errors == 4:
                    print(f"  (suppressing further warnings...)")

        await conn.commit()
        print(f"  Completed: {executed} statements executed, {errors} errors")


async def main():
    """Main function to download and import geographical data."""

    if not DB_USER or not DB_PASSWORD:
        print("Error: DB_USER and DB_PASSWORD must be set in .env")
        sys.exit(1)

    print("=" * 60)
    print("Geography Database Population Script")
    print("=" * 60)
    print(f"Target: {DB_USER}@{DB_HOST}:{DB_PORT}/{DATABASE_NAME}")
    print()

    # Create download directory
    download_dir = Path(__file__).parent / "geography_data"
    download_dir.mkdir(exist_ok=True)

    # Download all SQL files
    print("Step 1: Downloading SQL files...")
    downloaded_files = []
    for filename, compressed in SQL_FILES:
        url = f"{BASE_URL}/{filename}"
        dest_name = filename.replace('.gz', '') if compressed else filename
        dest = download_dir / dest_name

        if dest.exists():
            print(f"  Using cached: {dest_name}")
        else:
            download_file(url, dest, compressed)

        downloaded_files.append((dest, filename.replace('.sql.gz', '').replace('.sql', '')))

    # Connect to MariaDB
    print("\nStep 2: Connecting to MariaDB...")
    try:
        conn = await asyncmy.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            autocommit=False,
        )
        print(f"  Connected successfully")
    except Exception as e:
        print(f"Error connecting to MariaDB: {e}")
        sys.exit(1)

    try:
        async with conn.cursor() as cursor:
            # Create database
            print(f"\nStep 3: Creating database '{DATABASE_NAME}'...")
            await cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{DATABASE_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            await cursor.execute(f"USE `{DATABASE_NAME}`")
            await conn.commit()
            print(f"  Database ready")

            # Disable foreign key checks during import
            await cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            await conn.commit()

        # Import files in order
        print("\nStep 4: Importing data...")

        import_order = [
            ("schema", "Schema (table definitions)"),
            ("regions", "Regions (continents)"),
            ("subregions", "Subregions"),
            ("countries", "Countries"),
            ("states", "States/Provinces"),
            ("cities", "Cities/Towns"),
        ]

        for file_key, description in import_order:
            filepath = download_dir / f"{file_key}.sql"
            if filepath.exists():
                await execute_sql_file(conn, filepath, description)
            else:
                print(f"  Skipping {file_key}: file not found")

        # Re-enable foreign key checks
        async with conn.cursor() as cursor:
            await cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            await conn.commit()

        # Print summary
        print("\n" + "=" * 60)
        print("Import Complete! Summary:")
        print("=" * 60)

        async with conn.cursor() as cursor:
            tables = ['regions', 'subregions', 'countries', 'states', 'cities']
            for table in tables:
                try:
                    await cursor.execute(f"SELECT COUNT(*) as cnt FROM `{table}`")
                    result = await cursor.fetchone()
                    count = result[0] if result else 0
                    print(f"  {table}: {count:,} records")
                except Exception as e:
                    print(f"  {table}: Error - {e}")

        print("\nDatabase is ready to use!")
        print(f"Connection: mysql -u {DB_USER} -p -h {DB_HOST} -P {DB_PORT} {DATABASE_NAME}")

    finally:
        await conn.ensure_closed()


if __name__ == "__main__":
    asyncio.run(main())
