"""
A module for handling all statistical calculations.

https://www.carbonindependent.org/22.html
"""
import os
import re
import json
import time
import logging
import pyproj
from datetime import datetime

from geopy import distance
from shapely import geometry, ops, strtree

from . import db, config, models, error, thirdparty

LOG = logging.getLogger("aireyes.calculations")
LOG.setLevel( logging.DEBUG )


def epsg_code_for(longitude, latitude, crs = None, **kwargs) -> int:
    """
    Given a longitude and latitude; locate the EPSG for the location. A CRS can be provided but in absence of one, a pyproj
    Transformer must be given.
    """
    transformer = kwargs.get("transformer", None)
    # CRS only required if transformer is None.
    if not transformer and not crs:
        raise Exception("epsg_code_for failed! Both crs and transformer were not given.")
    elif not transformer:
        # If transformer not given, get one from the CRS.
        # Get the CRS, through which this long, lat is projected.
        crs = pyproj.crs.CRS.from_user_input(crs)
        # Build a transformer from this CRS to the CRS' geodetic CRS.
        transformer = pyproj.Transformer.from_crs(crs, crs.geodetic_crs, always_xy = True)
    # Now, transform these points to their geodetic equivalent.
    longitude, latitude = transformer.transform(longitude, latitude)
    return int(32700-round((45+latitude)/90,0)*100+round((183+longitude)/6,0))


def total_flight_time_from(flight_points_manager, **kwargs) -> int:
    """
    Returns the number of minutes of flight time from the given flight points manager.
    This will be rounded to a whole number.

    Arguments
    ---------
    :flight_points_manager: A flight points manager.

    Returns
    -------
    An integer; the total number of minutes in this flight.
    """
    try:
        # If no flight points, just return 0.
        if not flight_points_manager.num_flight_points:
            return 0
        # Get difference in seconds.
        total_difference_seconds = (flight_points_manager.last_point.timestamp - flight_points_manager.first_point.timestamp)
        # Round total number of minutes.
        return round(total_difference_seconds / 60)
    except ZeroDivisionError as zde:
        return 0
    except Exception as e:
        raise e


def total_distance_travelled_from(flight_points_manager, **kwargs) -> int:
    """
    Returns the number of meters travelled in total between all points in the given manager.
    This will be rounded to a whole number.

    Arguments
    ---------
    :flight_points_manager: A flight points manager.

    Returns
    -------
    An integer; the total number of meters travelled.
    """
    try:
        # From the flight points manager, get the flight path.
        flight_path = flight_points_manager.flight_path
        # Round and return the length of the flight path object.
        return round(flight_path.length)
    except ZeroDivisionError as zde:
        return 0
    except Exception as e:
        raise e


def average_speed_from(flight_points_manager, **kwargs) -> int:
    """
    Returns the average speed, in knots, between all points in the given manager.
    This is done by comprehending a list of all recorded speeds, filtering those that are None, then producing an average.

    Arguments
    ---------
    :flight_points_manager: A flight points manager.

    Returns
    -------
    An integer; the total number of minutes in this flight.
    """
    try:
        # Comprehend a list of all speeds from all points except those that are on the ground.
        speeds_list = [ flight_point.ground_speed if not flight_point.is_on_ground else None for flight_point in flight_points_manager.flight_points ]
        # Filter Nones.
        speeds_list = list(filter(lambda speed: speed != None, speeds_list))
        # Finally, create an average by summing all values and dividing result by number of speeds recorded.
        speed = sum(speeds_list)/len(speeds_list)
        # Return the rounded speed.
        return round(speed)
    except ZeroDivisionError as zde:
        return 0
    except Exception as e:
        raise e


def average_altitude_from(flight_points_manager, **kwargs) -> int:
    """
    Returns the average altitude, in feet, between all points in the given manager.
    This is done by comprehending a list of all recorded altitudes, filtering those that are None, then producing an average.

    Arguments
    ---------
    :flight_points_manager: A flight points manager.

    Returns
    -------
    An integer; the total number of minutes in this flight.
    """
    try:
        # Comprehend a list of all speeds from all points except those that are on the ground.
        altitudes_list = [ flight_point.altitude if (not flight_point.is_on_ground or (flight_point.altitude and flight_point.altitude >= 0)) else None
            for flight_point in flight_points_manager.flight_points ]
        # Filter Nones.
        altitudes_list = list(filter(lambda altitude: altitude != None, altitudes_list))
        # Finally, create an average by summing all values and dividing result by number of altitudes recorded.
        altitude = sum(altitudes_list)/len(altitudes_list)
        # Return the rounded altitude.
        return round(altitude)
    except ZeroDivisionError as zde:
        return 0
    except Exception as e:
        raise e


