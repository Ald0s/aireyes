"""
A module for creating and updating aircrafts, their flight points and any data involving the aforementioned two operations.
"""
import re
import os
import time
import uuid
import decimal
import hashlib
import pytz
import logging
import math
import pyproj
import json
from datetime import datetime, date, timedelta, time as time_, timezone

from shapely import geometry, ops

from .compat import insert

from flask import g
from sqlalchemy import func, and_, or_, asc, desc
from sqlalchemy.exc import IntegrityError
from marshmallow import Schema, fields, EXCLUDE, post_load, pre_load

from . import db, config, models, error, calculations, utility, inaccuracy, viewmodel, decorators

LOG = logging.getLogger("aireyes.airvehicles")
LOG.setLevel( logging.DEBUG )

target_crs = pyproj.crs.CRS.from_user_input(config.COORDINATE_REF_SYS)
transformer = pyproj.Transformer.from_crs(4326, target_crs, always_xy = True)
geodetic_transformer = pyproj.Transformer.from_crs(transformer.target_crs, transformer.target_crs.geodetic_crs.to_epsg(), always_xy = True)


class TargetVehicleSchema(Schema):
    class Meta:
        unknown = EXCLUDE
    icao                    = fields.Str(data_key = "icao")
    flight_name             = fields.Str(data_key = "name")
    airport_code            = fields.Str(data_key = "airportCode")


class AircraftFuelSchema(Schema):
    class Meta:
        unknown = EXCLUDE
    aircraft_type           = fields.Str(data_key = "aircraftType")
    aircraft_year           = fields.Int(data_key = "aircraftYear")

    fuel_type               = fields.Str(data_key = "fuelType")
    fuel_cost               = fields.Decimal(data_key = "fuelCost")
    fuel_capacity           = fields.Int(data_key = "fuelCapacity")
    fuel_consumption        = fields.Decimal(data_key = "fuelConsumption")
    fuel_range              = fields.Int(data_key = "fuelRange")
    fuel_endurance          = fields.Int(data_key = "fuelEndurance")
    fuel_passenger_load     = fields.Int(data_key = "fuelPassengerCount")
    fuel_co2_per_gram       = fields.Decimal(data_key = "fuelEmissions")
    top_speed               = fields.Int(data_key = "topSpeed")


class AircraftTimeoutReportSchema(Schema):
    """A schema that contains information relating to the timeout report for an aircraft."""
    class Meta:
        unknown = EXCLUDE
    aircraft_icao           = fields.Str(data_key = "aircraftIcao")
    # Timestamp, in seconds, for when last a binary update had been received for this aircraft. This can be None, meaning that an update has never been received since timing out and disappearing.
    last_binary_update      = fields.Int(data_key = "lastBinaryUpdate", allow_none = True)
    # The currently configured time (in seconds) an aircraft will timeout after, according to the worker.
    current_timeout_config  = fields.Int(data_key = "currentConfigAircraftTimeout")
    # Timestamp, in seconds, when this report was made.
    time_of_report          = fields.Int(data_key = "timeOfReport", load_default = lambda: int(time.time()))


class AircraftTimeoutResponseSchema(Schema):
    """
    A schema that contains information for reporting back to a worker the decision made by the server with respect to an aircraft timeout
    report, and how the worker should handle it.
    """
    class Meta:
        unknown = EXCLUDE
    determination           = fields.Str(data_key = "determination")


class FlightPointReceiptSchema(Schema):
    """A schema for dumping FlightPoint models for receipting purposes."""
    flight_point_hash       = fields.Str(data_key = "flightPointHash")
    aircraft_icao           = fields.Str(data_key = "AircraftIcao")
    #timestampMillis         = fields.Int(data_key = "timestamp")
    synchronised            = fields.Bool(data_key = "synchronised")


