"""
A module for managing traces and trace data for a particular aircraft. Through this module, an aircraft's presence on a particular day can be reported
as checked and verified. This module also offers functionality for locally importing and processing trace data that we maintain on disk - in case we
are spun up on a new server.
"""
import re
import os
import time
import uuid
import hashlib
import logging
import json
import decimal
from datetime import datetime, date, timedelta, timezone

from flask import g

from .compat import insert

from sqlalchemy import func, and_, or_
from sqlalchemy.exc import IntegrityError
from marshmallow import Schema, fields, EXCLUDE, post_load, pre_load

from . import db, config, models, airvehicles, error

LOG = logging.getLogger("aireyes.traces")
LOG.setLevel( logging.DEBUG )


class AircraftDayTraceHistorySchema(Schema):
    """
    A schema for loading a day's trace for a particular aircraft, or identifying the lack thereof. This is to be used by history-trawler
    type workers. This schema will contain a loaded aircraft schema, which in turn will contain all flight points needed to be loaded
    for this aircraft on the given day. Finally, the 'intentionally_empty' attribute, when True, indicates that this day did not have any
    trace data for the given aircraft at all; and as such, the aircraft's FlightPoints array should be empty.
    """
    day                     = fields.Date(allow_none = True, load_default = None, data_key = "day")
    aircraft                = fields.Nested(airvehicles.AircraftSchema, allow_none = True, load_default = None, required = False, many = False, data_key = "aircraft")
    intentionally_empty     = fields.Bool(data_key = "intentionallyEmpty", load_default = True)


class RequestAircraftDayTraceHistorySchema(Schema):
    """
    A schema for dumping a request for an aircrafts trace on a particular day.
    A request consists of a target aircraft ICAO and a target day. The icao should be the aircraft we want to locate history for and day
    should be an ISO format string, corresponding to the particular date.
    """
    target_aircraft_icao    = fields.Str(data_key = "targetAircraftIcao")
    target_day              = fields.Date(data_key = "targetDay", format = "%Y/%m/%d")


class AircraftDayTraceResponseSchema(Schema):
    """
    A schema for communicating with history-trawler type workers. This is usually dumped in response to the trace route,
    and can command the worker. The guiding principle for this schema is the 'command' attribute, which will dictate to
    the worker the kind of response to expect.

    Commands
    --------
    :trawl: Query for the given aircraft's trace on the given day.
    :shutdown: Do not continue, simply shutdown.
    """
    command                 = fields.Str(data_key = "command")

    # A list of receipts for submitted flight points that have been synchronised.
    receipts                = fields.List(fields.Nested(airvehicles.FlightPointReceiptSchema, many = False), dump_default = [], data_key = "receipts")

    # If command is 'trawl', this object will be given; otherwise None.
    requested_trace_history = fields.Nested(RequestAircraftDayTraceHistorySchema, dump_only = True, dump_default = None, data_key = "requestedTraceHistory")


def aircraft_trace_history_submitted(radar_worker, aircraft_day_trace_history, **kwargs):
    """
    Once a radar worker (of type history-trawler) has been assigned and sent a request for more trace data, the worker will respond with an instance
    of AircraftDayTraceHistorySchema. If the aircraft's flight points list is empty, but intentionally_empty is False, this means some error occurred
    while getting the trace. Otherwise, if the flight points list is empty but intentionally_empty is True, there is no trace data - so we can mark
    this day's history with this aircraft as verified.

    Otherwise, should points be present, the function will use aircraft_submitted to upsert the aircraft and all its points to the database. Then, the
    function will report the day's history with this aircraft as verified. Finally, the function will remove the aircraft day from the radar worker's
    assignments. The return value is a tuple; the aircraft, day (Date instance) and a list of ALL synchronised flight points.

    If the 'aircraft' attribute is None, this will raise a RequestWorkError which will simply skip the submission aspect.

    Arguments
    ---------
    :radar_worker: The worker submitting the given trace history.
    :aircraft_day_trace_history: A loaded AircraftDayTraceHistorySchema.

    Raises
    ------
    RequestWorkError: the worker is requesting more work; perhaps they have just started.

    Returns
    -------
    A tuple of three arguments;
        Aircraft instance found
        The Date instance for the selected day
        A list of FlightPoints that have been synchronised.
    """
    try:
        # Check for None aircraft.
        if aircraft_day_trace_history["day"] == None or aircraft_day_trace_history["aircraft"] == None:
            # Just raise RequestWorkError.
            raise error.RequestWorkError(radar_worker.name)
        # Get some basic info so we don't have to keep referring to messy dicts.
        day = aircraft_day_trace_history["day"]
        icao = aircraft_day_trace_history["aircraft"]["icao"]
        flight_name = aircraft_day_trace_history["aircraft"]["flight_name"]
        num_flight_points = len(aircraft_day_trace_history["aircraft"]["flight_points"])
        # Check for empty flight points.
        if not num_flight_points and not aircraft_day_trace_history["intentionally_empty"]:
            LOG.error(f"Unknown error occured! We have been sent an aircraft; {flight_name} ({icao}) on day {day.isoformat()} with a 0-point FlightPoints list, but intentionally_empty is False. What does this mean?")
            raise NotImplementedError()
        else:
            # Otherwise, process the aircraft either way.
            aircraft, flight_points, synchronised_flight_points = airvehicles.aircraft_submitted(aircraft_day_trace_history["aircraft"])
            # Just report on some statistics, lightly.
            if num_flight_points:
                # We have some flight points, we'll utilise the airvehicles module to submit this aircraft.
                LOG.debug(f"Worker {radar_worker.name} has reported that aircraft {flight_name} ({icao}) on day {day.isoformat()} flew {num_flight_points} flight points in total.")
            else:
                # We have no flight points.
                LOG.debug(f"Worker {radar_worker.name} has reported that aircraft {flight_name} ({icao}) on day {day.isoformat()} did not fly.")
        # Now, we'll report the aircraft's presence on the given day as verified.
        report, was_created = report_aircraft_presence(aircraft, day, history_verified = True)
        # Finally, remove this aircraft day from the worker's assigned work.
        if report in radar_worker.aircraft_day_work:
            radar_worker.aircraft_day_work.remove(report)
        # Return aircraft, day and synchronised flight points.
        return aircraft, day, synchronised_flight_points
    except Exception as e:
        raise e