def estimate_total_fuel_used_by(aircraft, flight_points_manager, **kwargs) -> int:
    """
    Returns the total estimated amount of fuel consumed by the aircraft from all points in the manager.
    This is done by using the fuel consumption data in the aircraft, along with the total distance travelled in the flight
    and how long the flight took.

    We'll use a standard formula for estimating this; gallons used = consumption rate (gallons per hour) * number of hours flown.

    Arguments
    ---------
    :aircraft: The Aircraft to use for fuel consumption figures.
    :flight_points_manager: A flight points manager.

    Raises
    ------
    MissingFuelFiguresError: The aircraft does not have any fuel consumption figures.

    Returns
    -------
    An integer; the total number of minutes in this flight.
    """
    try:
        # Ensure aircraft has required data.
        if not aircraft.has_valid_fuel_data:
            LOG.error(f"Failed to estimate total fuel used by {aircraft}, this aircraft does not have fuel consumption figures.")
            raise error.MissingFuelFiguresError(aircraft)
        # Get total flight time so far. Right now, we won't factor the aircraft speed, but it may pay to at some stage.
        total_flight_time_minutes = total_flight_time_from(flight_points_manager)
        # If 0 minutes, we will simply return 0 here.
        if total_flight_time_minutes == 0:
            return 0
        # Get hours from this.
        total_flight_time_hours = total_flight_time_minutes/60
        # Gallons of fuel used is equal to hours multiplied by consumption rate stored on aircraft. Round this off.
        return round(total_flight_time_hours*float(aircraft.fuel_consumption))
    except ZeroDivisionError as zde:
        return 0
    except Exception as e:
        raise e


def calculate_co2_emissions_per_hour(distance_travelled, average_speed, num_passengers, fuel_used, fuel_co2_per_gram):
    """
    Arguments
    ---------
    :distance_travelled: The number of kilometers flown.
    :average_speed: The average speed, in kilometers per hour.
    :num_passengers: The number of passengers present during the flight.
    :fuel_used: The number of tonnes of fuel used.
    :fuel_co2_per_gram: The amount of co2 produced per a gram of fuel.
    """
    fuel_use_per_pax_per_km = (fuel_used * 1000000) / (distance_travelled * num_passengers)
    co2_emissions_per_pax_per_km = fuel_use_per_pax_per_km * float(fuel_co2_per_gram)
    co2_emissions_per_pax_per_hour = round((co2_emissions_per_pax_per_km * average_speed) / 1000)
    return co2_emissions_per_pax_per_hour * num_passengers


"""
A quick check for shapely 2.0.X
If shapely's version moves to match this level, we will throw an exception right here, to switch over to the fix found in thirdparty for subclassing
geometry components
"""
import shapely
if shapely.__version__.startswith("2.0"):
    """
    TODO: load fix.
    """
    raise Exception("Could not load calculations module. Shapely 2.0 in use, please apply fix from thirdparty.py !!!!!")
else:
    from shapely.errors import EmptyPartError
    from shapely.geometry import point
    from shapely.geometry.base import BaseMultipartGeometry

    class AirportPoint(geometry.Point):
        @property
        def airport(self):
            return self._airport

        def __init__(self, *args, **kwargs):
            self._airport = kwargs.pop("airport", None)
            super().__init__(*args, **kwargs)


    class AirportMultiPoint(BaseMultipartGeometry):
        __slots__ = []

        def __new__(self, points=None):
            if points is None:
                # allow creation of empty multipoints, to support unpickling
                # TODO better empty constructor
                return shapely.from_wkt("MULTIPOINT EMPTY")
            elif isinstance(points, AirportMultiPoint):
                return points

            if len(points) == 0:
                return shapely.from_wkt("MULTIPOINT EMPTY")

            return shapely.multipoints(points)