class FlightPointSchema(Schema):
    """A schema for both loading FlightPoint models from raw JSON data originally submitted by the NodeJS worker."""
    class Meta:
        unknown = EXCLUDE
    flight_point_hash       = fields.Str(data_key = "flightPointHash")
    aircraft_icao           = fields.Str(data_key = "AircraftIcao")

    day_day                 = fields.Date(allow_none = True, load_default = None, data_key = "date")
    timestamp               = fields.Decimal(data_key = "timestamp")
    latitude                = fields.Decimal(allow_none = True, data_key = "latitude")
    longitude               = fields.Decimal(allow_none = True, data_key = "longitude")
    utm_epsg_zone           = fields.Int(allow_none = True, data_key = "utm_epsg_zone")

    altitude                = fields.Int(allow_none = True, data_key = "altitude")
    ground_speed            = fields.Decimal(allow_none = True, data_key = "groundSpeed")
    rotation                = fields.Decimal(allow_none = True, data_key = "rotation")
    vertical_rate           = fields.Int(allow_none = True, data_key = "verticalRate")
    data_source             = fields.Str(allow_none = True, data_key = "dataSource")

    is_on_ground            = fields.Bool(data_key = "isOnGround")
    is_ascending            = fields.Bool(data_key = "isAscending")
    is_descending           = fields.Bool(data_key = "isDescending")

    @post_load
    def flight_point_post_load(self, data, **kwargs):
        # One thing we must do prior to post loading flight point data is ensure we have a 'day' value. If not, utilise our 'timestamp'
        # attribute and parse that to a Date object.
        day = data.get("day_day", None)
        if not day:
            timestamp = data["timestamp"]
            # Parse this timestamp to a date and set in data dict.
            data["day_day"] = datetime.utcfromtimestamp(int(timestamp)).date()
        """TODO: improve how we get transformer."""
        # Pop both latitude and longitude from data.
        longitude = data.pop("longitude", None)
        latitude = data.pop("latitude", None)
        # Now, build the flight point instance.
        flight_point = models.FlightPoint(**data)
        # Now, optionally set position.
        if longitude and latitude:
            # If entire position is given, we will instantiate a new point and transform it to the locally used CRS.
            point = geometry.Point((longitude, latitude,))
            point = ops.transform(transformer.transform, point)
            # Set both the position and CRS.
            flight_point.set_crs(transformer.target_crs.to_epsg())
            flight_point.set_position(point)
            # Now, we'll calculate the UTM EPSG zone for this flight point.
            epsg = calculations.epsg_code_for(point.x, point.y, geodetic_transformer.target_crs, transformer = geodetic_transformer)
            flight_point.set_utm_epsg(epsg)
        # Return this flight point.
        return flight_point


class AircraftSchema(Schema):
    """
    A schema for loading a new instance of the Aircraft model, given a JSON object that is the direct serialisation of
    the contents of the Sequelize database in a worker, or from an aircraft states JSON file.
    """
    class Meta:
        unknown = EXCLUDE
    """ TODO: maybe remove allow None from type & registration? These should be enforced..."""
    icao                    = fields.Str(data_key = "icao")
    type                    = fields.Str(allow_none = True, data_key = "type")
    flight_name             = fields.Str(data_key = "flightName")
    registration            = fields.Str(allow_none = True, data_key = "registration")
    description             = fields.Str(allow_none = True, data_key = "description")
    year                    = fields.Int(allow_none = True, data_key = "year")
    owner_operator          = fields.Str(allow_none = True, data_key = "ownerOperator")
    image                   = fields.Str(allow_none = True, required = False, load_default = None)
    airport_code            = fields.Str(allow_none = True, required = False, load_default = None, data_key = "airportCode")

    flight_points           = fields.List(fields.Nested(FlightPointSchema, many = False), data_key = "FlightPoints")

    @post_load
    def aircraft_post_load(self, data, **kwargs):
        # If airport code is None, set it to the last two characters of the ICAO.
        if not data.get("airport_code", None):
            data["airport_code"] = data["icao"][4:]
        return data


