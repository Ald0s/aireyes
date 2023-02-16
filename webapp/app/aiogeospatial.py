"""
A module for handling all geospatial calculations. Including the importation of suburb data, association of suburb data with flight points etc.
This module combines async functionality with non-async functionality.
"""
import re
import os
import time
import asyncio
import aiofiles
import uuid
import decimal
import hashlib
import logging
import json
import pyproj
import geopandas

from datetime import date, datetime
from fastkml import kml
from shapely import geometry, ops

from sqlalchemy import func, and_, or_, asc, desc, update, select
from sqlalchemy.future import select
from sqlalchemy.orm import declarative_base, relationship, selectinload, sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, async_scoped_session, AsyncSession
from sqlalchemy.exc import IntegrityError, OperationalError, UnsupportedCompilationError
from marshmallow import Schema, fields, EXCLUDE, post_load
from .compat import insert

from . import db, config, models, error, aio, thirdparty

# Conditional legacy from when PostGIS was the addon. Can be removed.
if config.POSTGIS_ENABLED:
    from geoalchemy2 import shape

LOG = logging.getLogger("aireyes.aiogeospatial")
LOG.setLevel( logging.DEBUG )

logging_level = logging.ERROR
logging.getLogger('aiosqlite').setLevel(logging_level)
logging.getLogger('sqlalchemy').setLevel(logging_level)
logging.getLogger('sqlalchemy.engine').setLevel(logging_level)

engine = None
async_session_factory = None
AsyncScopedSession = None

australian_state_centers = {
    "ACT": (-35.4900000, 149.0013889),
    "NSW": (-32.1633333, 147.0166667),
    "NT": (-19.3833333, 133.3577778),
    "QLD": (-22.4869444, 144.4316667),
    "SA": (-30.0583333, 135.7633333),
    "TAS": (-42.0213889, 146.5933333),
    "VIC": (-36.8541667, 144.2811111),
    "WA": (-25.3280556, 122.2983333)
}
# Reverse coordinates from YX to XY.
australian_state_centers = dict((state_code, tuple(list(reversed(center_coordinates))),) for state_code, center_coordinates in australian_state_centers.items())


class SuburbImportResult():
    """
    A data class for storing the importation result for a single suburb.
    This class will indicate the success/failure status, hold the suburb itself, whether the suburb was created or updated.
    """
    @property
    def suburb(self):
        return self._suburb

    @property
    def error(self):
        return self._error

    @property
    def existed_no_change(self):
        return not self.was_created and not self.was_updated

    @property
    def is_error(self):
        return self._error != None

    def __init__(self, _suburb, _error = None, **kwargs):
        """
        Keyword arguments
        -----------------
        :was_created: Whether this suburb was created. Default is False.
        :was_updated: Whether this suburb was updated. Default is False.
        """
        self.was_created = kwargs.get("was_created", False)
        self.was_updated = kwargs.get("was_updated", False)

        self._suburb = _suburb
        self._error = _error


