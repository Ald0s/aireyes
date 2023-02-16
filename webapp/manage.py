from app import compat
compat.monkey_patch_sqlite()

import os
import json
import click
import requests
import logging
import asyncio
import time
import shutil
import tarfile
import pandas
from datetime import date, datetime, timezone, timedelta

from flask import current_app, url_for
from sqlalchemy_utils import create_database, database_exists
from sqlalchemy import func, and_, or_, asc, desc, event
from sqlalchemy.exc import UnsupportedCompilationError
from requests.exceptions import ConnectionError

from app.compat import insert

from app import create_app, db, config, models, radarworker, airvehicles, traces, aiotraces, flights, error, geospatial, compat

LOG = logging.getLogger("aireyes.manage")
LOG.setLevel( logging.DEBUG )

application = create_app()


def delete_directory(directory):
    try:
        shutil.rmtree(directory)
    except Exception as e:
        print(e)


def make_dir(directory):
    try:
        os.mkdir(directory)
    except IOError as io:
        pass


@application.cli.command("init-db", help = "Creates the database and all tables, only if they do not exist.")
def init_db():
    if config.APP_ENV != "Production":
        # Just ensure our db is created.
        db.create_all()
    else:
        # Otherwise, check if our current database exists.
        if not database_exists(config.SQLALCHEMY_DATABASE_URI):
            create_database(config.SQLALCHEMY_DATABASE_URI)
        db.create_all()
    try:
        models.Master.get()
    except error.NoMasterError as nme:
        LOG.debug(f"Creating a new Master instance - it does not exist yet.")
        # Create a new master instance here.
        # Create a UTC date for today.
        utc_datetime = datetime.utcnow()
        current_day_date = utc_datetime.date()
        # We will now load all aircraft we should track by default. They will all be added to the session separately, but we'll associate them with the master record also.
        LOG.debug(f"Creating master record - loading known aircraft config...")
        tracked_aircraft = airvehicles.read_aircraft_from(config.KNOWN_AIRCRAFT_CONFIG)
        LOG.debug(f"Creating master record - loaded {len(tracked_aircraft)} aircraft.")
        # Create the new master record.
        models.Master.new(current_day_date, tracked_aircraft)
    db.session.flush()
    db.session.commit()


@application.cli.command("execute-workers", help = "Attempts a radar worker execution pass.")
def execute_workers():
    # We must first determine whether the master server is actually running.
    LOG.debug(f"Attempting to execute radar workers, first checking master server status...")
    try:
        try:
            """TODO 0x11: improve how we construct this URL..."""
            target_url = f"http://127.0.0.1:{config.PORT}/api/worker/master"
            response = requests.get(target_url,
                headers = {"User-Agent": "aireyes/slave"})
            if response.status_code != 200:
                raise error.MasterServerOfflineError()
            LOG.debug(f"Master server is ONLINE! Running radar worker execution pass.")
        except ConnectionError as ce:
            raise error.MasterServerOfflineError()
    except error.MasterServerOfflineError as msofe:
        LOG.debug(f"Master server is OFFLINE. Skipping radar worker execution pass.")
        return
    radarworker.radar_worker_execution_pass()
    db.session.commit()


@application.cli.command("show-workers", help = "Locates and displays information about all running radar workers.")
def show_workers():
    LOG.debug(f"Querying all running radar workers...")
    running_workers = radarworker.query_running_workers()
    for idx, running_worker_ in enumerate(running_workers):
        radar_worker, start_command_d = running_worker_
        name = start_command_d["name"]
        type_ = start_command_d["worker_type"]
        # Print the report.
        print(f"Radar Worker #{idx+1}\nName: {name}\nPID: {radar_worker.pid}\nType: {type_}\nStatus: {radar_worker.status_str}\nExecuted: {radar_worker.executed_at}\n")


@application.cli.command("reset-workers", help = "")
def reset_workers():
    LOG.debug(f"Resetting all workers...")
    for radar_worker in models.RadarWorker.query.all():
        try:
            radarworker.shutdown_worker(radar_worker, "Reset by manage")
        except Exception as e:
            pass
    db.session.commit()


