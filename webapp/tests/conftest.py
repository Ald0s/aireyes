import os
import simplejson as json
from datetime import datetime, date

import pyproj
from shapely import geometry

from flask import g
from flask_testing import TestCase
from flask_login import FlaskLoginClient

from app import create_app, db, config, models, airvehicles, flights, compat, error


from sqlalchemy import event

class BaseCase(TestCase):

    def create_app(self):
        test_app = create_app()
        with test_app.app_context():
            # If PostGIS enabled and dialect is SQLite, we require SpatiaLite.
            if config.POSTGIS_ENABLED and db.engine.dialect.name == "sqlite":
                compat.should_load_spatialite_sync(db.engine)
        return test_app

    def setUp(self):
        db.create_all()
        try:
            models.Master.get()
        except error.NoMasterError as nme:
            utc_datetime = datetime.utcnow()
            current_day_date = utc_datetime.date()
            tracked_aircraft = airvehicles.read_aircraft_from(config.KNOWN_AIRCRAFT_CONFIG)
            models.Master.new(current_day_date, tracked_aircraft)
        db.session.flush()

    def tearDown(self):
        if "date_today" in g:
            g.date_today = None
        if "timestamp_now" in g:
            g.timestamp_now = None
        if "datetime_now" in g:
            g.datetime_now = None
        db.session.remove()
        db.drop_all()

    def reset_current_datetimenow(self):
        g.timestamp_now = None
        g.datetime_now = None

    def set_current_timestamp(self, timestamp):
        g.timestamp_now = float(timestamp)
        g.datetime_now = datetime.fromtimestamp(timestamp)

    def set_datetimenow(self, datetime):
        g.datetime_now = datetime
        g.timestamp_now = datetime.timestamp()

    def set_date_today(self, date):
        g.date_today = date

    def _submit_flight_point_dicts(self, aircraft_d, flight_points_d, **kwargs):
        """
        Submit the given flight points to the database along with the aircraft. Both the aircraft and flight points must be in dictionary form, that is,
        they should be dictionaries ready to be loaded as a AircraftSchema and FlightPointSchema.
        """
        aircraft_d["FlightPoints"] = flight_points_d
        aircraft = airvehicles.AircraftSchema().load(aircraft_d)
        aircraft, flight_points, synchronised_flight_points = airvehicles.aircraft_submitted(aircraft, **kwargs)
        db.session.flush()
        return aircraft, flight_points, synchronised_flight_points

    def _load_native_test_data(self, filename, **kwargs):
        """
        Given a filename, relative to testdata/native_testdata, load the contents and parse it all as a single aircraft, output of which is in the format
        given by maketestdata.js. This will then load the aircraft and its points for all days in the data into the database. For each day, a new AircraftPresentDay
        is created, whose state values can be modified via keyword args. Finally, the aircraft and all dates upon which that aircraft presents itself is returned.

        Keyword arguments
        -----------------
        :only_load_points_from: A list of Date instances. Flight points not on this date will be removed.
        :flights_verified: Is flights data verified on all days? Default False.
        :history_verified: Is history data verified on all days? Default False.
        """
        only_load_points_from = kwargs.get("only_load_points_from", [])
        flights_verified = kwargs.get("flights_verified", False)
        history_verified = kwargs.get("history_verified", False)

        # Load, as json, the target file.
        with open(os.path.join(os.getcwd(), config.TESTDATA_DIR, "native_testdata", filename), "r") as f:
            aircraft_json = json.loads(f.read())
        # Load the stored object with AircraftSchema; get back a dict for Aircraft.
        aircraft_d = airvehicles.AircraftSchema().load(aircraft_json)
        # Filter all flight points to only include those in only_load_points_from.
        if len(only_load_points_from):
            aircraft_d["flight_points"] = list(filter(lambda flight_point: flight_point.day_day in only_load_points_from, aircraft_d["flight_points"]))
        # Now, create each Day instance that will be required, in advance.
        dates = []
        for flight_point in aircraft_d["flight_points"]:
            day = flight_point.day_day
            # If not in dates list, add it.
            if not day in dates:
                dates.append(day)
            # Now, attempt to get the day from database, and create it if it does not already exist.
            if not models.Day.get_by_date(day):
                db.session.add(models.Day(day = day))
        db.session.flush()
        # Now, create the new aircraft & flight points.
        aircraft = models.Aircraft.get_by_icao(aircraft_d["icao"])
        if not aircraft:
            aircraft = models.Aircraft(**aircraft_d)
            # Add the aircraft to database, and flush.
            db.session.add(aircraft)
            db.session.flush()
        else:
            db.session.add_all(aircraft_d["flight_points"])
        # Iterate each day and create an aircraft presence for each.
        for day in dates:
            aircraft_present_day = models.AircraftPresentDay.find(aircraft.icao, day)
            if not aircraft_present_day:
                aircraft_present_day = models.AircraftPresentDay(
                    aircraft_icao = aircraft.icao,
                    day_day = day,
                    flights_verified = flights_verified,
                    history_verified = history_verified
                )
                db.session.add(aircraft_present_day)
        db.session.flush()
        return aircraft, dates

    def _setup_native_test_data_for(self, filename, total_points_num, points_date_filter, points_from_date_num, **kwargs):
        """
        """
        with open(os.path.join(os.getcwd(), config.TESTDATA_DIR, "native_testdata", filename), "r") as f:
            aircraft_json = json.loads(f.read())
        # Load the stored object with AircraftSchema; get back a dict for Aircraft.
        aircraft_d = airvehicles.AircraftSchema().load(aircraft_json)
        # Make the aircraft.
        aircraft = models.Aircraft(**aircraft_d)
        # Add the aircraft to database, and flush.
        db.session.add(aircraft)
        db.session.flush()
        # Ensure there are total_points_num points.
        self.assertEqual(aircraft.num_flight_points, total_points_num)
        # Now, ensure we've created a Day for this.
        day_date = points_date_filter
        day = models.Day(day = day_date)
        db.session.add(day)
        db.session.flush()
        # Add an aircraft present day.
        aircraft_present_day = models.AircraftPresentDay(
            aircraft_icao = aircraft.icao,
            day_day = day_date,
            flights_verified = False,
            history_verified = False
        )
        db.session.add(aircraft_present_day)
        db.session.flush()
        # Now, query all flight points from this day for the aircraft.
        flight_points = aircraft.flight_points_from_day(day_date)
        # Ensure points_from_date_num.
        self.assertEqual(len(flight_points), points_from_date_num)
        return aircraft, day_date, flight_points

    def _import_all_flights(self, filename, num_flights_required = None, **kwargs):
        """
        Import all Flights data from the given filename, that should be relative to the testdata/native_testdata directory.
        This will automatically import the aircraft & flight data, commit to database, ensure all aircraft present days are created for each date involved, then
        revise flight data will be called on each date involved. The return value will be the aircraft itself. Bare in mind, this function does not import known
        aircraft, so if the trace is an old version, please do this to ensure sufficient information is already stored. This function also does not import airports,
        so should you require the final output to be airport aware, ensure you do this prior.
        """
        # Use load native test data to load an aircraft instance, and a list of dates.
        aircraft, dates = self._load_native_test_data(filename, history_verified = True, flights_verified = False)
        # Now, for each date, revise flights data.
        flights_ = []
        for date in dates:
            revise_flights_receipt = flights.revise_flight_data_for(aircraft, date)
            db.session.flush()
            for flight in revise_flights_receipt.flights:
                if not flight in flights_:
                    flights_.append(flight)
        # If num flights required given, ensure this matches the length of flights.
        if num_flights_required:
            self.assertEqual(len(flights_), num_flights_required)
        # And return the aircraft.
        return aircraft, flights_

    def _build_flight_points(self, flight_point_hashes, coordinates, timestamps, **kwargs):
        # Source CRS, default this is 4326.
        source_crs = kwargs.get("source_crs", 4326)
        # Target CRS, default this is 3112.
        target_crs = kwargs.get("target_crs", 3112)
        # New transformer. Then, transform all items in coordinates.
        transformer = pyproj.Transformer.from_crs(source_crs, target_crs)
        coordinates = transformer.itransform(coordinates)
        # Now, build a flight point from each, then yield each.
        for flight_point_hash, coordinate, timestamp in zip(flight_point_hashes, coordinates, timestamps):
            new_flight_point = models.FlightPoint(flight_point_hash = flight_point_hash, timestamp = timestamp)
            # Build a new Point geometry from transformed coordinate, then set this flight point's geometry to hold this point and target CRS.
            coordinate_point = geometry.Point(coordinate)
            new_flight_point.set_crs(target_crs)
            new_flight_point.set_position(coordinate_point)
            yield new_flight_point

    def _transform_tuple(self, point_tuple, **kwargs):
        # Source CRS, default this is 4326.
        source_crs = kwargs.get("source_crs", 4326)
        # Target CRS, default this is 3112.
        target_crs = kwargs.get("target_crs", 3112)
        # New transformer. Then, transform all items in coordinates.
        transformer = pyproj.Transformer.from_crs(source_crs, target_crs)
        pos_x, pos_y = transformer.transform(*point_tuple)
        return (pos_x, pos_y,)

    def _import_nativedata_v2(self, filename):
        with open(os.path.join(os.getcwd(), config.TESTDATA_DIR, "native_testdata", filename), "r") as f:
            nativedata_json = json.loads(f.read())
        # If nativedata does not have a 'title' and 'description' key, its not a v2, so raise an exception.
        if not "title" in nativedata_json or not "description" in nativedata_json:
            raise Exception(f"{filename} is NOT a V2 nativedata source file.")
        print(f"Importing nativedata V2: {filename}")
        # Otherwise, we'll begin by importing and creating all specific days required.
        dates = []
        for date_iso in nativedata_json["days"]:
            date_ = date.fromisoformat(date_iso)
            day = models.Day.get_by_date(date_)
            if not day:
                day = models.Day(day_day = date_)
                db.session.add(day)
            dates.append(day)
        db.session.flush()
        # Next, import and create all aircraft required.
        aircraft = []
        for aircraft_json in nativedata_json["aircraft"]:
            aircraft_d = airvehicles.AircraftSchema().load(aircraft_json)
            aircraft_ = models.Aircraft(**aircraft_d)
            db.session.add(aircraft_)
            aircraft.append(aircraft_)
        db.session.flush()