async def import_suburb(session, state_code, suburb_hash, suburb_name, suburb_postcode, version_hash, suburb_multi_polygon, **kwargs) -> SuburbImportResult:
    """
    Given a state code and all information for a specific suburb, ensure the suburb has been created.
    If this is the case, also ensure the suburb's information is correct and up to date.

    Arguments
    ---------
    :session: The Session.
    :state_code: The state to which this suburb belongs.
    :suburb_hash: The generated hash for this suburb.
    :suburb_name: The name of this suburb.
    :suburb_postcode: The postcode for this suburb.
    :version_hash: A hash for the latest contents of the suburb.
    :suburb_multi_polygon: A shapely MultiPolygon containing all Polygons in this suburb. This geometry should be projected through its source projection.

    Keyword arguments
    -----------------
    :transformer: The transformer to use for projecting the suburb's multipolygon to the required CRS. By default, one will be created that transforms SOURCE_COORDINATE_REF_SYS to COORDINATE_REF_SYS.

    Returns
    -------
    An instance of SuburbImportResult.
    """
    try:
        transformer = kwargs.get("transformer", None)
        if not transformer:
            transformer = pyproj.Transformer.from_crs(config.SOURCE_COORDINATE_REF_SYS, config.COORDINATE_REF_SYS)
        suburb_result = None
        was_created = False
        was_updated = False
        # Transform this multi polygon into the final CRS.
        suburb_multi_polygon = ops.transform(transformer.transform, suburb_multi_polygon)
        # From the suburb multipolygon, grab the center. Also, use this polygon to produce a minimum rotated rectangle, from which we will extract the bounds.
        suburb_center = suburb_multi_polygon.centroid
        suburb_minimum_rotated_rectangle = suburb_multi_polygon.minimum_rotated_rectangle
        suburb_bounds = suburb_minimum_rotated_rectangle.bounds
        # Attempt to locate the suburb.
        LOG.debug(f"Ensuring suburb '{suburb_name}' (in {state_code}) is created and up to date...")
        find_suburb_stmt = (
            select(models.Suburb)
            .where(and_(models.Suburb.suburb_hash == suburb_hash, models.Suburb.state_code == state_code))
        )
        existing_suburb_result = await session.execute(find_suburb_stmt)
        existing_suburb = existing_suburb_result.scalar()
        # If doesn't exist, we will insert it.
        crs = transformer.target_crs.to_epsg()
        if not existing_suburb:
            LOG.debug(f"Suburb '{suburb_name}' (in {state_code}) does NOT yet exist. Creating it now...")
            suburb_values_d = dict(
                state_code = state_code,
                suburb_hash = suburb_hash,
                postcode = suburb_postcode,
                name = suburb_name,
                minx = suburb_bounds[0], miny = suburb_bounds[1], maxx = suburb_bounds[2], maxy = suburb_bounds[3],
                version_hash = version_hash,
                crs = crs
            )
            if config.POSTGIS_ENABLED:
                suburb_values_d["point_geom"] = shape.from_shape(suburb_center, srid = crs)
                suburb_values_d["multi_polygon_geom"] = shape.from_shape(suburb_multi_polygon, srid = crs)
            else:
                suburb_values_d["point_geom"] = suburb_center.wkb
                suburb_values_d["multi_polygon_geom"] = suburb_multi_polygon.wkb
            insert_suburb_stmt = (
                insert(models.Suburb.__table__)
                .values(**suburb_values_d)
            )
            # Insert this suburb into the database.
            await session.execute(insert_suburb_stmt)
            # This was created.
            was_created = True
            was_updated = False
        elif existing_suburb and existing_suburb.version_hash != version_hash:
            LOG.debug(f"Determined existing suburb {existing_suburb} requires an update, from version has {existing_suburb.version_hash} to {version_hash}.")
            LOG.error("Updating an existing suburb is not yet supported.")
            """TODO: this is where we set was_created to False and was_updated to True."""
            raise NotImplementedError()
        else:
            # Nothing to do. Simply return existing suburb.
            LOG.debug(f"Suburb '{suburb_name}' (in {state_code}) is still valid and up to date.")
            # Set both created & updated to False.
            was_created = False
            was_updated = False
            # Set suburb result.
            suburb_result = existing_suburb
        # If suburb_result is None, we should have successfully updated/created a Suburb. Perform another query for it and return that one.
        if not suburb_result:
            suburb_result = await session.execute(find_suburb_stmt)
            suburb_result = suburb_result.scalar()
        # Either way, instantiate and return a suburb importation result.
        return SuburbImportResult(suburb_result,
            was_created = was_created, was_updated = was_updated)
    except Exception as e:
        # Instantiate a suburb import result, with this error.
        LOG.error(e, exc_info = True)
        return SuburbImportResult(None, e)