@application.cli.command("shutdown-worker", help = "Locates an active worker by the given name and shuts it down.")
@click.argument("name")
def shutdown_worker(name):
    LOG.debug(f"Searching for and shutting down radar worker by name; '{name}'")
    running_workers = radarworker.query_running_workers(names = [name])
    for idx, running_worker_ in enumerate(running_workers):
        radar_worker, start_command_d = running_worker_
        LOG.debug(f"Found targeted radar worker; {radar_worker}")
        # Shutdown the radar worker.
        radarworker.shutdown_worker(radar_worker, "Shutdown requested by aireyes manage.")
    db.session.commit()


@application.cli.command("show-active-aircraft", help = "Collects and displays a brief for each aircraft currently active.")
def show_active_aircraft():
    LOG.debug(f"Querying active aircraft...")
    # We'll query all monitored aircraft.
    monitored_aircraft = airvehicles.get_monitored_aircraft(active_first = True)
    for idx, aircraft in enumerate(monitored_aircraft):
        # Ordered by active first so as soon as its not active, break.
        if not aircraft.is_active_now:
            break
        # We'll now print a brief report for this active aircraft.
        print(f"Aircraft #{idx+1}\nICAO: {aircraft.icao}\nName: {aircraft.flight_name}\nType: {aircraft.type}\nRegistration: {aircraft.registration}\nLast seen (s): {aircraft.seconds_since_last_seen}\n")


@application.cli.command("inspect-flight-data", help = "Given a native format flight data for an aircraft, extract and display some statistical data about it.")
@click.argument("filename", type = click.Path(exists = True))
def inspect_flight_data(filename):
    """Given a relative file path from working directory, we will open and read the flight data, then print out some info about it."""
    with open(os.path.join(os.getcwd(), filename), "r") as f:
        file_contents = f.read()
        flight_data_json = json.loads(file_contents)
    # Read info about this aircraft.
    icao = flight_data_json["icao"]
    flight_name = flight_data_json["flightName"]
    # Read info about the flight data itself.
    num_flight_points = len(flight_data_json["FlightPoints"])
    first_flight_point = flight_data_json["FlightPoints"][0]
    last_flight_point = flight_data_json["FlightPoints"][num_flight_points-1]
    # When did the data begin, and when did it end?
    start_point_datetime = datetime.utcfromtimestamp(first_flight_point["timestamp"])
    end_point_datetime = datetime.utcfromtimestamp(last_flight_point["timestamp"])
    # Determine all dates this flight data spans across.
    dates_span_across = {}
    # Iterate all flight points, get just a date from each, and if that date is not in the dict, increment the value integer at that date.
    for point in flight_data_json["FlightPoints"]:
        point_date = datetime.utcfromtimestamp(point["timestamp"]).date()
        if not point_date in dates_span_across:
            dates_span_across[point_date] = 1
        else:
            dates_span_across[point_date] += 1
    start_point_datetime = start_point_datetime.strftime("%Y-%m-%d %H:%M:%S Z")
    end_point_datetime = end_point_datetime.strftime("%Y-%m-%d %H:%M:%S Z")
    ### Now, print info for this flight data. ###
    print(f"Printing flight data information")
    print(f"--------------------------------")
    print(f"From:\t\t{filename}")
    print(f"Flight Name:\t{flight_name}")
    print(f"Aircraft ICAO\t{icao}\n")
    print(f"Data start:\t{start_point_datetime}")
    print(f"Data end:\t{end_point_datetime}\n")
    print(f"Flight point breakdown")
    print(f"----------------------")
    print(f"Number of flight points: {num_flight_points}")
    print(f"Number of flight points per day")
    # Now, for each of these, print a breakdown of the number of points for each day.
    for d, num_points in dates_span_across.items():
        print(f"\tOn {d.isoformat()}:\t{num_points} recorded.")