def assign_trace_history_work(radar_worker, **kwargs):
    """
    Request more work for the given radar worker, required to be a history-trawler. The function will raise an exception if the worker is
    the incorrect worker type. Otherwise, this function will locate AircraftPresentDay models, whose history is unverified, and who currently
    do not have any other radar workers assigned. Then, an association will be created between the given radar worker and the aircraft-day
    combination; locking that combination and assigning it to this radar worker. Finally, a dictionary compatible with RequestAircraftDayTraceHistorySchema
    will be returned, containing the order.

    Arguments
    ---------
    :radar_worker: The radar worker to assign further work to. This radar worker must be of type 'history-trawler'

    Keyword arguments
    -----------------
    :multiple_assignments_allowed: If True and the worker has an assignment currently due, function will still search for an assign another. Otherwise, that assignment will be returned. Default is False.

    Returns
    -------
    A dictionary, compatible with RequestAircraftDayTraceHistorySchema.
    """
    try:
        multiple_assignments_allowed = kwargs.get("multiple_assignments_allowed", False)

        # Ensure worker is a history trawler.
        if radar_worker.worker_type != "history-trawler":
            LOG.error(f"Failed to assign trace history work to worker {radar_worker}, only workers of type 'history-trawler' are supported.")
            raise TypeError("incorrect-worker-type")
        if not multiple_assignments_allowed and radar_worker.num_assigned_aircraft_day_work > 0:
            # Multiple assignments are NOT allowed and we have more than 0 current assignments. Get the first current assignment and use that.
            LOG.warning(f"While assigning work to {radar_worker}, multiple assignments not allowed and they have non-zero count currently. Using first assignment...")
            # Simply set selected_aircraft_day to this aircraft day to use it.
            selected_aircraft_day = radar_worker.aircraft_day_work[0]
        else:
            # Now, locate all aircraft present day instances, history_verified should be False, and they should not be assigned to any workers.
            prospective_aircraft_days = db.session.query(models.AircraftPresentDay)\
                .filter(models.AircraftPresentDay.history_verified == False)\
                .filter((
                    db.session.query(func.count(models.WorkerLockAircraftDay.radar_worker_name))
                        .filter(and_(models.WorkerLockAircraftDay.aircraft_day_day == models.AircraftPresentDay.day_day, models.WorkerLockAircraftDay.aircraft_icao == models.AircraftPresentDay.aircraft_icao))
                        .scalar_subquery()
                ) == 0)\
                .all()
            # If there are 0 items found, we will raise an error; NoAssignableWorkLeft. We no longer need this worker to run.
            if not len(prospective_aircraft_days):
                LOG.error(f"Found 0 prospective aircraft-days to assign to radar worker!")
                raise error.NoAssignableWorkLeft(radar_worker.name)
            LOG.debug(f"Located {len(prospective_aircraft_days)} prospective aircraft-days we may be able to assign to history worker {radar_worker}")
            # Select the very first one, but we can also apply some logic here to determine priority.
            selected_aircraft_day = prospective_aircraft_days[0]
            LOG.debug(f"Assigning aircraft-day {selected_aircraft_day} to history worker {radar_worker}")
            # Now, create an association between the worker and this aircraft-day.
            assignment = models.WorkerLockAircraftDay(
                aircraft_day_day = selected_aircraft_day.day_day,
                aircraft_icao = selected_aircraft_day.aircraft_icao,
                radar_worker_name = radar_worker.name
            )
            db.session.add(assignment)
        # Return a dictionary for this request.
        return dict(
            target_aircraft_icao = selected_aircraft_day.aircraft_icao,
            target_day = selected_aircraft_day.day_day
        )
    except Exception as e:
        raise e


