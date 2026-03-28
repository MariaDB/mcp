"""
Tests that the MariaDBServer exposes its FastMCP instance so callers can
register additional resources before starting the server.

This mirrors the pattern used by mcp_fmd_server.py, which adds a
``schema://context`` resource to the ``mcp`` object after construction.
"""

import sys
import unittest
from pathlib import Path

import anyio
from fastmcp.client import Client

sys.path.insert(0, str(Path(__file__).parent.parent))

from server import MariaDBServer

_SCHEMA_CONTENT = "# Test Schema\nThis is test schema documentation."


class TestCustomResource(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = MariaDBServer()

        # Register a custom resource on the exposed FastMCP instance —
        # this is the pattern the FMD wrapper (mcp_fmd_server.py) uses.
        @self.server.mcp.resource("schema://context")
        def schema_context() -> str:
            """FMD AI Schema documentation."""
            return _SCHEMA_CONTENT

    async def test_custom_resource_is_listed(self):
        """The schema://context resource should appear in the resource list."""
        async with Client(self.server.mcp) as client:
            resources = await client.list_resources()
            uris = [str(r.uri) for r in resources]
            self.assertIn("schema://context", uris)

    async def test_custom_resource_content(self):
        """Reading schema://context should return the registered content."""
        async with Client(self.server.mcp) as client:
            result = await client.read_resource("schema://context")
            # result is a list of resource content objects; grab the first text
            text = result[0].text if result else ""
            self.assertEqual(text, _SCHEMA_CONTENT)


if __name__ == "__main__":
    unittest.main()