class AirportSchema(Schema):
    """A schema for loading a new instance of the Airport model, given a JSON object."""
    class Meta:
        unknown = EXCLUDE
    airport_hash            = fields.Str(data_key = "airportHash", required = False, load_default = None)
    name                    = fields.Str(data_key = "name")
    kind                    = fields.Str(data_key = "kind", required = False, load_default = None)
    icao                    = fields.Str(data_key = "icao", required = False, load_default = None)
    iata                    = fields.Str(data_key = "iata", required = False, load_default = None)

    latitude                = fields.Method(deserialize = "load_coordinate")
    longitude               = fields.Method(deserialize = "load_coordinate")

    def load_coordinate(self, obj):
        """
        Load the latitude/longitude.
        Input data should have the compass direction listed after the number. Ex; -33.0000(S)
        The return value will be a Python Decimal object, which represent EPSG:4326 coordinate(s)
        """
        try:
            # Use re to match & return the decimal number.
            coordinate_match = re.match(r"^(-?\d+\.\d+)\(\w\)$", obj)
            if not coordinate_match:
                LOG.error(f"Coordinate digit not found in {obj}")
                raise Exception("coordinate-match-not-found")
            # Get the first group from coordinate match, parse as decimal and return.
            return decimal.Decimal(coordinate_match.group(1))
        except Exception as e:
            raise e

    @post_load
    def airport_post_load(self, data, **kwargs):
        """
        Make the airport's hash. This is done by hashing the name, latitude and longitude; as they appear after the loading of the
        airport. We'll then return the resulting dictionary.
        """
        # Attempt to pop 'radius' from data. If not found, get default_buffer_radius from context, default to AIRPORT_POINT_BUFFER_RADIUS_METERS.
        buffer_radius = data.get("radius", self.context.get("default_buffer_radius", config.AIRPORT_POINT_BUFFER_RADIUS_METERS))
        if int(buffer_radius) <= 0:
            raise Exception(f"Buffer radius on airport {self} may not be less than or equal to 0.")
        # Get airport name.
        airport_name = data["name"]
        # Replace '[DUPLICATE] ' with empty space.
        airport_name = airport_name.replace("[Duplicate] ", "")
        # Strip spaces & modify to be a title.
        airport_name = airport_name.strip().title()
        # Reset airport's name.
        data["name"] = airport_name
        # Use blake2b to hash, only if airport hash not already given.
        if not data["airport_hash"]:
            hash_input_data = (data["name"]+str(data["latitude"])+str(data["longitude"])).encode("utf-8")
            airport_hash = hashlib.blake2b(hash_input_data, digest_size = 16).hexdigest().lower()
            data["airport_hash"] = airport_hash
        # Now, post load the point geometry for this airport.
        # Pop both latitude and longitude from data.
        longitude = data.pop("longitude", None)
        latitude = data.pop("latitude", None)
        if not longitude or not latitude:
            raise Exception("Latitude or Longitude are None in AirportSchema")
        # Set the CRS to the target CRS.
        data["crs"] = target_crs.to_epsg()
        """
        TODO: improve how we get a transformer.
        """
        # Now, build a shapely Point from this, then transform the point to the target CRS.
        point = geometry.Point((longitude, latitude,))
        point = ops.transform(transformer.transform, point)
        # Now, buffer this point by buffer_radius to produce a polygon, and set this in the data dictionary.
        polygon = point.buffer(buffer_radius, cap_style = geometry.CAP_STYLE.square)
        data["polygon"] = polygon
        return data


