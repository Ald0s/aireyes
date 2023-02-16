"""
A module for performing CPU intensive geolocation functions with respect to flight data. Most of this should probably be used strictly from a background worker process.
"""
import re
import os
import time
import uuid
import asyncio
import decimal
import hashlib
import logging
import json
import geojson
import geopandas
import pyproj
from shapely import strtree, geometry, wkb
from fastkml import kml
from datetime import datetime, date, timedelta

from typing import List

from sqlalchemy.exc import OperationalError, UnsupportedCompilationError
from .compat import insert

from sqlalchemy import func, and_, or_, asc, desc
from sqlalchemy.exc import IntegrityError
from marshmallow import Schema, fields, EXCLUDE, post_load

from . import db, config, models, error, aiogeospatial, traces, calculations

# Conditional legacy from when PostGIS was the addon. Can be removed.
if config.POSTGIS_ENABLED:
    from geoalchemy2 import shape

LOG = logging.getLogger("aireyes.geospatial")
LOG.setLevel( logging.DEBUG )


# A list of non-overlapping CRSs to be used for the determination of the most appropriate to be used for a coordinate.
COORDINATE_REFERENCE_SYSTEMS = [
    ("Australia", 3112,)
]
# If not found in list above, this will be used.
DEFAULT_CRS = ("World", 3857,)


def locate_appropriate_crs_for_rectangle(polygon):
    # A list for all appropriate CRSs.
    appropriate_crs = []
    # Iterate each system info, and get their CRS from pyproj.
    for name, epsg in COORDINATE_REFERENCE_SYSTEMS:
        crs = pyproj.crs.CRS.from_user_input(epsg)
        # Now, build a Polygon geometry from the crs' bounds.
        area_of_use_poly = geometry.box(*crs.area_of_use.bounds)
        # Is there an intersection here? If so, add the name/epsg to the list.
        if polygon.intersects(area_of_use_poly):
            appropriate_crs.append((name, epsg,))
    # If there are no appropriate CRSs found, just return a list containing the default crs.
    if len(appropriate_crs) == 0:
        return [DEFAULT_CRS]
    # Otherwise, the appropriate ones.
    return appropriate_crs


def get_epsg_codes_for_polygon(polygon, crs) -> List[int]:
    # Get the CRS, through which this long, lat is projected.
    crs = pyproj.crs.CRS.from_user_input(crs)
    # Build a transformer from this CRS to the CRS' geodetic CRS.
    transformer = pyproj.Transformer.from_crs(crs, crs.geodetic_crs, always_xy = True)
    all_epsg_codes = []
    # Iterate polygon's exterior coords, and get the EPSG for each.
    for longitude, latitude in polygon.exterior.coords:
        # Add each EPSG to the overall list.
        code = calculations.epsg_code_for(longitude, latitude, crs, transformer = transformer)
        if not code in all_epsg_codes:
            all_epsg_codes.append(code)
    return all_epsg_codes


def get_epsg_codes_for(multi_polygon, crs) -> List[int]:
    # Get the CRS, through which this long, lat is projected.
    crs = pyproj.crs.CRS.from_user_input(crs)
    # Build a transformer from this CRS to the CRS' geodetic CRS.
    transformer = pyproj.Transformer.from_crs(crs, crs.geodetic_crs, always_xy = True)
    all_epsg_codes = []
    # Iterate each polygon in the multi polygon.
    for polygon in multi_polygon:
        # Iterate polygon's exterior coords, and get the EPSG for each.
        for longitude, latitude in polygon.exterior.coords:
            # Add each EPSG to the overall list.
            code = calculations.epsg_code_for(longitude, latitude, crs, transformer = transformer)
            if not code in all_epsg_codes:
                all_epsg_codes.append(code)
    return all_epsg_codes


def upsert_epsg_codes(epsg_codes):
    try:
        # Iterate each EPSG code, upserting.
        for epsg in epsg_codes:
            insert_epsg_stmt = (
                insert(models.UTMEPSG.__table__)
                .values(epsg = epsg)
            ).on_conflict_do_nothing(index_elements = ["epsg"])
            # Execute.
            db.session.execute(insert_epsg_stmt)
    except Exception as e:
        raise e


class GeospatialSuburbViewIntersection():
    """
    A container for a list of Suburb instances, that will automatically create an STRtree and facilitate queries for intersection by provided rectangular polygons.
    All suburbs will be stored as minimum rotated rectangles, calculated on the basis of their stored bounds data, making this class significantly quicker in the hopes
    that it can be called without consideration for overhead.
    """
    @property
    def all_source_suburbs(self):
        return self._all_suburbs

    @property
    def num_flight_points_ceiling(self):
        return self._highest_num_flight_points

    def __init__(self, _all_suburbs = None, **kwargs):
        """
        Initialise the instance.

        Arguments
        ---------
        :_all_suburbs: Optional. A list of Suburbs to be queried from. If not provided all suburbs will be queried.

        Keyword arguments
        -----------------
        :force_without_postgis: True if, regardless of PostGIS enabled status, our fallback calculations should be used. Default is False.
        """
        self._force_without_postgis = kwargs.get("force_without_postgis", False)

        # We must only perform the following setup if PostGIS is not available.
        if not config.POSTGIS_ENABLED or self._force_without_postgis:
            # Now, process given suburbs, or query from database.
            if not _all_suburbs:
                # Perform a query for all suburbs.
                _all_suburbs = db.session.query(models.Suburb).all()
                # Locate the highest number of flight points for any suburb. This defines the upper boundary for this suburb view.
                suburb_most_flight_points = db.session.query(
                    models.Suburb.suburb_hash,
                    models.Suburb.name,
                    func.coalesce(models.Suburb.num_flight_points, 0)
                )\
                .outerjoin(models.FlightPoint, models.FlightPoint.suburb_hash == models.Suburb.suburb_hash)\
                .group_by(models.Suburb.suburb_hash)\
                .order_by(desc(models.Suburb.num_flight_points))\
                .first()
                # If all suburbs is None, there are no associations between flight points and suburbs. Simply set highest to 0.
                if not _all_suburbs:
                    self._highest_num_flight_points = 0
                else:
                    self._highest_num_flight_points = suburb_most_flight_points[2]
                LOG.debug(f"Suburbs NOT provided to GeospatialSuburbViewIntersection, we will instead use all {len(_all_suburbs)} suburbs loaded.")
            else:
                # We have been provided with a suburbs list. We'll set our highest num flight points.
                # Sort the suburbs, num flight points descending.
                suburbs_descending = sorted(_all_suburbs, key = lambda suburb: suburb.num_flight_points, reverse = True)
                # Use the first point, which will be the suburb with the highest number of flight points.
                self._highest_num_flight_points = suburbs_descending[0].num_flight_points
                LOG.debug(f"Constructing GeospatialSuburbViewIntersection for {len(_all_suburbs)} suburbs.")
            # Make a tuple out of our suburbs list, to ensure its order and size is immutable.
            self._all_suburbs = tuple(_all_suburbs)
            # Now that we have a tuple of all suburbs to use, we'll develop a polygon on the basis of the bounding box for each.
            LOG.debug(f"Starting by building a minimum rotated polygon from each provided suburb...")
            self._all_suburbs_polygons = [ geometry.box(*suburb_.bbox) for suburb_ in self._all_suburbs ]
            # Now, create a lookup for a polygon's ID given its index. This index is relative to the _all_suburbs list.
            LOG.debug(f"Constructing suburb polygon identity dict for {len(self._all_suburbs)}...")
            self._index_by_id = dict((id(polygon), idx) for idx, polygon in enumerate(self._all_suburbs_polygons))
            # Now setup the STRtree.
            LOG.debug(f"Setting up an STRtree for all suburb polygons...")
            self._suburb_strtree = strtree.STRtree(self._all_suburbs_polygons)

    def locate_suburbs_within_view(self, view_polygon, crs, **kwargs):
        if config.POSTGIS_ENABLED and not self._force_without_postgis:
            # PostGIS is available, we'll instead launch a query to locate all intersections with the given polygon.
            intersecting_suburbs = db.session.query(models.Suburb)\
                .filter(func.ST_Intersects(models.Suburb.multi_polygon_geom, shape.from_shape(view_polygon, srid = crs)))\
                .all()
        else:
            # Simply perform a query for the STRtree to locate all intersecting suburbs, given the rectangular polygon.
            insersecting_polygons = [ (self._index_by_id[id(polygon)], polygon) for polygon in self._suburb_strtree.query(view_polygon) ]
            # Convert these polygons back to their neighbour equivalents.
            intersecting_suburbs = [ self._all_suburbs[insersecting_polygon[0]] for insersecting_polygon in insersecting_polygons ]
        # Simply return intersecting suburbs.
        return intersecting_suburbs