def ensure_aircraft_day_junction_exists(aircraft, day, **kwargs):
    """
    Creates or locates an instance of the AircraftPresentDay between the given aircraft and day. Optionally, default
    arguments for junction attributes can be supplied via keyword arguments in the case the entry does not exist. This
    function will also ensure the given day exists, and will create it if not so. However, aircraft must be created
    prior to calling this function.

    By default, the given Day will always be today's date, but taken from GMT+0 timezone. This is to keep inline with ADSBExchange's
    data storage/management standard.

    Arguments
    ---------
    :aircraft: The aircraft to associate with the given day.
    :day: A date instance, containing a specific day to report the aircraft as present on. By default, today.

    Keyword arguments
    -----------------
    :history_verified: A boolean, True if this aircraft's presence on this day has been verified. Default is True.
    :flights_verified: A boolean, True if all flight data for this aircraft, on this day, has been processed into partial flights. Default is False.
    :geolocation_verified: A boolean, True if all flight point data for this aircraft, on this day, has been geolocated. Default is False.

    Returns
    -------
    A tuple;
        The AircraftPresentDay instance
        A boolean; True if the record was created, otherwise False
    """
    try:
        history_verified = kwargs.get("history_verified", True)
        flights_verified = kwargs.get("history_verified", False)
        geolocation_verified = kwargs.get("geolocation_verified", False)
        # First, attempt to get day from Flask g, if we're in Test mode.
        if config.APP_ENV == "Test":
            day = g.get("date_today", day)
        # By default, we're going to get the day as at GMT+0.
        if not day:
            # Get a datetime at UTC.
            utc_now = datetime.fromtimestamp(time.time(), timezone.utc)
            # Now, set day to the date portion of this datetime.
            day = utc_now.date()
        # Ensure we have this day present by upserting it.
        # For now, on conflict we simply ignore, since there's no other day attributes we wish to update.
        ensure_days_created([day])
        db.session.flush()
        # We will perform this procedure by first attempting to locate an existing junction model between the two, and creating it should it not exist.
        # This way, we're future-safe for if we need to add further attributes to the junction model.
        existing_report = db.session.query(models.AircraftPresentDay)\
            .filter(and_(models.AircraftPresentDay.day_day == day, models.AircraftPresentDay.aircraft_icao == aircraft.icao))\
            .first()
        # If doesn't exist, make the record.
        if not existing_report:
            LOG.debug(f"Aircraft {str(aircraft)} has been reported as present on {day.isoformat()}")
            new_report = models.AircraftPresentDay(
                day_day = day,
                aircraft_icao = aircraft.icao,
                history_verified = history_verified,
                flights_verified = flights_verified,
                geolocation_verified = geolocation_verified
            )
            db.session.add(new_report)
            # Return the new report and True.
            return new_report, True
        # We found an existing record, return both the record and False.
        return existing_report, False
    except Exception as e:
        raise e


def report_aircraft_presence(aircraft, day = None, **kwargs):
    """
    Report the presence of an aircraft on a particular day.
    To the server, this means that the date can be ignored in reference to the given aircraft; there are no
    further traces or journies to be logged or researched.

    Arguments
    ---------
    :aircraft: An instance of Aircraft to report as present.
    :day: A date instance, containing a specific day to report the aircraft as present on. By default, today.

    Keyword arguments
    -----------------
    :history_verified: A boolean, True if this aircraft's presence on this day has been verified. Default is True.
    :reporter: The entity to account the report from. If None, :TODO:

    Returns
    -------
    A tuple of 2;
        The AircraftPresentDay instance,
        A boolean; whether or not the record was created.
    """
    try:
        history_verified = kwargs.get("history_verified", True)
        reporter = kwargs.get("reporter", None)

        # Ensure we have an Aircraft-Day junction table.
        report, was_created = ensure_aircraft_day_junction_exists(aircraft, day, history_verified = history_verified)
        # Otherwise, if it does exist, set verified to True only if the value is not already True, otherwise take no action.
        if not was_created and (history_verified and not report.history_verified):
            LOG.debug(f"Setting day presence verification for aircraft {str(aircraft)} on {day.isoformat()} to {history_verified}.")
            report.history_verified = history_verified
        return report, was_created
    except Exception as e:
        raise e


