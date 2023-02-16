"""
"""
import re
import inspect
import logging
from datetime import datetime

from functools import wraps
from flask import request, g, redirect, url_for, render_template
from flask_login import current_user, login_required
from werkzeug.exceptions import Unauthorized

from . import db, config, models, error

LOG = logging.getLogger("aireyes.decorators")
LOG.setLevel( logging.DEBUG )


def positional_to_keyword_args(**kwargs):
    """
    Nice:
    https://stackoverflow.com/a/59179221
    """
    def decorator(f):
        @wraps(f)
        def decorated_view(*args, **kwargs):
            kwargs = { **kwargs, **{k: v for k, v in zip(list(inspect.signature(f).parameters), args)} }
            if "self" in kwargs:
                del (kwargs["self"])
            return f(*args, **kwargs)
        return decorated_view
    return decorator


def get_master(**kwargs):
    """
    Decorator that supplies the current master record via keyword arguments to wrapped function.
    """
    def decorator(f):
        @wraps(f)
        def decorated_view(*args, **kwargs):
            # Get the current master record.
            master = models.Master.get()
            # Set master in keywords.
            kwargs["master"] = master
            return f(*args, **kwargs)
        return decorated_view
    return decorator


def workers_only(**kwargs):
    """
    A decorator that restricts a route to workers only- this is by both the remote addr of the client, which can only be local host, and also by
    the logged-in status of the worker, which should show current user only as a RadarWorker class. In the case that require_logged_in is set to
    False, this route will only require the request come from localhost.

    Keyword arguments
    -----------------
    :require_logged_in: True if the client should also be logged in via a RadarWorker instance. Default is True.
    """
    require_logged_in = kwargs.get("require_logged_in", True)

    def decorator(f):
        @positional_to_keyword_args()
        @wraps(f)
        def decorated_view(*args, **kwargs):
            try:
                # We'll first get the user agent, and ensure it is a valid aireyes worker string.
                user_agent = request.headers.get("User-Agent", None)
                if not user_agent or not re.match(r"^aireyes/slave", user_agent):
                    LOG.error(f"Denied {current_user} access to route. It does not carry the worker user agent.")
                    raise error.RadarWorkerRequiredError("bad-user-agent")
                # Get the remote address.
                remote_addr = request.remote_addr
                if remote_addr != "127.0.0.1" and remote_addr != "localhost":
                    LOG.error(f"Denied {current_user} access to route. It does not come from localhost! (was {remote_addr})")
                    raise error.RadarWorkerRequiredError("external-host")
                # A function to call to ensure this client is logged in, but only if its required.
                @login_required
                def require_login():
                    pass
                # If login required, ensure the client is logged in.
                if require_logged_in:
                    require_login()
                    # Now, we are logged in. Ensure this is a radar worker.
                    if not isinstance(current_user, models.RadarWorker):
                        LOG.error(f"Denied {current_user} access to route. It is not a radar worker.")
                        raise error.RadarWorkerRequiredError("invalid-user-type")
                    # This is a logged-in radar worker, we'll set the time of last update on the worker as well.
                    current_user.set_last_update()
                    db.session.commit()
                return f(**kwargs)
            except Unauthorized as u:
                return "Please Identify", 403
            except error.RadarWorkerRequiredError as rwre:
                return "Not Found", 404
        return decorated_view
    return decorator


def get_aircraft(**kwargs):
    """
    Reads an Aircraft ICAO from arguments and attempts to locate the corresponding aircraft.

    Keyword arguments
    -----------------
    :aircraft_icao_key: The key to use to search for the Aircraft ICAO. By default 'aircraft_icao'.
    :aircraft_output_key: The key under which to pass the Aircraft to kwargs under. Default is 'aircraft'.
    :required: True if the route should fail if no Aircraft ICAO is given, or no Aircraft exists for the given ICAO. Default is True.
    """
    aircraft_icao_key = kwargs.get("aircraft_icao_key", "aircraft_icao")
    aircraft_output_key = kwargs.get("aircraft_output_key", "aircraft")
    required = kwargs.get("required", True)

    def decorator(f):
        @positional_to_keyword_args()
        @wraps(f)
        def decorated_view(*args, **kwargs):
            aircraft_icao = kwargs.get(aircraft_icao_key, None)
            aircraft = models.Aircraft.get_by_icao(aircraft_icao)
            if not aircraft and required:
                LOG.error(f"Failed to find an Aircraft given ICAO '{aircraft_icao}'")
                """
                TODO: proper error here.
                """
                raise Exception("couldnt-find-aircraft")
            kwargs[aircraft_output_key] = aircraft
            return f(**kwargs)
        return decorated_view
    return decorator


def get_flight(**kwargs):
    """
    Reads a flight hash from arguments and attempts to locate the corresponding Flight.

    Keyword arguments
    -----------------
    :flight_hash_key: The key to use to search for the Flight hash. By default 'flight_hash'.
    :flight_output_key: The key under which to pass the Flight to kwargs under. Default is 'flight'.
    :required: True if the route should fail if no Flight hash is given, or no Flight exists for the given hash. Default is True.
    """
    flight_hash_key = kwargs.get("flight_hash_key", "flight_hash")
    flight_output_key = kwargs.get("flight_output_key", "flight")
    required = kwargs.get("required", True)

    def decorator(f):
        @positional_to_keyword_args()
        @wraps(f)
        def decorated_view(*args, **kwargs):
            flight_hash = kwargs.get(flight_hash_key, None)
            flight = models.Flight.get_by_hash(flight_hash)
            if not flight and required:
                LOG.error(f"Failed to find a Flight given hash '{flight_hash}'")
                """
                TODO: proper error here.
                """
                raise Exception("couldnt-find-flight")
            kwargs[flight_output_key] = flight
            return f(**kwargs)
        return decorated_view
    return decorator


def get_airport(**kwargs):
    """
    Reads an airport hash from arguments, then pass the located Airport back through keyword arguments.

    Keyword arguments
    -----------------
    :airport_hash_key: The key to use to search for the Airport hash. By default 'airport_hash'.
    :airport_output_key: The key under which to pass the Airport to kwargs under. Default is 'airport'.
    :required: True if the route should fail if no Airport hash is given, or no Airport exists for the given hash. Default is True.
    """
    airport_hash_key = kwargs.get("airport_hash_key", "airport_hash")
    airport_output_key = kwargs.get("airport_output_key", "airport")
    required = kwargs.get("required", True)

    def decorator(f):
        @positional_to_keyword_args()
        @wraps(f)
        def decorated_view(*args, **kwargs):
            airport_hash = kwargs.get(airport_hash_key, None)
            airport = models.Airport.get_by_hash(airport_hash)
            if not airport and required:
                LOG.error(f"Failed to find an Airport given hash '{airport_hash}'")
                """
                TODO: proper error here.
                """
                raise Exception("couldnt-find-airport")
            kwargs[airport_output_key] = airport
            return f(**kwargs)
        return decorated_view
    return decorator