class SuburbsToGeoJson():
    """A type for converting Suburb instances to GeoJSON, given a source and target CRS."""
    def __init__(self, source_crs, target_crs, **kwargs):
        self._show_only_aircraft = kwargs.get("show_only_aircraft", "all")

        # If this is a list, get the icaos from each aircraft; that becomes our filter.
        if isinstance(self._show_only_aircraft, list):
            self._show_only_aircraft = [ aircraft[0] for aircraft in db.session.query(models.Aircraft.icao)\
                .filter(models.Aircraft.flight_name.in_(self._show_only_aircraft))\
                .all() ]
        elif self._show_only_aircraft != "all":
            LOG.error(f"Invalid argument given to SuburbsToGeoJson for show_only_aircraft; {self._show_only_aircraft}")
            raise Exception(f"Invalid argument given to SuburbsToGeoJson for show_only_aircraft; {self._show_only_aircraft}")

        # Ensure source and target CRSs are valid.
        if not source_crs:
            LOG.error(f"SuburbsToGeoJson requires a source CRS be passed!")
            raise error.InvalidCRSError("suburbs-to-geojson-no-source-crs")
        elif not target_crs:
            LOG.error(f"SuburbsToGeoJson requires a target CRS be passed!")
            raise error.InvalidCRSError("suburbs-to-geojson-no-target-crs")
        # Ensure both are CRS objects.
        if not isinstance(source_crs, pyproj.crs.CRS):
            source_crs = pyproj.crs.CRS.from_user_input(source_crs)
        if not isinstance(target_crs, pyproj.crs.CRS):
            target_crs = pyproj.crs.CRS.from_user_input(target_crs)
        # Set class vars.
        self._source_crs = source_crs
        self._target_crs = target_crs

    def get_geojson(self, suburbs, **kwargs):
        dump = kwargs.get("dump", True)

        # Now, build a GeoJSON feature out of each suburb in the result.
        LOG.debug(f"Constructing GeoJSON response for all {len(suburbs)} found in view...")
        """
        TODO: this is where everything will slow down.
        In order to remedy this, we should alleviate the strain caused by reading & creating GeoJSON objects for all suburbs. One way to do this would be to (as our suburb source does,) store each suburb in a separate GeoJSON file
        post import, then, when ready, this function will instead return an nginx redirect to all applicable GeoJSON physical files along with supplementary style data to be somehow joined either on server level or client level.

        For now, and for testing purposes, we will simply create a feature collection each time.
        """
        # Transform all suburbs to the appropriate output CRS.
        transformed_geometries = self._transform_suburbs(suburbs)
        # Construct a feature from each suburb within view.
        all_features = []
        for suburb, multi_polygon in zip(suburbs, transformed_geometries):
            # Build properties for this suburb.
            properties = self._build_properties_for(suburb)
            # Create the feature object.
            feature = geojson.Feature(
                id = suburb.suburb_hash,
                properties = properties,
                geometry = geojson.MultiPolygon([[list(polygon.exterior.coords)] for polygon in multi_polygon.geoms])
            )
            # Add to features.
            all_features.append(feature)
        # Build a feature collection from all these features.
        feature_collection = geojson.FeatureCollection(all_features)
        if dump:
            # Dump and return the result, do not pretty print, to save bandwidth.
            return geojson.dumps(feature_collection, sort_keys = False)
        return feature_collection

    def _transform_suburbs(self, suburbs):
        # Build a GeoSeries from all geodetic multi polygons from these suburbs, setting CRS to the source CRS.
        suburbs_geoseries = geopandas.GeoSeries([suburb.multi_polygon for suburb in suburbs], crs = self._source_crs)
        # Now, transform to the target CRS.
        suburbs_geoseries = suburbs_geoseries.to_crs(self._target_crs)
        # Finally, return a list of all geometries.
        return list(suburbs_geoseries.geometry)

    def _build_properties_for(self, suburb):
        num_flight_points = self._get_num_flight_points(suburb)
        # Construct a properties dictionary.
        properties = {
            "name": suburb.name,
            "num_points": num_flight_points
        }
        return properties

    def _get_num_flight_points(self, suburb):
        # If show only aircraft is 'all', just return the number of flight points.
        if self._show_only_aircraft == "all":
            return suburb.num_flight_points
        # Otherwise, perform a new query that joins and counts the number of flight points, but only where the aircraft responsible appears in the show only aircraft list.
        return db.session.query(func.count(models.FlightPoint.flight_point_hash))\
            .filter(models.FlightPoint.suburb_hash == suburb.suburb_hash)\
            .filter(models.FlightPoint.aircraft_icao.in_(self._show_only_aircraft))\
            .scalar()