@decorators.get_master()
def get_monitored_aircraft(master, **kwargs):
    """
    Fetch all monitored aircraft from the database, or filtered to just a few aircraft ICAOS, optionally ordered by which are currently active. If the return
    value has only a single result, that result will be returned as a list. Otherwise, a list of results will be returned, or None if there are no results found
    for some reason. This function will return a list of AircraftViewModel objects for each result.

    Keyword arguments
    -----------------
    :aircraft_icaos: A list to filter the results down by aircraft.
    :active_first: True to order the aircraft such that those active are first. Default is True.

    Returns
    -------
    A list of AircraftViewModel.
    """
    try:
        aircraft_icaos = kwargs.get("aircraft_icaos", None)
        active_first = kwargs.get("active_first", True)

        LOG.debug(f"Attempting to get monitored aircraft...")
        """TODO: proper exceptions here."""
        monitored_aircraft_q = db.session.query(models.Aircraft)
        # If we should filter by icaos, apply a filter. Otherwise, automatically filter by the currently configured monitored aircraft.
        if aircraft_icaos and len(aircraft_icaos):
            monitored_aircraft_q = monitored_aircraft_q\
                .filter(models.Aircraft.icao.in_(aircraft_icaos))
        else:
            monitored_aircraft_q = monitored_aircraft_q\
                .filter(models.Aircraft.icao.in_([aircraft.icao for aircraft in master.tracked_aircraft]))
        # Now, if requested, apply ordering.
        if active_first:
            monitored_aircraft_q = monitored_aircraft_q\
                .order_by(desc(models.Aircraft.is_active_now))
        else:
            # By default though, order by last seen descending.
            monitored_aircraft_q = monitored_aircraft_q\
                .order_by(asc(models.Aircraft.seconds_since_last_seen))
        # Group by all aircraft ICAOs, then get all aircraft.
        monitored_aircraft = monitored_aircraft_q\
            .all()
        LOG.debug(f"Located {len(monitored_aircraft)} monitored aircraft.")
        return [viewmodel.AircraftViewModel(aircraft) for aircraft in monitored_aircraft]
    except Exception as e:
        raise e


def update_fuel_figures(aircraft = [], **kwargs):
    """
    Update fuel figures for the given aircraft. This accepts both a single aircraft or a list of aircraft, and will read the fuel figures JSON
    file from the system, and update where required. If an aircraft's information can not be found in the fuel information file, its figures
    will be nulled out.

    Arguments
    ---------
    :aircraft: Either a single aircraft, or a list containing multiple aircraft, whose fuel figures to update. Or, an empty list to update all aircraft currently in the database.

    Keyword arguments
    -----------------
    :filename: An optional override for the target filename. By default, AIRCRAFT_FUEL_FIGURES is used.
    :directory: An optional relative directory containing the target filename, this is added relative to the imports directory, if given.

    Raises
    ------
    NoFuelFiguresDataFound: The aircraft fuel figures file can't be found!

    Returns
    -------
    A list, containing all aircraft updated.
    """
    try:
        directory = kwargs.get("directory", "")
        filename = kwargs.get("filename", config.AIRCRAFT_FUEL_FIGURES)

        if isinstance(aircraft, models.Aircraft):
            # Ensure its a list.
            aircraft = [aircraft]
        elif isinstance(aircraft, list) and not len(aircraft):
            # Get all aircraft.
            aircraft = db.session.query(models.Aircraft).all()
        # Ensure our fuel figures JSON file exists.
        fuel_absolute_path = os.path.join(os.getcwd(), config.IMPORTS_DIR, directory, filename)
        if not os.path.isfile(fuel_absolute_path):
            LOG.error(f"Failed to update fuel figures for {len(aircraft)} aircraft, no figures JSON file was found! ({fuel_absolute_path})")
            raise error.NoFuelFiguresDataFound()
        LOG.debug(f"Updating fuel figures for {len(aircraft)} aircraft.")
        # Begin by reading from the file.
        with open(fuel_absolute_path, "r") as f:
            fuel_figures_json = json.loads(f.read())
        # Comprehend a list of each figure schema, loaded.
        fuel_figures = [ AircraftFuelSchema().load(fuel_figure_json) for fuel_figure_json in fuel_figures_json ]
        """TODO ISSUE 0x05: this is inefficient, I know, clean it up at some stage."""
        # Now, iterate all aircraft in our given aircrafts list.
        for aircraft_ in aircraft:
            # Iterate each fuel figure and compare type & year. If they're a match, preverse the required fuel figure dict.
            fuel_figure = None
            for fuel_figure_ in fuel_figures:
                if fuel_figure_["aircraft_type"] == aircraft_.type and fuel_figure_["aircraft_year"] == aircraft_.year:
                    # Save and break.
                    fuel_figure = fuel_figure_
                    LOG.debug(f"Updating fuel figures for aircraft of type {aircraft_.type} from year {aircraft_.year}...")
                    break
            # Update the aircraft's fuel information from the figure; with either None or a dictionary.
            aircraft_.update_fuel_figures(fuel_figure)
        return aircraft
    except Exception as e:
        raise e