class WorkerAppClient(FlaskLoginClient):
    def __init__(self, *args, **kwargs):
        self.override_user_agent = kwargs.pop("override_user_agent", None)
        super().__init__(*args, **kwargs)
        if "user" in kwargs and isinstance(kwargs["user"], models.RadarWorker):
            # Set persistent headers to include this radar worker's unique ID.
            self.worker_unique_id = kwargs["user"].unique_id
        else:
            self.worker_unique_id = None

    def open(self, *args, **kwargs):
        headers = kwargs.setdefault("headers", {})
        headers.setdefault("User-Agent", "aireyes/slave" if not self.override_user_agent else self.override_user_agent)
        if self.worker_unique_id:
            headers.setdefault("WorkerUniqueId", self.worker_unique_id)
        return super().open(*args, **kwargs)

    def set_user_agent(self, user_agent):
        self.override_user_agent = user_agent

    def clear_user_agent(self):
        self.override_user_agent = None

    def set_api_user_agent_ver(self, ver):
        self.override_user_agent = f"aireyes/slave"


class BaseBrowserCase(BaseCase):
    pass


class BaseWorkerAPICase(BaseCase):
    def create_app(self):
        test_app = super().create_app()
        test_app.test_client_class = WorkerAppClient
        return test_app


class BaseUserAPICase(BaseCase):
    def create_app(self):
        test_app = super().create_app()
        test_app.test_client_class = WorkerAppClient
        return test_app


class BaseSocketIOCase(BaseCase):
    def create_app(self):
        test_app = super().create_app()
        test_app.test_client_class = WorkerAppClient
        return test_app
