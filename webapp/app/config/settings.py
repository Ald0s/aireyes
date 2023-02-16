import os
import time
import json
from datetime import date


class WorkerGeneralConfig():
    NODE_EXECUTABLE_PATH = "/usr/local/bin/node"
    WORKER_RELATIVE_PATH = "../workerapp"
    WORKER_FILE_NAME = "aireyes.js"
    # The number of seconds to wait to determine that a worker, without change or any updates, is stuck.
    WORKER_STUCK_TIMEOUT = 5 * 60


class SocketConfig():
    SOCKETIO_MESSAGE_QUEUE = "redis://"
    SOCKETIO_PATH = "socket.io"
    SOCKETIO_ENGINEIO_LOGGER = False
    SOCKETIO_ROOM_AIRCRAFT_REALTIME = "aircraft-realtime"
    # True if, universally, updates should be sent via the SocketIO system where applicable.
    SHOULD_SEND_SOCKETIO_UPDATES = True


class GeospatialConfig():
    # The default timezone to use (for now.)
    TIMEZONE = "Australia/Sydney"
    # The source CRS to use, by default, for all incoming data.
    SOURCE_COORDINATE_REF_SYS = 4326
    # We will only deal with Australia in this project, at least for now, so set this to 3112.
    COORDINATE_REF_SYS = 3112
    # The tolerance to use for geometric simplification whilst creating a statewide geometry.
    STATEWIDE_SIMPLIFY_TOLERANCE = 800
    # The interval to use when buffering suburbs whilst creating a statewide geometry.
    STATEWIDE_BUFFER_INTERVAL = 1500
    # Set to True to execute the flight point geolocation process when a new flight-partial is received.
    SHOULD_GEOLOCATE_FLIGHT_POINTS = True
    # This deals with optimisation for transporting bulk suburb data to the User. If True; when a user queries for suburbs, depending on their zoom, queries for suburb coordinates will only return those
    # specific coordinates suitable for their detail level. If you change this boolean from False to True, the entire database must be imported once again. Turning this from True to False simply disables
    # the entire mechanism, but it can be re-enabled just as easily. This feature is since disabled as a more efficient method has been discovered.
    SHOULD_APPLY_COORDINATE_OPTIMISATION = True
    # Set this to True to execute suburb optimisation code whenever suburbs are recreated.
    SHOULD_OPTIMISE_SUBURBS = True
    # For example; (10, 1000, 1)
    # This should be read as, when the zoom level is 10 and under, tolerance is 1000, and also, the level of detail stored in the database for all associated coordinates is 1, and so on. The user's zoom level
    # is checked, if it is 11, the second level of detail is matched, and only coordinates whose detail column is 1 or greater is returned. If you change this configuration, your changes will only take effect
    # once you have reimported the entire database WITH SHOULD_OPTIMISE_SUBURBS set to True. Coordinates will ALWAYS be created given these configurations, irrespective of SHOULD_OPTIMISE_SUBURBS, but the detail
    # column itself may not be used if optimisation is disabled.
    ZOOM_TO_COORDINATE_TOLERANCE = [
        (10, 200, 4),
        (12, 150, 3),
        (13, 100, 2),
        (14, 50, 1),
        (20, 0, 0)
    ]
    # Add state codes here to disclude all others. Ex. 'VIC'
    USE_ONLY_STATES = []


class AircraftRealtimeConfig():
    # True if the aircraft route should invoke updates on existing/new Flights, given each partial flight submitted.
    SHOULD_SUBMIT_PARTIAL_FLIGHTS = True
    # If an Aircraft is last seen within this number of seconds, it is considered active from a time point of view.
    MAXIMUM_SECONDS_SINCE_SEEN_ACTIVE = 120
    # DEPRECATED
    # The maximum altitude to consider a disappearing Aircraft still active should they go over the MAXIMUM_SECONDS_SINCE_SEEN_ACTIVE limit; in feet.
    MAXIMUM_ALTITUDE_STILL_ACTIVE_OVER_LAST_SEEN = 5000
    # DEPRECATED
    # If an Aircraft is NOT seen within this number of seconds, it is considered inactive despite disappearing at MAXIMUM_ALTITUDE_STILL_ACTIVE_OVER_LAST_SEEN.
    MAXIMUM_ALTITUDE_STILL_ACTIVE_TIME_SECONDS_FAILSAFE = 2000


