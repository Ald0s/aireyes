import os
import re
import json
import logging
import time
import hashlib
import decimal
import asyncio
import aiofiles

from datetime import date, datetime

from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine, async_scoped_session
from sqlalchemy import func, and_, or_

from sqlalchemy.exc import OperationalError, UnsupportedCompilationError
from .compat import insert

from . import db, config, models, airvehicles, error, aio

LOG = logging.getLogger("aireyes.aiotraces")
LOG.setLevel( logging.DEBUG )

logging_level = logging.ERROR
logging.getLogger('aiosqlite').setLevel(logging_level)
logging.getLogger('sqlalchemy').setLevel(logging_level)
logging.getLogger('sqlalchemy.engine').setLevel(logging_level)

engine = None
async_session_factory = None
AsyncScopedSession = None


async def extract_flight_point(aircraft_icao, trace_object, **kwargs):
    """
    Given an aircraft's ICAO and a single trace object (which should be an array found in a trace's 'trace' attribute) extract
    and return a dictionary, compatible with FlightPointSchema, of the given trace object. The trace object given MUST have
    a normalised timestamp. This isn't checked however, and failing to satsify this requirement will simply result in dodgy
    return data.

    Arguments
    ---------
    :aircraft_icao: The aircraft's ICAO.
    :trace_object: A trace array item (which must also be an array.)

    Returns
    -------
    A dictionary, compatible with FlightPointSchema.
    """
    try:
        is_on_ground = False
        # First, attempt to get altitude & altitude rate. These can be nullable.
        if trace_object[3] != None and trace_object[3] == "ground":
            # Point is on the ground.
            is_on_ground = True
            altitude = 0
        elif trace_object[3] != None:
            # Some other altitude value, valid integer.
            altitude = int(trace_object[3])
        else:
            altitude = None
        # Second, attempt to get altitude rate.
        if trace_object[7] != None:
            altitude_rate = int(trace_object[7])
        else:
            altitude_rate = None
        # Generate a hash for this flight point.
        # As per proposed change 0x01, see __init__.py, we now also employ position and altitude for flight point hash.
        position_for_hash = str(trace_object[1])+str(trace_object[2])
        altitude_for_hash = str(altitude or "na")
        hash_input_data = (aircraft_icao + str(trace_object[0]) + position_for_hash + altitude_for_hash).encode("utf-8")
        flight_point_hash = hashlib.blake2b(hash_input_data, digest_size = 16).hexdigest().lower()
        # Comprehend and return a new dictionary now.
        return dict(
            AircraftIcao = aircraft_icao,
            flightPointHash = flight_point_hash,
            timestamp = trace_object[0],
            latitude = decimal.Decimal(trace_object[1]),
            longitude = decimal.Decimal(trace_object[2]),
            altitude = altitude,
            groundSpeed = trace_object[4],
            rotation = trace_object[5],
            verticalRate = altitude_rate,
            isAscending = False if not altitude_rate else altitude_rate > 0,
            isDescending = False if not altitude_rate else altitude_rate < 0,
            isOnGround = is_on_ground
        )
    except Exception as e:
        raise e


async def extract_aircraft_state(trace, **kwargs):
    """
    Given a JSON trace object, extract and return a dictionary that contains information about the given aircraft such
    as registration, type, description, owner etc. All flight points

    Arguments
    ---------
    :trace: The trace from which to extract information.

    Raises
    ------
    NoAircraftStateInTrace: This trace does not contain aircraft state.

    Returns
    -------
    A dictionary, compatible with AircraftSchema.
    """
    try:
        # Get the date and aircraft's icao from this trace.
        day = datetime.utcfromtimestamp(int(trace["timestamp"])).date()
        icao = trace["icao"]
        # If the trace does not contain "r" and "t" and a "desc", raise NoAircraftStateInTrace.
        if not "r" in trace and not "t" in trace and not "desc" in trace:
            raise error.NoAircraftStateInTrace(trace["icao"], trace)
        # First, find a valid trace object in the trace. That is, where the 8th index is not None.
        valid_trace_object = next(filter(lambda trace_object: trace_object[8] != None and "flight" in trace_object[8], trace["trace"]))
        # If valid trace object is None, raise an exception.
        if not valid_trace_object:
            LOG.error(f"Failed to extract aircraft state from a trace for ICAO {icao} on day {day.isoformat()}! Could not find a valid trace object!")
            raise Exception("no-valid-trace")
        # Otherwise, get the flight name from the object.
        flight_name = valid_trace_object[8]["flight"].strip()
        # Now, create and return a new dictionary, compatible with AircraftSchema, representing the aircraft.
        return dict(
            icao = trace["icao"],
            type = trace["t"],
            registration = trace["r"],
            flightName = flight_name,
            description = trace["desc"],
            year = trace["year"],
            ownerOperator = trace["ownOp"],
            FlightPoints = []
        )
    except Exception as e:
        raise e