async def import_suburbs_from_placemarks(session, state_code, suburb_placemarks, **kwargs):
    """
    Given a state code and a list of placemark objects, each which represent a suburb, extract data about each suburb and ensure that suburb
    has been created and is also up to date.

    Arguments
    ---------
    :session: The Session for this state.
    :state_code: The primary key for the state to which this suburb belongs.
    :suburb_placemarks: A list of Placemark KML objects.

    Keyword arguments
    -----------------
    :transformer: The transformer to use for the projection of suburb geometries. By default, one will be created that projects SOURCE_COORDINATE_REF_SYS to COORDINATE_REF_SYS.

    Returns
    -------
    A list of SuburbImportResults.
    """
    try:
        transformer = kwargs.get("transformer", None)
        if not transformer:
            LOG.warning(f"No transformer given to import_suburbs_from_placemarks, we'll create one with from {config.SOURCE_COORDINATE_REF_SYS} to config spec {config.COORDINATE_REF_SYS}.")
            transformer = pyproj.Transformer.from_crs(config.SOURCE_COORDINATE_REF_SYS, config.COORDINATE_REF_SYS, always_xy = True)
        LOG.debug(f"Ensuring all suburbs for {state_code} imported & up to date... There are {len(suburb_placemarks)} to process.")
        async def ensure_suburb_created(placemark, transformer):
            # Get extended data from the placemark, convert this extended data to a dictionary for the keys and values.
            extended_data = dict((data.name, data.value) for data in placemark.extended_data.elements)
            # Get the reported lat and long to ensure uniqueness.
            coordinates_ = str(extended_data["Lat_precise"])+str(extended_data["Long_precise"])
            # Get the postcode.
            suburb_postcode = int(extended_data["postcode"])
            # Convert name to a title.
            suburb_name = placemark.name.title()
            # Produce a suburb hash by blake2b'ing the title suburb name, postcode state code and coordinates (from KML.)
            hash_input_data = (suburb_name+str(suburb_postcode)+state_code+coordinates_)
            suburb_hash = hashlib.blake2b(hash_input_data.encode("utf-8"), digest_size = 16).hexdigest().lower()
            suburb_multi_polygon = placemark.geometry
            # Create a version hash for this suburb, given the inputs.
            hash_input_data = (suburb_name+state_code).encode("utf-8")
            version_hash = hashlib.blake2b(hash_input_data, digest_size = 16).hexdigest().lower()
            # Now, perform the insert/update/return.
            return await import_suburb(session, state_code, suburb_hash, suburb_name, suburb_postcode, version_hash, suburb_multi_polygon,
                transformer = transformer)
        results = []
        for chunk in thirdparty.chunks(suburb_placemarks, 150):
            results.extend(await asyncio.gather(*[ ensure_suburb_created(placemark, transformer) for placemark in chunk ]))
        return results
    except Exception as e:
        raise e


class StateImportResult():
    @property
    def num_suburbs(self):
        return len(self.suburb_import_results)

    @property
    def state_gs(self):
        """Return a GeoSeries of all Suburb MultiPolygons."""
        return geopandas.GeoSeries([import_result.suburb.multi_polygon for import_result in self.suburb_import_results], crs = config.COORDINATE_REF_SYS)

    def __init__(self, _code, _name, _suburb_import_results, **kwargs):
        self.state_code = _code
        self.state_name = _name
        self.suburb_import_results = _suburb_import_results


