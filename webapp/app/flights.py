"""
A module for assembling flight models given raw flight point data.
"""
import re
import os
import uuid
import logging
import time as time_
import json
from datetime import datetime, date, timedelta, time, timezone

import shapely
from shapely import geometry, ops

from sqlalchemy import func, and_, or_, desc, asc
from sqlalchemy.exc import IntegrityError
from marshmallow import Schema, fields, EXCLUDE, post_load

from .compat import insert

from . import db, config, models, error, traces, calculations, inaccuracy, geospatial

LOG = logging.getLogger("aireyes.flights")
LOG.setLevel( logging.DEBUG )


def query_flights(**kwargs):
    """
    Construct and return a query for a list of flights.

    Keyword arguments
    -----------------
    :newest_first: True if flights should be ordered such that the latest Flights are first. False otherwise. Default is True.

    Returns
    -------
    A query, constructed to specs, that will result in a list of Flights.
    """
    try:
        newest_first = kwargs.get("newest_first", True)

        """TODO 0x07: Where we join flight points to this query; this may not be necessary, and, in fact, it may even signficantly slow this down. Take a look at setting a proper first and last
        column within the Flight table, instead of this."""
        # We'll begin by building a subquery that queries from the flights table, for each flight and the timestamp at which the flight starts.
        flights_q = db.session.query(models.Flight)\
            .join(models.Flight.flight_points_)
        # Attach ordering.
        if newest_first:
            flights_q = flights_q\
                .order_by(desc(models.Flight.starts_at))
        else:
            flights_q = flights_q\
                .order_by(asc(models.Flight.starts_at))
        # Group by flight hash, so there is just a single starts at per row?
        flights_q = flights_q\
            .group_by(models.Flight.flight_id)
        return flights_q
    except Exception as e:
        raise e


def query_flights_from(aircraft, **kwargs):
    """
    Construct and return a query for a list of flights belonging to the given aircraft.

    Arguments
    ---------
    :aircraft: The aircraft from which to query flights.

    Keyword arguments
    -----------------
    :newest_first: True if flights should be ordered such that the latest Flights are first. False otherwise. Default is True.

    Returns
    -------
    A query, constructed to specs, that will result in a list of Flights.
    """
    try:
        newest_first = kwargs.get("newest_first", True)

        """TODO 0x07: Where we join flight points to this query; this may not be necessary, and, in fact, it may even signficantly slow this down. Take a look at setting a proper first and last
        column within the Flight table, instead of this."""
        # We'll begin by building a subquery that queries from the flights table, for each flight and the timestamp at which the flight starts.
        flights_q = db.session.query(models.Flight)\
            .filter(models.Flight.aircraft_icao == aircraft.icao)\
            .join(models.Flight.flight_points_)
        # Attach ordering.
        if newest_first:
            flights_q = flights_q\
                .order_by(desc(models.Flight.starts_at))
        else:
            flights_q = flights_q\
                .order_by(asc(models.Flight.starts_at))
        # Group by flight hash, so there is just a single starts at per row?
        flights_q = flights_q\
            .group_by(models.Flight.flight_id)
        return flights_q
    except Exception as e:
        raise e


class FlightPartialSubmissionReceipt():
    """A data model for passing back a receipt for the submission of a sequence of flight points."""
    def __init__(self, _aircraft_present_day, _flight, _was_created, **kwargs):
        self.aircraft_present_day = _aircraft_present_day
        self.flight = _flight
        self.was_created = _was_created


def flight_partial_submitted(aircraft, day, flight_points, **kwargs) -> FlightPartialSubmissionReceipt:
    """
    This function should be called after receiving new flight data for a CURRENT ongoing flight from a radar worker with mode 'aircraft-tracker'.
    The submitted partial must be a list of flight points recorded in realtime. The corresponding flight does not have to exist at the time, this
    will indicate a new Flight, created realtime.

    Since the aircraft present day in question is assumed to be current/ongoing, meaning the data will change continuously and nothing is 'set in
    stone', so to speak, this function will set the aircraft present day's verified indicators to False upon each call. This essentially means, at
    a later date, a radar worker will need to confirm this day's history, and then a background worker will need to revise flights data.

    Arguments
    ---------
    :aircraft: The aircraft responsible for recording these points.
    :day: A Date instance upon which these flights are to be recorded.
    :flight_points: A list of the FlightPoints that the worker submitted; they should already belong to the database & aircraft present day.

    Keyword arguments
    -----------------
    :geolocate_flight_points: True if the flight points provided should have their suburbs geolocated. Default is True.
    :radar_worker: The RadarWorker currently running; must be in 'aircraft-tracker' mode.
    :worker_required: True if this function should disallow radar worker to be None (default is True, unless Test is current app env; then False)

    Raises
    ------
    RadarWorkerRequiredError: A radar worker is required by this function.
    RadarWorkerIncompatibleError: The radar worker provided is probably not an aircraft-tracker type.

    Returns
    -------
    A FlightPartialSubmissionReceipt.
    """
    try:
        geolocate_flight_points = kwargs.get("geolocate_flight_points", True)
        radar_worker = kwargs.get("radar_worker", None)
        worker_required = kwargs.get("worker_required", True if config.APP_ENV != "Test" else False)
        if not radar_worker and worker_required:
            LOG.error(f"Could not submit partial flight for {aircraft} on day {day}, no radar worker was provided, but one is required.")
            raise error.RadarWorkerRequiredError()
        elif radar_worker and radar_worker.worker_type != "aircraft-tracker":
            LOG.error(f"Could not submit partial flight for {aircraft} on day {day}, the provided radar worker is INCOMPATIBLE!")
            raise error.RadarWorkerIncompatibleError(radar_worker, "aircraft-tracker")
        # If there are no flight points given, raise an error for that.
        if not len(flight_points):
            LOG.error(f"Failed to assimilate partial flight for {aircraft} on {day}, no flight points were even given.")
            raise error.NoFlightPointsToAssimilateError()
        # If we have been instructed to geolocate flight points, do so now. ***TODO*** We should maybe relocate this when we work out the best way to find a previous suburb.
        if config.SHOULD_GEOLOCATE_FLIGHT_POINTS and geolocate_flight_points:
            LOG.debug(f"Before submitting partial flight for {aircraft} on {day}, points will be geolocated...")
            geospatial.geolocate_suburbs_for(flight_points)
        # First step is to ensure an AircraftPresentDay junction exists for this aircraft/day combination. By default, set both history_verified and flights_verified to False.
        # Doing this will ensure that at a later date, the background system will still double check this day.
        aircraft_present_day, was_created = traces.ensure_aircraft_day_junction_exists(aircraft, day,
            history_verified = False, flights_verified = False)
        # If we weren't able to create one, raise an exception; this is an unknown error case.
        if not aircraft_present_day:
            LOG.error(f"Failed to submit flight data for aircraft {aircraft} on {day}, we were not able to ensure aircraft/day junction exists!")
            raise Exception("no-junction")
        LOG.debug(f"Commencing submitting of {len(flight_points)} flight points for {aircraft_present_day} by worker {radar_worker}")
        # We will always set history verified & flights verified to False.
        aircraft_present_day.history_verified = False
        aircraft_present_day.flights_verified = False
        # Begin by getting all flight points from that aircraft/day.
        day_flight_points = aircraft_present_day.all_flight_points
        LOG.debug(f"Located {len(day_flight_points)} on {aircraft_present_day} to construct our submission environment from.")
        # Now, create a daily flights view for these points, this should also include our provided points above.
        daily_flights_view = DailyFlightsView.from_args(aircraft, day, day_flight_points)
        LOG.debug(f"Within {aircraft_present_day}, we located {daily_flights_view.num_partial_flights} partial flights.")
        # Now, we will get a partial flight that this sequence of points is destined for.
        located_partial_flight = daily_flights_view.attempt_find_suitable_partial_for(flight_points)
        # If the located partial flight is None, we will assume that this is a multi-flight list of flight points, and so, we will need to assimilate the whole day, as in revise_flight_data_for.
        if not located_partial_flight:
            LOG.warning(f"Unable to locate partial flight for submitted sequence of flight points. This may be a multi-flight input. We will parse the daily flights view as a full day!")
            # Call out to assimilate partial flights to handle this daily flights view as a whole.
            resulting_flights, created, updated, error_ = assimilate_partial_flights_from_view(aircraft, aircraft_present_day, daily_flights_view)
            LOG.debug(f"Daily flight revision COMPLETED for {aircraft_present_day}. We created {created} flights and changed {updated} flights. We failed to assimilate {error_} flights.")
            # Just a quick failure check here, if created+updated is 0, raise a NoFlightsAssimilatedError.
            if not created+updated:
                LOG.error(f"Failed to assimilate flights at all for {aircraft_present_day}. In total, {error_} assimilations failed!")
                raise error.NoFlightsAssimilatedError("zero-created-updated")
            # Otherwise, since we have one or more successes, we will use the most recent flight for the receipt; since we'll assume, chronologically, that makes sense.
            flight, was_created = resulting_flights[len(resulting_flights)-1]
        else:
            LOG.debug(f"Got suitable partial flight to add submitted sequence of flight points to...")
            # Alright that's all good. We can now build an assimilator and assimilate this flight.
            assimilator = FlightAssimilator.from_partial_flight(aircraft, located_partial_flight)
            flight, was_created = assimilator.assimilate()
        # Return a receipt.
        return FlightPartialSubmissionReceipt(aircraft_present_day, flight, was_created)
    except error.NoPartialFlightFoundForSubmission as npfffs:
        """
        TODO: for now, we will just request this aircraft present day have its flights verified at some other stage.
        But, we should add some more complex management techniques here.
        """
        traces.handle_flight_data_revision(
            error.FlightDataRevisionRequired(aircraft, day,
                flights_verified = False))
        raise npfffs
    except Exception as e:
        raise e


class FlightDataRevisionReceipt():
    """A data model for storing and returning a receipt for a single execution of revise_flight_data_for."""
    @property
    def num_flights(self):
        return len(self.flights)

    @property
    def started(self):
        return datetime.fromtimestamp(self.timestamp_started).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def finished(self):
        return datetime.fromtimestamp(self.timestamp_finished).strftime("%Y-%m-%d %H:%M:%S")

    def __init__(self, _timestamp_started, _aircraft_present_day, _revised_flights, _num_created, _num_updated, _num_error, **kwargs):
        self.timestamp_started = _timestamp_started
        self.timestamp_finished = time_.time()
        self.aircraft_present_day = _aircraft_present_day
        self.flights = _revised_flights
        self.num_created = _num_created
        self.num_updated = _num_updated
        self.num_error = _num_error