def geojson_suburbs_within_view(crs, bounding_box_extent, zoom, **kwargs):
    """
    Return GeoJSON for all suburbs in the given bounding box view.

    Arguments
    ---------
    :crs: The CRS the given bounding box is projected through. It can be expected that returned data is also in this CRS.
    :bounding_box_extent: The bounding box for the client's view, in XY format.
    :zoom: The zoom level for the client's view.

    Keyword arguments
    -----------------
    :show_only_aircraft: A list of flight names to restrict the return values to. If not given, all will be used.
    :should_dump: True if this function should return JSON. False if a FeatureCollection should be returned. Default is True.

    Returns
    -------
    GeoJSON formatted data for all applicable suburbs, and their coordinates at the applicable detail level.
    """
    try:
        show_only_aircraft = kwargs.get("show_only_aircraft", "all")
        should_dump = kwargs.get("should_dump", True)

        # Get the source CRS; the user's view input is projected through this.
        source_crs = pyproj.crs.CRS.from_user_input(crs)
        # Get the target CRS; this is what we will project the user inputs through to perform calculations.
        target_crs = pyproj.crs.CRS.from_user_input(config.COORDINATE_REF_SYS)
        # If source and target do not match, we will need to transform.
        if source_crs != target_crs:
            # Transform the values in bbox to match our current CRS.
            transformer = pyproj.Transformer.from_crs(source_crs, target_crs, always_xy = True)
            bounding_box_extent = transformer.transform_bounds(*bounding_box_extent)
        # First, produce a Polygon from the bounding box extent.
        view_polygon = geometry.box(*bounding_box_extent)
        # Instantiate our suburb view intersection.
        suburb_container = GeospatialSuburbViewIntersection()
        # Locate all suburbs within this view.
        located_suburbs = suburb_container.locate_suburbs_within_view(view_polygon, config.COORDINATE_REF_SYS)
        # Now instantiate a suburbs to geojson instance. Remember, we want to SWAP the CRSs here, as we wish to transform the located suburbs back to an appropriate
        # CRS for the user's input.
        suburbs_to_geojson = SuburbsToGeoJson(target_crs, source_crs,
            show_only_aircraft = show_only_aircraft)
        # Return the GeoJSON for this view.
        return suburbs_to_geojson.get_geojson(located_suburbs, dump = should_dump)
    except Exception as e:
        raise e


class GeospatialFlightPointLocationResult():
    @property
    def seconds_taken(self):
        """Returns the total seconds taken to geolocate this result. If either is None, None is returned."""
        if not self._started or not self._ended:
            return None
        return int(self._ended-self._started)

    @property
    def num_flight_points(self):
        return self._num_flight_points

    @property
    def flight_points(self):
        return self._flight_points_tuple

    @property
    def num_geolocated(self):
        return len(list(filter(lambda outcome: outcome["was_successful"] == True, self._result_dict.values())))

    @property
    def num_overwritten_geolocated(self):
        return len(list(filter(lambda outcome: outcome["was_skipped"] == True, self._result_dict.values())))

    @property
    def num_skipped(self):
        return len(list(filter(lambda outcome: outcome["was_skipped"] == True, self._result_dict.values())))

    @property
    def num_error(self):
        return len(list(filter(lambda outcome: not outcome["was_successful"] and not outcome["was_skipped"], self._result_dict.values())))

    @property
    def outcome_dictionary(self):
        return self._result_dict

    def __init__(self, _flight_points_tuple, _crs):
        self._crs = _crs
        self._num_flight_points = len(_flight_points_tuple)
        self._flight_points_tuple = _flight_points_tuple
        # Build the dictionary itself, key will be the flight point, and value will (for now) be an empty dictionary.
        self._result_dict = dict((flight_point, {}) for flight_point in self._flight_points_tuple)
        # Time started and ended.
        self._started = None
        self._ended = None

    def set_result_for(self, flight_point, result_dict):
        if not flight_point in self._result_dict:
            raise Exception("no-flight-point-in-result")
        self._result_dict[flight_point] = result_dict

    def set_started(self):
        self._started = time.time()
        self._ended = None

    def set_ended(self):
        self._ended = time.time()