async def synchronise_flight_points(session, aircraft, flight_points, **kwargs):
    """
    Given an aircraft and a list of FlightPoint models, execute logic to synchronise these points with the database. For now, if the point already
    exists (determined only by pre-existence of flight_point_hash,) the flight point will have no further action taken. Otherwise, it will be added
    to the database. Return value will be a list of ALL flight points, whether new or old, but irrespective all synchronised.

    Arguments
    ---------
    :aircraft: The aircraft to which we will be adding these FlightPoints.
    :flight_points: The list of FlightPoints.

    Returns
    -------
    A list of FlightPoint, all synchronised.
    """
    try:
        LOG.debug(f"Synchronising {len(flight_points)} flight points for aircraft {aircraft.flight_name}...")
        # Spin up an iteration of all flight points.
        for flight_point in flight_points:
            # Attempt to fix any inaccuracies in this flight point.
            flight_point = airvehicles.attempt_flight_point_correction(aircraft, flight_point)
            # Get the flight point's timestamp, via the UTC timezone.
            day = datetime.utcfromtimestamp(int(flight_point.timestamp)).date()
            # Construct an insert for the flight point.
            insert_flight_point_stmt = (
                insert(models.FlightPoint.__table__)
                .values(
                    flight_point_hash = flight_point.flight_point_hash,
                    aircraft_icao = flight_point.aircraft_icao,
                    day_day = day,
                    timestamp = flight_point.timestamp,
                    altitude = flight_point.altitude,
                    ground_speed = flight_point.ground_speed,
                    rotation = flight_point.rotation,
                    vertical_rate = flight_point.vertical_rate,
                    is_on_ground = flight_point.is_on_ground,
                    is_ascending = flight_point.is_ascending,
                    is_descending = flight_point.is_descending,
                    synchronised = flight_point.synchronised,
                    crs = flight_point.crs,
                    point_geom = flight_point.point_geom,
                    utm_epsg_zone = flight_point.utm_epsg_zone
                )
            ).on_conflict_do_nothing(index_elements = ["flight_point_hash"])
            # Execute this insert.
            await session.execute(insert_flight_point_stmt)
        # Return our flight points.
        return flight_points
    except Exception as e:
        LOG.error(e, exc_info = True)
        raise e


async def import_aircraft_state(aircraft_icao, **kwargs):
    """
    Attempt to locate an aircraft's state in the imports/aircrafts.json file. If this file does not exist, or the aircraft does not exist within it,
    an error will be thrown. Otherwise, the aircraft's state will just be returned.

    Arguments
    ---------
    :aircraft_icao: The aircraft's ICAO to find in the aircrafts file.

    Raises
    ------
    NoAircraftStateInFile
    :no-aircrafts-file: The aircraft_states.json file does not exist.
    :no-aircraft: The aircraft with the given icao does not exist in the aircraft states file.

    Returns
    -------
    A dictionary; compatible with AircraftSchema.
    """
    try:
        # First, ensure the aircrafts JSON file exists. If it does not, raise an exception.
        aircrafts_absolute_path = os.path.join(os.getcwd(), config.IMPORTS_DIR, "aircraft_states.json")
        if not os.path.isfile(aircrafts_absolute_path):
            # No aircrafts file, raise.
            raise error.NoAircraftStateInFile(aircraft_icao, "no-aircrafts-file")
        # Read the aircrafts JSON list.
        async with aiofiles.open(aircrafts_absolute_path, "r") as f:
            aircraft_states_content = await f.read()
            aircraft_states_json = json.loads(aircraft_states_content)
        # Now, iterate all object and see if we can find the requested ICAO.
        for aircraft_state in aircraft_states_json:
            if aircraft_state["icao"].lower() == aircraft_icao.lower():
                return aircraft_state
        # We weren't able to find this aircraft.
        raise error.NoAircraftStateInFile(aircraft_icao, "no-aircraft")
    except Exception as e:
        raise e