@application.cli.command("check-days", help = "Considers the days range provided in configuration, and creates a Day model for those missing.")
def check_days():
    try:
        # Now we get master record.
        master = models.Master.get()
        # Update the current day.
        utc_datetime = datetime.utcnow()
        date_today = utc_datetime.date()
        if date_today != master.current_day:
            LOG.debug(f"Updating master's current day from {master.current_day} to {date_today}")
            master.current_day = date_today
    except error.NoMasterError as nme:
        # No master yet, not our responsibility to create it.
        pass
    # Get start and end dates. If end is None, get todays date.
    start_date = config.DATA_SOURCE_DAY_RANGE[0]
    end_date = config.DATA_SOURCE_DAY_RANGE[1]
    if end_date == None:
        end_date = date.today()
    # Create all days between these two (inclusive.)
    LOG.debug(f"Creating all Day models for dates between {start_date.isoformat()} -> {end_date.isoformat()}")
    days_to_create = pandas.date_range(start_date, end_date, freq = "d").to_list()
    traces.ensure_days_created(days_to_create)
    db.session.commit()


@application.cli.command("import-known-aircraft", help = "Imports all already-known aircraft from the aircraft_states JSON file..")
def import_known_aircraft():
    # Invoke airvehicles module to read all known aircraft. These aircraft will be tracked from the beginning.
    airvehicles.read_aircraft_from("aircraft_states.json")
    db.session.commit()


@application.cli.command("import-airports", help = "Import all Airports from the airports source file.")
def import_airports():
    # Invoke airvehicles module to read all known aircraft.
    airvehicles.read_airports_from("airports.json")
    db.session.commit()


@application.cli.command("verify-aircraft-day", help = "Ensures that, for all aircraft in the database, there exists an AircraftPresentDay junction record.")
def verify_aircraft_day():
    """
    We will ensure that all aircraft in the database has a junction record with each day in the database. We'll perform this task by iterating each aircraft,
    and for each aircraft, querying a list of Day records upon which they CAN'T join due to a missing AircraftPresentDay junction table. We'll then instantiate
    a junction table for each of these entries.

    This manage function should be called once per day.
    """
    # Begin by getting all aircraft.
    all_aircraft = db.session.query(models.Aircraft).all()
    # Now, iterate the aircraft.
    for aircraft in all_aircraft:
        # Now, perform a query for all DAY records, where there is no record of an AircraftPresentDay between this aircraft and that day.
        required_days = db.session.query(models.Day)\
            .outerjoin(models.AircraftPresentDay, and_(models.AircraftPresentDay.day_day == models.Day.day, models.AircraftPresentDay.aircraft_icao == aircraft.icao))\
            .filter(models.AircraftPresentDay.day_day == None)\
            .all()
        # Report statistics on how many was located.
        LOG.debug(f"For {aircraft.flight_name}, we discovered {len(required_days)} days that need creating, the aircraft already has {aircraft.num_days_present} days active.")
        # Now, perform an insert statement for each day, that will do nothing on conflict for safety.
        for required_day in required_days:
            insert_aircraft_present_day_stmt = (
                insert(models.AircraftPresentDay.__table__)
                .values(
                    day_day = required_day.day,
                    aircraft_icao = aircraft.icao,
                    history_verified = False,
                    flights_verified = False
                )
            ).on_conflict_do_nothing(index_elements = ["day_day", "aircraft_icao"])
            db.session.execute(insert_aircraft_present_day_stmt)
            db.session.flush()
        LOG.debug(f"Completed aircraft-day verification for {aircraft.flight_name} ({aircraft.icao})")
    # Commit this to the database.
    db.session.commit()


@application.cli.command("import-radar-workers", help = "Imports radar worker configuration and persists updates.")
def import_radar_workers():
    # Invoke radarworker module to read and update all workers.
    radarworker.read_radar_workers_from("worker.conf")
    db.session.commit()