def revise_flight_data_for(aircraft, day, **kwargs) -> FlightDataRevisionReceipt:
    """
    This function will revise all flights data for the given aircraft present day. This essentially will refresh all Flights involved with this day and
    aircraft specifically - so this may span across multiple days. In order for this function to execute logic upon the requested aircraft present day,
    the junction table's history_verified should be set to True. This function should not be called upon an aircraft/day whose flights_verified attribute
    is already True, unless the 'force' attribute is provided as True in keyword args.

    This function can be very inefficient, and is designed to be called only by background workers such as Celery. Upon completion, this function will set
    this aircraft present day's flights verified attribute to True.

    Arguments
    ---------
    :aircraft: The aircraft whose flight data we should revise.
    :day: A Date instance representing the day to use as the base reference for the flight data to revise.

    Keyword arguments
    -----------------
    :force: Whether history_verified/flights_verified should be ignored. Default is False.

    Raises
    ------
    NoFlightPointsError: No flight point data was provided.

    Returns
    -------
    An instance of FlightDataRevisionReceipt.
    """
    try:
        force = kwargs.get("force", False)

        started = time_.time()
        # Ensure we have an AircraftPresentDay to represent this aircraft/day junction. By default, set history & flights verified to False.
        # This also means that if this junction was just created, this function's execution can not continue, as history must be verified prior to flights verification, unless force is True.
        aircraft_present_day, was_created = traces.ensure_aircraft_day_junction_exists(aircraft, day,
            history_verified = False, flights_verified = False)
        # If we weren't able to create one, raise an exception; this is an unknown error case.
        if not aircraft_present_day:
            LOG.error(f"Failed to revise flight data for aircraft {aircraft} on {day}, we were not able to ensure aircraft/day junction exists!")
            raise Exception("no-junction")
        elif not aircraft_present_day.history_verified and not force:
            # If history verified is False, we will raise an exception requiring flight history revision.
            LOG.error(f"Failed to revise flight data for aircraft {aircraft} on {day}, history data must first be verified!")
            raise error.FlightDataRevisionRequired(aircraft, day, requires_history = True)
        elif aircraft_present_day.flights_verified and not force:
            # Flights data is already verified on this day, and so we will not execute this twice.
            LOG.error(f"Failed to revise flight data for aircraft {aircraft} on {day}, flights data is already verified, and the 'force' attribute was not provided!")
            raise error.FlightsVerifiedError(aircraft_present_day)
        LOG.debug(f"Commencing revision of flight data for {aircraft_present_day}")
        # We will set flights_verified to False at this point. If something goes wrong, we want this attempted ASAP.
        aircraft_present_day.flights_verified = False
        # Begin by getting all flight points from that aircraft/day.
        day_flight_points = aircraft_present_day.all_flight_points
        if len(day_flight_points) == 0:
            LOG.error(f"Failed to revise revise flight data on {aircraft_present_day}, no flight points were found.")
            raise error.NoFlightPointsError(aircraft, days = [day])
        LOG.debug(f"Located {len(day_flight_points)} on {aircraft_present_day} to construct our revision environment from.")
        # Now, create a daily flights view for these points.
        daily_flights_view = DailyFlightsView.from_args(aircraft, day, day_flight_points)
        LOG.debug(f"Within {aircraft_present_day}, we located {daily_flights_view.num_partial_flights} partial flights.")
        # Now assimilate flights for this daily flights view.
        resulting_flights_with_was_created, created, updated, error_ = assimilate_partial_flights_from_view(aircraft, aircraft_present_day, daily_flights_view)
        resulting_flights = list(zip(*resulting_flights_with_was_created))[0]
        LOG.debug(f"Daily flight revision COMPLETED for {aircraft_present_day}. We created {created} flights and changed {updated} flights. We failed to assimilate {error_} flights.")
        # Since this was completed successfully, we can set flights_verified True.
        aircraft_present_day.flights_verified = True
        # Return a receipt.
        return FlightDataRevisionReceipt(started, aircraft_present_day, resulting_flights, created, updated, error_)
    except Exception as e:
        raise e


def assimilate_partial_flights_from_view(aircraft, aircraft_present_day, daily_flights_view):
    """Extracted this function for modularisation."""
    try:
        created = 0
        updated = 0
        error_ = 0
        # Otherwise, for each partial flight, we will construct an assimilator.
        resulting_flights = []
        for partial_flight in daily_flights_view.partial_flights:
            try:
                # Provide just the partial flight, and in the case this flight stretches across days, all data will still be considered.
                assimilator = FlightAssimilator.from_partial_flight(aircraft, partial_flight)
                # Now that our assimilator is built, just call the assimilate function to either create a new Flight, or update an existing.
                # Add the result to our resulting flights list.
                flight, was_created = assimilator.assimilate()
                LOG.debug(f"Successfully assimilated {partial_flight} to return {flight} on {aircraft_present_day}")
                resulting_flights.append((flight, was_created,))
                if was_created:
                    created+=1
                else:
                    updated+=1
            except error.NoPartialFlightsError as npfe:
                # No partial flights found whilst creating this assimilator. We will pass this over to traces to deal with.
                traces.handle_no_partial_flights_found(aircraft_present_day, npfe)
                error_+=1
                """TODO: utilise error.FlightAssimilationError to report this single failure."""
        return resulting_flights, created, updated, error_
    except Exception as e:
        raise e


class AircraftDayIterator():
    FORWARD = 0
    BACKWARD = 1

    def __init__(self, _aircraft, _start_day, _direction, **kwargs):
        """
        Initialise an Aircraft/Day iterator given an aircraft and a starting day. This will iterate the day in the requested direction, and on
        each next, return the newly adjusted day. If there are no aircraft present day records for the requested combo, iteration will cease.

        Arguments
        ---------
        :_aircraft: The aircraft to locate AircraftPresentDays for.
        :_start_day: The day on which to begin; this is excluded.
        :_direction: An integer, one of the values above, indicating direction.

        Keyword arguments
        -----------------
        :max_it: Maximum number of times to iterate before giving up.
        """
        max_it = kwargs.get("max_it", 100)

        self.aircraft = _aircraft
        self.start_day = _start_day
        self._direction = _direction
        self.max_it = max_it

    def __iter__(self):
        self.current_it = 0
        self.current_day = self.start_day
        return self

    def __next__(self):
        # If current it hit, stop iteration.
        if self.current_it >= self.max_it:
            raise StopIteration
        # Depending on towards past, either subtract or add a timedelta with a single day to our current day.
        self.current_day = self.current_day-timedelta(days = 1) if self._direction==AircraftDayIterator.BACKWARD else self.current_day+timedelta(days = 1)
        # Attempt to locate an aircraft present day for this combination.
        aircraft_day = models.AircraftPresentDay.find(self.aircraft.icao, self.current_day)
        if not aircraft_day:
            # If none found, stop iteration.
            raise StopIteration
        self.current_it+=1
        # Otherwise, return the aircraft/day.
        return aircraft_day


class FlightPointStartDescriptor():
    """
    A descriptor type that wraps a single FlightPoint. This is intended to indicate a commencement of flight data, as opposed to indicating the commencement
    of a new flight.
    """
    @property
    def time_iso(self):
        return datetime.utcfromtimestamp(int(self.flight_point.timestamp)).time().isoformat()

    @property
    def timestamp(self):
        return self.flight_point.timestamp

    @property
    def altitude(self):
        return self.flight_point.altitude

    @property
    def is_on_ground(self):
        """Returns a boolean indicating whether or not this flight point is on the ground."""
        return self.flight_point.is_on_ground

    def __init__(self, _first_flight_point):
        self.flight_point = _first_flight_point


class FlightPointChangeDescriptor():
    """
    A descriptor type that wraps two consecutive flight points, belonging to the same aircraft. A change descriptor is used to provide data
    that can be used to determine whether a flight has commenced or finished. But is also useful for flagging inaccuracies in flight data that
    require further investigation.
    """
    @property
    def point1_time_iso(self):
        return datetime.utcfromtimestamp(int(self.flight_point1.timestamp)).time().isoformat()

    @property
    def point2_time_iso(self):
        return datetime.utcfromtimestamp(int(self.flight_point2.timestamp)).time().isoformat()

    @property
    def point1_grounded(self):
        return self.flight_point1.is_on_ground

    @property
    def point2_grounded(self):
        return self.flight_point2.is_on_ground

    @property
    def both_grounded(self):
        return self.point1_grounded and self.point2_grounded

    @property
    def constitutes_new_flight(self):
        """
        Return a boolean True or False indicating whether flight point change can be considered a new flight given our current configuration. This essentially means
        flight point #1 is thought of as the last point received after the aircraft landed, and flight point #2 is considered the first update from the aircraft for
        the next flight.

        This property will only handle very basic new flight cases; those in which it is highly plausible given some basic data points such as altitude and time
        difference, that the aircraft has performed a landing as its last action, and a takeoff as its most recent. If cases are extreme anomalies, an exception;
        FlightChangeInaccuracySolvencyRequired will be raised, to indicate further investigation is required.
        """
        if self.both_grounded and self.time_difference_seconds > config.TIME_DIFFERENCE_NEW_FLIGHT_GROUNDED:
            # New flight detected, criteria: both points grounded.
            return True
        elif self.point1_grounded and not self.point2_grounded and self.time_difference_seconds > config.TIME_DIFFERENCE_NEW_FLIGHT_MID_AIR_START \
            and (self.flight_point2.altitude or 0) < config.MAX_ALTITUDE_MID_AIR_DISAPPEAR_START_NEW_FLIGHT:
            # New flight detected, started midair. Criteria: point #1 on ground, point #2 airborne, time between point #1 and point #2 exceeds TIME_DIFFERENCE_NEW_FLIGHT_MID_AIR_START,
            # altitude of point #2 is less than MAX_ALTITUDE_MID_AIR_START_NEW_FLIGHT.
            return True
        elif not self.point1_grounded and self.point2_grounded and self.time_difference_seconds > config.TIME_DIFFERENCE_NEW_FLIGHT_MID_AIR_END \
            and (self.flight_point1.altitude or 0) < config.MAX_ALTITUDE_MID_AIR_DISAPPEAR_START_NEW_FLIGHT:
            # New flight detected, ended midair. Criteria: point #1 airborne, point #2 on ground, time between point #1 and point #2 exceeds TIME_DIFFERENCE_NEW_FLIGHT_MID_AIR_END,
            # altitude of point #1 is less than MAX_ALTITUDE_MID_AIR_DISAPPEAR_START_NEW_FLIGHT.
            return True
        elif not self.point1_grounded and not self.point2_grounded and self.time_difference_seconds >= config.TIME_DIFFERENCE_INACCURACY_CHECK_REQUIRED:
            # Request further investigation if neither point is grounded, and the time difference is TIME_DIFFERENCE_INACCURACY_CHECK_REQUIRED.
            LOG.warning(f"Flight change descriptor {self} applies for anomalous behaviour. Requesting inaccuracy solvency...")
            raise error.FlightChangeInaccuracySolvencyRequired(self)
        return False

    def __str__(self):
        return f"Point<{self.point1_time_iso} -> {self.point2_time_iso}>"

    def __init__(self, _flight_point1, _flight_point2):
        self.flight_point1 = _flight_point1
        self.flight_point2 = _flight_point2
        # Flight point change attributes.
        self.time_difference_seconds = _flight_point2.timestamp-_flight_point1.timestamp