def synchronise_flight_points(aircraft, flight_points, **kwargs):
    """
    Given an aircraft and a list of FlightPoint models, execute logic to synchronise these points with the database. For now, if the point already
    exists (determined only by pre-existence of flight_point_hash,) the flight point will have no further action taken. Otherwise, it will be added
    to the database. Returns two lists of FlightPoints, see below.

    Arguments
    ---------
    :aircraft: The aircraft to which we will be adding these FlightPoints.
    :flight_points: The list of FlightPoints.

    Returns
    -------
    A tuple with two items;
        A list of FlightPoints that DID NOT already exist.
        A list of FlightPoints, from the given flight points, that did already exist.
    """
    try:
        new = 0
        existed = 0
        # Populate this list only with points that did not already exist.
        new_flight_points = []
        LOG.debug(f"Synchronising {len(flight_points)} flight points for aircraft {aircraft.flight_name}...")
        #with db.session.no_autoflush:
        # Spin up an iteration of all flight points.
        for flight_point in flight_points:
            # Attempt to fix any inaccuracies in this flight point.
            flight_point = inaccuracy.attempt_flight_point_correction(aircraft, flight_point)
            if db.session.query(models.FlightPoint).filter(models.FlightPoint.flight_point_hash == flight_point.flight_point_hash).first():
                existed+=1
                # Quick fix here, just to set those returned as 'synchronised points' as synchronised.
                flight_point.synchronised = True
                continue
            new+=1
            # Add the new flight point to the aircraft.
            aircraft.flight_points_.append(flight_point)
            new_flight_points.append(flight_point)
        LOG.debug(f"Done. We created {new} flight points. {existed} already existed, and so we took no action.")
        # Return our flight points; if we must return only new points, just return that list.
        return new_flight_points, flight_points
    except Exception as e:
        raise e