@application.cli.command("import-trace-data", help = "Imports all existing aircraft trace data from /traces/, this can take FOREVER. Optionally, this can also geolocate the flight points after importation.")
@click.option("-t", "--from-tar", default = False, is_flag = True)
@click.option("-g", "--geolocate", default = False, is_flag = True)
def import_trace_data(from_tar, geolocate):
    try:
        # Relative path to the traces directory.
        traces_dir = config.TRACES_DIR
        # A path to the temporary traces directory, where we extract the tar to. Relative directory from current working directory.
        temporary_traces_absolute_dir = None
        if from_tar:
            # Make a temporary traces relative directory.
            traces_dir = os.path.join("traces-temp")
            temporary_traces_absolute_dir = os.path.join(os.getcwd(), traces_dir)
            delete_directory(temporary_traces_absolute_dir)
            make_dir(temporary_traces_absolute_dir)
            # If we should read from tarball, we will now use tarfile to extract the entire trace history.
            tar_absolute_path = os.path.join(os.getcwd(), config.IMPORTS_DIR, config.TRACES_TARBALL)
            if not os.path.isfile(tar_absolute_path):
                LOG.error(f"Failed to read trace data from tar, the file does NOT exist! Path: {tar_absolute_path}")
                raise Exception("no-tarball-file")
            LOG.debug(f"Importing trace data from tar; {tar_absolute_path}...")
            with tarfile.open(tar_absolute_path, "r:gz") as traces:
                traces.extractall(path = temporary_traces_absolute_dir)
        # Asynchronously import all trace data from the traces directory. This function handles commit.
        importation_result = asyncio.run(aiotraces.import_traces_from(traces_dir,
            should_geolocate = geolocate))
        if importation_result.is_error:
            LOG.error(importation_result.error)
        else:
            LOG.info(f"Aircraft: {importation_result.num_aircraft_imported}, flight points: {importation_result.num_flight_points_imported}")
    except Exception as e:
        LOG.error(e, exc_info = True)
    finally:
        if from_tar:
            delete_directory(temporary_traces_absolute_dir)


@application.cli.command("import-suburbs", help = "Imports all geospatial data such as Suburb information.")
@click.option("-t", "--from-tar", default = False, is_flag = True)
def import_suburb_data(from_tar):
    try:
        # Relative path to the suburbs directory.
        suburbs_dir = config.SUBURBS_DIR
        # A path to the temporary suburbs directory, where we extract the tar to. Relative directory from current working directory.
        temporary_suburbs_absolute_dir = None
        if from_tar:
            # Make a temporary suburbs relative directory.
            suburbs_dir = os.path.join("suburbs-temp")
            temporary_suburbs_absolute_dir = os.path.join(os.getcwd(), suburbs_dir)
            delete_directory(temporary_suburbs_absolute_dir)
            make_dir(temporary_suburbs_absolute_dir)
            # If we should read from tarball, we will now use tarfile to extract the entire suburbs tar. config.IMPORTS_DIR
            tar_absolute_path = os.path.join(os.getcwd(), config.IMPORTS_DIR, config.SUBURBS_TARBALL)
            if not os.path.isfile(tar_absolute_path):
                LOG.error(f"Failed to read suburbs data from tar, the file does NOT exist! Path: {tar_absolute_path}")
                raise Exception("no-tarball-file")
            LOG.debug(f"Importing suburb data from tar; {tar_absolute_path}...")
            with tarfile.open(tar_absolute_path, "r:gz") as suburbs:
                suburbs.extractall(path = temporary_suburbs_absolute_dir)
        # Import geospatial suburb data & process relationships for it all.
        importation_result = geospatial.read_suburbs_from(suburbs_dir, process_neighbourships = True)
        db.session.commit()
    except Exception as e:
        LOG.error(e, exc_info = True)
    finally:
        if from_tar:
            delete_directory(temporary_suburbs_absolute_dir)


@application.cli.command("revise-flights", help = "Searches for all aircraft/days where flights data has not yet been verified and processes them.")
def revise_flights_data():
    # Searches for and revises flight data for all aircraft/day instances where history verified is True but flights verified is False.
    appropriate_aircraft_days = db.session.query(models.AircraftPresentDay)\
        .filter(models.AircraftPresentDay.history_verified == True)\
        .filter(models.AircraftPresentDay.flights_verified == False)\
        .order_by(asc(models.AircraftPresentDay.day_day))\
        .all()
    LOG.debug(f"Located {len(appropriate_aircraft_days)} aircraft/day combinations that require flight revision.")
    days_complete = 0
    created = 0
    updated = 0
    error = 0
    for aircraft_present_day in appropriate_aircraft_days:
        try:
            revision_receipt = flights.revise_flight_data_for(aircraft_present_day.aircraft, aircraft_present_day.day_day)
            LOG.debug(f"Finished revising flight data for {aircraft_present_day} (started={revision_receipt.started},finished={revision_receipt.finished}). We got back {revision_receipt.num_flights}, the actual breakdown; created {revision_receipt.num_created}, updated {revision_receipt.num_updated} and {revision_receipt.num_error} failed.")
            created+=revision_receipt.num_created
            updated+=revision_receipt.num_updated
            error+=revision_receipt.num_error
            db.session.flush()
            days_complete+=1
        except Exception as e:
            # Just log a warning for it.
            LOG.warning(e, exc_info = True)
        finally:
            if days_complete == 50:
                days_complete = 0
                db.session.commit()
    LOG.debug(f"Finished revising aircraft/day combos. In total; created {created}, updated {updated} and failed {error} flights.")
    db.session.commit()