class FlightPointEndDescriptor():
    """
    A descriptor type that wraps a single FlightPoint. This is intended to indicate the end of flight data, as opposed to indicating the end
    of a flight.
    """
    @property
    def time_iso(self):
        return datetime.utcfromtimestamp(int(self.flight_point.timestamp)).time().isoformat()

    @property
    def timestamp(self):
        return self.flight_point.timestamp

    @property
    def altitude(self):
        return self.flight_point.altitude

    @property
    def is_on_ground(self):
        """Returns a boolean indicating whether or not this flight point is on the ground."""
        return self.flight_point.is_on_ground

    def __init__(self, _last_flight_point):
        self.flight_point = _last_flight_point


class FlightPointsManager():
    """
    A base class for common functionality among subtypes that feature a flight points collection.
    Subtypes therefore must implement a list-type property with name 'flight_points' on the instance level.
    """
    @property
    def crs(self):
        """Return the CRS common among all flight points in the manager."""
        return self._common_crs

    @property
    def flight_path(self):
        """
        Returns a Shapely LineString geometry containing all points in this flight points manager. A common CRS must be set.
        There must also be at least two positional flight points in this manager. Failing this, the flight points path will be considered not a proper path.
        """
        if not self.crs:
            LOG.error(f"Could not get flight path from flight points manager {self}, no CRS is set! Raising an InvalidCRSError.")
            raise error.InvalidCRSError("no-crs-set", flight_points = self.flight_points)
        if self.num_positional_flight_points < config.MINIMUM_POSITIONAL_FLIGHT_PATH_POINTS:
            LOG.error(f"Could not get flight path from flight points manager {self}, this flight path has {self.num_positional_flight_points} whereas the minimum required for a valid path is {config.MINIMUM_POSITIONAL_FLIGHT_PATH_POINTS}.")
            raise error.NoFlightPathError()
        return geometry.LineString([flight_point.position for flight_point in self.positional_flight_points])

    @property
    def first_point(self):
        """Return first flight point or None if there are no points."""
        if not self.num_flight_points:
            return None
        return self._flight_points[0]

    @property
    def positional_first_point(self):
        """Return the first flight point, with a valid position, or none if there are no points."""
        if not self.num_positional_flight_points:
            return None
        return self.positional_flight_points[0]

    @property
    def last_point(self):
        """Return last flight point or None if there are no points."""
        if not self.num_flight_points:
            return None
        return self._flight_points[self.num_flight_points-1]

    @property
    def positional_last_point(self):
        """Return the last flight point, with a valid position or None if there are no points."""
        if not self.num_positional_flight_points:
            return None
        return self.positional_flight_points[self.num_positional_flight_points-1]

    @property
    def positional_flight_points(self):
        """Returns all flight points, but without those that do not have positional data."""
        return list(filter(lambda flight_point: flight_point.is_position_valid, self._flight_points))

    @property
    def flight_points(self):
        return self._flight_points

    @flight_points.setter
    def flight_points(self, value):
        self._flight_points = value

    @property
    def num_flight_points(self):
        return len(self._flight_points)

    @property
    def num_positional_flight_points(self):
        return len(self.positional_flight_points)

    def __getitem__(self, index):
        return self._flight_points[index]

    def __setitem__(self, index, value):
        self._flight_points[index] = value

    def __delitem__(self, key):
        """Not allowed to delete flight points."""
        raise NotImplementedError("FlightPointsManager can't delete flight points!")

    def __init__(self, _flight_points = [], **kwargs):
        self.set_flight_points(_flight_points)

    def set_flight_points(self, flight_points):
        self._flight_points = flight_points
        self._find_common_crs()

    def derive_manager(self, **kwargs):
        """
        Instantiate and return a flight points manager constructed from this manager's inner flight points, but with certain filtering
        criteria applied; such as, only points that are airborne.

        Keyword arguments
        -----------------
        :airborne_only: True if only flight points that are airborn should be used. Default is False.
        :within_hours_range: A tuple with two Time objects, timezone aware. The new manager will only contain flight points WITHIN the range.
        """
        try:
            airborne_only = kwargs.get("airborne_only", False)
            within_hours_range = kwargs.get("within_hours_range", None)
            # Ensure hours range, if given, is valid.
            if within_hours_range and (not isinstance(within_hours_range, tuple) or len(within_hours_range) != 2 or not isinstance(within_hours_range[0], time) or not isinstance(within_hours_range[1], time)):
                raise ValueError("within_hours_range must be a tuple, containing just TWO time objects.")
            # Make a filtration function applying the given criteria.
            def filter_point(flight_point):
                # If flight point is on ground, but we require only airborn points, exclude.
                if airborne_only and flight_point.is_on_ground:
                    return False
                # If we've been given a range to filter outliers from.
                if within_hours_range:
                    # Make an aware datetime for the flight point's timestamp, we'll use the first Time's tzinfo.
                    aware_datetime = datetime.fromtimestamp(int(flight_point.timestamp), tz = within_hours_range[0].tzinfo)
                    # Combine flight points date, with the respective bound in given range, with equivalent timezone.
                    lower_bound = datetime.combine(aware_datetime.date(), within_hours_range[0], tzinfo = within_hours_range[0].tzinfo)
                    upper_bound = datetime.combine(aware_datetime.date(), within_hours_range[1], tzinfo = within_hours_range[0].tzinfo)
                    # Adjust aware datetime to be a day ahead, should the range continue into next day.
                    if upper_bound <= lower_bound:
                        upper_bound += timedelta(days = 1)
                    if aware_datetime <= lower_bound:
                        aware_datetime += timedelta(days = 1)
                    if not lower_bound <= aware_datetime <= upper_bound:
                        # Flight point is not within range.
                        return False
                return True
            # Get a new list of flight points from this inner flight points list.
            derived_flight_points = list(filter(filter_point, self._flight_points))
            # Return a new manager.
            return FlightPointsManager(derived_flight_points)
        except Exception as e:
            raise e

    def calculate_surrounding_points(self, flight_point_idx):
        """
        Given a flight point index, return three flight points; the one preceding the point at the given index, the point at the given index and
        the point proceeding the point at the given index. Current point can't be None, but the other two can be.

        Arguments
        ---------
        :flight_point_idx: The point at which to gather surrounding points.

        Returns
        -------
        Three flight points;
            The point preceding flight point index, nullable.
            The point at flight point index.
            The point proceeding flight point index, nullable.
        """
        points = [None, self._flight_points[flight_point_idx], None]
        # Get previous point.
        points[0] = None if flight_point_idx-1 < 0 else self._flight_points[flight_point_idx-1]
        # Get the next point.
        points[2] = None if flight_point_idx+1 >= len(self._flight_points) else self._flight_points[flight_point_idx+1]
        # Return points.
        return points[0], points[1], points[2]

    def _find_common_crs(self):
        """
        Locate a common CRS from all flight points contained within this manager.

        Raises
        ------
        InvalidCRSError:
        :position-valid: The position on a flight point was valid, but the CRS was not.
        :flight-point-crs-mismatch: There are multiple CRSs within the same flight path.
        """
        # Variable for holding an EPSG code for the common CRS among all flight points. If any are different, this class will raise an exception for now.
        self._common_crs = None
        common_crs = None
        # Iterate all flight points.
        for flight_point in self._flight_points:
            if not flight_point.crs:
                # This flight point does not have a valid CRS. This is OK as long as it does not have a valid position either.
                if flight_point.is_position_valid:
                    # This flight point has a valid position, which means something went wrong whilst geolocating it.
                    LOG.error(f"Failed to find common CRS for flight path {self}, flight point {flight_point} has a CRS that is invalid, but position is VALID!")
                    raise error.InvalidCRSError("position-valid")
                else:
                    # Otherwise, this flight point was not geolocated because it does not even have a location; we can therefore skip this.
                    LOG.warning(f"Skipped considering {flight_point} within calculation of common CRS for {self}, it does not have a valid location and was therefore never geolocated.")
                    continue
            # Otherwise, if flight point has a CRS and common CRS is None, set common CRS and continue.
            if not common_crs:
                common_crs = flight_point.crs
                continue
            # Finally, if we've gotten to this point, ensure flight point's CRS matches the common CRS. If not, raise an exception.
            if common_crs != flight_point.crs:
                LOG.error(f"Failed to find common CRS for flight path {self}, flight point {flight_point} has a different CRS to the current common (this={flight_point.crs}, com={common_crs})")
                raise InvalidCRSError("flight-point-crs-mismatch")
        # Finally, set the instance level common CRS to this CRS.
        self._common_crs = common_crs