def find_airport_via_search_for(aircraft, position, **kwargs) -> models.Airport:
    """
    Locate the best airport given an aircraft instance and a tuple. The tuple should contain two items; a latitude and longitude respectively.
    Multiple techniques will be used to speed this process up, but at its core, this function will utilise the shapely package to painfully
    search ALL airports in the database; this can surely be more efficient.

    Arguments
    ---------
    :aircraft: The aircraft instance in question.
    :position: A Shapely Point geometry, or a tuple; which will be turned into a Point. Geometry layout must be XY.

    Returns
    -------
    An Airport.
    """
    try:
        # If the position is None, raise a FlightPointIntegrityError.
        if not position:
            raise error.FlightPointPositionIntegrityError(aircraft, position, "find-airport", "position can't be None!")
        if not isinstance(position, geometry.Point):
            position = geometry.Point(position)
        # Get all airports (we can cache this, surely. TODO)
        airports = dict((airport.center.coords[0], airport,) for airport in db.session.query(models.Airport).all())
        airport_points_multi = geometry.MultiPoint([ position for position, airport in airports.items() ])
        # Now, use nearest_points to locate the nearest airport.
        try:
            nearest_pair = [ nearest_pair for nearest_pair in ops.nearest_points(airport_points_multi, position) ]
        except ValueError as ve:
            raise error.NoAirportsLoaded()
        # Ensure the results are exactly two items long; any other case, raise an exception.
        if len(nearest_pair) != 2:
            LOG.error(f"Failed to find nearest airport for {aircraft} with position {position}, returned result is NOT 2 items long; {nearest_pair}")
            raise Exception("invalid-nearest-pair")
        # Result is two items long. Our destination airport is found in the first result.
        destination_airport_sequence = nearest_pair[0].coords[0]
        """TODO: what happens if there are no results .. ? Contemplate this"""
        return airports[(destination_airport_sequence[0], destination_airport_sequence[1])]
    except KeyError as ke:
        LOG.error(f"Failed to find nearest airport for {aircraft} with position {position}, destination sequence not in airports dict!")
        """
        TODO: proper raise of an error.
        """
        raise ke
    except Exception as e:
        raise e


def find_airport_via_epsg_for(aircraft, flight_point, **kwargs) -> models.Airport:
    """
    Locate the best airport given an aircraft instance and a FlightPoint instance. This function operates by locating all airports
    in the same EPSG UTM zone as the the given flight point, from this list, those that the flight point intersect are located,
    then from that list, the one nearest to the given flight point is returned as the determined airport.

    Arguments
    ---------
    :aircraft: The aircraft instance in question.
    :flight_point: A FlightPoint instance.

    Raises
    ------
    FlightPointIntegrityError:
    :position-is-none: The given FlightPoint's position is None.
    :utm-epsg-zone-is-none: The given FlightPoint's UTM EPSG zone is None.

    NoAirportFound: No airport was located. This aircraft wasn't near one.

    Returns
    -------
    An Airport.
    """
    try:
        # If the flight point does not have a valid position, OR a valid UTM EPSG, raise a FlightPointPositionIntegrityError.
        if not flight_point.is_position_valid:
            LOG.error(f"Could not locate airport given {flight_point}, the position is None!")
            raise error.FlightPointIntegrityError(aircraft, flight_point, "position-is-none")
        elif not flight_point.utm_epsg_zone:
            LOG.error(f"Could not locate airport given {flight_point}, the UTM EPSG zone is None!")
            raise error.FlightPointIntegrityError(aircraft, flight_point, "utm-epsg-zone-is-none")
        # Get the flight point's position.
        position = flight_point.position
        # Get all airports that are associated with this flight point's utm epsg zone.
        airports = tuple(models.Airport.get_all_from_utm_epsg(flight_point.utm_epsg_zone))
        # Create a tuple of all polygons for all airports.
        airport_geometries = [airport.polygon for airport in airports]
        # Create an index by reference for these geometries.
        index_by_id = dict((id(polygon), idx) for idx, polygon in enumerate(airport_geometries))
        # Create an STRtree for locating intersecting airports, and immediately isolate the polygons that intersect position.
        intersecting_airport_polygons = strtree.STRtree(airport_geometries).query(position)
        # If there are none found, raise NoAirportFound.
        if not len(intersecting_airport_polygons):
            LOG.error(f"No airport found for flight point {flight_point}, there were no intersecting airport polygons found!")
            raise error.NoAirportFound()
        # Now, create a tuple of all Airport center geometries along with the polygon equivalents.
        airport_center_polygon_geometries = [(polygon, polygon.centroid,) for polygon in intersecting_airport_polygons]
        # Create an index by id reference for these geometries, but by using the index found in the original index_by_id for each polygon in airport_center_polygon_geometries.
        index_by_id = dict((id(center), index_by_id[id(polygon)]) for polygon, center in airport_center_polygon_geometries)
        # Create an STRtree for finding the nearest geom, from all centroids.
        airport_center_strtree = strtree.STRtree([x[1] for x in airport_center_polygon_geometries])
        # Find nearest given flight point's position.
        nearest_airport_center = airport_center_strtree.nearest(position)
        # If None, raise NoAirportFound.
        if not nearest_airport_center:
            LOG.error(f"No airport found for flight point {flight_point}, could not find a nearest center!")
            raise erorr.NoAirportFound()
        # Otherwise, locate and return the required Airport instance from airports tuple.
        return airports[index_by_id[id(nearest_airport_center)]]
    except Exception as e:
        raise e
