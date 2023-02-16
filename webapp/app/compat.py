import sys
import logging

from sqlalchemy import event, inspect

from . import config

LOG = logging.getLogger("aireyes.compat")
LOG.setLevel( logging.DEBUG )


DISABLE_SQLITE = False

if DISABLE_SQLITE or config.APP_ENV == "Production" or config.APP_ENV == "LiveDevelopment" or config.APP_ENV == "Development":
    from sqlalchemy.dialects.postgresql import insert as insert_
elif config.APP_ENV == "Test":
    from sqlalchemy.dialects.sqlite import insert as insert_
else:
    raise Exception("unknown app env")
insert = insert_


def monkey_patch_sqlite():
    try:
        # First, attempt to import sqlite3, and from it, connect to a memory database. On the database connection, attempt to get enable_load_extension.
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.enable_load_extension
    except AttributeError as ae:
        # If this does not exist, this will raise an AttributeError, we will then monkey patch the sqlite3 module with the imported pysqlite3 module.
        LOG.warning(f"Attempt to find enable_load_extension in sqlite3 failed, using pysqlite3 instead!")
        """
        Amazing! Why could no one else find this for me?
        https://stackoverflow.com/a/65198886
        """
        sys.modules["sqlite3"] = __import__("pysqlite3")


def should_load_spatialite_sync(engine):
    try:
        # Attempt to open a connection for this engine, so we can enable extension loading, load spatialite and setup metadata for it all.
        def load_spatialite(dbapi_conn, connection_record):
            # Enable load extension and load by both function and SQL. Just in case.
            dbapi_conn.enable_load_extension(True)
            dbapi_conn.load_extension("mod_spatialite")
            dbapi_conn.execute("SELECT load_extension(\"mod_spatialite\");")
            # We can now disable extension loading.
            dbapi_conn.enable_load_extension(False)
            # We'll now check for the metadata table, and init it if it does not exist.
            try:
                dbapi_conn.execute("SELECT COUNT(*) FROM spatial_ref_sys")
                # If this succeeded, there's no need to load.
            except Exception as e:
                # We require spatialite to be loaded.
                dbapi_conn.execute("SELECT InitSpatialMetaData(1);")
                LOG.debug(f"Successfully loaded SpatiaLite extension and ran init metadata!")
        event.listen(engine, "connect", load_spatialite)
    except AttributeError as ae:
        LOG.error(f"Failed to load spatialite extension, but it is required for your configuration! Original error as follows...")
        LOG.error(ae, exc_info = True)
        raise NotImplementedError()
    except Exception as e:
        raise e


async def should_load_spatialite_async(engine):
    try:
        def load_spatialite(dbapi_conn, connection_record):
            # Enable load extension and load by both function and SQL. Just in case.
            dbapi_conn.run_async(lambda con: con.enable_load_extension(True))
            dbapi_conn.run_async(lambda con: con.load_extension("mod_spatialite"))
            dbapi_conn.run_async(lambda con: con.execute("SELECT load_extension(\"mod_spatialite\");"))
            # We can now disable extension loading.
            dbapi_conn.run_async(lambda con: con.enable_load_extension(False))
            # We'll now check for the metadata table, and init it if it does not exist.
            try:
                dbapi_conn.run_async(lambda con: con.execute("SELECT COUNT(*) FROM spatial_ref_sys"))
                # If this succeeded, there's no need to load.
            except Exception as e:
                # We require spatialite metadata tables to be created.
                dbapi_conn.run_async(lambda con: con.execute("SELECT InitSpatialMetaData(1);"))
                LOG.debug(f"Successfully loaded SpatiaLite extension and ran init metadata asynchronously!")
        event.listen(engine.sync_engine, "connect", load_spatialite)
    except AttributeError as ae:
        LOG.error(f"[ASYNC SPATIALITE] Failed to load spatialite extension, but it is required for your configuration! Original error as follows...")
        LOG.error(ae, exc_info = True)
        raise NotImplementedError()
    except Exception as e:
        raise e