class GeospatialFlightPointLocator():
    """A class that facilitates the geolocation of one or more flight points."""
    @property
    def last_result(self):
        """Return the last created location result. This will raise an exception if one does not exist, or the flight points have not yet been geolocated."""
        if not self._result or not self._flight_points_prepared or not self._geolocation_complete:
            LOG.error(f"Failed to get result from locator {self}, there is no result, or existing flight points have not yet been geolocated.")
            raise error.FlightPointsGeolocatorError("no-last-result", flight_points = self._flight_points)
        return self._result

    @property
    def _current_flight_point(self):
        return self._flight_points[self._glob_index]

    @property
    def _current_point_geometry(self):
        return self._points_geometry[self._glob_index]

    def __init__(self, _flight_points = None, **kwargs):
        """
        Initialise the locator, optionally with a list of flight points, in no particular order.
        Optionally, provide location evidence to speed up the search.

        Arguments
        ---------
        :_flight_points: Optional. Either a single instance or list of instances of FlightPoint to geolocate.

        Keyword arguments
        -----------------
        :crs: An EPSG code through which all flight points given are projected.
        :last_suburb: The suburb to use as the base of searching.
        :overwrite_existing: If flight points already have a suburb and this is False, skip, otherwise, recalculate. Default is False.
        :force_without_postgis: True if, regardless of PostGIS enabled status, our fallback calculations should be used. Default is False.
        """
        crs = kwargs.get("crs", None)
        last_suburb = kwargs.get("last_suburb", None)
        overwrite_existing = kwargs.get("overwrite_existing", False)
        self._force_without_postgis = kwargs.get("force_without_postgis", False)

        # Create an empty class level variable for the CRS.
        self._crs = crs
        # Create an empty result variable.
        self._result = None
        # A boolean, whether or not there is a set of flight points prepared.
        self._flight_points_prepared = False
        # A boolean, whether or not a geolocation has been completed.
        self._geolocation_complete = False
        # If we were given flight points, prepare them.
        if _flight_points:
            self.prepare_flight_points(_flight_points)
        # Make a map for Suburb instances to suburb polygons, so we don't have to reconstruct polygons that have already been created.
        self._suburb_polygon_map = {}
        # Make a map for State to state containers.
        self._states = {}
        # Extract evidence from keyword arguments.
        self._last_suburb = last_suburb or None
        self._last_state = last_suburb.state if last_suburb else None
        self._overwrite_existing = overwrite_existing

    def prepare_flight_points(self, flight_points, **kwargs):
        """
        Prepare this instance for the geolocation of a new set of flight points.

        Keyword arguments
        -----------------
        :crs: An EPSG code through which all flight points are projected. By default, None.
        :force: True if an existing prepared, but not geolocated list of flight points should be overwritten by these flight points. False to raise an exception. Default is False.
        """
        crs = kwargs.get("crs", None)
        force = kwargs.get("force", False)

        if (self._flight_points_prepared and not self._geolocation_complete) and not force:
            LOG.error(f"Failed to prepare flight points of count {len(flight_points)}, there is a waiting list of flight points that have NOT been geolocated. Pass 'force' to override this check.")
            raise error.FlightPointsGeolocatorError("flight-points-already-prepared", flight_points = flight_points)
        # Ensure flight points is a list, making one if not.
        if not isinstance(flight_points, list):
            flight_points = [flight_points]
        # If there are no flight points given, raise an error.
        if not len(flight_points):
            LOG.error(f"Failed to prepare flight points for geolocation, no flight points were given!")
            raise error.FlightPointsGeolocatorError("no-flight-points-in-list", flight_points = flight_points)
        # A global index, that can refer to the same item in both the flight points list and the points geometry. This will increment as each point is located.
        self._glob_index = 0
        # Create an ordered tuple of flight points by their timestamp, ascending.
        self._flight_points = tuple(sorted(flight_points, key = lambda flight_point: flight_point.timestamp))
        # The number of points.
        self._num_flight_points = len(self._flight_points)
        # Now, we must found a common CRS among all flight points, if class level CRS is not given. Before this though, update our class level CRS with the one provided, as long as it is not None.
        if crs != None:
            self._crs = crs
        # If class CRS is still None, we will now locate a common one manually.
        if not self._crs:
            LOG.warning(f"No CRS passed to GeospatialFlightPointLocator::prepare_flight_points(), manually finding a common one...")
            self._find_common_crs()
            if not self._crs:
                LOG.error(f"Failed to find common CRS, even still!")
                raise error.InvalidCRSError("GeospatialFlightPointLocator-flight-point-no-crs")
        # Based on this flight points tuple, we'll now generate a Point geometry for each, and save that to a tuple as well.
        #self._points_geometry = tuple([ geometry.Point(flight_point.longitude, flight_point.latitude) for flight_point in self._flight_points ])
        self._points_geometry = tuple([ flight_point.position for flight_point in self._flight_points ])
        # Initialise a new result object for this set of flight points.
        self._result = GeospatialFlightPointLocationResult(self._flight_points, self._crs)
        # Set geolocation complete to False and flight points prepared to True.
        self._geolocation_complete = False
        self._flight_points_prepared = True
        LOG.debug(f"Flight points of length {self._num_flight_points} prepared for geolocation successfully.")

    def geolocate_all(self, flight_points = None, **kwargs) -> GeospatialFlightPointLocationResult:
        """
        Begin the procedure of geolocating all flight points in our flight points tuple, by their position in the points geometry. We'll do this in steps, relative to the evidence available.
        This function will iterate each flight point given. For each given, the last suburb will be used to determine whether the flight point is actually in that suburb, or any of its neighbours.
        Failing this check, the last suburb will be cleared and the flight point's UTM EPSG will be determined, and used to find all potential suburb in which the point may belong; this list is then
        searched for the best match.

        But, on each level, should a check experience success, its parameters will be saved as the new overriding evidence for the next index in flight points. For example, should a check for a flight
        point in the last suburb fail, but a check for it in one of the last suburb's neighbours succeed, that neighbour will be saved as the new last suburb, and so on.

        Arguments
        ---------
        :flight_points: Optional. Either a single instance, or a list of FlightPoint instances to geolocate.

        Returns
        -------
        An instance of GeospatialFlightPointLocationResult.
        """
        # If we were given a new list of flight points, prepare those.
        if flight_points != None:
            self.prepare_flight_points(flight_points)
        # If we have PostGIS enabled, we will employ measures to geolocate that way instead.
        if config.POSTGIS_ENABLED and not self._force_without_postgis:
            return self._geolocate_via_postgis()
        # Set the result started.
        self._result.set_started()
        # Otherwise, we'll default back to our classic algorithm.
        for flight_point_idx, flight_point in enumerate(self._flight_points):
            try:
                # Constantly update flight point index.
                self._glob_index = flight_point_idx
                # If the current flight point already HAS a suburb, and overwriting is false, we can skip. But prior to skipping, we'll save this point's suburb as last suburb.
                if self._current_flight_point.suburb and not self._overwrite_existing:
                    LOG.debug(f"Skipping geolocation of {self._current_flight_point}, suburb is already set and we have not been instructed to overwrite.")
                    self._last_suburb = self._current_flight_point.suburb
                    self._last_state = self._last_suburb.state
                    continue
                # Now, setup some data per each flight point.
                suburb_point_in = None
                # If we have a last suburb, check whether this point is in that suburb, or whether this point is in a neighbour of that suburb. Receive back the suburb in which the point is in; set suburb_point_in.
                if self._last_suburb:
                    LOG.debug(f"Attempting to locate suburb for {self._current_flight_point} from last suburb {self._last_suburb}")
                    suburb_point_in = self._exact_suburb_containing(self._last_suburb)
                    if suburb_point_in:
                        LOG.debug(f"Successfully located suburb for {self._current_flight_point} by using last suburb {self._last_suburb}")
                    else:
                        LOG.debug(f"Failed to find suburb for {self._current_flight_point} by using last suburb {self._last_suburb}")
                # If suburb point in is None, but we have a last state, locate all potential suburbs given an EPSG, filtered by the last state. Receive back the suburb in which the point is in; set suburb_point_in.
                if not suburb_point_in and self._last_state:
                    LOG.debug(f"Attempting to locate suburb for {self._current_flight_point} from last state {self._last_state}")
                    suburb_point_in = self._exact_suburb_by_epsg(state_code = self._last_state.state_code)
                    if suburb_point_in:
                        LOG.debug(f"Successfully located suburb for {self._current_flight_point} by using last state {self._last_state}")
                    else:
                        LOG.debug(f"Failed to find suburb for {self._current_flight_point} by using last state {self._last_state}")
                # If suburb point in is None, perform a state-wide (all states) search for the closest state, then closest suburb, then the suburb the point is actually in. Receive back the suburb in which the point is in; set suburb_point_in.
                if not suburb_point_in:
                    LOG.debug(f"Attempting to locate suburb for {self._current_flight_point} from nowhere (no state bias...)")
                    suburb_point_in = self._exact_suburb_by_epsg()
                    if suburb_point_in:
                        LOG.debug(f"Successfully located suburb for {self._current_flight_point} from nowhere!!")
                    else:
                        LOG.debug(f"Failed to find suburb for {self._current_flight_point} from nowhere. Are you sure geospatial data for suburbs is imported?")
                # Now, upon success, we will attach suburb_point_in to the current flight point, then continue.
                # Otherwise, if we weren't able to locate the point, we should either kill this process, or continue; depending on config.
                if suburb_point_in:
                    LOG.debug(f"Attaching suburb for {self._current_flight_point} to suburb {suburb_point_in}")
                    self._current_flight_point.suburb = suburb_point_in
                    continue
                else:
                    # Raise search exhausted.
                    raise error.SuburbSearchExhausted("search-exhausted")
            except error.SuburbSearchExhausted as sse:
                LOG.error(f"Unable to find suburb for {self._current_flight_point}. Coordinates; {self._current_flight_point.geodetic_point}")
                # Save a summary of this failure to the result.
                self._report_failed_flight_point_result(coordinates = self._current_flight_point.geodetic_point)
                # For now, we'll just continue.
                continue
        # Now, we'll print some debug information, or assemble a receipt object.
        self._result.set_ended()
        self._geolocation_complete = True
        LOG.debug(f"Completed flight point geolocation for {self._num_flight_points}!")
        # We'll return our current result.
        return self._result

    def _exact_suburb_by_epsg(self, **kwargs):
        """
        Searches for the current point in the given state. This will first determine a UTM EPSG for the current point geometry, and will then query all suburbs that are associated
        with this EPSG, filtered by the given state. Then, each potential suburb will be iterated and checked for whether the current point belongs or not.
        """
        try:
            state_code = kwargs.get("state_code", None)
            # First, determine EPSG for the current point geometry.
            epsg = calculations.epsg_code_for(*self._current_point_geometry.coords[0], self._crs)
            # Now, collect all potential suburbs based on this EPSG, providing the state's code for further filtering.
            potential_suburbs = self._find_potential_suburbs_by_epsg(epsg, state_code = state_code)
            # Now, iterate each potential suburb, checking whether any of them contains the current point.
            for potential_suburb in potential_suburbs:
                if self._does_suburb_contain_point(potential_suburb):
                    # Get the exact resulting suburb now, and set the result for this flight point to 'state'
                    self._report_successful_flight_point_result(found_from = "state-epsg")
                    self._last_state = potential_suburb.state
                    self._last_suburb = potential_suburb
                    return potential_suburb
            return None
        except error.NoSuburbFoundError as nsfe:
            # Set last state to None and return None.
            self._last_state = None
            return None
        except Exception as e:
            raise e

    def _exact_suburb_containing(self, suburb):
        """
        Searches for the current point in the given suburb, then searches for the point in each neighbour to the given suburb, if the initial search isn't successful.
        On success, the located suburb is returned and the last suburb is updated to point toward this located suburb, as well as the last state to point to this suburb's state.
        On failure, the last suburb is set to None, the last state is retained.
        """
        # A variable for the resulting suburb.
        resulting_suburb = None
        if self._does_suburb_contain_point(suburb):
            # This suburb does contain the current point.
            LOG.debug(f"Determined that {self._current_flight_point} is contained by {suburb}, which is the primary suburb passed to _exact_suburb_containing!")
            if suburb == self._last_suburb:
                self._report_successful_flight_point_result(found_from = "exact-last-suburb")
            else:
                self._report_successful_flight_point_result(found_from = "exact-suburb")
            resulting_suburb = suburb
        else:
            # The target suburb may be a neighbour for the given suburb. Attempt to locate this now with a loop.
            for neighbour_suburb in suburb.neighbours:
                if self._does_suburb_contain_point(neighbour_suburb):
                    LOG.debug(f"Determined that {self._current_flight_point} is contained by {neighbour_suburb}, which is a neighbour of primary suburb {suburb}")
                    self._report_successful_flight_point_result(found_from = "neighbour-last-suburb")
                    resulting_suburb = neighbour_suburb
                    break
        # Set last suburb & last state.
        self._last_suburb = resulting_suburb
        # Return result.
        return resulting_suburb

    def _does_suburb_contain_point(self, suburb):
        # Attempt to get the suburb polygon from our existing polygon map. If it does not exist, then construct it.
        suburb_polygon = self._suburb_polygon_map.get(suburb, None)
        if not suburb_polygon:
            suburb_polygon = suburb.multi_polygon
            # Store this suburb polygon.
            self._suburb_polygon_map[suburb] = suburb_polygon
        # Now, simply call contains with our current point geometry.
        return suburb_polygon.contains(self._current_point_geometry)

    def _find_potential_suburbs_by_epsg(self, epsg, **kwargs):
        state_code = kwargs.get("state_code", None)
        potential_suburb_q = db.session.query(models.Suburb)\
            .join(models.SuburbUTMEPSG, models.SuburbUTMEPSG.suburb_hash == models.Suburb.suburb_hash)\
            .filter(models.SuburbUTMEPSG.utmepsg_epsg == epsg)
        if state_code:
            potential_suburb_q = potential_suburb_q\
                .filter(models.Suburb.state_code == state_code)
        return potential_suburb_q.all()

    def _geolocate_via_postgis(self):
        def find_state_for(flight_point):
            return db.session.query(models.State)\
                .filter(func.ST_Contains(models.State.multi_polygon_geom, shape.from_shape(flight_point.position, srid = self._crs)))\
                .first()

        def find_suburb_for(state, flight_point):
            return db.session.query(models.Suburb)\
                .filter(models.Suburb.state_code == state.state_code)\
                .filter(func.ST_Contains(models.Suburb.multi_polygon_geom, shape.from_shape(flight_point.position, srid = self._crs)))\
                .first()

        # Set the result started.
        self._result.set_started()
        # Variable to hold state last located, as a hint.
        last_located_state = None
        last_located_suburb = None
        for flight_point_idx, flight_point in enumerate(self._flight_points):
            try:
                # Constantly update flight point index.
                self._glob_index = flight_point_idx
                # If the current flight point already HAS a suburb, and overwriting is false, we can skip. But prior to skipping, we'll save this point's suburb as last suburb.
                if self._current_flight_point.suburb and not self._overwrite_existing:
                    LOG.debug(f"Skipping geolocation of {self._current_flight_point}, suburb is already set and we have not been instructed to overwrite.")
                    continue
                # If current flight point does not have a position, fail this point and continue.
                if not flight_point.position:
                    LOG.debug(f"Skipping geolocation of {self._current_flight_point}, no flight point position set.")
                    raise error.SuburbSearchExhausted("flight-point-without-position")
                # Now, setup some data per each flight point.
                suburb_point_in = None
                # If we have a last suburb, check whether the point is in there.
                if last_located_suburb and self._does_suburb_contain_point(last_located_suburb):
                    suburb_point_in = last_located_suburb
                else:
                    last_located_suburb = None
                    # If we have no last state, find one now.
                    if not last_located_state:
                        last_located_state = find_state_for(flight_point)
                        # If no state could be located, it perhaps has not been imported, or the aircraft is over the sea.
                        if not last_located_state:
                            raise error.SuburbSearchExhausted("not-in-any-state")
                    # If we have a last located state, attempt to locate the flight point in any suburb in that state.
                    suburb_point_in = find_suburb_for(last_located_state, flight_point)
                    if not suburb_point_in:
                        # If we were not able to find the suburb in that state, attempt to look for a new state.
                        last_located_state = find_state_for(flight_point)
                        if not last_located_state:
                            # No longer a state, the aircraft has perhaps since flown over sea or into a state not imported.
                            raise error.SuburbSearchExhausted("not-in-any-state-anymore")
                        # Finally, attempt to find flight point in latest located state.
                        suburb_point_in = find_suburb_for(last_located_state, flight_point)
                        if not suburb_point_in:
                            # Aircraft is in this state, but no suburb could be established. Is the suburb itself imported?
                            raise error.SuburbSearchExhausted("in-state-no-suburb")
                # Now, upon success, we will attach suburb_point_in to the current flight point, then continue.
                # Otherwise, if we weren't able to locate the point, we should either kill this process, or continue; depending on config.
                if suburb_point_in:
                    last_located_suburb = suburb_point_in
                    LOG.debug(f"Attaching suburb for {self._current_flight_point} to suburb {suburb_point_in}")
                    self._current_flight_point.suburb = suburb_point_in
                    self._report_successful_flight_point_result(found_from = "postgis")
                    continue
                # Raise search exhausted.
                raise error.SuburbSearchExhausted("search-exhausted")
            except error.SuburbSearchExhausted as sse:
                LOG.error(f"Unable to find suburb for {self._current_flight_point}. Coordinates; {self._current_flight_point.geodetic_point} Reason {sse.error_code}")
                # Save a summary of this failure to the result.
                self._report_failed_flight_point_result(coordinates = self._current_flight_point.geodetic_point)
                # For now, we'll just continue.
                continue
        # Now, we'll print some debug information, or assemble a receipt object.
        self._result.set_ended()
        self._geolocation_complete = True
        LOG.debug(f"Completed flight point geolocation for {self._num_flight_points}!")
        # We'll return our current result.
        return self._result

    def _find_common_crs(self):
        # Variable for holding an EPSG code for the common CRS among all flight points. If any are different, this class will raise an exception for now.
        self._crs = None
        common_crs = None
        # Iterate all flight points.
        for flight_point in self._flight_points:
            if not flight_point.crs:
                LOG.error(f"TODO")
                raise NotImplementedError("implement errors on FlightPointsManager()!")
                raise Exception("flight-point-no-crs")
            # Otherwise, if flight point has a CRS and common CRS is None, set common CRS and continue.
            if not common_crs:
                common_crs = flight_point.crs
                continue
            # Finally, if we've gotten to this point, ensure flight point's CRS matches the common CRS. If not, raise an exception.
            if common_crs != flight_point.crs:
                LOG.error(f"TODO")
                raise NotImplementedError("implement errors on FlightPointsManager()!")
                raise Exception("flight-point-crs-mismatch")
        # Finally, set the instance level common CRS to this CRS.
        self._crs = common_crs

    def _report_successful_flight_point_result(self, overwritten = False, **kwargs):
        """
        Enter a successful report for this current flight point.
        Provide all the same keyword arguments as you would for the default report function.
        """
        # Overwritten is equal to the instance level _overwrite_existing
        overwritten = self._overwrite_existing
        self._report_flight_point_result(True, overwritten = overwritten, **kwargs)

    def _report_skipped_flight_point_result(self, reason = None, **kwargs):
        """
        Enter a skipped report for this current flight point.
        Provide all the same keyword arguments as you would for the default report function.
        """
        self._report_flight_point_result(False, was_skipped = True, **kwargs)

    def _report_failed_flight_point_result(self, **kwargs):
        """
        Enter a failed report for this current flight point.
        Provide all the same keyword arguments as you would for the default report function.
        """
        self._report_flight_point_result(False, **kwargs)

    def _report_flight_point_result(self, was_successful, **kwargs):
        """
        Enter a report under this flight point's index into the result object, describing the outcome of the processing method for that point and
        its outcome, what can be improved etc. If no arguments are given, the result will just be a dictionary with None for all items.

        Arguments
        ---------
        :was_successful: Could we successfully geolocate this point.

        Keyword arguments
        -----------------
        :found_from: A textual indication of the exact methodological source for this flight point's geolocation; 'exact-suburb', 'exact-last-suburb', 'neighbour-last-suburb', 'state', 'nowhere' or 'not-set'.
        :overwritten: Whether this flight point's suburb was overwritten. Default is False.
        :was_skipped: A boolean; whether this flight point was skipped. Default is False.
        :coordinates: A tuple, containing a latitude & longitude.
        """
        found_from = kwargs.get("found_from", "not-set")
        overwritten = kwargs.get("overwritten", False)
        was_skipped = kwargs.get("was_skipped", False)
        coordinates = kwargs.get("coordinates", None)

        # Set the result. This will overwrite any previous results.
        self._result.set_result_for(self._current_flight_point, dict(
            was_successful = was_successful,
            was_skipped = was_skipped,
            overwritten = overwritten,
            methodology = found_from,
            coordinates = coordinates
        ))