async def import_state(session, states_absolute_path, state_kml_filename, **kwargs) -> StateImportResult:
    """
    Given an absolute path to the directory containing state KMLs, and a single state KML filename, perform an import of that state along with all its suburbs.

    Arguments
    ---------
    :session: The Session.
    :states_absolute_path: Absolute path to the directory containing the state KML file.
    :state_kml_filename: The filename of the state to be imported.

    Returns
    -------
    An instance of StateImportResult.
    """
    try:
        state_kml_absolute_path = os.path.join(states_absolute_path, state_kml_filename)
        # Read all contents from the state KML file.
        async with aiofiles.open(state_kml_absolute_path, "rb") as f:
            file_contents = await f.read()
        # Now, parse the state as KML.
        state_kml = kml.KML()
        # Populate from file contents.
        state_kml.from_string(file_contents)
        # Get the document from this KML instance.
        state_document = list(state_kml.features())[0]
        # Read code & name from document.
        state_code = state_document.id
        state_name = state_document.name
        # Apply a filter for only required state imports right here. If USE_ONLY_STATES has a non-zero length, ensure state_code is in that array.
        if len(config.USE_ONLY_STATES) > 0:
            if not state_code in config.USE_ONLY_STATES:
                LOG.debug(f"Skipping import of state '{state_name}' ({state_code}), it is not required by configuration.")
                return
        # For now, always skip Unknown.
        if state_code == "Unknown":
            LOG.debug(f"Skipping import of state '{state_name}' ({state_code}), it is not required by configuration.")
            return
        state_suburbs = list(state_document.features())
        LOG.debug(f"Beginning import of state {state_name} ({state_code}) with {len(state_suburbs)} suburbs...")
        # Ensure the state is created, we'll do this by upserting the state as it is.
        LOG.debug(f"Ensuring state {state_name} ({state_code}) is created...")
        # Attempt to get a centroid for this state.
        state_centroid = australian_state_centers.get(state_code, None)
        if not state_centroid:
            LOG.warning(f"Could not find a centroid for state with code '{state_code}'")
            state_centroid = (None, None,)
        # Now that we have the state centroid, which is a tuple in CRS 4326 and layout YX, we'll transform this point to the required CRS.
        source_crs = pyproj.crs.CRS.from_user_input(config.SOURCE_COORDINATE_REF_SYS)
        target_crs = pyproj.crs.CRS.from_user_input(config.COORDINATE_REF_SYS)
        transformer = pyproj.Transformer.from_crs(source_crs, target_crs, always_xy = True)
        state_center = transformer.transform(*state_centroid)
        # Create a dictionary that will contain the values to in/upsert.
        state_values_d = dict(
            state_code = state_code,
            name = state_name,
            crs = config.COORDINATE_REF_SYS
        )
        if config.POSTGIS_ENABLED:
            state_values_d["point_geom"] = shape.from_shape(geometry.Point(state_center), srid = config.COORDINATE_REF_SYS)
        else:
            state_values_d["point_geom"] = geometry.Point(state_center).wkb
        # Now, upsert the state.
        insert_state_stmt = (
            insert(models.State.__table__)
            .values(**state_values_d)
        ).on_conflict_do_nothing(index_elements = ["state_code"])
        # Execute to complete insert/upsert.
        await session.execute(insert_state_stmt)
        LOG.debug(f"State {state_name} ({state_code}) ensured created. Upserting all suburb coordinates now...")
        # Import all suburbs from this placemark list into this state.
        suburb_import_results = await import_suburbs_from_placemarks(session, state_code, state_suburbs,
            transformer = transformer)
        # Create a state import result.
        state_import_result = StateImportResult(state_code, state_name, suburb_import_results)
        # Now that we have a GeoSeries for all these suburbs, we can finally create the Statewide geometry.
        LOG.debug(f"Finished upserting all suburbs in state {state_code}, now finalising statewide geometry with {state_import_result.num_suburbs} suburbs...")
        state_multipoly = state_import_result.state_gs.unary_union
        state_multipoly = state_multipoly.simplify(config.STATEWIDE_SIMPLIFY_TOLERANCE)
        # Now buffer the multipoly.
        buffer_kw = dict(cap_style = geometry.CAP_STYLE.square, join_style = geometry.JOIN_STYLE.mitre)
        state_multipoly = state_multipoly.buffer(config.STATEWIDE_BUFFER_INTERVAL/2, **buffer_kw).buffer(-(config.STATEWIDE_BUFFER_INTERVAL/2), **buffer_kw)
        # If this is not a multipolygon, make it one.
        if not isinstance(state_multipoly, geometry.MultiPolygon):
            state_multipoly = geometry.MultiPolygon([state_multipoly])
        # Now, we'll update the state to own this geometry as well.
        if config.POSTGIS_ENABLED:
            update_state_d = dict(multi_polygon_geom = shape.from_shape(state_multipoly, srid = config.COORDINATE_REF_SYS))
        else:
            update_state_d = dict(multi_polygon_geom = state_multipoly.wkb)
        update_state_stmt = (
            update(models.State.__table__)
            .where(models.State.__table__.c.state_code == state_code)
            .values(**update_state_d)
        )
        await session.execute(update_state_stmt)
        return state_import_result
        return None
    except Exception as e:
        raise e


class ImportAllStatesResult():
    def __init__(self, _states_import_result, **kwargs):
        self.states_import_result = _states_import_result


async def import_all_states(states_absolute_path, state_kml_files, **kwargs) -> ImportAllStatesResult:
    """
    Given an absolute path to the directory containing all state KML files, and the list of KML filenames, execute an asynchronous import of all states
    and all suburbs within those states. This function will set its own async database connection up, toward whatever the current config requires. Note,
    this will not work with sqlite memory databases.

    Arguments
    ---------
    :states_absolute_path: An absolute path to the directory containing the state KML files.
    :state_kml_files: A list of filenames, each a state containing suburbs.

    Returns
    -------
    An instance of ImportAllStatesResult.
    """
    try:
        # Results list.
        import_state_results = []
        async with aio.open_db() as engine:
            async_session_factory = sessionmaker(engine,
                expire_on_commit = False, class_ = AsyncSession)
            async with aio.session_scope(async_session_factory) as session:
                # For each state file sequentially, await an import of it.
                for state_kml_file in state_kml_files:
                    import_state_result = await import_state(session, states_absolute_path, state_kml_file)
                    import_state_results.append(import_state_result)
                await session.commit()
        return ImportAllStatesResult(import_state_results)
    except Exception as e:
        raise e
