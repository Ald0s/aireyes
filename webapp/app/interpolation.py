"""
A module for handling flight point position interpolation and general data correction.
"""
import re
import os
import time
import uuid
import asyncio
import decimal
import hashlib
import logging
import json
import geojson
import geopandas
import pyproj
from shapely import strtree, geometry, wkb
from fastkml import kml
from datetime import datetime, date, timedelta

from typing import List

from sqlalchemy.exc import OperationalError, UnsupportedCompilationError
from .compat import insert

from sqlalchemy import func, and_, or_, asc, desc
from sqlalchemy.exc import IntegrityError
from marshmallow import Schema, fields, EXCLUDE, post_load

from . import db, config, models, error

LOG = logging.getLogger("aireyes.interpolation")
LOG.setLevel( logging.DEBUG )


class FlightPointInterpolator():
    """
    A class for accepting a list of flight points, and interpolating a position for those without a position.
    At least the first flight point in the list should be totally valid (with position,) and the remainder being either positional duplicates, or non-positional flight points.
    """
    def __init__(self, _aircraft, _flight_points):
        self._aircraft = _aircraft
        self._flight_points = tuple(_flight_points)
        # First, we'll prepare these flight points for interpolation.
        self._prepare_flight_points()

    def _prepare_flight_points(self):
        # If there are no flight points given, raise a NoFlightPointsError.
        if len(self._flight_points) == 0:
            LOG.error(f"Failed to prepare flight points for interpolation; there are no flight points provided.")
            raise error.NoFlightPointsError(_aircraft, "No flight points given.")
        # A dictionary for tracking duplicates.
        duplicates_report = dict(
            aircraft_icao = self._aircraft.icao,
            duplicates = dict()
        )
        LOG.debug(f"Preparing flight points for interpolation. First step is to remove all duplicate positions, and replace them with None.")
        previous_position = None
        num_duplicates = 0
        for flight_point in self._flight_points:
            # If flight point does not have a position, continue.
            if not flight_point.is_position_valid:
                continue
            elif not previous_position:
                # If flight point has a position, but the previous saved position is None, use that as previous and continue.
                previous_position = flight_point.position
                continue
            # Otherwise, we have a previous position and current flight point is not None. We'll test if the current flight point's position is IDENTICAL to the previous position.
            if flight_point.position == previous_position:
                # These positions are the same. This probably means current flight point position is a duplicate.
                # Increment duplicates.
                num_duplicates+=1
                # Log this, and set it to None.
                LOG.warning(f"Set position for flight point has {flight_point.flight_point_hash} belonging to aircraft {self._aircraft} to None! It is a duplicate! ({previous_position} duplicated {duplicates} times.)")
                flight_point.clear_position()
            else:
                # Log this in report.
                duplicates_report["duplicates"][previous_position] = num_duplicates
                # Otherwise, positions are different, change previous to match current.
                previous_position = flight_point.position
                # Also set number of duplicates counted to 0.
                num_duplicates = 0


def find_duplicate_positions(aircraft, day, **kwargs):
    """

    """
    try:
        pass
    except Exception as e:
        raise e


def correct_duplicate_positions(aircraft, day, **kwargs):
    """
    Given a specific aircraft and day combination, this function will locate all flight points that have geometries, but whose geometries are
    duplicates of one another, despite the aircraft being moving, or the positions being identical to the Nth decimal point. These flight points
    will have their positions nulled out.

    Arguments
    ---------
    :aircraft: An instance of Aircraft.
    :day: A Date instance.

    Returns
    -------
    All FlightPoints modified.
    """
    try:
        pass
    except Exception as e:
        raise e


def interpolate_aircraft_day():
    pass