class FlightAssimilator(FlightPointsManager):
    """
    A class that assists in the creation and assimilation of database Flight models, given a list of PartialFlight objects. The partial flights given
    do not have to be complete flights in their own right. Partial flights do not have to be ordered. The assimilator will also calculate the latest
    statistics for the Flight based on all flight points given. If the Flight already exists, this will simply be updated at the end of the assimilation
    procedure.
    """
    @property
    def first_partial_flight(self):
        if not len(self.partial_flights):
            return None
        return self.partial_flights[0]

    @property
    def last_partial_flight(self):
        if not len(self.partial_flights):
            return None
        return self.partial_flights[len(self.partial_flights)-1]

    @property
    def most_recent_point(self):
        """Return the absolute last point in this assimilator's flight points list, or None."""
        return self.last_point

    def __str__(self):
        return f"FlightAssimilator<{self.aircraft},#partials={len(self.partial_flights)}>"

    def __init__(self, _aircraft, _partial_flights, **kwargs):
        """
        Initialise a new flight assimilator.

        Arguments
        ---------
        :_aircraft: The aircraft that performed the flight.
        :_partial_flights: A list of PartialFlight instances.
        """
        self.aircraft = _aircraft
        self.partial_flights = _partial_flights
        # New tuple for containing raw flight points for the whole flight.
        self.flight_points = ()
        # Flight assimilator will have its own timeline.
        self._timeline = ()
        # The single flight determined to potentially be the dominant flight.
        self._dominant_flight = None

        # We will preserve our start and end descriptors.
        self._start_descriptor = None
        self._end_descriptor = None

        ### Realtime statistics ###
        self._is_on_ground = None

        ### Flight statistics ###
        # Airport at which this flight took off.
        self._takeoff_airport = None
        # Airport at which this flight landed.
        self._landing_airport = None
        # Distance travelled (meters.)
        self._distance_travelled = None
        # Fuel used (gallons.) Fuel consumption data for the associated aircraft is required.
        self._fuel_used = None
        # Average speed (knots.)
        self._average_speed = None
        # Average altitude (feet.)
        self._average_altitude = None
        # Total flight time.
        self._flight_time_total = None
        # Average time spent, in seconds, flying within prohibited times (see statistic_calculation_research.txt)
        self._flight_time_prohibited = None
        # Total amount of co2 emitted, in kilograms.
        self._total_co2_emissions = None
        # Has this flight been off the ground yet?
        self._has_been_airborne = False

        # Aggregate descriptors that will indicate whether there is adequate detail to deem this flight having begun/ended properly.
        self._has_departure_details = False
        self._has_arrival_details = False
        # A descriptor that will be set True if the Flight has never left the ground.
        self._taxi_only = False

    def assimilate(self):
        """
        A function that will attempt to assimilate this assembled flight to the database. This function will NOT be automatically called by either of the class level
        factory functions, since it converses directly with the database. Programmer should therefore call this function themselves, perhaps on a separate worker.

        The easiest way this function succeeds is by lateral assimilation; wherein one or more flight points is already tied to a Flight, and as such, should the system
        determine those without a Flight are yet in the same Flight, that Flight will be made dominant. Otherwise, this function will assemble a new Flight model.
        TODO: perhaps intersection check? Seems sort've redundant.

        Returns
        -------
        A tuple containing two items;
            A flight model,
            A boolean indicating whether or not the Flight was created.
        """
        try:
            if self._dominant_flight:
                # We have a single associated flight. We shall now simply ensure all flight points in this collection is associated with the same flight.
                LOG.debug(f"Located dominant flight {self._dominant_flight} for flight assimilation {self}. Ensuring all points are attached to this flight...")
                num_attached = 0
                num_already_attached = 0
                for flight_point in self.flight_points:
                    if flight_point.flight != self._dominant_flight:
                        num_attached+=1
                        flight_point.flight = self._dominant_flight
                    else:
                        num_already_attached+=1
                LOG.debug(f"Finished assimilating points for {self} to {self._dominant_flight}. We newly attached {num_attached} points, whilst there were {num_already_attached} points already attached.")
                # Set statistics.
                self._copy_statistics_to(self._dominant_flight)
                # Return dominant flight.
                db.session.flush()
                return self._dominant_flight, False
            """TODO: we can add some more checks in here, perhaps an intersection check of sorts?"""
            # Otherwise, its time to create a new Flight.
            LOG.debug(f"Could not find any dominant flights for {self}, we will create a new one.")
            new_flight = models.Flight(
                flight_hash = self._new_flight_hash(),
                aircraft = self.aircraft
            )
            # Add to session & flush.
            db.session.add(new_flight)
            db.session.flush()
            # Now, set flight points.
            new_flight.set_flight_points(self.flight_points)
            # Set statistics.
            self._copy_statistics_to(new_flight)
            # Finally return this new flight.
            db.session.flush()
            LOG.debug(f"New flight {new_flight} has been successfully generated from {self}.")
            return new_flight, True
        except Exception as e:
            raise e

    def _new_flight_hash(self):
        """
        Generate a flight hash for this assimilator.
        The flight hash, at the moment, will just be a UUID.
        """
        return uuid.uuid4().hex.lower()

    def _copy_statistics_to(self, flight, **kwargs):
        """
        Handling the moving of our calculated flight statistics from this assimilator instance to whatever Flight model we
        have decided will represent the assimilator.
        """
        try:
            LOG.debug(f"Copying statistics from {self} to flight {flight}...")
            # Copy realtime statistics.
            flight.is_on_ground = self._is_on_ground

            # Copy calculated statistics.
            if self._takeoff_airport:
                flight.takeoff_airport = self._takeoff_airport
            if self._landing_airport:
                flight.landing_airport = self._landing_airport
            flight.distance_travelled = self._distance_travelled
            flight.fuel_used = self._fuel_used
            flight.average_speed = self._average_speed
            flight.average_altitude = self._average_altitude
            flight.flight_time_total = self._flight_time_total
            flight.flight_time_prohibited = self._flight_time_prohibited
            flight.total_co2_emissions = self._total_co2_emissions

            # Copy over has departure/arrival details. If the aircraft has taken off again, arrival details will yet again report False.
            flight.has_departure_details = self._has_departure_details
            flight.has_arrival_details = self._has_arrival_details

            # Copy over whether this flight is (so far) just taxiing.
            flight.taxi_only = self._taxi_only
        except Exception as e:
            raise e

    def _extract_flight_points(self):
        """
        Extract flight points from all partial flights provided to this assimilator. This will essentially filter out all descriptors,
        sort the remaining points by timestamp ascending, and set the instance attribute 'flight_points' as the final tuple.

        Raises
        ------
        NoPartialFlightsError: No flight partials were given to this assimilator.
        NoFlightPointsError: No flight points were located in any of the partials given to this assimilator.
        """
        try:
            if not len(self.partial_flights):
                LOG.error(f"Failed to make timeline for flight assimilator; no flight partials given.")
                raise error.NoPartialFlightsError(self)
            # We will first create a single huge list of all flight points involved in this flight, by collapsing all entries in partial flights.
            all_flight_points = []
            for partial_flight in self.partial_flights:
                # Ensure this is a partial flight.
                if not isinstance(partial_flight, PartialFlight):
                    LOG.error(f"Object given as a PartialFlight to FlightAssimilator is instead of type {type(partial_flight)}")
                    raise TypeError("not-partial-flight")
                # Next, extend all flight points by this partial flight's flight points.
                """TODO: perhaps there's a more efficient way to do this?"""
                all_flight_points.extend(partial_flight.flight_points)
            # If there are no flight points at all in all_flight_points, raise an error reporting this.
            if len(all_flight_points) == 0:
                LOG.error(f"No flight points found at all in {self}!")
                raise error.NoFlightPointsError(self.aircraft, partial_flights = self.partial_flights)
            # Now, sort flight points by timestamp, and set this class' flight points. This will also locate the common CRS.
            self.set_flight_points(tuple(sorted(all_flight_points, key = lambda flight_point: flight_point.timestamp)))
        except Exception as e:
            raise e

    def _make_timeline(self):
        """
        Make an assimilator timeline. This timeline differs to that held by DailyFlightsView because the focus of a single instance of assimilator is regarding
        a single Flight instance. The timeline is structured the same however, beginning with a start descriptor, ending with an end descriptor and peppered with
        change descriptors between every other point.
        """
        try:
            if not self.num_flight_points:
                LOG.error(f"Failed to make timeline for flight assimilator; no flight points given.")
                raise error.NoPartialFlightsError(self)
            # Now, we will process all flight points into another list, which is to be our timeline tuple. This is where we'll inject our descriptors.
            timeline_list = []
            for flight_point_idx, flight_point in enumerate(self.flight_points):
                # If any of these flight points report being off the ground, set _has_been_airborne to True.
                if not flight_point.is_on_ground:
                    self._has_been_airborne = True
                # Attempt to locate the previous point, current point and next point.
                previous_point, current_point, next_point = self.calculate_surrounding_points(flight_point_idx)
                if not previous_point:
                    # If our previous point is None, this is a start point. So instantiate a flight point start descriptor, for the first point.
                    # Also if next point is not None, instantiate a flight point change descriptor between the current point and next point.
                    start_descriptor = self._start_descriptor = FlightPointStartDescriptor(current_point)
                    timeline_list.append(start_descriptor)
                    timeline_list.append(current_point)
                    if next_point:
                        change_descriptor = FlightPointChangeDescriptor(current_point, next_point)
                        timeline_list.append(change_descriptor)
                elif current_point and next_point:
                    # If our current point is not None, and our next point is not None, we will now create a FlightPointChangeDescriptor from current
                    # to next point, then add the point, then the change descriptor.
                    change_descriptor = FlightPointChangeDescriptor(current_point, next_point)
                    timeline_list.append(current_point)
                    timeline_list.append(change_descriptor)
                elif current_point and not next_point:
                    # If we have our current point, but our next point is None, this is our final point. We will create a FlightPointEndDescriptor for
                    # the current point, then add the point, then the descriptor.
                    end_descriptor = self._end_descriptor = FlightPointEndDescriptor(current_point)
                    timeline_list.append(current_point)
                    timeline_list.append(end_descriptor)
            # Now, we've constructed our timeline, tuple-ise it and set to instance attribute.
            self._timeline = tuple(timeline_list)
        except Exception as e:
            raise e

    def _enumerate_associated_flights(self):
        """
        Iterate all flight points given as a part of this flight assimilator, if any have their flight attribute not-none, this flight is saved to the
        instance for further use as potentially the dominant flight.

        Raises
        ------
        MultiplePotentialFlightsFoundError: Multiple flights have been found among the flight points. This is indicative of a mismatch in deterministic logic.
        """
        try:
            associated_flights = []
            for flight_point in self.flight_points:
                if flight_point.flight and not flight_point.flight in associated_flights:
                    associated_flights.append(flight_point.flight)
            # If more than 1 associated flight, raise an error.
            if len(associated_flights) > 1:
                LOG.error(f"Failed to assimilate {self}, multiple associated flights ({len(associated_flights)}) have been discovered. The maximum is 1.")
                raise error.MultiplePotentialFlightsFoundError(self)
            elif not len(associated_flights):
                # No potential associated flights found. Dominant flight is None.
                self._dominant_flight = None
            else:
                # Dominant flight is the first entry.
                self._dominant_flight = associated_flights[0]
        except Exception as e:
            raise e

    def _calculate_flight_statistics(self):
        """
        Perform all required calculations for this flight.
        This data will, upon flight assimilation/creation, be set in the resulting Flight model.
        """
        try:
            # Calculate total distance travelled.
            try:
                self._distance_travelled = calculations.total_distance_travelled_from(self)
            except error.InvalidCRSError as ice:
                # No CRS detected among any flight point. Check the number of positional flight points, this could be the reason for failure.
                if self.num_positional_flight_points > 0:
                    # There are multiple positional flight points. It makes no sense as to why this error occurred, we can now raise another InvalidCRSError.
                    LOG.error(f"Failed to calculate distance travelled for {self} as a result of a lack of CRS. We will re-raise this error.")
                    raise ice
                # Otherwise, no issues; the integrity of all flight points in this flight so far are insufficient.
                LOG.warning(f"Skipped calculating distance travelled for {self}, as the integrity of these flight points are not sufficient; the aircraft's position is not being located.")
                # Flag this for further work at a later date.
                """ TODO """
                traces.handle_flight_point_integrity()
                self._distance_travelled = None
            except error.NoFlightPathError as nfpe:
                # All we can do is wait.
                LOG.warning(f"Could not get flight path from {self}, this manager does not yet have at least two points.")
                self._distance_travelled = None
            # Next, get total flight time.
            self._flight_time_total = calculations.total_flight_time_from(self)
            # Next, get total flight time in prohibited hours. We'll do this by deriving a manager with only points within our prohibited hours, then getting total flight time for that.
            timezone_gmt10 = timezone(timedelta(hours = 10))
            # Now, derive the manager with just our prohibited points.
            prohibited_points_manager = self.derive_manager(
                within_hours_range = (
                    time(hour = 20, minute = 0, second = 0, tzinfo = timezone_gmt10),
                    time(hour = 7, minute = 0, second = 0, tzinfo = timezone_gmt10),
                )
            )
            # Locate airports.
            try:
                self._locate_airports()
            except error.FlightPointsIntegrityError as fpie:
                """ TODO: """
                traces.handle_flight_point_integrity()
            except Exception as e:
                LOG.warning(e, exc_info = True)
            # Use this to get our flight time prohibited.
            self._flight_time_prohibited = calculations.total_flight_time_from(prohibited_points_manager)
            # Calculate average speed.
            self._average_speed = calculations.average_speed_from(self)
            # Calculate average altitude.
            self._average_altitude = calculations.average_altitude_from(self)
            # Calculate total estimated fuel used.
            try:
                self._fuel_used = calculations.estimate_total_fuel_used_by(self.aircraft, self)
            except error.MissingFuelFiguresError as mffe:
                # This aircraft does not have fuel figures set. We will report this.
                traces.handle_missing_fuel_figures(mffe)
                self._fuel_used = None
            # Does our first partial begin with a takeoff? If so, we have departure details.
            self._has_departure_details = self.first_partial_flight.started_with_takeoff
            # Does our last partial end with a landing? If so, we have departure details.
            self._has_arrival_details = self.last_partial_flight.ended_with_landing
            # Now, attempt to calculate co2 emissions data.
            self._calculate_emissions_statistics()
        except Exception as e:
            raise e

    def _locate_airports(self):
        """
        This function will execute some complex logic to attempt the location of both the takeoff airport and the landing airport. They each have some
        separate requirements for their respective determination. Before any time consuming logic is executed, the dominant flight will be checked for
        existence, and existing values used if they're present. Otherwise, if individual values do not exist, they will be separately produced. If the
        Flight does not exist at all, this logic will be executed anyway.

        This function will raise FlightDataRevisionRequired exceptions in the case that it is unable to locate a partial with a takeoff or landing in
        backwards and forwards direction respectively. This will automatically invoke handle_flight_data_revision.
        """
        try:
            LOG.debug(f"Attempting to locate airports for {self} ...")
            # We can fail straight away if there are no positional flight points associated with this manager.
            if self.num_positional_flight_points == 0:
                LOG.warning(f"Skipped locating airports for {self}, there are no positional flight points (yet?)")
                raise error.FlightPointsIntegrityError("locate-airports", "no-positional-flight-points", flight_points = self.flight_points)
            # Do we have a Flight?
            if self._dominant_flight:
                # Nice! Now, check and retrieve each airport, if necessary, separately.
                # Take off airport.
                if self._dominant_flight.takeoff_airport:
                    # Flight already has a takeoff airport.
                    LOG.debug(f"Flight {self._dominant_flight} already has a takeoff airport ({self._dominant_flight.takeoff_airport}). Skipping calculations...")
                    self._takeoff_airport = self._dominant_flight.takeoff_airport
                # Landing airport.
                # We'll recalculate the landing airport EVERY update.
                '''if self._dominant_flight.landing_airport:
                    # Flight already has a landing airport.
                    LOG.debug(f"Flight {self._dominant_flight} already has a landing airport ({self._dominant_flight.landing_airport}). Skipping calculations...")
                    self._landing_airport = self._dominant_flight.landing_airport'''
            # Perform a None-check on each instance takeoff and landing airports. If None, we must recalculate.
            if not self._takeoff_airport:
                LOG.debug(f"We must determine take off airport for {self}")
                try:
                    # If our very first partial does not begin with a takeoff, we will not continue logic - as we need more information.
                    if not self.first_partial_flight.started_with_takeoff:
                        LOG.error(f"Could not determine takeoff airport for {self}, this flight assimilator was not given the partial flight that contains its takeoff!")
                        raise error.FlightDataRevisionRequired(self.aircraft, self.first_point.day_day-timedelta(days = 1), history_verified = False)
                    else:
                        # Otherwise, we will use the calculations module to ascertain the appropriate airport, given the first point in this assimilator.
                        # If there is no first positional flight point, raise FlightPointPositionIntegrityError.
                        if not self.positional_first_point:
                            raise error.FlightPointPositionIntegrityError(self.aircraft, None, "find-takeoff-airport", "No positional first point!")
                        #self._takeoff_airport = calculations.find_airport_for(self.aircraft, self.positional_first_point.position)
                        self._takeoff_airport = calculations.find_airport_via_epsg_for(self.aircraft, self.positional_first_point)
                        LOG.debug(f"Identified take off airport for {self} to be {self._takeoff_airport}")
                except error.FlightPointPositionIntegrityError as fppie:
                    LOG.warning(f"Could not locate TAKEOFF airport for flight point {self.first_point} from {self.aircraft}, this position is None!")
                    """
                    TODO: flag this data for interpolation and recomprehension.
                    """
                    traces.handle_flight_point_integrity()
                    #raise error.FlightPointIntegrityError(self.first_point, "locate-takeoff-airport", "Position is None!")
                except error.NoAirportFound as naf:
                    # No airport found, simply set takeoff airport to None for now.
                    self._takeoff_airport = None
                except error.NoAirportsLoaded as nal:
                    raise nal
                except error.FlightDataRevisionRequired as fdrr:
                    traces.handle_flight_data_revision(fdrr)
                except Exception as e:
                    LOG.error(e, exc_info = True)
                    self._takeoff_airport = None
            if self._has_been_airborne:
                LOG.debug(f"We must determine landing airport for {self}")
                try:
                    # If our last partial does not end with a landing, we will not continue logic - as we need more information.
                    if not self.last_partial_flight.ended_with_landing:
                        LOG.error(f"Could not determine landing airport for {self}, this flight assimilator was not given the partial flight that contains its landing!")
                        raise error.FlightDataRevisionRequired(self.aircraft, self.last_point.day_day+timedelta(days = 1), history_verified = False)
                    else:
                        # Otherwise, we will use the calculations module to ascertain the appropriate airport, given the last point in this assimilator.
                        # If there is no first positional flight point, raise FlightPointPositionIntegrityError.
                        if not self.positional_last_point:
                            raise error.FlightPointPositionIntegrityError(self.aircraft, None, "find-landing-airport", "No positional last point!")
                        #self._landing_airport = calculations.find_airport_for(self.aircraft, self.positional_last_point.position)
                        self._landing_airport = calculations.find_airport_via_epsg_for(self.aircraft, self.positional_last_point)
                        LOG.debug(f"Identified landing airport for {self} to be {self._landing_airport}")
                except error.FlightPointPositionIntegrityError as fppie:
                    LOG.warning(f"Could not locate TAKEOFF airport for flight point {self.last_point} from {self.aircraft}, this position is None!")
                    """
                    TODO: flag this data for interpolation and recomprehension.
                    """
                    traces.handle_flight_point_integrity()
                    #raise error.FlightPointIntegrityError(self.last_point, "locate-takeoff-airport", "Position is None!")
                except error.NoAirportsLoaded as nal:
                    raise nal
                except error.NoAirportFound as naf:
                    # No airport found, simply set landing airport to None for now.
                    self._landing_airport = None
                except error.FlightDataRevisionRequired as fdrr:
                    traces.handle_flight_data_revision(fdrr)
                except Exception as e:
                    LOG.error(e, exc_info = True)
                    self._landing_airport = None
            elif not self._has_been_airborne:
                LOG.warning(f"Did not attempt to locate landing airport for {self}, as this flight has not yet been airborne, instead, this assimilator will be logged as 'still taxiing'.")
                self._landing_airport = None
                self._taxi_only = True
        except error.NoAirportsLoaded as nal:
            LOG.error(f"Failed to determine either takeoff or landing airport for {self}, there are no airports in the database!")
        except Exception as e:
            raise e

    def _determine_realtime_statistics(self):
        """
        Determine all realtime statistics; this is usually based on the very latest point, and how that point relates to other
        points in the flight so far, or how that point relates to time/environment etc.
        """
        try:
            # Get most recent point.
            most_recent_point = self.most_recent_point
            # Only continue with a valid point.
            if most_recent_point:
                LOG.debug(f"Updating realtime statistics for {self}")
                # Is in ground is simply equal to whether the end descriptor reports us as on the ground.
                self._is_on_ground = self._end_descriptor.is_on_ground
            else:
                LOG.warning(f"Did not update realtime statistics for {self}, most recent point is None!")
        except Exception as e:
            raise e

    def _calculate_emissions_statistics(self):
        """
        Perform calculations on the data already given to determine some co2 data.
        """
        try:
            # We will calculate all co2 related information. For this, we require the following data points to be given and valid:
            # distance travelled, average speed, fuel used and finally, valid fuel data on the given aircraft. If any of these are not satisfied, do not continue.
            if not self._distance_travelled or not self._average_speed or not self._fuel_used or not self.aircraft.has_valid_fuel_data:
                LOG.warning(f"Skipped calculating CO2 emissions for {self}, one or more required data points is not valid or set.")
                return
            # Otherwise, we need to convert the given values into the appropriate format.
            # The first, distance travelled, we required the number of kilometers.
            distance_travelled = self._distance_travelled / 1000
            # The second, average speed, we require from knots to km/h.
            average_speed = self._average_speed * 1.852
            # Finally, we require the amount of fuel used in tonnes, not gallons.
            fuel_used = self._fuel_used * 0.031491395793499
            # We are ready to get our co2 in kg per hour (in total.)
            co2_emission_total_per_hour = calculations.calculate_co2_emissions_per_hour(distance_travelled, average_speed, self.aircraft.fuel_passenger_load, fuel_used, self.aircraft.fuel_co2_per_gram)
            # Now, get the number of hours this flight flew for.
            num_hours_flown = self._flight_time_total / 60
            # This is the total amount of co2 emitted by this vehicle.
            self._total_co2_emissions = num_hours_flown * co2_emission_total_per_hour
        except Exception as e:
            raise e

    @classmethod
    def from_args(cls, aircraft, partial_flights, **kwargs):
        """
        Class level function to instantiate a new assimilator for the given flight fragments and execute all required functions upon it.

        Arguments
        ---------
        :aircraft: The aircraft to attribute the Flight to.
        :partial_flights: A list of PartialFlight objects, all belonging to the same Flight.

        Returns
        -------
        Instantiated and setup FlightAssimilator for the Flight.
        """
        assimilator = FlightAssimilator(aircraft, partial_flights)
        assimilator._extract_flight_points()
        assimilator._make_timeline()
        assimilator._enumerate_associated_flights()
        assimilator._calculate_flight_statistics()
        assimilator._determine_realtime_statistics()
        return assimilator

    @classmethod
    def from_partial_flight(cls, aircraft, partial_flight, **kwargs):
        """
        Class level function to instantiate a new assimilator from a single partial flight. The partial flight's history
        and future will be collected, concatenated and the procedure via from_args will then be followed, rendering an
        identical result.

        Arguments
        ---------
        :aircraft: The aircraft to attribute the Flight to.
        :partial_flight: An instance of PartialFlight, the past and future of which will be collected.

        Returns
        -------
        Instantiated and setup FlightAssimilator for the Flight.
        """
        # First, attempt to collect both the past and future for this partial flight.
        backwards_until_takeoff = partial_flight.collect_partials_until_takeoff()
        forwards_until_landing = partial_flight.collect_partials_until_landing()
        # Concatenate into a single list of partials.
        partial_flights = backwards_until_takeoff+[partial_flight]+forwards_until_landing
        # We'll now call from args, resuming ordinary procedure.
        return FlightAssimilator.from_args(aircraft, partial_flights, **kwargs)