class FlightPointGeolocationReceipt():
    @property
    def time_taken(self):
        return int(self.timestamp_finished-self.timestamp_started)

    @property
    def started(self):
        return datetime.fromtimestamp(self.timestamp_started).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def finished(self):
        return datetime.fromtimestamp(self.timestamp_finished).strftime("%Y-%m-%d %H:%M:%S")

    def __init__(self, _timestamp_started, _aircraft_present_day, _num_geolocated, _num_overwritten_geolocated, _num_skipped, _num_error, **kwargs):
        self.timestamp_started = _timestamp_started
        self.timestamp_finished = time.time()
        self.aircraft_present_day = _aircraft_present_day
        self.num_geolocated = _num_geolocated
        self.num_overwritten_geolocated = _num_overwritten_geolocated
        self.num_skipped = _num_skipped
        self.num_error = _num_error


def revise_geolocation_for(aircraft, day, **kwargs) -> FlightPointGeolocationReceipt:
    """
    This function will revise all flight points geolocation data for the given aircraft present day. This essentially will ensure that all flight points within this aircraft/day
    actually has a suburb associated with it. Failures to locate suburbs for any flight points will be ignored and the aircraft/day will have its geolocation verified anyway. This
    function requires that the aircraft/day have a verified trace data. This function also requires that geolocation must NOT be already verified. Both of these requirements may be
    overruled by supplying the 'force' keyword argument as True.

    This function can be very inefficient, and is designed to be called only by background workers such as Celery. Upon completion, this function will set this aircraft present day's
    geolocation verified attribute to True.

    Arguments
    ---------
    :aircraft: The aircraft whose flight points we should geolocate.
    :day: A Date instance representing the day to use as the base reference for the flight points to geolocate.

    Keyword arguments
    -----------------
    :force: Whether history_verified/geolocation_verified should be ignored. Default is False.
    :locator: Optional. An instance of GeospatialFlightPointLocator to use for this process. By default, one is created.

    Returns
    -------
    An instance of FlightPointGeolocationReceipt.
    """
    started = time.time()

    try:
        force = kwargs.get("force", False)
        locator = kwargs.get("locator", None)

        # Ensure we have an AircraftPresentDay to represent this aircraft/day junction. By default, set history & flights verified to False.
        # This also means that if this junction was just created, this function's execution can not continue, as history must be verified prior to flights verification, unless force is True.
        aircraft_present_day, was_created = traces.ensure_aircraft_day_junction_exists(aircraft, day,
            history_verified = False, flights_verified = False, geolocation_verified = False)
        # If we weren't able to create one, raise an exception; this is an unknown error case.
        if not aircraft_present_day:
            LOG.error(f"Failed to geolocate flight points for aircraft {aircraft} on {day}, we were not able to ensure aircraft/day junction exists!")
            raise Exception("no-junction")
        elif not aircraft_present_day.history_verified and not force:
            # If history verified is False, we will raise an exception requiring flight history revision.
            LOG.error(f"Failed to geolocate flight points for aircraft {aircraft} on {day}, history data must first be verified!")
            raise error.FlightDataRevisionRequired(aircraft, day, requires_history = True)
        elif aircraft_present_day.geolocation_verified and not force:
            # Flight point geolocation is already verified on this day, and so we will not execute this twice.
            LOG.error(f"Failed to geolocate flight points for aircraft {aircraft} on {day}, flight points have already been geolocated, and the 'force' attribute was not provided!")
            raise error.FlightPointsGeolocatedError(aircraft_present_day)
        LOG.debug(f"Commencing geolocation of flight points for {aircraft_present_day}")
        # We will set geolocation_verified to False at this point.
        aircraft_present_day.geolocation_verified = False
        # Begin by getting all flight points from that aircraft/day.
        day_flight_points = aircraft_present_day.all_flight_points
        LOG.debug(f"Located {len(day_flight_points)} on {aircraft_present_day} to geolocate.")
        # Now, if we do not yet have a locator, we'll create one. Otherwise, we'll use the existing one we've been given.
        if not locator:
            locator = GeospatialFlightPointLocator()
        try:
            # Now use the locator to geolocate all flight points we've just been given.
            geolocation_result = locator.geolocate_all(day_flight_points)
            LOG.debug(f"Geolocation of suburbs for flight points on {aircraft_present_day} was successful!")
            # Since this was completed successfully, we can set geolocation_verified True.
            aircraft_present_day.geolocation_verified = True
            # Return a receipt.
            return FlightPointGeolocationReceipt(started, aircraft_present_day,
                geolocation_result.num_geolocated, geolocation_result.num_overwritten_geolocated, geolocation_result.num_skipped, geolocation_result.num_error)
        except error.FlightPointsGeolocatorError as fpge:
            if fpge.error_code == "no-flight-points-in-list":
                # Simply return a None.
                return FlightPointGeolocationReceipt(started, aircraft_present_day,
                    0, 0, 0, 0)
            else:
                raise fpge
    except Exception as e:
        raise e


