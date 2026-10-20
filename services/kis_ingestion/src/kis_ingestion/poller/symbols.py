import logging
from importlib import import_module
from typing import Protocol, cast


logger = logging.getLogger(__name__)

SYMBOLS_QUERY = "SELECT ticker FROM reference.entity_symbol ORDER BY ticker"


class SymbolSource(Protocol):
    async def list_symbols(self) -> list[str]: ...


class _Record(Protocol):
    def __getitem__(self, key: str) -> object: ...


class _Connection(Protocol):
    async def fetch(self, query: str) -> list[_Record]: ...

    async def close(self) -> None: ...


class _Connect(Protocol):
    async def __call__(self, dsn: str) -> _Connection: ...


def _asyncpg_connect() -> _Connect:
    return cast(_Connect, cast(object, getattr(import_module("asyncpg"), "connect")))


class ReferenceSymbolSource:
    """Reads the poll universe from reference.entity_symbol and keeps the last good list."""

    def __init__(self, database_url: str, connect: _Connect | None = None) -> None:
        self._dsn: str = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        self._connect: _Connect = connect if connect is not None else _asyncpg_connect()
        self._last_symbols: list[str] = []

    async def list_symbols(self) -> list[str]:
        try:
            connection = await self._connect(self._dsn)
            try:
                rows = await connection.fetch(SYMBOLS_QUERY)
            finally:
                await connection.close()
        except Exception:
            if not self._last_symbols:
                raise
            logger.exception("Symbol lookup failed, reusing %d cached symbols", len(self._last_symbols))
            return list(self._last_symbols)

        self._last_symbols = [str(row["ticker"]) for row in rows]
        return list(self._last_symbols)
