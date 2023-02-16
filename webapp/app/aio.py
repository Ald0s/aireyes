"""
"""
import asyncio
import aiofiles
import logging

from contextlib import asynccontextmanager
from sqlalchemy import func, and_, or_, asc, desc, event
from sqlalchemy.future import select
from sqlalchemy.orm import declarative_base, relationship, selectinload, sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, async_scoped_session, AsyncSession
from sqlalchemy.exc import IntegrityError, OperationalError, UnsupportedCompilationError
from marshmallow import Schema, fields, EXCLUDE, post_load
from .compat import insert

from . import db, config, models, error, compat

LOG = logging.getLogger("aireyes.aio")
LOG.setLevel( logging.DEBUG )

logging_level = logging.ERROR
logging.getLogger('aiosqlite').setLevel(logging_level)
logging.getLogger('asyncpg').setLevel(logging_level)
logging.getLogger('sqlalchemy').setLevel(logging_level)
logging.getLogger('sqlalchemy.engine').setLevel(logging_level)


@asynccontextmanager
async def open_db(**kwargs):
    recreate = kwargs.get("recreate", False)
    # Initialise an asynchronous database connection.
    # Create a new async engine, using the same database used by the main app. This will remain local.
    engine = create_async_engine(
        config.AIOSQLALCHEMY_DATABASE_URL,
        echo = False
    )
    try:
        # If PostGIS enabled and dialect is SQLite, we require SpatiaLite.
        if config.POSTGIS_ENABLED and engine.dialect.name == "sqlite":
            await compat.should_load_spatialite_async(engine)
        # Now, recreate all tables.
        async with engine.begin() as conn:
            # If we must recreate, begin a connection to drop and create all.
            if recreate:
                await conn.run_sync(db.metadata.drop_all)
                await conn.run_sync(db.metadata.create_all)
        # Yield the engine.
        yield engine
    except Exception as e:
        LOG.error(e, exc_info = True)
        raise e
    finally:
        # If engine, await its disposal.
        if engine:
            await engine.dispose()


@asynccontextmanager
async def session_scope(session_factory):
    # Use session maker to get a new session.
    session = session_factory()
    try:
        yield session
        await session.commit()
    except Exception as e:
        LOG.error(e, exc_info = True)
        await session.rollback()
        raise e
    finally:
        await session.close()
