"""
Custom asyncmy Connection and Pool classes that disable MULTI_STATEMENTS capability.

asyncmy hardcodes MULTI_STATEMENTS in Connection.__init__, but we need to disable it
for security reasons (to prevent SQL injection via multiple statements).
"""

import asyncio
from asyncmy.connection import Connection
from asyncmy.constants.CLIENT import MULTI_STATEMENTS, LOCAL_FILES
from asyncmy.pool import Pool
from asyncmy.contexts import _PoolContextManager
from asyncmy import converters
from asyncmy.constants.FIELD_TYPE import DATE, DATETIME, TIMESTAMP

from config import MCP_READ_ONLY


class SafeConnection(Connection):
    """
    A Connection subclass that removes the MULTI_STATEMENTS client flag.
    
    asyncmy automatically sets MULTI_STATEMENTS in __init__,
    but MariaDB's default behavior is to NOT allow multiple statements. 
    This class restores that safer default by clearing bit 16 before authentication.
    """
    
    async def connect(self):
        """
        Override connect to clear MULTI_STATEMENTS flag before authentication.
        
        The parent __init__ sets: client_flag |= MULTI_STATEMENTS
        We need to clear it before _request_authentication() is called.
        """
        # Clear the MULTI_STATEMENTS bit (bit 16 = 0x10000 = 65536) before connecting
        self._client_flag = self._client_flag & ~MULTI_STATEMENTS

        if MCP_READ_ONLY:
            self._client_flag = self._client_flag & ~LOCAL_FILES
        
        # Now proceed with normal connection
        return await super().connect()

    def close(self):
        """
        Close the socket and mark the connection as disconnected.

        asyncmy's close() drops the socket but leaves `connected` True, so
        Pool.release() would put the dead connection back into the free list.
        """
        super().close()
        self._connected = False


def _lenient(convert):
    """
    Wrap an asyncmy date/time decoder so unrepresentable values (e.g. MariaDB
    zero dates like '0000-00-00 00:00:00') come back as the raw string.

    asyncmy's convert_date tries to return the raw string on failure, but it is
    declared to return datetime.date, so Cython raises TypeError instead and
    aborts the result set mid-read.
    """
    def wrapper(obj):
        try:
            return convert(obj)
        except (TypeError, ValueError):
            return obj.decode("ascii") if isinstance(obj, (bytes, bytearray)) else obj
    return wrapper


SAFE_CONVERSIONS = dict(converters.conversions)
for _field_type in (DATE, DATETIME, TIMESTAMP):
    SAFE_CONVERSIONS[_field_type] = _lenient(converters.conversions[_field_type])


async def safe_connect(**kwargs) -> SafeConnection:
    """
    Create a SafeConnection instead of a regular Connection.

    This is a drop-in replacement for asyncmy.connect() that uses SafeConnection.
    """
    kwargs.setdefault("conv", SAFE_CONVERSIONS)
    conn = SafeConnection(**kwargs)
    await conn.connect()
    return conn


def _connection_stream_broken(conn) -> bool:
    """
    Check whether a pooled connection's underlying stream is no longer usable.

    asyncmy has changed how it exposes this across versions: newer releases
    expose a `_stream_broken` property, while older releases (and the pinned
    minimum, asyncmy>=0.2.10) only expose the raw `_reader` StreamReader.
    Support both so the pool doesn't break on either side of that change.
    """
    if hasattr(conn, "_stream_broken"):
        return bool(conn._stream_broken)
    reader = getattr(conn, "_reader", None)
    return reader is not None and (reader.at_eof() or reader.exception())


class SafePool(Pool):
    """
    A Pool subclass that uses SafeConnection instead of Connection.
    
    This ensures all connections from the pool have MULTI_STATEMENTS disabled.
    """
    
    async def fill_free_pool(self, override_min: bool = False):
        """
        Override fill_free_pool to use safe_connect instead of connect.
        
        This is the method that creates new connections for the pool.
        """
        # iterate over free connections and remove timeouted ones
        free_size = len(self._free)
        n = 0
        while n < free_size:
            conn = self._free[-1]
            if _connection_stream_broken(conn):
                self._free.pop()
                conn.close()
            elif self._recycle > -1 and self._loop.time() - conn.last_usage > self._recycle:
                self._free.pop()
                try:
                    await conn.ensure_closed()
                except Exception:
                    conn.close()
            else:
                self._free.rotate()
            n += 1

        while self.size < self.minsize:
            self._acquiring += 1
            try:
                conn = await safe_connect(**self._conn_kwargs)
                self._free.append(conn)
                self._cond.notify()
            finally:
                self._acquiring -= 1
        if self._free:
            return

        if override_min and self.size < self.maxsize:
            self._acquiring += 1
            try:
                conn = await safe_connect(**self._conn_kwargs)
                self._free.append(conn)
                self._cond.notify()
            finally:
                self._acquiring -= 1


def create_safe_pool(
        minsize: int = 1, 
        maxsize: int = 10, 
        echo: bool = False, 
        pool_recycle: int = 3600, 
        **kwargs
):
    """
    Create a SafePool instead of a regular Pool.
    
    This is a drop-in replacement for asyncmy.create_pool() that uses SafeConnection.
    """
    coro = _create_safe_pool(
        minsize=minsize, 
        maxsize=maxsize, 
        echo=echo, 
        pool_recycle=pool_recycle, 
        **kwargs
    )
    return _PoolContextManager(coro)


async def _create_safe_pool(
        minsize: int = 1, 
        maxsize: int = 10, 
        echo: bool = False, 
        pool_recycle: int = 3600, 
        **kwargs
):
    """Internal coroutine to create and initialize a SafePool."""
    pool = SafePool(
        minsize=minsize, 
        maxsize=maxsize, 
        echo=echo, 
        pool_recycle=pool_recycle, 
        **kwargs
    )
    if minsize > 0:
        async with pool.cond:
            await pool.fill_free_pool(False)
    return pool