@application.cli.command("revise-flights-for", help = "Given an aircraft icao and a date (ISO format; YYYY-MMM-DD), revise all flights for that combination.")
@click.argument("aircraft_icao")
@click.argument("day_iso")
def revise_flights_for_icao_day(aircraft_icao, day_iso):
    # Locates an aircraft for the given aircraft icao, and ensures it exists.
    aircraft = models.Aircraft.get_by_icao(aircraft_icao)
    # Parse the day as a date.
    day = date.fromisoformat(day_iso)
    LOG.debug(f"Revising flight data for aircraft {aircraft} and day {day}")
    # Now revise the flight data for this aircraft/day.
    revision_receipt = flights.revise_flight_data_for(aircraft, day, force = True)
    LOG.debug(f"Finished revising flight data for {aircraft} on {day} (started={revision_receipt.started},finished={revision_receipt.finished}). We got back {revision_receipt.num_flights}, the actual breakdown; created {revision_receipt.num_created}, updated {revision_receipt.num_updated} and {revision_receipt.num_error} failed.")
    db.session.commit()


@application.cli.command("geolocate-flight-points", help = "Searches for all aircraft/days where flight points geolocation data has not yet been verified and processes them.")
def geolocate_flight_points():
    # Searches for and revises flight point geolocation data for all aircraft/day instances where geolocation verified is False AND trace verified is True.
    # Trace verified MUST be true, otherwise this function is skipped.
    appropriate_aircraft_days = db.session.query(models.AircraftPresentDay)\
        .filter(models.AircraftPresentDay.history_verified == True)\
        .filter(models.AircraftPresentDay.geolocation_verified == False)\
        .order_by(asc(models.AircraftPresentDay.day_day))\
        .all()
    LOG.debug(f"Located {len(appropriate_aircraft_days)} aircraft/day combinations that require flight point geolocation. This may take FOREVER!!!")
    days_complete = 0
    geolocated = 0
    overwritten_geolocated = 0
    skipped = 0
    error = 0
    # Instantiate a locator, so we reuse the same one. This should speed things up.
    locator = geospatial.GeospatialFlightPointLocator()
    for aircraft_present_day in appropriate_aircraft_days:
        try:
            revision_receipt = geospatial.revise_geolocation_for(aircraft_present_day.aircraft, aircraft_present_day.day_day,
                locator = locator)
            LOG.debug(f"Finished revising geolocation for flight points on {aircraft_present_day} (started={revision_receipt.started},finished={revision_receipt.finished}). We geolocated {revision_receipt.num_geolocated}, overwrote {revision_receipt.num_overwritten_geolocated} skipped {revision_receipt.num_skipped} and {revision_receipt.num_error} failed.")
            geolocated+=revision_receipt.num_geolocated
            overwritten_geolocated+=revision_receipt.num_overwritten_geolocated
            skipped+=revision_receipt.num_skipped
            error+=revision_receipt.num_error
            db.session.flush()
            days_complete+=1
        except Exception as e:
            # Just log a warning for it.
            LOG.warning(e, exc_info = True)
        finally:
            if days_complete == 50:
                days_complete = 0
                # Commit every 50 days.
                db.session.commit()
    LOG.debug(f"Finished revising aircraft/day flight point geolocation. In total; geolocated {geolocated}, overwrote {overwritten_geolocated}, skipped {skipped} and failed {error} flights.")
    db.session.commit()


@application.cli.command("statistics", help = "")
def statistics():
    num_aircraft = models.Aircraft.query.count()
    num_flight_points = models.FlightPoint.query.count()
    print(f"Aircraft: {num_aircraft}, flight points: {num_flight_points}")