def geolocate_suburbs_for(flight_points, **kwargs):
    """
    Given a list of FlightPoint instances, attempt to locate the suburbs in which they reside. This function will use the GeospatialFlightPointLocator to associate each flight point
    in the supplied list as efficiently as possible. The return value from this function will be a list of the input flight points, with suburb set.

    Arguments
    ---------
    :flight_points: A list of FlightPoint instances.

    Keyword arguments
    -----------------
    :crs: The CRS through which the flight points are projected. Default is taken from config.
    :overwrite: True if even flight points WITH a suburb should be processed. Default is False.
    :last_seen_suburb: An instance of Suburb, to provide a clue as to where a previous set was located at.
    :return_locator_result: True if the GeospatialFlightPointLocationResult instance should be returned alongside flight points. Default is False.

    Returns
    -------
    If return_locator is True, a tuple;
        The list of flight points,
        The GeospatialFlightPointLocationResult
    Otherwise;
        The list of flight points.
    """
    try:
        crs = kwargs.get("crs", config.COORDINATE_REF_SYS)
        overwrite = kwargs.get("overwrite", False)
        last_seen_suburb = kwargs.get("last_seen_suburb", None)
        return_locator = kwargs.get("return_locator", False)

        # Ensure we have been given points, also ensure that we have at least one state.
        if not len(flight_points):
            LOG.warning(f"Didn't geolocate suburbs for an EMPTY list of flight points.")
            return flight_points
        elif not db.session.query(models.State).count():
            LOG.warning(f"Skipping geolocation for {len(flight_points)}, no states in database. (Sure you've imported geospatial data?)")
            return flight_points
        # Instantiate an instance of GeospatialFlightPointLocator targeting the given flight points, also provide our keyword arguments.
        flight_point_locator = GeospatialFlightPointLocator(flight_points,
            overwrite_existing = overwrite, last_suburb = last_seen_suburb, crs = crs)
        # Geolocate all flight points.
        geolocated_flight_points = flight_point_locator.geolocate_all()
        # If we must return the locator, return that alongside points.
        if return_locator:
            return geolocated_flight_points, flight_point_locator.result
        # Otherwise, just return the flight points.
        return geolocated_flight_points
    except error.NoSuburbFoundError as nsfe:
        raise nsfe
    except error.NoStateFoundError as nsfe1:
        raise nsfe1
    except Exception as e:
        raise e


