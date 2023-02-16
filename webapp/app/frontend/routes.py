import os
import re
import json
import time
import logging
from datetime import datetime

from werkzeug.urls import url_parse
from flask import request, render_template, redirect, flash, url_for, send_from_directory, abort, jsonify, current_app
from flask_login import login_required, current_user, login_user, logout_user
from marshmallow import Schema, fields
from sqlalchemy import asc, desc

from .. import db, config, login_manager, models, decorators, flights, airvehicles
from . import frontend

LOG = logging.getLogger("aireyes.frontend.routes")
LOG.setLevel( logging.DEBUG )


def get_back_button():
    current_page = request.full_path
    previous_page = request.referrer
    print(f"{previous_page} => {current_page}")
    scheme, netloc, path, query, fragment = url_parse(current_page)
    print(f"{scheme} | {netloc} | {path} | {query} | {fragment}")
    scheme, netloc, path, query, fragment = url_parse(previous_page)
    print(f"{scheme} | {netloc} | {path} | {query} | {fragment}")
    return "OK"


@frontend.context_processor
def frontend_template_context():
    """All frontend templates will have access to these variables."""
    return dict(
        app_config = config,
        get_back_button = get_back_button)


@frontend.route("/", methods = [ "GET" ])
def index():
    """
    This is the index page, it will display the list of aircrafts being tracked, in summary.
    This is essentially where the realtime aspects come to life.
    """
    try:
        # Get all aircraft from the database, order by current active.
        monitored_aircraft = airvehicles.get_monitored_aircraft(active_first = True)
        # The number of aircraft in total.
        num_aircraft = len(monitored_aircraft)
        # Get the first entry from the source day range. This is the first date we started recording.
        data_recording_started = config.DATA_SOURCE_DAY_RANGE[0]
        return render_template(
            "index.html.j2",
            monitored_aircraft = monitored_aircraft,
            num_aircraft = num_aircraft,
            data_recording_started = data_recording_started.strftime("%A, %d %B %Y")), 200
    except Exception as e:
        LOG.error(e, exc_info = True)
        return "Server Error", 500


@frontend.route("/statistics", methods = [ "GET" ])
def statistics():
    """
    TODO: show some overall statistics.
    """
    try:
        raise NotImplementedError("The statistics frontend route is not implemented.")
    except Exception as e:
        LOG.error(e, exc_info = True)
        return "Server Error", 500


@frontend.route("/heatmap", methods = [ "GET" ])
def heatmap():
    """
    Present the world map to the user. This will show hot zones for tracked users, based on their already-stored traffic. Essentially, where they've spent most
    of their time, organised by Suburbs across Australia.
    """
    try:
        # Get all aircraft from the database, order by current active.
        monitored_aircraft = airvehicles.get_monitored_aircraft(active_first = True)
        # Render a template.
        return render_template(
            "heatmap.html.j2",
            monitored_aircraft = monitored_aircraft)
    except Exception as e:
        LOG.error(e, exc_info = True)
        return "Server Error", 500


@frontend.route("/about", methods = [ "GET" ])
def about():
    """
    TODO: show some info about the project.
    """
    try:
        raise NotImplementedError("The about frontend route is not implemented.")
    except Exception as e:
        LOG.error(e, exc_info = True)
        return "Server Error", 500


@frontend.route("/aircraft/<aircraft_icao>", methods = [ "GET" ])
@decorators.get_aircraft()
def aircraft_overview(aircraft, **kwargs):
    """
    View the overview for a particular aircraft, given its ICAO.
    This page will list statistics and some deeper dives into the Aircraft itself.

    TODO: not implemented.
    """
    try:
        raise NotImplementedError("The aircraft overview frontend route is not implemented.")
    except Exception as e:
        LOG.error(e, exc_info = True)
        return "Server Error", 500


@frontend.route("/flights", methods = [ "GET" ])
def flights_overview(**kwargs):
    """
    View a list of all Flights logged in this database.
    No filtration is performed on the basis of specific aircraft. This route supports pagination via a request argument 'p'.
    """
    try:
        page = int(request.args.get("p", 1))

        # Query all flights newest to oldest.
        # Apply pagination to this query.
        flights_q = flights.query_flights(newest_first = True)
        flights_pagination = flights_q\
            .paginate(page = page, max_per_page = config.PAGE_SIZE_FLIGHTS, error_out = False)
        # Get flights from the pagination.
        flights_ = flights_pagination.items
        # Get the number of flights.
        num_flights = flights_pagination.total
        LOG.debug(f"Located {num_flights} in total, serving page #{page}.")
        # Now, render a template for these flights.
        return render_template(
            "all-flights-overview.html.j2",
            num_flights = num_flights,
            flights = flights_,
            pagination = flights_pagination), 200
    except Exception as e:
        raise e


@frontend.route("/aircraft/<aircraft_icao>/flights", methods = [ "GET" ])
@decorators.get_aircraft()
def aircraft_flights(aircraft, **kwargs):
    """
    View a list of Flights for the given Aircraft, by its ICAO. This route will display all flights in a newest first order. Pagination is applied
    to adjust each page to a size of 50. The pagination object received from database is passed to the template. By default, page 1 will be returned.
    Also, some filtration criteria can be supplied via URL arguments alongside requested page.
    """
    try:
        page = int(request.args.get("p", 1))

        # Query all flights for the given aircraft.
        # Apply pagination to this query.
        flights_q = flights.query_flights_from(aircraft, newest_first = True)
        flights_pagination = flights_q\
            .paginate(page = page, max_per_page = config.PAGE_SIZE_FLIGHTS, error_out = False)
        # Get flights from the pagination.
        flights_ = flights_pagination.items
        # Get the number of flights.
        num_flights = flights_pagination.total
        LOG.debug(f"Located {num_flights} for aircraft {aircraft}.")
        # Now, render a template for these flights.
        return render_template(
            "aircraft-flights.html.j2",
            aircraft = aircraft,
            num_flights = num_flights,
            flights = flights_,
            pagination = flights_pagination), 200
    except Exception as e:
        raise e


@frontend.route("/flight/<flight_hash>", methods = [ "GET" ])
@decorators.get_flight()
def flight_overview(flight, **kwargs):
    """
    View the specifics of a particular flight.
    This will render the flight-overview template along with the required Flight and Aircraft.

    TODO: not implemented.
    """
    try:
        raise NotImplementedError("The flight overview frontend route is not implemented.")
    except Exception as e:
        LOG.error(e, exc_info = True)
        return "Server Error", 500


@frontend.route("/airport/<airport_hash>", methods = [ "GET" ])
@decorators.get_airport()
def airport_overview(airport, **kwargs):
    """
    View the specifics of a particular airport.
    This will render the airport-overview template along with the required Airport.

    TODO: not implemented.
    """
    try:
        raise NotImplementedError("The airport overview frontend route is not implemented.")
    except Exception as e:
        LOG.error(e, exc_info = True)
        return "Server Error", 500
