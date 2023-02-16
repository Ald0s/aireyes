"""
A module for heavily investigating flight data inaccuracies.
Examples of this can be where aircraft suddenly disappear, then reappear some time and distance later.
"""
import re
import os
import uuid
import logging
import time as time_
import json
from datetime import datetime, date, timedelta, time, timezone

from sqlalchemy import func, and_, or_, desc, asc
from sqlalchemy.exc import IntegrityError
from marshmallow import Schema, fields, EXCLUDE, post_load

from .compat import insert

from . import db, config, models, error, calculations

LOG = logging.getLogger("aireyes.inaccuracy")
LOG.setLevel( logging.DEBUG )


class FlightInaccuracySolution():
    """
    A data model for containing the solution for a flight inaccuracy, the reason for the determined inaccuracy and the action taken by the
    system to rectify the inaccuracy in ongoing data. It's expected that these data models are transformed into database models and stored
    alongside the Flight by the assimilator.
    """
    def __init__(self, _new_flight, _reason_code, _reason_args = {}):
        self.constitutes_new_flight = _new_flight
        self.reason_code = _reason_code
        self.reason_args = _reason_args


def smart_constitutes_new_flight(aircraft, day, change_descriptor, **kwargs):
    """
    Apply in depth investigative logic to determine whether the given change descriptor constitutes a new flight. This function is called under anomalous conditions. Such as
    the time difference between two points being extreme, but both points are (significantly) airborne at the time. By default, if no new flight can be detected, an anomalous
    flight point change will result in a continuation for the Flight by returning False.

    Arguments
    ---------
    :aircraft:
    :day:
    :change_descriptor:

    Returns
    -------
    A tuple of two items;
        A boolean; whether this constitutes a new flight or not.
        An instance of FlightInaccuracySolution.
    """
    try:
        # We will first determine whether we'll bother with this. If solvency disabled, simply return False alongside a solution reporting why.
        if not config.INACCURACY_SOLVENCY_ENABLED:
            LOG.warning(f"Flight data inaccuracy solvency is DISABLED, so no investigative action was taken for {change_descriptor} by {aircraft} on {day}.")
            return False, FlightInaccuracySolution(False, "inaccuracy-solvency-disabled")
        # Otherwise, begin applying our special case conditionals here.
        if not change_descriptor.point1_grounded and not change_descriptor.point2_grounded \
            and change_descriptor.time_difference_seconds > config.TIME_DIFFERENCE_NEW_FLIGHT_MID_AIR_START_AND_END:
            # New flight detected; sort of a debug catch-all. Criteria; If neither point ends on the ground, but time difference is SIGNIFICANT, just return True since we shouldn't be tracking
            # an aircraft with that kind of range anyway.
            return True, FlightInaccuracySolution(True, "catch-all")
        return False, FlightInaccuracySolution(False, "not-new-flight")
    except Exception as e:
        raise e


def attempt_flight_point_correction(aircraft, flight_point, **kwargs):
    """
    Given an instance of Aircraft, and an instance of FlightPoint, run correction logic on the incoming flight point. This will look for inaccurate,
    corrupt or otherwise impossible anomalies in the flight point and correct them prior to the flight point being persisted.

    Arguments
    ---------
    :aircraft: An instance of Aircraft.
    :flight_point: An instance of FlightPoint.

    Returns
    -------
    The FlightPoint.
    """
    try:
        # If the aircraft has a top speed, ensure the ground speed in the flight point does not exceed it.
        if aircraft.top_speed != None and flight_point.ground_speed != None and flight_point.ground_speed > aircraft.top_speed:
            LOG.warning(f"Flight point {flight_point} has a ground speed greater than its aircraft's ({aircraft}) top speed, setting it to None! ({flight_point.ground_speed} > {aircraft.top_speed})")
            flight_point.ground_speed = None
        return flight_point
    except Exception as e:
        raise e