class GeospatialSuburbContainer():
    """
    A container for a list of Suburb instances, that will automatically create an STRtree and facilitate queries for intersection by other suburbs on a borders basis,
    this means that the class can be very inefficient. There is a choice of two boundary modes; inprecise and precise. Precise will read all coordinates from all suburbs
    and construct a tree from that. Inprecise will simply generate a minimum rotated rectangle from the suburb's bounding box.

    The primary difference between modes is speed; with inprecise obviously being a lot quicker. But, this does also mean that there may be more neighbours than expected
    for some suburbs; but this shouldn't really be an issue.
    """
    @property
    def all_suburbs(self):
        return self._all_suburbs

    @property
    def are_suburb_boundaries_precise(self):
        return self._precise_suburb_boundaries

    def __init__(self, _all_suburbs, **kwargs):
        """
        Initialise the container.

        Arguments
        ---------
        :_all_suburbs: A list of Suburbs to be queried from.

        Keyword arguments
        -----------------
        :precise_suburb_boundaries: True if boundaries for suburbs should be determined by their actual coordinates. False if boundaries should be inferred by
            using a minimum rotated rectangle from the suburb's bbox. This argument is automatically ignored if PostGIS is enabled. Default False.
        :force_without_postgis: True if, regardless of PostGIS enabled status, our fallback calculations should be used. Default is False.
        """
        self._precise_suburb_boundaries = kwargs.get("precise_suburb_boundaries", False)
        self._force_without_postgis = kwargs.get("force_without_postgis", False)

        # This preparation only need be undertaken if we do not have PostGIS enabled in the current configuration.
        if not config.POSTGIS_ENABLED or self._force_without_postgis:
            # Make a tuple out of our suburbs list, to ensure its order and size is immutable.
            self._all_suburbs = tuple(_all_suburbs)
            # First, convert all suburbs to Polygons. If we must use precise boundaries, this will take longer but we'll construct polygons from the actual coordinates for each suburb.
            if self._precise_suburb_boundaries:
                LOG.debug(f"Constructing GeospatialSuburbContainer for {len(self._all_suburbs)} suburbs using PRECISE boundaries, this may take a while. Starting by building a polygon from each provided suburb...")
                self._all_suburbs_polygons = [ suburb_.multi_polygon for suburb_ in self._all_suburbs ]
            else:
                # Otherwise, we'll construct these polygons from a minimum rotated rectangle determined by the suburb's bounding box.
                LOG.debug(f"Constructing GeospatialSuburbContainer for {len(self._all_suburbs)} suburbs using minimum rotated rect boundaries. Starting by building a polygon from each provided suburb...")
                self._all_suburbs_polygons = [ geometry.box(*suburb_.bbox) for suburb_ in self._all_suburbs ]
            # Now, create a lookup for a polygon's ID given its index. This index is relative to the _all_suburbs list.
            LOG.debug(f"Constructing suburb polygon identity dict for {len(self._all_suburbs)}...")
            self._index_by_id = dict((id(polygon), idx) for idx, polygon in enumerate(self._all_suburbs_polygons))
            # Now setup the STRtree.
            LOG.debug(f"Setting up an STRtree for all suburb polygons...")
            self._suburb_strtree = strtree.STRtree(self._all_suburbs_polygons)

    def locate_neighbours_for(self, suburb):
        if config.POSTGIS_ENABLED and not self._force_without_postgis:
            # PostGIS enabled. We'll therefore be querying all interecting suburbs. Precise suburb bounaries are not utilised when PostGIS is enabled.
            neighbour_suburbs = db.session.query(models.Suburb)\
                .filter(models.Suburb.suburb_hash != suburb.suburb_hash)\
                .filter(func.ST_Intersects(models.Suburb.multi_polygon_geom, suburb.multi_polygon_geom))\
                .all()
            return neighbour_suburbs
        else:
            # PostGIS not enabled, we'll be using the prepared STRtree.
            # Convert this suburb to a polygon. If we have inprecise boundaries, this will simply be a minimum rotated rectangle from the suburb's bounding box.
            if self._precise_suburb_boundaries:
                target_suburb_polygon = suburb.multi_polygon
            else:
                target_suburb_polygon = geometry.box(*suburb.bbox)
            # Buffer the target suburb polygon by 300 meters.
            # Query for all intersecting polygons given our target suburb polygon.
            neighbour_polygons = [ (self._index_by_id[id(polygon)], polygon) for polygon in self._suburb_strtree.query(target_suburb_polygon) ]
            # Query the corresponding Suburb instances for each ID returned.
            neighbour_suburbs = [ self._all_suburbs[neighbour_polygon[0]] for neighbour_polygon in neighbour_polygons ]
            # Now, filter out the target suburb.
            neighbour_suburbs = list(filter(lambda suburb_: suburb_ != suburb, neighbour_suburbs))
        # Finally, return the neighbours list.
        return neighbour_suburbs