async def normalise_trace_timestamps(trace_json, **kwargs):
    """
    Given a trace in JSON form, normalise all timestamps by iterating the 'trace' attribute, and for each, adding the outer 'timestamp'
    attribute to the first index. The return value is the same JSON object, but normalised.

    Arguments
    ---------
    :trace_json: A JSON object containing a relative trace.

    Returns
    -------
    The same JSON, normalised.
    """
    try:
        # Save our outer timestamp, this is in seconds.
        timestamp = trace_json["timestamp"]
        # Iterate all items in the 'trace' attribute, adding timestamp to the first index on each.
        for trace_object in trace_json["trace"]:
            trace_object[0] += timestamp
        # Return the trace json.
        return trace_json
    except Exception as e:
        raise e


async def merge_traces(traces, **kwargs):
    """
    Expecting a list of JSON objects, which each should correspond to a separate trace, but for the same aircraft, timestamps MUST be normalised
    prior to calling this function. By default, this function will allow overlapping of trace points as a byproduct of its function. All items
    in the given traces' 'trace' lists will simply be merged into a single list. This list will then be sorted ascending by the absolute timestamp
    value at index 0 of each point.

    Arguments
    ---------
    :traces: A list of JSON objects, each containing a trace.

    Keyword arguments
    -----------------
    :normalise: True if first, all traces should be normalised before merging. Default is False.

    Returns
    -------
    A single trace JSON object, timestamps normalised.
    """
    try:
        normalise = kwargs.get("normalise", False)

        if not len(traces):
            LOG.error(f"Failed to merge traces! None given!")
            raise Exception("no-traces")
        # Use the first trace as the reference.
        reference_trace = traces[0]
        # If first index of first trace object of first trace is 0, this is relative timestamps. Fail unless we are going to be normalising all timestamps first.
        if not normalise and reference_trace["trace"][0][0] == 0:
            LOG.error(f"Failed to merge traces! Please normalise timestamps prior to calling merge!")
            raise Exception("cant-merge-relative-timestamps")
        # If normalise is True, overwrite traces list with a new list comprehension of each trace normalised.
        if normalise:
            traces = await asyncio.gather(*[ normalise_trace_timestamps(input_trace) for input_trace in traces ])
        # Now, assemble a huge list of all trace points.
        all_trace_points = []
        for trace in traces:
            # For each trace, extend the master trace pts list by its 'trace' list attribute.
            all_trace_points.extend(trace["trace"])
        # Sort all trace points by index 0, ascending.
        all_trace_points.sort(key = lambda trace_point: trace_point[0])
        # Now, create a new resulting basic trace dict, with all trace points in there.
        merged_trace = {
            "icao": reference_trace["icao"],
            "timestamp": reference_trace["timestamp"],
            "trace": all_trace_points
        }
        # Now, iterate optional keys and set them in merged trace only if they exist in reference trace.
        for key in ["r", "t", "desc", "ownOp", "year"]:
            if key in reference_trace:
                merged_trace[key] = reference_trace[key]
        # Return this merged trace.
        return merged_trace
    except Exception as e:
        raise e