def set_aircraft_day_flights_verified(aircraft, day = None, **kwargs):
    """
    Given an aircraft and a date instance, report this aircraft's flight data as having been processed by a background worker. This should
    be set to False whenever an aircraft's flight data has been updated for the day, and should be set to True once a background worker has
    determined whether or not there are any variations in the frontend flights data.

    Arguments
    ---------
    :aircraft: An instance of Aircraft.
    :day: A date instance, containing a specific day to set flights verified status upon. By default, today.

    Keyword arguments
    -----------------
    :flights_verified: A boolean, True if this aircraft's flight data on this day has been processed.
    :reporter: The entity to account the report from. If None, :TODO:

    Returns
    -------
    A tuple of 2;
        The AircraftPresentDay instance,
        A boolean; whether or not the record was created.
    """
    try:
        flights_verified = kwargs.get("flights_verified", True)
        reporter = kwargs.get("reporter", None)

        # Ensure we have an Aircraft-Day junction table.
        report, was_created = ensure_aircraft_day_junction_exists(aircraft, day, flights_verified = flights_verified)
        # Otherwise, if it does exist, simply set flights_verified to whatever we have been given.
        if not was_created:
            LOG.debug(f"Setting flights verification status for aircraft {str(aircraft)} on {day.isoformat()} to {flights_verified}.")
            report.flights_verified = flights_verified
        return report, was_created
    except Exception as e:
        raise e


def set_aircraft_day_geolocation_verified(aircraft, day = None, **kwargs):
    """
    Given an aircraft and a date instance, report this aircraft's flight points as having been geolocated by a background worker.

    Arguments
    ---------
    :aircraft: An instance of Aircraft.
    :day: A date instance, containing a specific day to set geolocation verified status upon. By default, today.

    Keyword arguments
    -----------------
    :geolocation_verified: A boolean, True if this aircraft's flight points on this day has been geolocated.
    :reporter: The entity to account the report from. If None, :TODO:

    Returns
    -------
    A tuple of 2;
        The AircraftPresentDay instance,
        A boolean; whether or not the record was created.
    """
    try:
        geolocation_verified = kwargs.get("geolocation_verified", True)
        reporter = kwargs.get("reporter", None)

        # Ensure we have an Aircraft-Day junction table.
        report, was_created = ensure_aircraft_day_junction_exists(aircraft, day, geolocation_verified = geolocation_verified)
        # Otherwise, if it does exist, simply set geolocation_verified to whatever we have been given.
        if not was_created:
            LOG.debug(f"Setting geolocation verification status for aircraft {str(aircraft)} on {day.isoformat()} to {geolocation_verified}.")
            report.geolocation_verified = geolocation_verified
        return report, was_created
    except Exception as e:
        raise e


def ensure_days_created(days):
    """
    Given a list of Dates, ensure these are all created on the database.

    Arguments
    ---------
    :days: A list of Date instances.
    """
    try:
        for day in days:
            # Construct an insert for each day.
            insert_day_stmt = (
                insert(models.Day.__table__)
                .values( day = day )
            ).on_conflict_do_nothing(index_elements = ["day"])
            db.session.execute(insert_day_stmt)
    except Exception as e:
        raise e


def ensure_days_created_from_aircraft(aircraft_d):
    """
    Given a newly loaded Aircraft schema, ensure all days referenced by all flight points are created on the database.

    Arguments
    ---------
    :aircraft_d: A loaded AircraftSchema.
    """
    try:
        dates = []
        for flight_point in aircraft_d["flight_points"]:
            day = flight_point.day_day
            # If not in dates list, add it.
            if not day in dates:
                dates.append(day)
        # Ensure these days are created.
        ensure_days_created(dates)
    except Exception as e:
        raise e


def handle_flight_data_revision(data_revision_required, **kwargs):
    """
    """
    try:
        LOG.debug(f"Attempting to queue data revision for {data_revision_required.aircraft} on day {data_revision_required.day} (history={data_revision_required.requires_history}, flights={data_revision_required.requires_flight})")
    except Exception as e:
        raise e


def handle_missing_fuel_figures(missing_fuel_figures, **kwargs):
    """
    """
    try:
        LOG.debug(f"Handling report of missing fuel figures for aircraft {missing_fuel_figures.aircraft}")
    except Exception as e:
        raise e


def handle_flight_point_integrity(**kwargs):
    """
    """
    try:
        """
        TODO
        """
        pass
    except Exception as e:
        raise e


def handle_no_partial_flights_found(aircraft_present_day, no_partial_flights, **kwargs):
    """
    This can be called even when there are honestly no flight points on that day. This function is intended to determine
    the likliehood that this aircraft/day is simply empty.
    """
    try:
        LOG.debug(f"Handling case in which there are no partial flights on {aircraft_present_day}")
    except Exception as e:
        raise e