class PartialFlight(FlightPointsManager):
    """
    A data model representing a partial flight; also known as a leg. This is essentially a full flight, UNLESS the flight runs into another day.
    This should only be used by a DailyFlightsView, and needs to be instantiated with a tuple type list that contains a mixture of descriptors &
    flight points describing this potential partial flight.
    """
    @property
    def is_complete_flight(self):
        """
        Returns a boolean indicating whether this is a complete flight; that is, it begins with a takeoff, ends with a landing and both past and future
        have complete flight data.
        """
        return self.started_with_takeoff and self.ended_with_landing and not self.incomplete_past and not self.incomplete_future

    @property
    def incomplete_past(self):
        """
        Returns a boolean indicating whether or not this partial flight forms only part of a flight; in a backwards direction.
        This is determined by checking whether the first point began with a takeoff.
        """
        return not self.started_with_takeoff

    @property
    def incomplete_future(self):
        """
        Returns a boolean indicating whether or not this partial flight forms only part of a flight in a forwards direction.
        This is determined by checking whether the last point ended with a landing.
        """
        return not self.ended_with_landing

    @property
    def started_with_takeoff(self):
        """Returns a boolean indicating whether or not this partial flight has been determined as beginning in a take off."""
        if self._start_descriptor.is_on_ground:
            return True
        elif (self._start_descriptor.altitude or 0) < config.MAX_ALTITUDE_MID_AIR_DISAPPEAR_START_NEW_FLIGHT:
            return True
        elif self._started_with_takeoff_override:
            return True
        return False

    @property
    def ended_with_landing(self):
        """Returns a boolean indicating whether or not this partial flight has been determined as ending in a landing."""
        if self._end_descriptor.is_on_ground:
            return True
        elif (self._end_descriptor.altitude or 0) < config.MAX_ALTITUDE_MID_AIR_DISAPPEAR_END_FLIGHT:
            return True
        elif self._ended_with_landing_override:
            return True
        return False

    @property
    def starts_at(self):
        return self._start_descriptor.timestamp

    @property
    def ends_at(self):
        return self._end_descriptor.timestamp

    @property
    def num_flight_points(self):
        return len(self.flight_points)

    def __str__(self):
        if not self._start_descriptor or not self._end_descriptor:
            return f"PartialFlight<***UNDER CONSTRUCTION***>"
        return f"PartialFlight<start={self._start_descriptor.time_iso},takeoff={self.started_with_takeoff}->end={self._end_descriptor.time_iso},landed={self.ended_with_landing}>"

    def __init__(self, _aircraft, _day, _timeline_subsection, **kwargs):
        """
        Instantiate this partial flight view, given a timeline subsection. This subsection is a tuple-like list that contains both FlightPoints and
        possibly descriptors.

        Keyword arguments
        -----------------
        :change_descriptor: An instance of FlightPointChangeDescriptor that caused the creation of this partial flight this can be None.
        """
        self.change_descriptor = kwargs.get("change_descriptor", None)

        self.aircraft = _aircraft
        self.day = _day
        self._timeline_subsection = _timeline_subsection

        self._started_with_takeoff_override = False
        self._ended_with_landing_override = False
        self._start_descriptor = None
        self._end_descriptor = None

        self._extract_flight_points()
        # We will extract the very first and very last items from this subsection; remember these can be either FlightPoints or descriptors.
        self._start_descriptor = _timeline_subsection[0]
        self._end_descriptor = _timeline_subsection[len(_timeline_subsection)-1]
        # Ensure types of start and end are correct.
        assert isinstance(self._start_descriptor, FlightPointStartDescriptor)
        assert isinstance(self._end_descriptor, FlightPointEndDescriptor)

    def _extract_flight_points(self):
        # Filter our inner timeline to return just flight points.
        flight_points_it = filter(lambda timeline_item: isinstance(timeline_item, models.FlightPoint), self._timeline_subsection)
        # Now, return this list sorted by the timestamps ascending.
        self.set_flight_points(list(sorted(flight_points_it, key = lambda flight_point: flight_point.timestamp)))

    def collect_partials_until_takeoff(self, **kwargs):
        """
        Locate all partials up until and including the partial that features this partial flight's takeoff. If this is a complete flight already, or the partial's past is complete,
        this will simply return an empty list. If at any time, an error with previous day flight data is detected the iteration will be halted and that particular aircraft/day queued
        for revision.

        The function will still return the list of located partials up until that point, unless handle revision is set to False. Please note, irrespective of how the function goes, the
        return value, being a list of PartialFlight, could potentially be unordered. Please order prior to utilising results!

        Keyword arguments
        -----------------
        :should_handle_revision_req: A boolean indicating whether flight data revision requests should be handled in house. If this is False, the exception will be raised. Default True.

        Raises
        ------
        FlightDataRevisionRequired: There is missing flight data.

        Returns
        -------
        A list of PartialFlight, potentially unordered.
        """
        should_handle_revision_req = kwargs.get("should_handle_revision_req", True)

        LOG.debug(f"Collecting all partials from {self} until it takes off.")
        if self.is_complete_flight or not self.incomplete_past:
            LOG.debug(f"Skipped collecting all partials from {self} until it takes off; it is either a complete flight, or already has a complete past.")
            return []
        past_partials = []
        try:
            # Otherwise, iterate aircraft/days in a backwards fashion, creating a daily flights view from this aircraft day.
            for aircraft_day in iter(AircraftDayIterator(self.aircraft, self.day, AircraftDayIterator.BACKWARD)):
                daily_flights_view = DailyFlightsView.from_args(self.aircraft, aircraft_day.day_day, aircraft_day.all_flight_points)
                # Is the last partial flight None? Not enough info yet.
                previous_partial_flight = daily_flights_view.last_partial_flight
                if not previous_partial_flight:
                    # No flight data on previous day. Therefore, we'll raise a FlightDataRevisionRequired.
                    LOG.warning(f"Couldn't collect partials from {self} until it takes off; we require some flight data on PREVIOUS day; {daily_flights_view.day}!")
                    raise error.FlightDataRevisionRequired(daily_flights_view.aircraft, daily_flights_view.day)
                elif not previous_partial_flight.incomplete_future:
                    # Previous partial flight data is obviously corrupt; this partial flight requires a take off, but previous partial flight does not have an incomplete future. We'll request flight data revision.
                    LOG.warning(f"Couldn't collect partials from {self} until it takes off; there is flight data on PREVIOUS day; {daily_flights_view.day}, but it does not require a future!")
                    raise error.FlightDataRevisionRequired(daily_flights_view.aircraft, daily_flights_view.day)
                else:
                    """TODO: determine join suitability. I won't bother with this for now as I just want to press ahead."""
                    # We must now determine join suitability in a BACKWARD direction between the current partial and that denoted by previous_partial_flight.
                    # Construct a flight point change descriptor between this partial's first point and the previous partial's last point. If the descriptor constitutes the change as a new flight.
                    # it is a new flight.
                    flight_point_change_descriptor = FlightPointChangeDescriptor(previous_partial_flight.last_point, self.first_point)
                    try:
                        constitutes_new_flight = flight_point_change_descriptor.constitutes_new_flight
                    except error.FlightChangeInaccuracySolvencyRequired as fcisr:
                        """TODO: flight inaccuracy solvency requested. Execute this here, the return value of which will be a deeply investigated 'constitutes_new_flight'"""
                        # Call out for heavy investigation into this anomaly.
                        constitutes_new_flight, solution = inaccuracy.smart_constitutes_new_flight(self.aircraft, self.day, flight_point_change_descriptor)
                    if constitutes_new_flight:
                        # We've determined these two cross-day partials do not join at all. Therefore, we will set an override for takeoff that will let this partial know it actually does start with a takeoff.
                        LOG.debug(f"Determined {self} is NOT backward continued by ANY other partial (though we have past partials,) as the change between this and the previous partial CONSTITUTES A NEW FLIGHT! Started with takeoff override SET.")
                        self._started_with_takeoff_override = True
                        break
                    else:
                        LOG.debug(f"Determined {previous_partial_flight} is a BACKWARDS continuation from {self}")
                        # There is previous flight data that requires future. Append this to past partials.
                        past_partials.append(previous_partial_flight)
                # Now, if previous partial flight does NOT take off on this day, we will continue our loop, otherwise, break our loop.
                if not previous_partial_flight.started_with_takeoff:
                    LOG.debug(f"Continuation {previous_partial_flight} does not start with a take off, continuing our loop to the previous day.")
                    continue
                else:
                    LOG.debug(f"Flight partial {self} has been determined to take off within previous partial {previous_partial_flight}, whilst traversing over a further {len(past_partials)-1} partials.")
                    break
        except error.FlightDataRevisionRequired as fdrr:
            if should_handle_revision_req:
                # If we have been told to handle this, employ trace module to report the request.
                traces.handle_flight_data_revision(fdrr)
            else:
                # Otherwise, raise.
                raise fdrr
        # Return past partials, potentially unordered.
        return past_partials

    def collect_partials_until_landing(self, **kwargs):
        """
        Locate all partials up until and including the partial that features this partial flight's landing. If this is a complete flight already, or this partial's future is complete,
        this will simply return an empty list. If at any time, an error with next day flight data is detected the iteration will be halted and that particular aircraft/day queued for
        revision.

        The function will still return the list of located partials up until that point, unless handle revision is set to False. Please note, irrespective of how the function goes, the
        return value, being a list of PartialFlight, could potentially be unordered. Please order prior to utilising results!

        Keyword arguments
        -----------------
        :should_handle_revision_req: A boolean indicating whether flight data revision requests should be handled in house. If this is False, the exception will be raised. Default True.

        Returns
        -------
        A list of PartialFlight, potentially unordered.
        """
        should_handle_revision_req = kwargs.get("should_handle_revision_req", True)

        LOG.debug(f"Collecting all partials from {self} until it lands.")
        if self.is_complete_flight or not self.incomplete_future:
            LOG.debug(f"Skipped collecting all partials from {self} until it lands; it is either a complete flight, or already has a complete future.")
            return []
        future_partials = []
        try:
            # Otherwise, iterate aircraft/days in a forwards fashion, creating a daily flights view from this aircraft day.
            for aircraft_day in iter(AircraftDayIterator(self.aircraft, self.day, AircraftDayIterator.FORWARD)):
                daily_flights_view = DailyFlightsView.from_args(self.aircraft, aircraft_day.day_day, aircraft_day.all_flight_points)
                # Is the first partial flight None? Not enough info yet.
                next_partial_flight = daily_flights_view.first_partial_flight
                if not next_partial_flight:
                    # No flight data on next day. Therefore, we'll raise a FlightDataRevisionRequired.
                    LOG.warning(f"Couldn't collect partials from {self} until it lands; we require some flight data on NEXT day; {daily_flights_view.day}!")
                    raise error.FlightDataRevisionRequired(daily_flights_view.aircraft, daily_flights_view.day)
                elif not next_partial_flight.incomplete_past:
                    # Next partial flight data is obviously corrupt; this partial flight requires a landing, but next partial flight does not have an incomplete past. We'll request flight data revision.
                    LOG.warning(f"Couldn't collect partials from {self} until it lands; there is flight data on NEXT day; {daily_flights_view.day}, but it does not require a past!")
                    raise error.FlightDataRevisionRequired(daily_flights_view.aircraft, daily_flights_view.day)
                else:
                    # We must now determine join suitability in a FORWARD direction between the current partial and that denoted by next_partial_flight.
                    # Construct a flight point change descriptor between this partial's last point and the next partial's first point. If the descriptor constitutes the change as a new flight.
                    # it is a new flight.
                    flight_point_change_descriptor = FlightPointChangeDescriptor(self.last_point, next_partial_flight.first_point)
                    try:
                        constitutes_new_flight = flight_point_change_descriptor.constitutes_new_flight
                    except error.FlightChangeInaccuracySolvencyRequired as fcisr:
                        """TODO: flight inaccuracy solvency requested. Execute this here, the return value of which will be a deeply investigated 'constitutes_new_flight'"""
                        # Call out for heavy investigation into this anomaly.
                        constitutes_new_flight, solution = inaccuracy.smart_constitutes_new_flight(self.aircraft, self.day, flight_point_change_descriptor)
                    if constitutes_new_flight:
                        # We've determined these two cross-day partials do not join at all. Therefore, we will set an override for landing that will let this partial know it actually does end in a landing.
                        LOG.debug(f"Determined {self} is NOT forward continued by ANY other partial (though we have future partials,) as the change between this and the next partial CONSTITUTES A NEW FLIGHT! Ended with landing override SET.")
                        self._ended_with_landing_override = True
                        break
                    else:
                        LOG.debug(f"Determined {next_partial_flight} is a FORWARDS continuation from {self}")
                        # There is next flight data that requires past. Append this to future partials.
                        future_partials.append(next_partial_flight)
                # Now, if next partial flight does NOT land on this day, we will continue our loop, otherwise, break our loop.
                if not next_partial_flight.ended_with_landing:
                    LOG.debug(f"Continuation {next_partial_flight} does not end with a landing, continuing our loop to the next day.")
                    continue
                else:
                    LOG.debug(f"Flight partial {self} has been determined to land within future partial {next_partial_flight}, whilst traversing over a further {len(future_partials)-1} partials.")
                    break
        except error.FlightDataRevisionRequired as fdrr:
            if should_handle_revision_req:
                # If we have been told to handle this, employ trace module to report the request.
                traces.handle_flight_data_revision(fdrr)
            else:
                # Otherwise, raise.
                raise fdrr
        # Return future partials, potentially unordered.
        return future_partials


