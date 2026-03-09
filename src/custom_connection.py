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


async def safe_connect(**kwargs) -> SafeConnection:
    """
    Create a SafeConnection instead of a regular Connection.
    
    This is a drop-in replacement for asyncmy.connect() that uses SafeConnection.
    """
    conn = SafeConnection(**kwargs)
    await conn.connect()
    return conn


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
            if conn._reader.at_eof() or conn._reader.exception():
                self._free.pop()
                conn.close()
            elif self._recycle > -1 and self._loop.time() - conn.last_usage > self._recycle:
                self._free.pop()
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