async def import_aircraft_trace_on_day(session, aircraft_icao, day, files, **kwargs):
    """
    Given an aircraft ICAO, a Date instance, and a list of absolute paths to each trace file, import all trace from the given absolute paths.
    Importation will be done by first reading ALL contributing trace files and merging them. Then, the aircraft will be read from the trace.
    If the aircraft does not already exist, it will be created, otherwise no further action toward the aircraft will be taken (old trace=out of date.)

    Arguments
    ---------
    :aircraft_icao: The aircraft's ICAO.
    :day: An instance of Date, representing this day.
    :files: A list of absolute file paths to each trace file of a varying type belonging to the aircraft.

    Raises
    ------
    HistoryVerifiedError: This aircraft/day combination has already been reported as having verified history

    Returns
    -------
    The Aircraft instance.
    """
    try:
        find_aircraft_present_day_stmt = (
            select(models.AircraftPresentDay)
            .where(and_(models.AircraftPresentDay.day_day == day, models.AircraftPresentDay.aircraft_icao == aircraft_icao))
            .where(models.AircraftPresentDay.history_verified == True)
        )
        aircraft_present_day_result = await session.execute(find_aircraft_present_day_stmt)
        aircraft_present_day = aircraft_present_day_result.scalar()
        if aircraft_present_day:
            # If we are able to find a report of history verified for this aircraft on this day, skip importing trace.
            LOG.warning(f"Skipping importation of trace for aircraft {aircraft_icao} on day {day.isoformat()}, history for this day is verified.")
            raise error.HistoryVerifiedError(aircraft_icao)
        LOG.debug(f"Importing all trace for aircraft with icao {aircraft_icao} on {day.isoformat()}, there is {len(files)} file(s) in total for this day.")
        # Read each file in the given list, parse the JSON and add the resulting object to a new list.
        async def read_json_from(file):
            async with aiofiles.open(file, "r") as f:
                file_contents = await f.read()
                file_json = json.loads(file_contents)
            return file_json
        # Our list for storing all read files.
        all_trace_data_json = await asyncio.gather(*[ read_json_from(file) for file in files ])
        LOG.debug(f"Successfully read {len(all_trace_data_json)} trace data JSONs from the given files.")
        # Now, normalise and merge all trace data in the input list.
        merged_trace = await merge_traces(all_trace_data_json, normalise = True)
        # Now, we require the target aircraft's instance. What we'll do first is attempt to locate the aircraft in our database. Failing that, we'll
        # fall back to aircraft state extraction or querying.
        find_aircraft_stmt = (
            select(models.Aircraft)
            .where(models.Aircraft.icao == aircraft_icao)
        )
        find_aircraft_result = await session.execute(find_aircraft_stmt)
        aircraft = find_aircraft_result.scalar()
        if not aircraft:
            LOG.warning(f"Failed to find existing aircraft in database with icao {aircraft_icao}, attempting alternative sources...")
            try:
                # Now, extract aircraft state data from this merged trace. This may throw a no aircraft state in trace error, in which case, we must
                # use an alternative method, perhaps requests, to query as much of this aircraft's info from another source as we can.
                aircraft_state = await extract_aircraft_state(merged_trace)
            except error.NoAircraftStateInTrace as nasit:
                try:
                    # Attempt to locate this aircraft's state in the aircraft states JSON file.
                    aircraft_state = await import_aircraft_state(aircraft_icao)
                except error.NoAircraftStateInFile as nasif:
                    if nasif.reason == "no-aircrafts-file":
                        LOG.error(f"Failed to import aircraft state for icao {aircraft_icao}, the aircrafts state file does not exist.")
                    elif nasif.reason == "no-aircraft":
                        LOG.error(f"Failed to import aircraft state for icao {aircraft_icao}, the file does not contain a state for this aircraft.")
                    """TODO: we must now query from another source. Perhaps use requests to query planefinder, flightaware etc."""
                    aircraft_state = None
                    raise NotImplementedError()
            # Now we have this aircraft's state, we will create a new Aircraft from it using AircraftSchema.
            aircraft_d = airvehicles.AircraftSchema().load(aircraft_state)
            # Create an aircraft model.
            aircraft = models.Aircraft(**aircraft_d)
            # Perform an insert for this aircraft.
            insert_aircraft_stmt = (
                insert(models.Aircraft.__table__)
                .values(
                    icao = aircraft.icao,
                    type = aircraft.type,
                    flight_name = aircraft.flight_name,
                    registration = aircraft.registration,
                    description = aircraft.description,
                    year = aircraft.year,
                    owner_operator = aircraft.owner_operator,
                )
            ).on_conflict_do_nothing(index_elements = ["icao"])
            await session.execute(insert_aircraft_stmt)
            LOG.debug(f"Successully sourced aircraft {aircraft.flight_name} ({aircraft.icao}) from alt source.")
        # Extract all flight points.
        extracted_flight_points = await asyncio.gather(*[ extract_flight_point(aircraft_icao, trace_object) for trace_object in merged_trace["trace"] ])
        # Now we can continue, comprehend all flight points in our merged trace; we want a list of loaded FlightPoints.
        flight_points = [
            airvehicles.FlightPointSchema().load(flight_point_state)
            for flight_point_state in extracted_flight_points
        ]
        # Then, we will invoke the airvehicles module to synchronise all these flight points to our aircraft instance.
        synchronised_flight_points = await synchronise_flight_points(session, aircraft, flight_points)
        LOG.debug(f"Finished importing all trace data for {aircraft.flight_name} ({aircraft.icao}) on day {day.isoformat()}. We imported {len(flight_points)} total points.")
        # Finally, report this aircraft, on this day, as having its presence verified, but its flights as not verified. We'll also set positional as verified; as there's no way to get those lost points.
        # We'll also set geolocation verified to False, as this probably still needs to be done.
        insertion_state_values = dict(
            history_verified = True,
            flights_verified = False,
            geolocation_verified = False
        )
        insert_aircraft_present_day_stmt = (
            insert(models.AircraftPresentDay.__table__)
            .values(
                aircraft_icao = aircraft.icao,
                day_day = day,
                **insertion_state_values
            )
        ).on_conflict_do_update(
            index_elements = ["aircraft_icao", "day_day"],
            set_ = insertion_state_values
        )
        await session.execute(insert_aircraft_present_day_stmt)
        # Return the aircraft.
        return aircraft
    except Exception as e:
        raise e


