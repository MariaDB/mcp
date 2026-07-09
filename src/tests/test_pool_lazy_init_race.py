import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# Match the other suites: run from src/ with the package dir on sys.path, so
# server.py's own top-level `from config import ...` resolves.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from server import MariaDBServer


class TestPoolLazyInitRace(unittest.IsolatedAsyncioTestCase):
    """With the pool created lazily on first query, concurrent first queries must
    not each build a pool and orphan all but the last (leaking its connections)."""

    async def test_concurrent_initialize_pool_creates_single_pool(self):
        server = MariaDBServer()
        server.pool = None
        server.is_read_only = False  # skip the post-create privilege probe

        created = 0

        async def slow_create_pool(**_kwargs):
            nonlocal created
            created += 1
            await asyncio.sleep(0.05)  # widen the check-then-create window
            return SimpleNamespace(close=lambda: None, wait_closed=lambda: None)

        with patch("server.create_safe_pool", side_effect=slow_create_pool), \
             patch("server.DB_USER", "u"), patch("server.DB_PASSWORD", "p"):
            await asyncio.gather(*(server.initialize_pool() for _ in range(8)))

        self.assertEqual(created, 1, f"race: {created} pools created, {created - 1} orphaned")
        self.assertIsNotNone(server.pool)


if __name__ == "__main__":
    unittest.main(verbosity=2)