class FlightProcessingConfig():
    # The current setting for the per capita co2 emissions in Australia as per World Bank in 2020 (or 19?)
    YEARLY_CO2_EMISSIONS_PER_CAPITA_AUSTRALIA = 15.2
    # The minimum number of positional flight points required to consider a flight path valid.
    MINIMUM_POSITIONAL_FLIGHT_PATH_POINTS = 2
    # A partial flight must have at least this many flight fragments in order to be considered anything to actually process.
    MINIMUM_FRAGMENTS_FOR_PARTIAL = 3
    # The maximum number of meters a flight point should be within (toward) an airport to consider that airport for takeoff/landing association. This also represents the buffer,
    # in meters, given to all airports upon creation; so this will also influence which UTM EPSGs the airport is placed within; enabling faster discovery.
    AIRPORT_POINT_BUFFER_RADIUS_METERS = 5000
    # Should inaccuracies in input flight data, such as sudden disappearances & reappearances, even many hours later, be specially considered and have
    # alternative algorithms applied to, as best as possible, differentiate between new flights? Disabling this will mean that ANY large gap in the middle
    # of a flight will be included, as they are, in final statistics as part of whatever flight currently in progress. This can throw end statistics way off if set to False.
    INACCURACY_SOLVENCY_ENABLED = True
    # Should inaccuraces in input flight data be flagged? These will end up being associated with specific Flight instances, and will isolate times of interest
    # and decisions taken by the system. Flagging will run independently from solvency logic.
    INACCURACY_SOLVENCY_FLAGGING_ENABLED = False
    # The time difference, in seconds, at which point to request further investigation for inaccuracy solvency.
    TIME_DIFFERENCE_INACCURACY_CHECK_REQUIRED = 1 * 60 * 60
    # The time difference between flight points, in seconds, to consider point #2 a new flight in the case that both point #1 and point #2 are grounded.
    TIME_DIFFERENCE_NEW_FLIGHT_GROUNDED = 1200
    # The time difference between flight points, in seconds, to consider point #2 a new flight, in the case that point #1 was grounded, but point #2 is airborne (disappeared takeoff).
    TIME_DIFFERENCE_NEW_FLIGHT_MID_AIR_START = 1200
    # The time difference between flight points, in seconds, to consider point #2 a new flight, in the case that point #1 was airborne, but point #2 is airborne (disappeared landing).
    TIME_DIFFERENCE_NEW_FLIGHT_MID_AIR_END = 1200
    # The time difference between flight points, in seconds, to consider point #2 a new flight, in the case that both points #1 and #2 are airborne (disappeared everything).
    TIME_DIFFERENCE_NEW_FLIGHT_MID_AIR_START_AND_END = 12600
    # Maximum altitude of first point in partial flight to consider this a takeoff.
    MAX_ALTITUDE_MID_AIR_DISAPPEAR_START_NEW_FLIGHT = 1500
    # Maximum altitude of final point in partial flight to consider this a landing.
    MAX_ALTITUDE_MID_AIR_DISAPPEAR_END_FLIGHT = 1000
    # If the time, in seconds, between the end of one partial and the beginning of another is equal to or within this count, those two partials are considered
    # a part of the same flight, and will therefore be joined.
    MAX_TIME_BETWEEN_PARTIAL_FOR_LINKAGE = 1200
    # If the number of meters travelled between the end of one partial and the beginning of another is equal to or within this limit, those two partials are considered
    # a part of the same flight, and will therefore be joined.
    MAX_DISTANCE_BETWEEN_PARTIAL_FOR_LINKAGE = 900
    # True if time on the ground (taxiing/taxi-only events) should be included in and reported as flight usage calculations.
    STAT_COUNT_GROUND_TIME_AS_FLIGHT_TIME = False


