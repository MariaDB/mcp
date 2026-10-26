"""
Tests for connection recovery after mid-query failures.

A failure while reading a result set (e.g. a MariaDB zero date the decoder
can't represent) must not leave a half-read connection in the pool, and
zero dates must decode instead of aborting the query.
"""

import unittest
import asyncio
import datetime
import sys
import os
from unittest.mock import AsyncMock, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from server import MariaDBServer
from custom_connection import SafeConnection, SAFE_CONVERSIONS
from asyncmy.constants.FIELD_TYPE import DATE, DATETIME, TIMESTAMP


class TestLenientDateDecoders(unittest.TestCase):
    """Test that date/time decoders tolerate values Python can't represent."""

    def test_zero_datetime_returns_raw_string(self):
        for field_type in (DATETIME, TIMESTAMP):
            with self.subTest(field_type=field_type):
                self.assertEqual(SAFE_CONVERSIONS[field_type]('0000-00-00 00:00:00'), '0000-00-00 00:00:00')

    def test_zero_date_returns_raw_string(self):
        self.assertEqual(SAFE_CONVERSIONS[DATE]('0000-00-00'), '0000-00-00')

    def test_valid_values_still_decoded(self):
        self.assertEqual(SAFE_CONVERSIONS[DATETIME]('2024-02-09 13:45:00'), datetime.datetime(2024, 2, 9, 13, 45))
        self.assertEqual(SAFE_CONVERSIONS[DATE]('2024-02-09'), datetime.date(2024, 2, 9))

    def test_bytes_input_returns_str(self):
        self.assertEqual(SAFE_CONVERSIONS[DATETIME](b'0000-00-00 00:00:00'), '0000-00-00 00:00:00')


class TestSafeConnectionClose(unittest.TestCase):
    """Test that close() marks the connection disconnected."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        try:
            self.loop.close()
        finally:
            asyncio.set_event_loop(None)

    def test_close_marks_disconnected(self):
        conn = SafeConnection(host='localhost')
        conn._connected = True
        conn.close()
        self.assertFalse(conn.connected)


class TestExecuteQueryDiscardsConnection(unittest.TestCase):
    """Test that _execute_query closes the connection when a query fails mid-read."""

    def setUp(self):
        self.server = MariaDBServer(server_name="TestServer")
        self.server.is_read_only = True

        self.cursor = MagicMock()
        self.cursor.execute = AsyncMock()
        self.cursor.fetchone = AsyncMock(return_value={'DATABASE()': 'testdb'})
        self.cursor.fetchall = AsyncMock(return_value=[{'x': 1}])
        cursor_cm = MagicMock()
        cursor_cm.__aenter__ = AsyncMock(return_value=self.cursor)
        cursor_cm.__aexit__ = AsyncMock(return_value=None)

        self.conn = MagicMock()
        self.conn.cursor = MagicMock(return_value=cursor_cm)
        acquire_cm = MagicMock()
        acquire_cm.__aenter__ = AsyncMock(return_value=self.conn)
        acquire_cm.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=acquire_cm)
        self.server.pool = pool

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        try:
            self.loop.close()
        finally:
            asyncio.set_event_loop(None)

    def test_decoder_error_closes_connection(self):
        self.cursor.execute.side_effect = [None, TypeError("Cannot convert str to datetime.date")]
        with self.assertRaises(RuntimeError):
            self.loop.run_until_complete(self.server._execute_query("SELECT 1", database="testdb"))
        self.conn.close.assert_called_once()

    def test_cancellation_closes_connection(self):
        self.cursor.execute.side_effect = [None, asyncio.CancelledError()]
        with self.assertRaises(asyncio.CancelledError):
            self.loop.run_until_complete(self.server._execute_query("SELECT 1", database="testdb"))
        self.conn.close.assert_called_once()

    def test_success_keeps_connection(self):
        result = self.loop.run_until_complete(self.server._execute_query("SELECT 1", database="testdb"))
        self.assertEqual(result, [{'x': 1}])
        self.conn.close.assert_not_called()


if __name__ == '__main__':
    unittest.main()