async def import_traces_from_(session, relative_trace_dir = os.path.join(config.TRACES_DIR), **kwargs):
    """
    Import all trace data from the TRACES_DIR directory. The contents of this folder is expected to be a list of folders, each named after the day it represents.
    Within each day directory, multiple JSON files containing trace data for a specific aircraft will be found. There may be multiple files for a single aircraft,
    as long as the naming convention is consistent; <icao>_full/recent.json

    Each of these file names will be grouped by their ICAOs, then read in sequence. All traces for a single ICAO will be merged, then transformed into an Aircraft
    dictionary containing the flight points. This will be committed to the database as usual, the day containing these traces will also be marked as verified,
    given the aircraft the ICAO represents.

    This function should never be auto-invoked, and should only be run if explicitly required to by direct command line command.

    Arguments
    ---------
    :relative_trace_dir: A relative directory, from current working, to the directory from which we will import all traces.

    Keyword arguments
    -----------------
    :should_geolocate: True if all flight points should be geolocated as soon as a day is done being imported.

    Returns
    -------
    A list of Aircraft.
    """
    try:
        should_geolocate = kwargs.get("should_geolocate", False)

        time_started = time.time()
        result_aircraft = []
        LOG.debug(f"Reading all trace data from {relative_trace_dir}")
        # First, list all directories.
        all_day_directories_path = os.path.join(os.getcwd(), relative_trace_dir)
        # Sort them in ascending order.
        all_day_directories = sorted(os.listdir(all_day_directories_path), reverse = False)
        LOG.debug(f"Located {len(all_day_directories)} directories from which we can read trace data.")
        # Begin iterating each directory.
        for day_directory in all_day_directories:
            # Ensure what we're looking at is a directory.
            day_directory_files_path = os.path.join(all_day_directories_path, day_directory)
            if not os.path.isdir(day_directory_files_path):
                LOG.warning(f"Skipping import of directory; {day_directory_files_path}, it is NOT a directory.")
                continue
            # Day directory will also be an ISO format date for that day; so get that now, too.
            """TODO: handle error arising from non-date format directory name"""
            day = date.fromisoformat(day_directory)
            LOG.debug(f"Reading ALL potential trace data from day directory; {day_directory}")
            # Now, list this directory.
            day_directory_files = os.listdir(day_directory_files_path)
            LOG.debug(f"{len(day_directory_files)} potential trace data found in day {day_directory}")
            # Iterate these files, and check them against a regular expression, ensuring the first part is a 6 char hex value, the second is either full or recent and finally,
            # that the file type is .json. We'll create a new dictionary of those files found to be valid. The dictionary will associate the ICAO with all filenames of that
            # same icao, but different types (full/recent.)
            trace_files = {}
            for filename in day_directory_files:
                # Check with re.
                filename_match = re.match(r"^(\w{6})_(full|recent)\.json$", filename)
                if filename_match:
                    icao = filename_match.group(1)
                    type = filename_match.group(2)
                    LOG.debug(f"Found potential trace file {filename} in day {day_directory} for ICAO {icao}")
                    # Add this to this icao's filename array under the icao key in trace files dictionary.
                    icao_filenames = trace_files.get(icao, [])
                    icao_filenames.append(filename)
                    trace_files[icao] = icao_filenames
            # Now, we will import each trace group separately. First, just announce how many GROUPS we found.
            LOG.debug(f"Given the potential trace files in day {day_directory}, we located {len(trace_files.keys())} different aircrafts.")
            # Now, iterate the trace_files dictionary as tuples. We will then import trace on each group.
            day_import_started = time.time()
            async def import_icao_trace(icao, filenames):
                try:
                    absolute_filenames = [ os.path.join(day_directory_files_path, filename) for filename in filenames ]
                    return await import_aircraft_trace_on_day(session, icao, day, absolute_filenames)
                except error.HistoryVerifiedError as hve:
                    # Just continue.
                    return None
            result_aircraft = await asyncio.gather(*[ import_icao_trace(icao, filenames) for icao, filenames in trace_files.items() ])
            day_import_ended = time.time()
            LOG.debug(f"Importation of all aircraft trace from day {day.isoformat()} took {day_import_ended-day_import_started} seconds!")
            await session.flush()
        time_ended = time.time()
        LOG.debug(f"Importation of all trace took {time_ended-time_started} seconds!")
        return result_aircraft
    except Exception as e:
        raise e