def aircraft_submitted(new_aircraft, **kwargs):
    """
    Called by the API when an Aircraft has been submitted. This is done either when a new aircraft has been detected, and its full trace merged & sent,
    or when an aircraft has been updated via binary. Either way, a new Aircraft object is created and submitted, along with all the flight points that
    require synchronisation. By default this function will return the Aircraft and all flight points associated with the input aircraft.

    This function will, if the Aircraft does not yet, exist, add the Aircraft along with all submitted flight points straight to the database. If the Aircraft
    does exist, the new Aircraft instance will be used to update the existing Aircraft's information. All flight points will simply be appended to the database.

    Arguments
    ---------
    :new_aircraft: An instance of AircraftSchema.

    Keyword arguments
    -----------------
    :should_update_fuel_figures: If True, upon completion of this function, the aircraft's fuel figures will be updated. Default is False.

    Returns
    -------
    A tuple with three items;
        An Aircraft instance,
        A list of FlightPoints that DID NOT already exist.
        A list of FlightPoints, from the submitted aircraft, that already existed.
    """
    try:
        should_update_fuel_figures = kwargs.get("should_update_fuel_figures", False)

        # Start by creating/updating the aircraft.
        aircraft_icao = new_aircraft["icao"]
        aircraft = models.Aircraft.get_by_icao(aircraft_icao)
        # If aircraft exists, perform an update for its data attributes.
        if aircraft:
            LOG.debug(f"Aircraft {str(aircraft)} already exists! Updating it...")
            aircraft.update_from_schema(new_aircraft)
            # Update fuel figures, if required.
            if should_update_fuel_figures:
                LOG.debug(f"Updating fuel figures for an existing aircraft...")
                update_fuel_figures(aircraft)
            # Now that we've updated the aircraft, we will proceed to sync these new points from the new_aircraft instance. We'll return that return value.
            flight_points, synchronised_flight_points = synchronise_flight_points(aircraft, new_aircraft["flight_points"])
            return aircraft, flight_points, synchronised_flight_points
        # Otherwise, the Aircraft does not exist. We will simply add it, and all its flight points to the database.
        new_aircraft = models.Aircraft(**new_aircraft)
        LOG.debug(f"Aircraft {new_aircraft} does not exist yet. Adding it to the database, along with {new_aircraft.num_flight_points} points!")
        db.session.add(new_aircraft)
        # Flush to get this aircraft into the database.
        db.session.flush()
        # Update fuel figures, if required.
        if should_update_fuel_figures:
            LOG.debug(f"Setting fuel figures for a new aircraft...")
            update_fuel_figures(new_aircraft)
        # Flight points will automatically be added to the session. So no need to sync, but we will just return THIS aircraft's flight points.
        return new_aircraft, new_aircraft.flight_points, new_aircraft.flight_points
    except Exception as e:
        raise e


class AircraftTimeoutReceipt():
    @property
    def is_landing(self):
        return self.determination == "landing"

    def __init__(self, _determination):
        self.determination = _determination


def aircraft_timeout_reported(aircraft, aircraft_timeout_report, **kwargs) -> AircraftTimeoutReceipt:
    """
    Process a timeout report for the specified aircraft. This occurs when an aircraft, while being tracked by a worker, disappears. This may be for multiple reasons; the
    aircraft has landed, the aircraft is not being tracked effectively, network issues. This function will determine the specific type of timeout that has occurred and as
    a product, how the worker should treat the aircraft.

    Arguments
    ---------
    :aircraft: The aircraft that has timed out.
    :aircraft_timeout_report: An AircraftTimeoutReportSchema loaded.

    Returns
    -------
    An AircraftTimeoutReceipt.
    """
    try:
        LOG.debug(f"Aircraft {aircraft} has been reported as timed out - figuring out why...")
        # Get the aircraft's latest flight.
        latest_flight = aircraft.latest_flight
        # If the aircraft has a latest flight; check whether that flight has arrival details. If so, this would mean a proper landing.
        if latest_flight and latest_flight.has_arrival_details:
            # Proper landing. We will now return a response indicating a landing.
            LOG.debug(f"Timeout reported for {aircraft} determined to be due to a landing (has arrival details & latest flight.)")
            return AircraftTimeoutReceipt("landing")
        # Return a holding instruction.
        LOG.debug(f"Could not determine why timeout for {aircraft} was reported. Instructing a holding ...")
        return AircraftTimeoutReceipt("hold")
    except Exception as e:
        raise e