class BaseConfig(WorkerGeneralConfig, GeospatialConfig, AircraftRealtimeConfig, FlightProcessingConfig, SocketConfig):
    TESTING = False
    DEBUG = False
    USE_RELOADER = False

    HOST = "0.0.0.0"
    PORT = 5000
    """TODO 0x11"""
    #SERVER_NAME = f"localhost.localdomain:{PORT}"
    #SERVER_NAME = f"127.0.0.1:{PORT}"

    POSTGIS_ENABLED = True
    POSTGIS_MANAGEMENT = False

    SQLALCHEMY_SESSION_OPTS = {}
    SQLALCHEMY_ENGINE_OPTS = {}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SECRET_KEY = ""

    # Configuration for ProxyFix. As base is the most applicable to our various local/testing routes, our configuration will be 0 by default.
    FORWARDED_FOR = 0
    FORWARDED_PROTO = 0
    FORWARDED_HOST = 0
    FORWARDED_PORT = 0
    FORWARDED_PREFIX = 0
    # Some import directories.
    ERRORS_DIR = "error"
    IMPORTS_DIR = "imports"
    SUBURBS_DIR = os.path.join(IMPORTS_DIR, "suburbs")
    TRACES_DIR = os.path.join(IMPORTS_DIR, "traces")
    IMAGES_DIR = os.path.join("app", "static", "images")
    # Some important resource files, these are expected to be present at launch time.
    AIRCRAFT_FUEL_FIGURES = "aircraft_fuel.json"
    KNOWN_AIRCRAFT_CONFIG = "aircraft_states.json"
    AIRPORTS_CONFIG = "airports.json"
    # The following should be provided as tarballs containing compatible data.
    TRACES_TARBALL = "traces.tgz"
    SUBURBS_TARBALL = "suburbs.tgz"
    # What timezone should be used to report dates across the app irrespective of their relevant locations? Set to None to disable.
    GLOBAL_REPORTING_TIMEZONE = None
    # Two ISO format dates (no times) representing the start and end days (inclusive) that this server should aim to maintain a comprehensive trace
    # log for all vehicles throughout. The end date can be None, in which case this will be constantly substituted for 'current day.' Essentially,
    # in operational downtime, history trawlers will be invoked to follow up on any work yet to be done.
    """TODO 0x13"""
    DATA_SOURCE_DAY_RANGE = (
        date(2020, 7, 8),
        None
    )
    # Name of the project; obviously.
    PROJECT_NAME = "Aireyes"
    # How many flights should be displayed per page?
    PAGE_SIZE_FLIGHTS = 25

    def __init__(self):
        self.make_dirs()
        if self.GLOBAL_REPORTING_TIMEZONE:
            os.environ["TZ"] = self.GLOBAL_REPORTING_TIMEZONE
            time.tzset()

    def make_dirs(self):
        def make_dir(path):
            try:
                os.makedirs(os.path.join(os.getcwd(), path))
            except OSError as o:
                pass
        make_dir(self.IMPORTS_DIR)
        make_dir(self.TRACES_DIR)
        make_dir(self.ERRORS_DIR)