class DailyFlightsView(FlightPointsManager):
    """
    A class responsible for accepting an aircraft's behaviour bound to a single day, and from that information, extracting subsections that represent
    flights as per our current configuration. It is important that all flight points provided to the constructor of this class is recorded as having
    been created on the same day. This is the calling code's responsibility.
    """
    @property
    def first_partial_flight(self):
        if not len(self.partial_flights):
            return None
        return self.partial_flights[0]

    @property
    def last_partial_flight(self):
        if not len(self.partial_flights):
            return None
        return self.partial_flights[len(self.partial_flights)-1]

    @property
    def timeline(self):
        return self._timeline

    @property
    def partial_flights(self):
        return self._partial_flights

    @property
    def num_partial_flights(self):
        return len(self.partial_flights)

    @property
    def aircraft_id(self):
        return f"{self.aircraft.flight_name} ({self.aircraft.icao})"

    @property
    def day_iso(self):
        return self.day.isoformat()

    def __init__(self, _aircraft, _day, _flight_points, **kwargs):
        """
        Instantiate this factory with the given aircraft, day and flight points.

        Arguments
        ---------
        :_aircraft: An instance of Aircraft.
        :_day: A Date instance.
        :_flight_points: A list of all flight points by this aircraft, on this day.
        """
        self.aircraft = _aircraft
        self.day = _day
        # Sort all flight points in ascending order by their timestamps. This should have been done prior, but this is also a failsafe.
        self.set_flight_points(sorted(_flight_points, key = lambda flight_point: flight_point.timestamp))

        # All flight points in this factory instance, but converted to a timeline state.
        self._timeline = ()
        # A tuple container for all constructed partial flights; these should inherently be in chronological order.
        self._partial_flights = ()

    def make_partial_flights(self, **kwargs):
        """
        Using our timeline we've just generated, create PartialFlight instances and add them to our partial flights tuple attribute. The primary dividing mark for flights
        is whether or not the change descriptor attribute, constitutes_new_flight, returns True or not. FlightPointStartDescriptors will automatically be added to the current
        partial flight. FlightPointChangeDescriptors will only be added to the current partial flight if they do not constitute a new flight. FlightPointEndDescriptors will
        always be added to the current partial flight.
        """
        # List to hold all partial flight points in creation.
        partial_flights_list = []
        # Now a list to hold all contents of the current partial flight being constructed.
        current_partial_flight = []
        # Begin our iteration.
        for timeline_item in self._timeline:
            if isinstance(timeline_item, FlightPointStartDescriptor) or isinstance(timeline_item, FlightPointEndDescriptor):
                # We encounter either of these, append to partial flight and continue.
                current_partial_flight.append(timeline_item)
                continue
            elif isinstance(timeline_item, FlightPointChangeDescriptor):
                try:
                    constitutes_new_flight = timeline_item.constitutes_new_flight
                except error.FlightChangeInaccuracySolvencyRequired as fcisr:
                    """TODO: flight inaccuracy solvency requested. Execute this here, the return value of which will be a deeply investigated 'constitutes_new_flight'"""
                    # Call out for heavy investigation into this anomaly.
                    constitutes_new_flight, solution = inaccuracy.smart_constitutes_new_flight(self.aircraft, self.day, timeline_item)
                if constitutes_new_flight:
                    # This change descriptor constitutes a new flight. We can safely skip this descriptor, and instead, create a partial flight out of what we have so far.
                    LOG.debug(f"Detected a new flight for {self.aircraft_id} on {self.day_iso}, last flight ended at {timeline_item.point1_time_iso}, this flight commencing specifically at {timeline_item.point2_time_iso}!")
                    # If current partial flight has 0 items, do not make it.
                    if not len(current_partial_flight):
                        LOG.warning(f"Skipped making a partial flight from partial flight fragments, no fragments given!")
                        current_partial_flight = []
                    try:
                        partial_flight = self.construct_partial_flight_from(current_partial_flight, timeline_item)
                        partial_flights_list.append(partial_flight)
                    except error.InsufficientPartialFlightError as ipfe:
                        LOG.warning(f"A partial flight {current_partial_flight} does not reach the minimum partial flight criteria! Skipping ...")
                        continue
                    current_partial_flight = []
                    # Continue to ignore the descriptor.
                    continue
            # Otherwise, simply append the timeline item to the partial flight.
            current_partial_flight.append(timeline_item)
        # If there are items in our current partial flight list, execute new flight.
        if len(current_partial_flight):
            try:
                partial_flight = self.construct_partial_flight_from(current_partial_flight)
                partial_flights_list.append(partial_flight)
            except error.InsufficientPartialFlightError as ipfe:
                LOG.warning(f"A partial flight {current_partial_flight} does not reach the minimum partial flight criteria! Skipping ...")
                pass
            current_partial_flight = []
        else:
            LOG.warning(f"Skipped making a partial flight from partial flight fragments, no fragments given!")
        # We have extracted partial flights from this day.
        LOG.debug(f"Succesfully extracted {len(partial_flights_list)} partial flights for {self.aircraft_id} on {self.day_iso}!")
        for partial_flight in partial_flights_list:
            LOG.debug(f"\t{partial_flight}")
        # Tuple-ise the list.
        self._partial_flights = tuple(partial_flights_list)

    def make_timeline(self, **kwargs):
        """
        Using the list of flight points within this object instance, the attribute 'timeline' will be created and populated. This is a tuple-type attribute
        where values are an alternating instance of FlightPointChangeDescriptor and FlightPoint, on the order of; (fpcd, fp, fpcd, fp, fpcd...) Each flight
        point change descriptor describes how the aircraft state or environmental state has changed between the two flight points on either side. For instance,
        FPCD1 may calculate the time change difference between FP2 and FP1.

        The first flight point in flight_points could either be; an honest beginning of a new flight on a new day, or it may be a continuation of a flight that
        spans one or more days. As such, the element preceding the first flight point in the timeline will be a FlightPointStartDescriptor that, will calculate
        data points necessary to LATER ON, when supplied with a potential FlightPointEndDescriptor, guess whether this is a new flight, or a flight continuation.

        The last flight point in flight_points could either be; an honest end to the last flight for the day, or could be a continuation into a flight stretching
        into the NEXT day. As such, the element proceeding the last flight point in the timeline will be a FlightPointEndDescriptor that, will calculate data points
        necessary to LATER ON, be supplied to a FlightPointStartDescriptor in order to better guess whether this is a continuation or not.
        """
        try:
            # If we have 0 items, then just set timeline tuple to an empty one.
            if not len(self.flight_points):
                LOG.debug(f"Skipped (re)building timeline tuple for {self.aircraft_id} on {self.day_iso} - there are no flight points.")
                self._timeline = ()
                return
            # We'll first create the timeline with a list, then we'll convert that to the resulting tuple.
            timeline_list = []
            LOG.debug(f"(Re)building timeline tuple for {self.aircraft_id} on {self.day_iso}, using {len(self.flight_points)} flight points.")
            # Now, iterate all other flight points (except the final one,) and create a flight point change descriptor for each.
            for flight_point_idx, flight_point in enumerate(self.flight_points):
                # Attempt to locate the previous point, current point and next point.
                previous_point, current_point, next_point = self.calculate_surrounding_points(flight_point_idx)
                if not previous_point:
                    # If our previous point is None, this is a start point. So instantiate a flight point start descriptor, for the first point.
                    # Also if next point is not None, instantiate a flight point change descriptor between the current point and next point.
                    start_descriptor = FlightPointStartDescriptor(current_point)
                    timeline_list.append(start_descriptor)
                    timeline_list.append(current_point)
                    if next_point:
                        change_descriptor = FlightPointChangeDescriptor(current_point, next_point)
                        timeline_list.append(change_descriptor)
                elif current_point and next_point:
                    # If our current point is not None, and our next point is not None, we will now create a FlightPointChangeDescriptor from current
                    # to next point, then add the point, then the change descriptor.
                    change_descriptor = FlightPointChangeDescriptor(current_point, next_point)
                    timeline_list.append(current_point)
                    timeline_list.append(change_descriptor)
                elif current_point and not next_point:
                    # If we have our current point, but our next point is None, this is our final point. We will create a FlightPointEndDescriptor for
                    # the current point, then add the point, then the descriptor.
                    end_descriptor = FlightPointEndDescriptor(current_point)
                    timeline_list.append(current_point)
                    timeline_list.append(end_descriptor)
            # Now, we've constructed our timeline, tuple-ise it and set to instance attribute.
            self._timeline = tuple(timeline_list)
        except Exception as e:
            raise e

    def construct_partial_flight_from(self, partial_flight_fragments, change_descriptor = None):
        """
        Given a list of partial flight fragments, that is, a combination of FlightPoints and descriptors, construct a partial flight data model expressing
        the flight. Optionally, a change descriptor, which should be the FlightPointChangeDescriptor between the end flight point and the start flight point
        can be provided.

        Arguments
        ---------
        :partial_flight_fragments: A list of a combination of FlightPoint and descriptors.
        :change_descriptor: Optionally, the FlightPointChangeDescriptor between the last flight point and the first in this flight.

        Returns
        -------
        A PartialFlight model.
        """
        try:
            # If partial flight fragments has less than MINIMUM_FRAGMENTS_FOR_PARTIAL, raise an InsufficientPartialFlightError.
            if len(partial_flight_fragments) < config.MINIMUM_FRAGMENTS_FOR_PARTIAL:
                LOG.warning(f"Ignoring creation of partial flight from a list of fragments {len(partial_flight_fragments)} long. This is not sufficient.")
                raise error.InsufficientPartialFlightError(partial_flight_fragments)
            first_item_idx = 0
            # We'll construct a new flight point start descriptor for the first timeline item, unless the first timeline item is already one.
            if not isinstance(partial_flight_fragments[first_item_idx], FlightPointStartDescriptor):
                start_descriptor = FlightPointStartDescriptor(partial_flight_fragments[first_item_idx])
                partial_flight_fragments.insert(first_item_idx, start_descriptor)
            last_item_idx = len(partial_flight_fragments)-1
            # Also construct a flight point end descriptor for the last timeline item, unless the last timeline item is already one.
            if not isinstance(partial_flight_fragments[last_item_idx], FlightPointEndDescriptor):
                partial_flight_fragments.append(FlightPointEndDescriptor(partial_flight_fragments[last_item_idx]))
            # Construct a partial flight tuple and return it.
            return PartialFlight(self.aircraft, self.day, tuple(partial_flight_fragments), change_descriptor = change_descriptor)
        except Exception as e:
            raise e

    def attempt_find_suitable_partial_for(self, flight_points, **kwargs):
        """
        A functional wrapper for locate_partial_with that, in the case an existing partial on this day can't be found, a new partial flight
        is returned instead of None. This will always be done in a None case.

        Arguments
        ---------
        :flight_points: A list of flight points to find a suitable partial flight for.

        Returns
        -------
        A PartialFlight or None, if the day needs to be processed completely.
        """
        try:
            # First order of business, attempt to locate a directly preceding partial flight.
            directly_preceding_partial = self.locate_partial_with(flight_points)
            # If not none, apply logic to verify this can be attached to the preceding partial.
            if directly_preceding_partial:
                # If we found a directly preceding partial, we should instantiate a flight change descriptor between its last point, and our
                # flight point's first point. We will then use flight determination logic in that descriptor to determine new flight.
                change_descriptor = FlightPointChangeDescriptor(directly_preceding_partial.last_point, flight_points[0])
                # Now, if this DOES NOT constitute a new flight, we will return the preceding partial.
                try:
                    constitutes_new_flight = change_descriptor.constitutes_new_flight
                except error.FlightChangeInaccuracySolvencyRequired as fcisr:
                    """TODO: flight inaccuracy solvency requested. Execute this here, the return value of which will be a deeply investigated 'constitutes_new_flight'"""
                    # Call out for heavy investigation into this anomaly.
                    constitutes_new_flight, solution = inaccuracy.smart_constitutes_new_flight(self.aircraft, self.day, change_descriptor)
                if not constitutes_new_flight:
                    LOG.debug(f"Found suitable predecessor partial; {directly_preceding_partial} for sequence beginning at {flight_points[0].datetime_iso} - not a new flight!")
                    return directly_preceding_partial
            LOG.debug(f"Couldn't find suitable predecessor partial for sequence beginning with {flight_points[0].datetime_iso}, parsing as a new sequence!")
            # There is no preceding partial for the given sequence. This may be a brand new submission for the day. If this is the case, fall back to executing like revise_flight_data_for.
            return None
        except Exception as e:
            raise e

    def locate_partial_with(self, flight_points, **kwargs):
        """
        Given a list of flight points, locate the partial that contains this sequence.
        This works by looking for the partial in which the first flight point comes AFTER the partial's first point timestamp and BEFORE the next
        partial's timestamp, if applicable. Otherwise, if next partial not applicable, that partial is selected.

        This function does not ensure flight_points has any entries. Please ensure this is done prior.

        Arguments
        ---------
        :flight_points: A list of flight points to locate a partial for.

        Returns
        -------
        A PartialFlight, or None.
        """
        try:
            LOG.debug(f"Locating partial flight of best fit for flight points array beginning at {flight_points[0].datetime_iso} and ending at {flight_points[len(flight_points)-1].datetime_iso}")
            for partial_idx, partial_flight in enumerate(self.partial_flights):
                # Get the first flight point.
                first_point = flight_points[0]
                # Get the last flight point.
                last_point = flight_points[len(flight_points)-1]
                # Attempt to get the next partial.
                next_partial = None if partial_idx+1 >= self.num_partial_flights else self.partial_flights[partial_idx+1]

                # Calculate some early statistics.
                # If first point in the sequence comes AFTER the current partials start, starts_after_current_partial is True.
                starts_after_current_partial = True if first_point.timestamp >= partial_flight.starts_at else False
                # If no next partial OR we have a next partial and our question sequence ends BEFORE that partial's start, ends_before_next_partial is True.
                ends_before_next_partial = True if not next_partial or next_partial and last_point.timestamp < next_partial.starts_at else False
                # And so, if the question sequence start comes after the current partials start, and theres either no next partial, or the question sequence ends before
                # the next partial starts, the most suitable flight is the current partial flight!
                if starts_after_current_partial and ends_before_next_partial:
                    LOG.debug(f"Located partial {partial_flight} directly preceding sequence beginning at {first_point.datetime_iso}")
                    return partial_flight
            # Otherwise, return none.
            first_point = flight_points[0]
            LOG.warning(f"Failed to locate partial directly preceding sequence beginning at {first_point.datetime_iso}")
            return None
        except Exception as e:
            raise e

    @classmethod
    def from_args(cls, _aircraft, _day, _flight_points, **kwargs):
        """Factory method that will instantiate and assemble a daily flights view."""
        daily_flights_view = DailyFlightsView(_aircraft, _day, _flight_points, **kwargs)
        daily_flights_view.make_timeline()
        daily_flights_view.make_partial_flights()
        return daily_flights_view

    @classmethod
    def from_aircraft_present_day(cls, aircraft_present_day, **kwargs):
        """Factory method that will use an instance of AircraftPresentDay in the further creation of a daily flights view."""
        return DailyFlightsView.from_args(
            aircraft_present_day.aircraft,
            aircraft_present_day.day_day,
            aircraft_present_day.all_flight_points,
            **kwargs)