def read_aircraft_from(filename, **kwargs):
    """
    Read all aircraft from the given file, identified by ICAO. The file should be present within the imports/ directory
    to be located. This function, once all workers are read, will only ensure the aircraft CURRENTLY exists, and will
    not issue any updates.

    Arguments
    ---------
    :filename: The name, relative to imports/ directory, of the file.

    Returns
    -------
    A list of all Aircraft found.
    """
    try:
        aircraft = []
        # If doesn't exist, raise an error.
        aircraft_state_absolute_path = os.path.join(os.getcwd(), config.IMPORTS_DIR, filename)
        if not os.path.isfile(aircraft_state_absolute_path):
            LOG.error(f"Failed to locate requested aircraft states configuration file at {aircraft_state_absolute_path}")
            raise Exception("no-existing-aircraft")
        # Then, open and read JSON from the config.
        with open(aircraft_state_absolute_path, "r") as f:
            aircraft_list_json = json.loads(f.read())
        # Now, we will only execute creation logic if the aircraft's ICAO does not exist.
        for aircraft_d in [ AircraftSchema().load(a) for a in aircraft_list_json ]:
            icao = aircraft_d["icao"]
            name = aircraft_d["flight_name"]
            if models.Aircraft.get_by_icao(aircraft_d["icao"]):
                LOG.warning(f"Skipping creating/updating aircraft {name} ({icao}) from known aircraft, it already exists.")
                continue
            # Otherwise, submit the aircraft.
            LOG.debug(f"Creating known aircraft {name} ({icao}), it does not exist yet.")
            # Submit the aircraft. Do not update fuel figures one by one.
            aircraft_, flight_points, synchronised_flight_points = aircraft_submitted(aircraft_d, should_update_fuel_figures = False)
            aircraft.append(aircraft_)
        # Now, before returning, update all fuel figures at once.
        update_fuel_figures(aircraft)
        # Return all read aircraft.
        return aircraft
    except Exception as e:
        raise e


def determine_epsg_codes_for_airport(airport):
    from . import geospatial

    # Get the airport's geometry and CRS.
    airport_crs = airport.crs
    airport_geometry = airport.polygon
    # Get all EPSG codes for this geometry.
    epsg_codes = geospatial.get_epsg_codes_for_polygon(airport_geometry, airport_crs)
    # Ensure we've upserted all EPSG codes.
    geospatial.upsert_epsg_codes(epsg_codes)
    # Now, we can simply set EPSG codes for this Suburb.
    airport.utm_epsg_airports = [ models.AirportUTMEPSG(airport = airport, utmepsg_epsg = epsg) for epsg in epsg_codes ]


def read_airports_from(filename, **kwargs):
    """
    Read all airports from the given file and ensure database contents are up to date.

    Arguments
    ---------
    :filename: The name of the file containing all Airport JSON.

    Keyword arguments
    -----------------
    :directory: A directory relative to imports in which to search for the target file.

    Returns
    -------
    A list of all Airport found.
    """
    try:
        directory = kwargs.get("directory", "")

        airports = []
        # If doesn't exist, raise an error.
        airports_absolute_path = os.path.join(os.getcwd(), config.IMPORTS_DIR, directory, filename)
        if not os.path.isfile(airports_absolute_path):
            LOG.error(f"Failed to locate requested airports configuration file at {airports_absolute_path}")
            raise Exception("no-existing-airports")
        # Then, open and read JSON from the config.
        with open(airports_absolute_path, "r") as f:
            airport_list_json = json.loads(f.read())
        # Default buffer radius.
        default_buffer_radius = config.AIRPORT_POINT_BUFFER_RADIUS_METERS
        if default_buffer_radius <= 0:
            LOG.error("A default airport buffer radius less than 0 is NOT allowed!")
            """TODO proper exception."""
            raise Exception("invalid-airport-buffer-radius")
        # Now, we will only execute creation logic if the airport's hash does not exist.
        for airport_d in [ AirportSchema(context = dict(default_buffer_radius = default_buffer_radius)).load(a) for a in airport_list_json ]:
            airport_hash = airport_d["airport_hash"]
            airport_name = airport_d["name"]
            if models.Airport.get_by_hash(airport_hash):
                LOG.warning(f"Skipping creating/updating airport {airport_name}, it already exists.")
                continue
            # Otherwise, submit the aircraft.
            LOG.debug(f"Creating airport {airport_name}, it does not exist yet.")
            airport = models.Airport(**airport_d)
            # Add the airport to the session.
            db.session.add(airport)
            # Determine all UTM EPSGs for this airport.
            determine_epsg_codes_for_airport(airport)
            # Add the airport to our results list.
            airports.append(airport)
        # Return all read airports.
        return airports
    except Exception as e:
        raise e