class ImportTracesResult():
    """
    """
    @property
    def is_error(self):
        return self.error != None

    def __init__(self, _relative_trace_dir, **kwargs):
        self.relative_trace_dir = _relative_trace_dir
        self.error = kwargs.get("error", None)
        self.num_aircraft_imported = kwargs.get("num_aircraft_imported", 0)
        self.num_flight_points_imported = kwargs.get("num_flight_points_imported", 0)


async def import_traces_from(relative_trace_dir = os.path.join(config.TRACES_DIR), **kwargs):
    """

    Keyword arguments
    -----------------
    :should_geolocate: True if all flight points should be geolocated day by day.
    """
    try:
        should_geolocate = kwargs.get("should_geolocate", False)

        # Hold our eventual result.
        import_traces_result = None
        async with aio.open_db() as engine:
            async_session_factory = sessionmaker(engine,
                expire_on_commit = False, class_ = AsyncSession)
            async with aio.session_scope(async_session_factory) as session:
                # Import traces.
                await import_traces_from_(session, relative_trace_dir,
                    should_geolocate = should_geolocate)
                # Collect some diagnostic information.
                num_aircraft_result = await session.execute(select(func.count(models.Aircraft.icao)))
                num_flight_point_result = await session.execute(select(func.count(models.FlightPoint.flight_point_hash)))
                # Set result.
                import_traces_result = ImportTracesResult(relative_trace_dir,
                    num_aircraft_imported = num_aircraft_result.scalar(), num_flight_points_imported = num_flight_point_result.scalar())
        # Now, return import traces result.
        return import_traces_result
    except OperationalError as oe:
        if oe.code == "e3q8":
            return ImportTracesResult(relative_trace_dir, error = f"Failed to import traces from {relative_trace_dir}, please setup database tables prior to attempting this.")
        else:
            raise oe
