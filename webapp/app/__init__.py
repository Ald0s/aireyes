"""
TODO: Research
--------------
Look into the fact that a partial flight that has just begun taxiing is reported as a complete flight (also has landing cos remember, it ends on the ground.)
I haven't worked out any way in which this can cause issues just yet. But its something to keep in mind.

Discovered that when we set max_requests in gunicorn config, this will force restart the aireyes slave & gunicorn worker. This isn't a huge issue if we also run crontab,
but it may pay to actually look into how we can improve this. Either by removing auto restarts (ill-advised), or by figuring out how to run multiple instances and staggering
them, since the main concern isn't so much the slave restarting as it is potential user disruption.

Set timezone information to come from config in the following locations;
    -> flights.py // derive_manager
    -> flights.py // _calculate_flight_statistics
    -> models.py // Flight: starts_at_friendly_datetime, ends_at_friendly_datetime

THINGS TO DO
------------
TODO 0x01: Rewrite aiogeospatial to first build all geometries from the disk, then in/upsert them separately.
TODO 0x02: (ADSB plugin) Implement use of testHide and testUnhide to deactivate/reactivate searches for aircraft after periods of inactivity
TODO 0x06: We have updated timezones to now always be Australia/Sydney, but obviously this doesn't work for 100% of cases.
TODO 0x07: Where we join flight points to this query; this may not be necessary, and, in fact, it may even signficantly slow this down. Take a look at setting a proper first and last column within the Flight table, instead of this.
TODO 0x08: Provide more validation to arguments given to the suburbs route in API.
TODO 0x09: On heatmap, check states differ between HTML and jQuery after refreshing
TODO 0x10: Implement INACCURACY_SOLVENCY_FLAGGING_ENABLED
TODO 0x11: We can use this to set up a better way of getting the localhost worker master route in runworkers.
TODO 0x13: Implement history trawler to work properly.
TODO 0x14: Authenticating radarworker should be more secure.
TODO 0x15: Implement is_ongoing
TODO 0x16: Move this to a viewmodel.

ISSUE CODES
-----------
ISSUE 0x04: Determine a way to speed up the insertion of new flight points in airvehicles.py
ISSUE 0x05: Clean up and make more efficient the technique used to read aircraft fuel figures in airvehicles.py
"""

import os
import logging
import requests

from flask import Flask, request, g, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_caching import Cache
from werkzeug.middleware.proxy_fix import ProxyFix

from . import config, compat
compat.monkey_patch_sqlite()

LOG = logging.getLogger("aireyes")
LOG.setLevel( logging.DEBUG )

db = SQLAlchemy(
    session_options = config.SQLALCHEMY_SESSION_OPTS,
    engine_options = config.SQLALCHEMY_ENGINE_OPTS
)
migrate = Migrate()
login_manager = LoginManager()
socketio = SocketIO()

from .api import api as api_blueprint
from .frontend import frontend as frontend_blueprint
from .socket import setup_socketio
from . import handler as app_handler


def create_app():
    logging.info(f"Creating Flask instance in the '{config.APP_ENV}' environment")
    app = Flask(__name__)
    app.config.from_object(config)
    app.url_map.strict_slashes = False
    db.init_app(app)
    migrate.init_app(app, db)
    socketio.init_app(app,
        path = config.SOCKETIO_PATH, engineio_logger = config.SOCKETIO_ENGINEIO_LOGGER)
    login_manager.init_app(app)
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for = config.FORWARDED_FOR,
        x_proto = config.FORWARDED_PROTO,
        x_host = config.FORWARDED_HOST,
        x_port = config.FORWARDED_PORT,
        x_prefix = config.FORWARDED_PREFIX)
    # Setup socket io handlers.
    setup_socketio(socketio)
    with app.app_context():
        # If required, load the spatialite mod onto the sqlite driver.
        if config.POSTGIS_ENABLED and db.engine.dialect.name == "sqlite":
            compat.should_load_spatialite_sync(db.engine)
        # Register all blueprints.
        app.register_blueprint(api_blueprint)
        app.register_blueprint(frontend_blueprint)
        app_handler.configure_app_handlers(app)
    return app
