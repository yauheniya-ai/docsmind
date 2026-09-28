"""One shared async engine per `DocsmindConfig`. Kept separate from
`repository.py` so other adapters (e.g. a future `agent_tables` repository)
can reuse the same engine without importing chunk/section-specific code.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from docsmind.config import DocsmindConfig

_engines: dict[str, AsyncEngine] = {}


def get_engine(config: DocsmindConfig) -> AsyncEngine:
    """Cached per database_url so repeated calls (e.g. one per CLI command
    in the same process) don't each open a fresh connection pool."""
    if config.database_url not in _engines:
        _engines[config.database_url] = create_async_engine(config.database_url, pool_pre_ping=True)
    return _engines[config.database_url]


def get_sessionmaker(config: DocsmindConfig) -> async_sessionmaker:
    return async_sessionmaker(get_engine(config), expire_on_commit=False)