def determine_neighbours_for(suburb, **kwargs):
    """
    Locate and ensure the existence of neighbour relationships between this suburb and those around it.
    Neighbourships are never deleted. This is to be run for each suburb created/update after importation and can be very CPU intensive so ensure this is run
    only when the server is being created; usually as part of running import trace.

    Arguments
    ---------
    :suburb: The Suburb to establish neighbour relationships for.

    Keyword arguments
    -----------------
    :suburb_container: An optional instance of GeospatialSuburbContainer. If not given, this will be created.
    :precise_suburb_boundaries: True if boundaries for suburbs should be determined by their actual coordinates. False if boundaries should be inferred by using a minimum rotated rectangle from the suburb's bbox. Default False.

    Returns
    -------
    A list of Suburbs that neighbour this suburb.
    """
    try:
        suburb_container = kwargs.get("suburb_container", None)
        precise_suburb_boundaries = kwargs.get("precise_suburb_boundaries", False)

        LOG.debug(f"Refreshing neighbour relationships for Suburb {suburb}...")
        # If suburb container is None, create one now.
        if not suburb_container:
            # Get all suburbs in smae state as suburb.
            all_suburbs = db.session.query(models.Suburb)\
                .filter(models.Suburb.state_code == suburb.state_code)\
                .all()
            suburb_container = GeospatialSuburbContainer(all_suburbs, precise_suburb_boundaries = precise_suburb_boundaries)
        neighbour_suburbs = suburb_container.locate_neighbours_for(suburb)
        LOG.debug(f"{suburb} has {len(neighbour_suburbs)} neighbour suburbs... Ensuring relationships created.")
        # Upsert those relationships here.
        for neighbour_suburb in neighbour_suburbs:
            # For each neighbour suburb, upsert an insert statement for the suburb neighbour table.
            insert_suburb_neighbour_stmt = (
                insert(models.suburb_neighbour)
                .values(
                    left_suburb_hash = suburb.suburb_hash,
                    right_suburb_hash = neighbour_suburb.suburb_hash
                )
            ).on_conflict_do_nothing(index_elements = ["left_suburb_hash", "right_suburb_hash"])
            # Execute this insert.
            db.session.execute(insert_suburb_neighbour_stmt)
        return neighbour_suburbs
    except Exception as e:
        raise e


def determine_epsg_codes_for_suburb(suburb):
    # Get the suburb's geometry and CRS.
    suburb_crs = suburb.crs
    suburb_geometry = suburb.multi_polygon
    # Get all EPSG codes for this geometry.
    epsg_codes = get_epsg_codes_for(suburb_geometry, suburb_crs)
    # Ensure we've upserted all EPSG codes.
    upsert_epsg_codes(epsg_codes)
    # Now, we can simply set EPSG codes for this Suburb.
    for epsg in epsg_codes:
        # Create any where an EPSG is not already existing for this suburb.
        if not epsg in suburb.epsgs:
            suburb.utm_epsg_suburbs.append(models.SuburbUTMEPSG(utmepsg_epsg = epsg))


class ReadSuburbsResult():
    """
    """
    def __init__(self):
        pass


def read_suburbs_from(relative_dir, **kwargs) -> ReadSuburbsResult:
    """
    Reads all suburbs from the given directory. This function takes a relative path to a directory from the current working directory, in which a KML file should be present for each state.
    This function will then read each State one by one, process the internal KML and produce Suburb instances from each. This is a CPU intensive method, that will block for a while; depending
    on the size of the input file(s)... Which will probably always be huge. Only call this from startup/import functions.

    Arguments
    ---------
    :relative_dir: The name, relative to working directory, of the directory containing all states/suburb KMLs.

    Keyword arguments
    -----------------
    :process_neighbourships: True if, after importing all suburbs, we should automatically run code to create neighbour associations between suburbs. Default is False.

    Returns
    -------
    An instance of ReadSuburbsResult.
    """
    try:
        process_neighbourships = kwargs.get("process_neighbourships", False)

        # If doesn't exist, raise an error.
        suburbs_absolute_path = os.path.join(os.getcwd(), relative_dir)
        if not os.path.isdir(suburbs_absolute_path):
            LOG.error(f"Failed to locate requested suburbs STATE directory at {suburbs_absolute_path}")
            raise Exception("no-state-suburbs-dir")
        LOG.debug(f"Beginning read of suburbs from {suburbs_absolute_path}. ***WARNING*** this may take ages, do NOT run this during uptime!!!!!")
        # List all files in this directory. Each will be a KML file containing an entire state.
        state_kml_files = os.listdir(suburbs_absolute_path)
        LOG.debug(f"Located {len(state_kml_files)} state KML files to import.")
        asyncio.run(aiogeospatial.import_all_states(suburbs_absolute_path, state_kml_files))
        # If requested, process neighbourships for all imported states/suburbs.
        if process_neighbourships:
            LOG.debug(f"Processing all suburb neighbourships...")
            # Locate all state codes.
            state_codes = db.session.query(models.Suburb.state_code)\
                .group_by(models.Suburb.state_code)\
                .all()
            LOG.debug(f"Located {len(state_codes)} state to reprocess...")
            # Now, iterate the results of a query for all suburbs within each state code...
            for state_code in state_codes:
                """Query returns a tuple. Maybe fix this?"""
                state_code = state_code[0]
                # Collect all suburbs within this state name.
                suburbs = db.session.query(models.Suburb)\
                    .filter(models.Suburb.state_code == state_code)\
                    .all()
                LOG.debug(f"Reprocessing all {len(suburbs)} suburbs for state; {state_code}")
                # Create a container.
                suburb_container = GeospatialSuburbContainer(suburbs)
                # Now iterate all suburbs, determining neighbours for each given our suburb container, and
                for suburb in suburbs:
                    # Determine EPSGs for each suburb.
                    determine_epsg_codes_for_suburb(suburb)
                    # Now, determine neighbours for each suburb.
                    neighbours_ = determine_neighbours_for(suburb, suburb_container = suburb_container)
                    """TODO: figure out what result should contain and add it."""
                    LOG.debug(f"Located {len(neighbours_)} neighbours for {suburb}")
            # Return a result.
            return ReadSuburbsResult()
    except Exception as e:
        raise e