class TestConfig(BaseConfig):
    """
    Unit test profile.
    Runs a memory database, disables most auxiliary systems.
    Can read and run some data/configuration straight from disk.
    """
    FLASK_ENV = "development"
    FLASK_DEBUG = True
    PRESERVE_CONTEXT_ON_EXCEPTION = False
    SQLALCHEMY_ENGINE_OPTS = {}
    TESTING = True
    DEBUG = True
    USE_RELOADER = True

    POSTGIS_ENABLED = True
    # Required whenever using SQLite.
    POSTGIS_MANAGEMENT = True
    # The NodeJS file to execute for unit testing. This should be a dud client that doesn't do much.
    WORKER_FILE_NAME = "aireyestester.js"
    # Disabling message queue for test environment.
    SOCKETIO_MESSAGE_QUEUE = None
    # Set the database to a memory one.
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    AIOSQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
    # Disable suburb optimisation for testing.
    SHOULD_OPTIMISE_SUBURBS = False
    # Disable considering suburb optimisation for testing.
    SHOULD_APPLY_COORDINATE_OPTIMISATION = False

    TESTDATA_DIR = "imports/test_data"
    IMPORTS_DIR = TESTDATA_DIR
    # Some tests use exposed traces/suburbs; just not very many. These are exposed here.
    TRACES_DIR = os.path.join(IMPORTS_DIR, "test-traces")
    SUBURBS_DIR = os.path.join(IMPORTS_DIR, "test-suburbs")
    # Good middle ground.
    GLOBAL_REPORTING_TIMEZONE = "Etc/GMT"

    def make_dirs(self):
        super().make_dirs()


class DevelopmentConfig(BaseConfig):
    """
    Local development profile.
    Runs a local SQLite database. Disables some auxiliary systems.
    Requires tar configuration and test data.
    """
    FLASK_ENV = "development"
    FLASK_DEBUG = True
    USE_RELOADER = True

    DEBUG = True

    POSTGIS_ENABLED = True
    # Required whenever using SQLite.
    POSTGIS_MANAGEMENT = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///aireyes.db"
    AIOSQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///instance/aireyes.db"
    #TESTDATA_DIR = "imports/test_data"
    #TRACES_DIR = os.path.join(TESTDATA_DIR, "test-traces")
    # Set imports directory to the test data subdirectory within imports. This must contain our test tarballs.
    IMPORTS_DIR = os.path.join("imports", "test_data")

    # Only view 5 flights at a time in development.
    PAGE_SIZE_FLIGHTS = 5
    # Use only VIC for development builds.
    USE_ONLY_STATES = ["VIC"]

    def make_dirs(self):
        super().make_dirs()


class LiveDevelopmentConfig(BaseConfig):
    """
    Live development profile.
    Uses a real locally-hosted PostGIS enabled PostgreSQL database.
    This is the closest to the real thing as its going to get; uses the real imports directory.
    Still technically development.
    """
    FLASK_ENV = "development"
    FLASK_DEBUG = True
    USE_RELOADER = False
    DEBUG = True

    SQLALCHEMY_SESSION_OPTS = {}
    SQLALCHEMY_ENGINE_OPTS = {}

    POSTGIS_ENABLED = True
    # Disabled now because we are using PostgreSQL.
    POSTGIS_MANAGEMENT = False

    SQLALCHEMY_DATABASE_URI = "postgresql+psycopg2://USERNAME:PASSWORD@localhost:5432/aireyes_test"
    AIOSQLALCHEMY_DATABASE_URL = "postgresql+asyncpg://USERNAME:PASSWORD@localhost:5432/aireyes_test"

    def make_dirs(self):
        super().make_dirs()


class ProductionConfig(BaseConfig):
    """
    The production profile.
    A realistic configuration scenario- this should synchronise with your servercfg.
    No auxiliary systems disabled, uses real import directory.
    """
    FLASK_ENV = "production"
    PORT = 8081

    POSTGIS_ENABLED = True
    POSTGIS_MANAGEMENT = False
    # Path to your Node executable.
    NODE_EXECUTABLE_PATH = "/usr/bin/node"
    SQLALCHEMY_DATABASE_URI = "postgresql+psycopg2://USERNAME:PASSWORD@localhost:5432/aireyes"
    AIOSQLALCHEMY_DATABASE_URL = "postgresql+asyncpg://USERNAME:PASSWORD@localhost:5432/aireyes"
    # A realistic proxy configuration now that should match your architecture.
    FORWARDED_FOR = 2
    FORWARDED_PROTO = 0
    FORWARDED_HOST = 0
    FORWARDED_PORT = 0
    FORWARDED_PREFIX = 0
