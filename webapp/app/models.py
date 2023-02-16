import re
import os
import time
import uuid
import random
import logging
import pytz
import string
import json
import binascii
import hashlib
from datetime import datetime, date, timedelta, timezone

import pyproj
from shapely import geometry, wkb, ops

from flask_login import AnonymousUserMixin, UserMixin, current_user
from flask import request, g
from sqlalchemy import asc, desc, or_, and_, func, select, case
from sqlalchemy.orm import relationship, aliased, with_polymorphic, declared_attr
from sqlalchemy.sql.expression import cast
from sqlalchemy.types import TypeDecorator, BINARY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.hybrid import hybrid_property, hybrid_method
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.dialects.postgresql import UUID
from sqlite3 import IntegrityError as SQLLite3IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from marshmallow import Schema, fields, EXCLUDE, post_load

from . import db, config, login_manager, error

# Conditional legacy from when PostGIS was the addon. Can be removed.
if config.POSTGIS_ENABLED:
    from geoalchemy2 import Geometry, shape

LOG = logging.getLogger("aireyes.models")
LOG.setLevel( logging.DEBUG )


class UTMEPSG(db.Model):
    """Table for a UTM EPSG. Keep tracking of objects placed in these small EPSGs works quite well for triangulation."""
    __tablename__ = "utm_epsg"

    epsg                    = db.Column(db.Integer, primary_key = True)

    # Suburbs that have at least one point within this EPSG.
    suburb_epsgs            = db.relationship("SuburbUTMEPSG", back_populates = "epsg", uselist = True, cascade = "all, delete")
    # Airports whose radius intersects this EPSG.
    airport_epsgs           = db.relationship("AirportUTMEPSG", back_populates = "epsg", uselist = True, cascade = "all, delete")

    def __repr__(self):
        return f"UTMEPSG<{self.epsg}>"


class EPSGWrapperMixin():
    """Mixin for enabling geodetic transformation on objects."""
    @property
    def crs_object(self):
        if not self.crs:
            return None
        return pyproj.crs.CRS.from_user_input(self.crs)

    @property
    def geodetic_transformer(self):
        return pyproj.Transformer.from_crs(self.crs_object, self.crs_object.geodetic_crs, always_xy = True)

    @declared_attr
    def crs(cls):
        return db.Column(db.Integer, nullable = True, default = None)

    def set_crs(self, crs):
        self.crs = crs


class PointGeometryMixin(EPSGWrapperMixin):
    """Enables all subclasses to own a Point; perhaps as a center for example."""
    @property
    def is_position_valid(self):
        return self.point_geom != None

    @declared_attr
    def point_geom(self):
        """Represents a column for a geometry of type Point that defines the center/position of this object."""
        if config.POSTGIS_ENABLED:
            return db.Column(Geometry("POINT", srid = config.COORDINATE_REF_SYS, management = config.POSTGIS_MANAGEMENT))
        else:
            return db.Column(db.LargeBinary(length=(2**24)-1), default = None, nullable = True)

    @property
    def point(self) -> geometry.Point:
        """Return a XY format Point for this object's longitude & latitude."""
        if not self.point_geom:
            return None
        if config.POSTGIS_ENABLED:
            return shape.to_shape(self.point_geom)
        else:
            return wkb.loads(self.point_geom)

    @point.setter
    def point(self, value):
        if not value:
            self.point_geom = None
        else:
            if config.POSTGIS_ENABLED:
                if not self.crs:
                    raise Exception("No CRS set! We can't set this point geom.")
                self.point_geom = shape.from_shape(value, srid = self.crs)
            else:
                self.point_geom = value.wkb

    @property
    def position(self):
        return self.point

    @property
    def geodetic_point(self):
        if not self.point_geom:
            return None
        return ops.transform(self.geodetic_transformer.transform, self.point)

    def set_position(self, point):
        if not self.crs:
            raise AttributeError(f"Could not set position for {self}, this object does not have a CRS set!")
        if isinstance(point, tuple):
            point = geometry.Point(point)
        self.point = point

    def clear_position(self):
        """This clears both the Point geometry and CRS currently set."""
        self.point = None
        self.crs = None


class PolygonGeometryMixin(EPSGWrapperMixin):
    """Enables all subclasses to own an arbitrary Polygon geometry, such as a Suburb."""
    @declared_attr
    def polygon_geom(self):
        """Represents a column for a geometry of type Polygon"""
        if config.POSTGIS_ENABLED:
            return db.Column(Geometry("POLYGON", srid = config.COORDINATE_REF_SYS, management = config.POSTGIS_MANAGEMENT))
        else:
            return db.Column(db.LargeBinary(length=(2**24)-1), default = None, nullable = True)

    @property
    def polygon(self) -> geometry.Polygon:
        if not self.polygon_geom:
            return None
        if config.POSTGIS_ENABLED:
            return shape.to_shape(self.polygon_geom)
        else:
            return wkb.loads(self.polygon_geom)

    @polygon.setter
    def polygon(self, value):
        if not value:
            self.polygon_geom = None
        else:
            if config.POSTGIS_ENABLED:
                if not self.crs:
                    raise Exception("No CRS set! We can't set this polygon geom.")
                self.polygon_geom = shape.from_shape(value, srid = self.crs)
            else:
                self.polygon_geom = value.wkb

    @property
    def geodetic_polygon(self):
        return ops.transform(self.geodetic_transformer.transform, self.polygon)

    def set_geometry(self, polygon):
        if not self.crs:
            raise AttributeError(f"Could not set geometry for {self}, this object does not have a CRS set!")
        self.polygon = polygon


class MultiPolygonGeometryMixin(EPSGWrapperMixin):
    """Enables subclasses to own a multipolygon geometry, such as a State or a Suburb that is for some reason two separate regions."""
    @declared_attr
    def multi_polygon_geom(self):
        """Represents a column for a geometry of type MultiPolygon"""
        if config.POSTGIS_ENABLED:
            return db.Column(Geometry("MULTIPOLYGON", srid = config.COORDINATE_REF_SYS, management = config.POSTGIS_MANAGEMENT))
        else:
            return db.Column(db.LargeBinary(length=(2**24)-1), default = None, nullable = True)

    @property
    def multi_polygon(self) -> geometry.MultiPolygon:
        if not self.multi_polygon_geom:
            return None
        if config.POSTGIS_ENABLED:
            return shape.to_shape(self.multi_polygon_geom)
        else:
            return wkb.loads(self.multi_polygon_geom)

    @multi_polygon.setter
    def multi_polygon(self, value):
        if not value:
            self.multi_polygon_geom = None
        else:
            if config.POSTGIS_ENABLED:
                if not self.crs:
                    raise Exception("No CRS set! We can't set this multipolygon geom.")
                self.multi_polygon_geom = shape.from_shape(value, srid = self.crs)
            else:
                self.multi_polygon_geom = value.wkb

    @property
    def geodetic_multi_polygon(self):
        return ops.transform(self.geodetic_transformer.transform, self.multi_polygon)

    def set_geometry(self, multi_polygon):
        if not self.crs:
            raise AttributeError(f"Could not set geometry for {self}, this object does not have a CRS set!")
        self.multi_polygon = multi_polygon


class Flight(db.Model):
    """
    A flight represents a single journey by an aircraft; that is, the aircraft must take off once and land once; except in the case of taxi-only flights (if tracked.)
    The flight will maintain static products of statistical calculations, that will need to be re-executed each time a flight point owned by the Flight is
    changed/modified/added/removed. A Flight connects to its flight points directly, by a one-to-many relationship. A flight may have many Days involved, however;
    this is strictly via the flight points attached to the flight, not directly.

    Almost all concrete/property attributes are None-able. This is so that we can have a reliable indicator of whether a flight, or specific attributes for that
    flight, are suitable for inclusion in calculations and statistical displays. None is a more indicative value than, say, returning 0.
    """
    __tablename__ = "flight"

    flight_id               = db.Column(db.Integer, primary_key = True)
    aircraft_icao           = db.Column(db.String(12), db.ForeignKey("aircraft.icao"))
    # Two foreign keys to the Airport table, denoting the take off airport and the landing airport.
    takeoff_airport_hash    = db.Column(db.String(32), db.ForeignKey("airport.airport_hash"))
    landing_airport_hash    = db.Column(db.String(32), db.ForeignKey("airport.airport_hash"))

    # This flight's hash.
    flight_hash             = db.Column(db.String(32), unique = True, nullable = False)

    ### Realtime statistics for this Flight. These have no meaning if the Flight is not ongoing. ###
    # Is the aircraft on the ground currently? Property is_airborne uses the inverse of this value.
    is_on_ground_           = db.Column(db.Boolean, default = None, nullable = True)

    ### Statistics for this Flight. ###
    # Distance this aircraft has travelled over this flight. In meters.
    distance_travelled      = db.Column(db.Integer, default = None, nullable = True)
    # The amount of fuel used by this flight, in gallons. This can be null if the system does not hold a reference for determining estimated fuel for this specific aircraft.
    fuel_used               = db.Column(db.Integer, default = None, nullable = True)
    # The average ground speed of this aircraft, in knots.
    average_speed           = db.Column(db.Integer, default = None, nullable = True)
    # The average altitude, in feet.
    average_altitude        = db.Column(db.Integer, default = None, nullable = True)
    # Total flight time, in minutes.
    flight_time_total       = db.Column(db.Integer, default = None, nullable = True)
    # The average time, in minutes, spent flying within prohibited times (8pm-7am.)
    flight_time_prohibited  = db.Column(db.Integer, default = None, nullable = True)
    # The amount of co2 emitted by this flight in total (including all passengers), in kilograms.
    total_co2_emissions     = db.Column(db.Integer, default = None, nullable = True)

    # Set to True if the very start of the flight data currently held by this Flight has been determined, against our configuration, to constitute an on-the-ground start.
    has_departure_details   = db.Column(db.Boolean, default = False)
    # Set to True if the current end of the flight data held by this Flight has been determined, against our configuration, to constitute an on-the-ground end.
    has_arrival_details     = db.Column(db.Boolean, default = False)
    # Set to True if the aircraft never left the ground (or hasn't, if this is ongoing.)
    taxi_only               = db.Column(db.Boolean, default = False)

    # Take off airport. Can be None if past data does not exist yet.
    takeoff_airport         = db.relationship(
        "Airport",
        uselist = False,
        foreign_keys = [ takeoff_airport_hash ])
    # Land airport. Can be None if past data does not exist yet.
    landing_airport         = db.relationship(
        "Airport",
        uselist = False,
        foreign_keys = [ landing_airport_hash ])

    # The aircraft that performed this flight.
    aircraft                = db.relationship(
        "Aircraft",
        back_populates = "flights_",
        uselist = False)
    # All flight points associated with this flight. These points can stretch across multiple days. A flight point can only belong to a SINGLE flight.
    # This is a dynamic query, offering us ample opportunity to customise how we query from the list.
    flight_points_          = db.relationship(
        "FlightPoint",
        back_populates = "flight",
        uselist = True,
        lazy = "dynamic")

    def __repr__(self):
        if not self.first_point or not self.last_point:
            return f"Flight<{self.aircraft}, ***NEW FLIGHT***>"
        return f"Flight<{self.aircraft},start={self.first_point.datetime_iso},end={self.last_point.datetime_iso}>"

    @property
    def is_ongoing(self):
        """Determine if this Flight is ongoing."""
        """TODO 0x15"""
        return False

    @property
    def flight_number(self):
        """
        Return an integer to represent this Flight's position within the aircraft's flights.
        This is determined by querying for the count of flights belonging to the aircraft with a start before or equal to this flight's start. This function will never
        return 0, and instead will min that to 1. We should probably improve this for efficiency at some stage.
        """
        flight1 = aliased(Flight)
        flight2 = aliased(Flight)
        calculate_flight_no_sq = db.session.query(func.count(flight2.flight_id))\
            .filter(flight2.flight_id <= self.flight_id)\
            .filter(flight2.aircraft_icao == self.aircraft_icao)\
            .scalar_subquery()
        return db.session.query(calculate_flight_no_sq)\
            .filter(flight1.aircraft_icao == self.aircraft_icao)\
            .group_by(flight1.aircraft_icao)\
            .scalar()

    @property
    def flight_name(self):
        """Returns the flight number alongside the aircraft's name."""
        return f"{self.aircraft.flight_name} Flight #{self.flight_number}"

    @property
    def formatted_flight_time(self):
        """Returns a time delta between the very first point, and the very last point pretty-formatted on the order of; Xh Xm"""
        """TODO 0x16"""
        if not self.flight_time_total:
            return "n/a"
        # Divide our total flight time by 60, and round, to get hours.
        hours = round(self.flight_time_total/60)
        # Now modulo total flight time by 60 and use the remainder as minutes.
        minutes = round(self.flight_time_total%60)
        # Construct a string representing this.
        result = ""
        if hours:
            result += f"{hours}<span class='ampm-font'>h</span> "
        if minutes:
            result += f"{minutes}<span class='ampm-font'>m</span>"
        return result

    @hybrid_property
    def distance_travelled_kilometers(self):
        """Returns distance travelled as kilometers rounded to whole number but, if None, 0."""
        if not self.distance_travelled:
            return 0
        return round(self.distance_travelled/1000)

    @distance_travelled_kilometers.expression
    def distance_travelled_kilometers(cls):
        """Returns expression level query for distance travelled by this flight, in kilometers. If None, 0."""
        return case([
            (func.coalesce(cls.distance_travelled, 0) > 0, func.round(cls.distance_travelled/1000))
        ], else_ = 0)

    @property
    def is_on_ground(self):
        return self.is_on_ground_

    @property
    def is_airborne(self):
        return not self.is_on_ground

    @is_on_ground.setter
    def is_on_ground(self, value):
        self.is_on_ground_ = value

    @property
    def flight_points(self):
        """Return the default query for flight points, each flight point is ordered by timestamp ascending."""
        return self.flight_points_\
            .order_by(asc(FlightPoint.timestamp))

    @property
    def all_flight_points(self):
        """Return all flight points as instances."""
        return self.flight_points.all()

    @property
    def num_flight_points(self):
        return db.session.query(func.count(FlightPoint.flight_point_id))\
            .filter(FlightPoint.flight_id == self.flight_id)\
            .scalar()

    @property
    def days(self):
        """Returns a dynamic query for all days on on which this flight has presence."""
        return db.session.query(Day)\
            .join(FlightPoint, FlightPoint.day_day == Day.day)\
            .filter(FlightPoint.flight_id == self.flight_id)\
            .group_by(Day.day)\
            .order_by(asc(Day.day))

    @property
    def num_days_across(self):
        """Returns the number of days this Flight spans."""
        return self.days.count()

    @property
    def first_point(self):
        return self.flight_points.first()

    @property
    def last_point(self):
        return self.flight_points_\
            .order_by(desc(FlightPoint.timestamp))\
            .first()

    @property
    def starts_at_friendly_datetime(self):
        """Returns a friendly datetime for the starts_at timestamp, if available, in the local timezone. If not available, None is returned."""
        """TODO 0x16"""
        if not self.has_departure_details:
            return None
        # TODO 0x06:
        utc_dt = datetime.utcfromtimestamp(int(self.starts_at)).replace(tzinfo = pytz.utc)
        au_tz = pytz.timezone(config.TIMEZONE)
        aware_datetime = au_tz.normalize(utc_dt.astimezone(au_tz))
        am_pm = aware_datetime.strftime("<span class='ampm-font'>%p</span>").lower()
        return aware_datetime.strftime(f"%a, %d %B %Y at %I:%M{am_pm}")

    @hybrid_property
    def starts_at(self):
        """Return the first point's timestamp."""
        return db.session.query(Flight.starts_at)\
            .filter(FlightPoint.flight_id == self.flight_id)\
            .scalar()

    @starts_at.expression
    def starts_at(cls):
        return func.min(FlightPoint.timestamp)

    @property
    def ends_at_friendly_datetime(self):
        """Returns a friendly datetime for the ends_at timestamp, if available, in the local timezone. If not available, None is returned."""
        """TODO 0x16"""
        if not self.has_arrival_details:
            return None
        # TODO 0x06:
        utc_dt = datetime.utcfromtimestamp(int(self.ends_at)).replace(tzinfo = pytz.utc)
        au_tz = pytz.timezone(config.TIMEZONE)
        aware_datetime = au_tz.normalize(utc_dt.astimezone(au_tz))
        am_pm = aware_datetime.strftime("<span class='ampm-font'>%p</span>").lower()
        return aware_datetime.strftime(f"%a, %d %B %Y at %I:%M{am_pm}")

    @hybrid_property
    def ends_at(self):
        """Return the last point's timestamp."""
        return db.session.query(Flight.ends_at)\
            .filter(FlightPoint.flight_id == self.flight_id)\
            .scalar()

    @ends_at.expression
    def ends_at(cls):
        return func.max(FlightPoint.timestamp)

    def set_flight_points(self, flight_points):
        """Abstracts the logic behind the setting of points for this Flight."""
        LOG.debug(f"Setting flight points for {self} to list containing {len(flight_points)} points.")
        for x in flight_points:
            self.flight_points_.append(x)

    @classmethod
    def get_by_hash(cls, flight_hash, **kwargs):
        """Given a flight hash, attempt to find a Flight record."""
        return db.session.query(models.Flight)\
            .filter(models.Flight.flight_id == flight_id)\
            .first()


class WorkerLockAircraftDay(db.Model):
    """
    A junction model associating a particular aircraft-day junction with a radar worker. The purpose of this is to reserve a particular
    subsection of work as being handled by a radar worker. This will stop this aircraft-day combination from being reassigned to another
    worker whilst its being handled. These worker-aircraft-day associations MUST be cleared whenever the radar worker disconnects, whether
    or not the job was complete.
    """
    __tablename__ = "worker_lock_aircraft_day"

    # Cascade on the radar worker's side, since this association must be destroyed when the radar is destroyed.
    radar_worker_name       = db.Column(db.String(32), db.ForeignKey("radar_worker.name"), primary_key = True)
    # Composite foreign key to AircraftPresentDay.
    aircraft_day_day        = db.Column(db.Date, nullable = False, primary_key = True)
    aircraft_icao           = db.Column(db.String(12), nullable = False, primary_key = True)
    # Junction attributes.
    # A timestamp for when this lock was placed.
    lock_set_on             = db.Column(db.BigInteger, default = time.time)

    __table_args__ = (
        db.ForeignKeyConstraint(
            ["aircraft_day_day", "aircraft_icao"],
            ["aircraft_present_day.day_day", "aircraft_present_day.aircraft_icao"]
        ),
    )


class AircraftPresentDay(db.Model):
    """
    The junction model for associating an aircraft and a day in the context of presence. The third attribute, history_verified, is used to
    differentiate between a partial detection of presence and a full detection of presence. When history_verified is False, this means that
    the report of presence was made by an active 'aircraft-tracker' worker. Essentially, a 'history-trawler' will still be assigned this day
    as a job to reach a verified conclusion. This is True by default.

    The flights verified attribute indicates whether or not the aircraft, on this day, has had its trace data mapped to 'partial flight'
    clusters. Background workers will be responsible for over time, trawling these records and processing the flight data found associated.
    Also, should further changes be made for this aircraft, on this day, this will be set to False so it can once again invoke a recalculation
    of the day's partial flights. This is False by default.

    Geolocation verified is set to True only when all flight points occurring on this day, by this aircraft, has been successfully geolocated, or
    the geolocation has been unsuccessful.
    """
    __tablename__ = "aircraft_present_day"

    day_day                 = db.Column(db.Date, db.ForeignKey("day.day"), primary_key = True)
    aircraft_icao           = db.Column(db.String(12), db.ForeignKey("aircraft.icao"), primary_key = True)

    # A boolean; whether the trace history has been verified.
    history_verified        = db.Column(db.Boolean, default = True)
    # A boolean; whether the flights data has been verified.
    flights_verified        = db.Column(db.Boolean, default = False)
    # A boolean; whether the flight point information has been geolocated for this day.
    geolocation_verified    = db.Column(db.Boolean, default = False)

    # If any, the radar worker assigned to handle this aircraft-day combination.
    assigned_worker         = db.relationship("RadarWorker", back_populates = "aircraft_day_work", uselist = False, secondary = "worker_lock_aircraft_day")

    def __repr__(self):
        return f"AircraftDay<{self.aircraft_icao}, {self.day_day}>"

    @property
    def aircraft(self):
        """Since we are avoiding using relationships for this, we will instead use a property."""
        return db.session.query(Aircraft)\
            .filter(Aircraft.icao == self.aircraft_icao)\
            .first()

    @property
    def all_flight_points(self):
        return self.get_flight_points()

    def get_flight_points(self):
        return db.session.query(FlightPoint)\
            .filter(FlightPoint.aircraft_icao == self.aircraft_icao)\
            .filter(FlightPoint.day_day == self.day_day)\
            .order_by(asc(FlightPoint.timestamp))\
            .all()

    @classmethod
    def find(cls, aircraft_icao, day, **kwargs):
        """
        Locate an association between the given aircraft icao and day, optionally filter by junction attributes.

        Keyword arguments
        -----------------
        :history_verified: If provided, filter by this attribute.
        :flights_verified: If provided, filter by this attribute.
        :geolocation_verified: If provided, filter by this attribute.
        """
        history_verified = kwargs.get("history_verified", None)
        flights_verified = kwargs.get("flights_verified", None)
        geolocation_verified = kwargs.get("geolocation_verified", None)

        base_query = db.session.query(AircraftPresentDay)\
            .filter(and_(AircraftPresentDay.aircraft_icao == aircraft_icao, AircraftPresentDay.day_day == day))
        if history_verified:
            base_query = base_query\
                .filter(AircraftPresentDay.history_verified == history_verified)
        if flights_verified:
            base_query = base_query\
                .filter(AircraftPresentDay.flights_verified == flights_verified)
        if geolocation_verified:
            base_query = base_query\
                .filter(AircraftPresentDay.geolocation_verified == geolocation_verified)
        return base_query.first()


class Day(db.Model):
    """
    The purpose of a day is to ensure that we have enumerated ALL possible days for a specific Aircraft's presence. In this way, if the server does not have proof
    of a particular target aircraft's presence on a particular day, it can perform a request via a worker. The idea of this system is to eventually enumerate all
    days for all aircraft starting from a single day.
    """
    __tablename__ = "day"

    day                     = db.Column(db.Date, primary_key = True)

    # A dynamic one-to-many query for all flight points that occurred on this day.
    flight_points_          = db.relationship(
        "FlightPoint",
        back_populates = "day",
        uselist = True,
        lazy = "dynamic")
    # A dynamic many-to-many query for all aircraft that have been reported as present on this day.
    active_aircraft         = db.relationship(
        "Aircraft",
        back_populates = "days_active",
        secondary = "aircraft_present_day",
        uselist = True,
        lazy = "dynamic")

    def __repr__(self):
        return f"Day<{self.day.isoformat()}>"

    @property
    def num_flight_points(self):
        """Returns the total number of flight points logged on this Day."""
        return db.session.query(func.count(FlightPoint.flight_point_id))\
            .filter(FlightPoint.day_day == self.day)\
            .scalar()

    @property
    def flights(self):
        """Returns a dynamic query for all Flights with presence on this Day."""
        return db.session.query(Flight)\
            .join(FlightPoint, FlightPoint.flight_id == Flight.flight_id)\
            .filter(FlightPoint.day_day == self.day)\
            .group_by(Flight.flight_id)

    @property
    def num_flights(self):
        """Return the number of flights featured on this Day."""
        return self.flights.count()

    @classmethod
    def get_by_date(cls, date):
        """Return a Day instance given a Date instance."""
        return db.session.query(Day)\
            .filter(Day.day == date)\
            .first()


class FlightPoint(PointGeometryMixin, db.Model):
    """
    A database model that represents a particular flight point, recorded by an aircraft on a day. The point is uniquely identified by its flight point hash,
    which is a blake2b hash of the aircraft's icao, timestamp, position and altitude.
    """
    __tablename__ = "flight_point"

    flight_point_id         = db.Column(db.Integer, primary_key = True)
    aircraft_icao           = db.Column(db.String(12), db.ForeignKey("aircraft.icao"))
    day_day                 = db.Column(db.Date, db.ForeignKey("day.day"))
    # The flight this flight point belongs to. This can be null.
    flight_id               = db.Column(db.Integer, db.ForeignKey("flight.flight_id"))
    # The suburb in which this flight point was recorded. Can be null.
    suburb_hash             = db.Column(db.String(32), db.ForeignKey("suburb.suburb_hash"))

    # The flight point hash that uniquely identifies this flight point.
    flight_point_hash       = db.Column(db.String(32), unique = True, nullable = False)

    # The UTM EPSG zone in which this flight point occurred.
    utm_epsg_zone           = db.Column(db.Integer, nullable = True)
    # When this update occurred.
    timestamp               = db.Column(db.Numeric(13, 3), nullable = False)
    ### These attributes are all nullable as sometimes data points aren't given. ###
    # Barometric altitude of the aircraft at this flight point, in feet.
    altitude                = db.Column(db.Integer, nullable = True)
    # Ground speed of the aircraft at this flight point, in knots.
    ground_speed            = db.Column(db.Numeric(5, 1), nullable = True)
    # Rotation of the aircraft at this flight point, in degrees, ground track.
    rotation                = db.Column(db.Numeric(4, 1), nullable = True)
    # Vertical rate, signed integer, in feet/minutes.
    vertical_rate           = db.Column(db.Integer, nullable = True)
    # The data source this flight point was acquired from.
    data_source             = db.Column(db.String(32), nullable = True)

    is_on_ground            = db.Column(db.Boolean, default = False)
    is_ascending            = db.Column(db.Boolean, default = False)
    is_descending           = db.Column(db.Boolean, default = False)
    # True if this flight point exists on the server. Default is True since, if we're creating this record, its already on the server.
    synchronised            = db.Column(db.Boolean, default = True)

    # The flight this flight point belongs to. This can be null.
    flight                  = db.relationship("Flight", back_populates = "flight_points_", uselist = False)
    # The day upon which this flight point was created; there may be only one per flight point.
    day                     = db.relationship("Day", back_populates = "flight_points_", uselist = False)
    # The aircraft that owns this point. Note, we've disabled save-update for this relationship.
    aircraft                = db.relationship("Aircraft", back_populates = "flight_points_", uselist = False)
    # The suburb in which this flight point occurred.
    suburb                  = db.relationship("Suburb", back_populates = "flight_points_", uselist = False)

    __table_args__ = (
        db.Index(
            "idx_icao_timestamp",
            aircraft_icao, timestamp,
            postgresql_using = "btree"),
        db.Index(
            "idx_flight_id_timestamp",
            flight_id, timestamp,
            postgresql_using = "btree"),
        db.Index(
            "idx_suburb_epsg",
            suburb_hash, utm_epsg_zone,
            postgresql_using = "btree"),
    )

    def __repr__(self):
        if not self.aircraft:
            return f"FlightPoint<***NO AIRCRAFT***,time={self.datetime_iso}>"
        return f"FlightPoint<{self.aircraft.flight_name},time={self.datetime_iso}>"

    @property
    def time_iso(self):
        """Return just the time at which this flight point was recorded."""
        # TODO 0x06:
        utc_dt = datetime.utcfromtimestamp(int(self.timestamp)).replace(tzinfo = pytz.utc)
        au_tz = pytz.timezone(config.TIMEZONE)
        aware_datetime = au_tz.normalize(utc_dt.astimezone(au_tz))
        return aware_datetime.time().isoformat()

    @property
    def datetime_iso(self):
        """Return the date/time at which this flight point was recorded."""
        # TODO 0x06:
        utc_dt = datetime.utcfromtimestamp(int(self.timestamp)).replace(tzinfo = pytz.utc)
        au_tz = pytz.timezone(config.TIMEZONE)
        aware_datetime = au_tz.normalize(utc_dt.astimezone(au_tz))
        return aware_datetime.strftime("%Y-%m-%d %H:%M:%S")

    def set_utm_epsg(self, utm_epsg):
        self.utm_epsg_zone = utm_epsg


class Aircraft(db.Model):
    """
    A database model that represents a particular Aircraft. This is essentially an identity upon which to tie flight data. This model is also
    the central target of the aireyes radar reporting system.
    """
    __tablename__ = "aircraft"

    # 24 bit address as primary key.
    icao                    = db.Column(db.String(12), primary_key = True)

    type                    = db.Column(db.String(32), nullable = False)
    flight_name             = db.Column(db.String(32), nullable = False)
    registration            = db.Column(db.String(32), nullable = False)
    owner_operator          = db.Column(db.String(128), nullable = True)
    airport_code            = db.Column(db.String(8), nullable = True)
    # The year of build for this aircraft.
    year                    = db.Column(db.Integer, nullable = True)

    ### Some info specifically about this aircraft ###
    # A description of this aircraft.
    description             = db.Column(db.String(192), nullable = True)
    # The image, relative to the static/images directory.
    image                   = db.Column(db.String(128), nullable = True)
    # The top speed of this aircraft, in knots.
    top_speed               = db.Column(db.Integer, nullable = True)

    ### Fuel consumption figures ###
    # The type of fuel this aircraft takes.
    fuel_type               = db.Column(db.String(18), nullable = True)
    # The cost of this fuel, in AUD per gallon.
    fuel_cost               = db.Column(db.Numeric(5, 2), nullable = True)
    # The fuel consumption, in gallons per hour, for this aircraft.
    fuel_consumption        = db.Column(db.Numeric(5, 2), nullable = True)
    # The fuel capacity, in pounds (lbs.)
    fuel_capacity           = db.Column(db.Integer, nullable = True)
    # The fuel range, in nautical miles.
    fuel_range              = db.Column(db.Integer, nullable = True)
    # The aircraft endurance, in minutes.
    fuel_endurance          = db.Column(db.Integer, nullable = True)
    # The number of passengers, on average, this aircraft carries.
    fuel_passenger_load     = db.Column(db.Integer, nullable = True)
    # The amount of co2 produced with 1 gram of this fuel.
    fuel_co2_per_gram       = db.Column(db.Numeric(5, 2), nullable = True)

    # A backref to the master record that owns these aircraft.
    master                  = db.relationship("Master", back_populates = "tracked_aircraft", uselist = False, secondary = "aircraft_master")
    # A query for all flights completed by this aircraft.
    flights_                = db.relationship("Flight", back_populates = "aircraft", uselist = True, lazy = "dynamic")
    # A query for all flight points associated with this aircraft, ordered by timestamp in descending order.
    flight_points_          = db.relationship("FlightPoint", back_populates = "aircraft", uselist = True, lazy = "dynamic")
    # A dynamic many-to-many query for all days on which this aircraft has been active.
    days_active             = db.relationship(
        "Day",
        back_populates = "active_aircraft",
        secondary = "aircraft_present_day",
        uselist = True,
        lazy = "dynamic")

    def __repr__(self):
        return f"Aircraft<{self.flight_name},({self.icao})>"

    @hybrid_property
    def is_enabled(self):
        """Returns True if this Aircraft will be monitored."""
        return True

    @is_enabled.expression
    def is_enabled(cls):
        return True

    @property
    def has_valid_image(self):
        return self.image != None

    @property
    def latest_flight(self):
        """Get and return the latest flight for this aircraft. None will be returned in the case there are no flights. This is measured by ordering starts_at descending."""
        latest_flight_point_sq = db.session.query(FlightPoint.flight_id)\
            .filter(FlightPoint.flight_id != None)\
            .filter(FlightPoint.aircraft_icao == self.icao)\
            .order_by(desc(FlightPoint.timestamp))\
            .group_by(FlightPoint.flight_id)\
            .limit(1)\
            .subquery()
        return db.session.query(Flight)\
            .join(latest_flight_point_sq, latest_flight_point_sq.c.flight_id == Flight.flight_id)\
            .filter(latest_flight_point_sq.c.flight_id == Flight.flight_id)\
            .first()

    @property
    def has_valid_fuel_data(self):
        """Returns true if this aircraft has a non-null value for each fuel figure."""
        return self.fuel_type != None and self.fuel_cost != None and self.fuel_consumption != None and self.fuel_capacity != None and self.fuel_range != None \
            and self.fuel_endurance != None and self.fuel_passenger_load != None and self.fuel_co2_per_gram != None

    @property
    def all_flight_points(self):
        return self.flight_points_\
            .order_by(asc(FlightPoint.timestamp))\
            .all()

    @property
    def flight_points(self):
        """A getter for ALL flight points. This will return all flight points ordered descending by timestamp."""
        return self.flight_points_\
            .order_by(asc(FlightPoint.timestamp))\
            .all()

    @flight_points.setter
    def flight_points(self, value):
        """A setter for the flight points. This will actually just append all flight points given, to the aircraft."""
        for flight_point in value:
            self.flight_points_.append(flight_point)

    @property
    def num_flight_points(self):
        return db.session.query(func.count(FlightPoint.flight_point_id))\
            .filter(FlightPoint.aircraft_icao == self.icao)\
            .scalar()

    @property
    def num_days_present(self):
        """Returns the number of days on which this aircraft has database association."""
        return self.days_active.count()

    @property
    def num_days_active(self):
        """Returns the number of days on which this aircraft has had at least one flight point recorded."""
        raise NotImplementedError()

    @property
    def flights(self):
        return self.flights_.all()

    @hybrid_property
    def num_flights(self):
        """The number of flights attached to this Aircraft."""
        return db.session.query(func.count(Flight.flight_id))\
            .filter(Flight.aircraft_icao == self.icao)\
            .scalar()

    @num_flights.expression
    def num_flights(cls):
        """Expression level return for the number of flights attached to this Aircraft."""
        return func.count(Flight.flight_id)

    @hybrid_property
    def is_active_now(self):
        """
        An instance level property for indicating whether this aircraft is currently in the air.
        If the Aircraft was last seen within MAXIMUM_SECONDS_SINCE_SEEN_ACTIVE, it is active.
        """
        if self.seconds_since_last_seen != None and self.seconds_since_last_seen < config.MAXIMUM_SECONDS_SINCE_SEEN_ACTIVE:
            return True
        return False

    @is_active_now.expression
    def is_active_now(cls):
        """Expression level property for determing whether an aircraft is currently active. This is satisfied if the seconds since last seen is within MAXIMUM_SECONDS_SINCE_SEEN_ACTIVE."""
        return func.coalesce(cls.seconds_since_last_seen, config.MAXIMUM_SECONDS_SINCE_SEEN_ACTIVE+1) < config.MAXIMUM_SECONDS_SINCE_SEEN_ACTIVE

    @hybrid_property
    def flight_time_total(self):
        """Return the total number of minutes of flight time for this Aircraft."""
        # Filter all flights where total flight time is None.
        valid_flights = list(filter(lambda flight: flight.flight_time_total != None, self.flights))
        # Now return a sum of a comprehended list of all flight times from all flights.
        return sum([ flight.flight_time_total for flight in valid_flights ])

    @flight_time_total.expression
    def flight_time_total(cls):
        """Expression level return for the total number of minutes of flight time for this Aircraft."""
        # Coalesce flight time total to 0 for summation purposes.
        return func.sum(func.coalesce(Flight.flight_time_total, 0))

    @hybrid_property
    def flight_time_prohibited(self):
        """Return the number of minutes of flight time during prohibited hours for this Aircraft."""
        # Filter all flights where flight time prohibited is None.
        valid_flights = list(filter(lambda flight: flight.flight_time_prohibited != None, self.flights))
        # Now return a sum of a comprehended list of all flight times from all flights.
        return sum([ flight.flight_time_prohibited for flight in valid_flights ])

    @flight_time_prohibited.expression
    def flight_time_prohibited(cls):
        """Expression level return for the number of minutes of flight time during prohibited hours for this Aircraft."""
        # Coalesce flight time prohibited to 0 for summation purposes.
        return func.sum(func.coalesce(Flight.flight_time_prohibited, 0))

    @hybrid_property
    def distance_travelled(self):
        """Return the total number of meters this Aircraft has travelled."""
        # Filter all flights where total distance travelled is None.
        valid_flights = list(filter(lambda flight: flight.distance_travelled != None, self.flights))
        # Now return a sum of a comprehended list of all distances from all flights.
        return sum([ flight.distance_travelled for flight in valid_flights ])

    @distance_travelled.expression
    def distance_travelled(cls):
        """Expression level return for the total number of meters this Aircraft has travelled."""
        # Coalesce distance travelled to 0 for summation purposes.
        return func.sum(func.coalesce(Flight.distance_travelled, 0))

    @hybrid_property
    def distance_travelled_kilometers(self):
        """Return the total number of kilometers this Aircraft has travelled."""
        # Filter all flights where total distance travelled (kilometers) is None.
        valid_flights = list(filter(lambda flight: flight.distance_travelled_kilometers != None, self.flights))
        # Now return a sum of a comprehended list of all distances from all flights.
        return sum([ flight.distance_travelled_kilometers for flight in valid_flights ])

    @distance_travelled_kilometers.expression
    def distance_travelled_kilometers(cls):
        """Expression level return for the total number of kilometers this Aircraft has travelled."""
        # Coalesce distance travelled to 0 for summation purposes.
        return func.sum(func.coalesce(Flight.distance_travelled_kilometers, 0))

    @hybrid_property
    def total_carbon_emissions(self):
        """Return the total number of kilograms of co2 emitted by this aircraft."""
        valid_flights = list(filter(lambda flight: flight.total_co2_emissions != None, self.flights))
        return round(sum([flight.total_co2_emissions for flight in valid_flights]))

    @total_carbon_emissions.expression
    def total_carbon_emissions(cls):
        """Expression level return for the total number of kilograms of co2 emitted by this aircraft."""
        return func.round(func.sum(func.coalesce(Flight.total_co2_emissions, 0)))

    @hybrid_property
    def total_fuel_used(self):
        """Return the total estimated amount of fuel, in gallons, used by this aircraft."""
        # Filter all flights where total fuel used is None.
        valid_flights = list(filter(lambda flight: flight.fuel_used != None, self.flights))
        # Now return a sum of a comprehended list of all fuel used from all flights.
        return sum([ flight.fuel_used for flight in valid_flights ])

    @total_fuel_used.expression
    def total_fuel_used(cls):
        """Expression level return for the total estimated amount of fuel, in gallons, used by this aircraft."""
        # Coalesce fuel used to 0 for summation purposes.
        return func.sum(func.coalesce(Flight.fuel_used, 0))

    @hybrid_property
    def last_seen_altitude(self):
        """
        DEPRECATED
        """
        """Return the very first point's altitude for this aircraft. If the aircraft is on the ground or there are no flight points, returns None."""
        if not self.num_flight_points:
            return None
        # Get the very first point, from a flight point DESCENDING perspective (latest first.)
        first_flight_point = self.flight_points_\
            .order_by(desc(FlightPoint.timestamp))\
            .first()
        # Return altitude for this timestamp.
        return first_flight_point.altitude

    @last_seen_altitude.expression
    def last_seen_altitude(cls):
        """
        DEPRECATED
        """
        """Expression level return for first point's altitude."""
        return select(FlightPoint.altitude)\
            .where(cls.icao == FlightPoint.aircraft_icao)\
            .correlate(cls)\
            .limit(1)\
            .scalar_subquery()

    @hybrid_property
    def timestamp_first_seen(self):
        """Returns the timestamp on which this aircraft was seen for the first time."""
        if not self.num_flight_points:
            return None
        # Get the Date from the first point in ASCENDING order; meaning the very first ever recorded.
        first_flight_point = self.flight_points_\
            .order_by(asc(FlightPoint.timestamp))\
            .first()
        return first_flight_point.timestamp

    @timestamp_first_seen.expression
    def timestamp_first_seen(cls):
        """Expression level return for the timestamp on which this aircraft was seen for the first time."""
        raise NotImplementedError("Expression level timestamp_first_seen is NOT yet defined.")

    @hybrid_property
    def seconds_since_last_seen(self):
        """
        Return the number of seconds since this aircraft has last been seen.
        That is, it has reported more flight points. Simply, this is the first flight point's timestamp subtracted from the current time.
        If the aircraft has never been seen before, None will be returned.
        """
        # Get the very first point, from a flight point DESCENDING perspective (latest first.)
        first_flight_point_ts = db.session.query(func.max(FlightPoint.timestamp))\
            .filter(FlightPoint.aircraft_icao == self.icao)\
            .scalar()
        if not first_flight_point_ts:
            return None
        # Return current timestamp minus last flight point's timestamp.
        return round(g.get("timestamp_now", time.time()) - float(first_flight_point_ts))

    @seconds_since_last_seen.expression
    def seconds_since_last_seen(cls):
        """Return an expression for the number of seconds since this aircraft was last seen. If the aircraft has never been seen before, return None."""
        return func.coalesce(func.round(g.get("timestamp_now", time.time())-(
            select(func.max(FlightPoint.timestamp))
            .where(FlightPoint.aircraft_icao == Aircraft.icao)
            .scalar_subquery()
        )), None)

    def update_fuel_figures(self, fuel_figures):
        """
        TODO: this should become general data updating sometime in the future; as this no longer pertains to just fuel.
        """
        if not fuel_figures:
            self.fuel_type = None
            self.fuel_cost = None
            self.fuel_consumption = None
            self.fuel_capacity = None
            self.fuel_range = None
            self.fuel_endurance = None
            self.fuel_passenger_load = None
            self.fuel_co2_per_gram = None

            self.top_speed = None
        else:
            self.fuel_type = fuel_figures.get("fuel_type")
            self.fuel_cost = fuel_figures.get("fuel_cost")
            self.fuel_consumption = fuel_figures.get("fuel_consumption")
            self.fuel_capacity = fuel_figures.get("fuel_capacity")
            self.fuel_range = fuel_figures.get("fuel_range")
            self.fuel_endurance = fuel_figures.get("fuel_endurance")
            self.fuel_passenger_load = fuel_figures.get("fuel_passenger_load")
            self.fuel_co2_per_gram = fuel_figures.get("fuel_co2_per_gram")

            self.top_speed = fuel_figures.get("top_speed")

    def update_from_schema(self, aircraft):
        """
        """
        self.type = aircraft["type"]
        self.flight_name = aircraft["flight_name"]
        self.registration = aircraft["registration"]
        self.description = aircraft["description"]
        self.year = aircraft["year"]
        self.owner_operator = aircraft["owner_operator"]
        if aircraft["image"]:
            self.image = aircraft["image"]

    def flight_points_from_day(self, day):
        """
        Return a list of FlightPoints from the given Date instance.

        TODO: convert this to use the dynamic query when its set that way
        """
        return db.session.query(FlightPoint)\
            .filter(FlightPoint.aircraft_icao == self.icao)\
            .filter(FlightPoint.day_day == day)\
            .order_by(asc(FlightPoint.timestamp))\
            .all()

    @classmethod
    def get_by_icao(cls, icao):
        return db.session.query(Aircraft)\
            .filter(Aircraft.icao == icao)\
            .first()


class SuburbGeometryMixin(PointGeometryMixin, MultiPolygonGeometryMixin):
    minx                    = db.Column(db.Integer, nullable = False)
    miny                    = db.Column(db.Integer, nullable = False)
    maxx                    = db.Column(db.Integer, nullable = False)
    maxy                    = db.Column(db.Integer, nullable = False)

    @property
    def bbox(self):
        """
        Returns an array representing the bounding box of this Suburb.
        Notably, this function will return the bounding box with coordinates OPPOSITELY situated than standard due to the entire project being constructed this way.
        This can be changed if we ever refactor to swap lats/longs.
        """
        return [self.minx, self.miny, self.maxx, self.maxy]


class SuburbUTMEPSG(db.Model):
    __tablename__ = "suburb_utmepsg"

    utmepsg_epsg            = db.Column(db.Integer, db.ForeignKey("utm_epsg.epsg", ondelete = "CASCADE"), primary_key = True)
    suburb_hash             = db.Column(db.String(32), db.ForeignKey("suburb.suburb_hash", ondelete = "CASCADE"), primary_key = True)

    epsg                    = db.relationship("UTMEPSG", back_populates = "suburb_epsgs", uselist = False)
    suburb                  = db.relationship("Suburb", back_populates = "utm_epsg_suburbs", uselist = False)


suburb_neighbour = db.Table("suburb_neighbour", db.metadata,
    db.Column("left_suburb_hash", db.String(32), db.ForeignKey("suburb.suburb_hash"), primary_key = True),
    db.Column("right_suburb_hash", db.String(32), db.ForeignKey("suburb.suburb_hash"), primary_key = True)
)

class Suburb(SuburbGeometryMixin, db.Model):
    """A single Suburb. Flight points should be associated with a specific suburb."""
    __tablename__ = "suburb"

    # Suburb hash is a hash of the suburb name, all in caps.
    suburb_hash             = db.Column(db.String(32), primary_key = True)
    # Foreign key to the state in which this suburb resides.
    state_code              = db.Column(db.String(32), db.ForeignKey("state.state_code"))

    # Name is title'd, meaning, first letter of each word is upper case.
    name                    = db.Column(db.String(128), nullable = False)
    # The postcode for this suburb.
    postcode                = db.Column(db.Integer, nullable = False)
    # A that identifies the integrity of the suburb.
    version_hash            = db.Column(db.String(32), nullable = True)

    # Association proxy for all EPSGs this suburb intersects.
    epsgs                   = association_proxy("utm_epsg_suburbs", "utmepsg_epsg")

    # All UTM zones for this Suburb.
    utm_epsg_suburbs        = db.relationship(
        "SuburbUTMEPSG",
        back_populates = "suburb",
        uselist = True,
        cascade = "all, delete-orphan")

    # All neighbours for this Suburb. This is an eager relationship.
    right_neighbours        = db.relationship(
        "Suburb",
        backref = db.backref("left_neighbours"),
        primaryjoin = suburb_hash == suburb_neighbour.c.left_suburb_hash,
        secondaryjoin = suburb_neighbour.c.right_suburb_hash == suburb_hash,
        secondary = suburb_neighbour
    )

    # All flight points present in this suburb.
    flight_points_          = db.relationship("FlightPoint", back_populates = "suburb", uselist = True, lazy = "dynamic")

    # The state in which this suburb resides.
    state                   = db.relationship("State", back_populates = "suburbs_", uselist = False)

    def __repr__(self):
        return f"Suburb<{self.name},{self.state_name}>"

    @property
    def state_name(self):
        """The state's name in which this suburb resides."""
        return self.state.name

    @property
    def num_coordinates(self):
        """TODO: improve this"""
        num_coordinates_ = 0
        for geom in self.multi_polygon.geoms:
            num_coordinates_+=len(geom.exterior.coords)
        return num_coordinates_

    @hybrid_property
    def num_flight_points(self):
        """Return instance level total number of flight points."""
        return db.session.query(func.count(FlightPoint.flight_point_id))\
            .filter(FlightPoint.suburb_hash == self.suburb_hash)\
            .scalar()

    @num_flight_points.expression
    def num_flight_points(cls):
        """Return expression level total number of flight points."""
        return func.count(FlightPoint.flight_point_id)

    @property
    def neighbours(self):
        """Return all Suburb instances for this Suburb's neighbours."""
        return self.right_neighbours

    @property
    def num_neighbours(self):
        """Return the number of neighbours for this Suburb."""
        return len(self.right_neighbours)

    @classmethod
    def get_by_hash(cls, suburb_hash):
        """Locate a suburb by the given hash."""
        return db.session.query(Suburb)\
            .filter(Suburb.suburb_hash == suburb_hash)\
            .first()

    @classmethod
    def get_by_name(cls, suburb_name):
        """Locate a suburb by the given name."""
        return db.session.query(Suburb)\
            .filter(Suburb.name == suburb_name)\
            .first()


class State(PointGeometryMixin, MultiPolygonGeometryMixin, db.Model):
    """
    Represents a single State.
    This is a container of Suburbs. These suburbs will also be used to construct a geometry that contains the entire state. This can be used to determine
    which state in which a search for a flight point's location should be conducted.
    """
    __tablename__ = "state"

    state_code              = db.Column(db.String(32), primary_key = True)
    name                    = db.Column(db.String(128), nullable = False)

    # Dynamic relationship for all suburbs in this state.
    suburbs_                = db.relationship("Suburb", back_populates = "state", uselist = True, lazy = "dynamic")

    def __repr__(self):
        return f"State<{self.name}>"

    @property
    def suburbs(self):
        """Query and return all Suburb instances from this State."""
        return self.suburbs_.all()

    @property
    def num_suburbs(self):
        """Return the count of suburbs in this State."""
        return self.suburbs_.count()

    def find_suburb_by_name(self, suburb_name):
        return db.session.query(Suburb)\
            .filter(func.upper(Suburb.state_code) == self.state_code.upper())\
            .filter(func.lower(Suburb.name) == suburb_name.lower())\
            .first()

    def find_suburb_by_hash(self, suburb_hash):
        return db.session.query(Suburb)\
            .filter(func.upper(Suburb.state_code) == self.state_code.upper())\
            .filter(func.lower(Suburb.suburb_hash) == suburb_hash.lower())\
            .first()

    @classmethod
    def get_by_name(cls, name):
        return db.session.query(State)\
            .filter(func.lower(State.name) == name.lower())\
            .first()

    @classmethod
    def get_by_code(cls, state_code):
        return db.session.query(State)\
            .filter(func.upper(State.state_code) == state_code.upper())\
            .first()


class AirportUTMEPSG(db.Model):
    __tablename__ = "airport_utmepsg"

    utmepsg_epsg            = db.Column(db.Integer, db.ForeignKey("utm_epsg.epsg", ondelete = "CASCADE"), primary_key = True)
    airport_hash            = db.Column(db.String(32), db.ForeignKey("airport.airport_hash", ondelete = "CASCADE"), primary_key = True)

    epsg                    = db.relationship("UTMEPSG", back_populates = "airport_epsgs", uselist = False)
    airport                 = db.relationship("Airport", back_populates = "utm_epsg_airports", uselist = False)


class Airport(PolygonGeometryMixin, db.Model):
    """
    A database model to represent various airports, for both tracking flights and also for better predicting whether a disappearing
    aircraft resulted in a landing. Airports have been transformed to instead hold a polygon rather than a point, so it can be associated
    with UTM EPSGs and be located that way.
    """
    __tablename__ = "airport"

    # Airport hash is the latitude, longitude and name hashed.
    airport_hash            = db.Column(db.String(32), primary_key = True)

    name                    = db.Column(db.String(64), nullable = False)
    kind                    = db.Column(db.String(16))
    icao                    = db.Column(db.String(12))
    iata                    = db.Column(db.String(12))

    # Association proxy for all EPSGs this airport intersects.
    epsgs                   = association_proxy("utm_epsg_airports", "utmepsg_epsg")

    # All UTM zones for this Airport.
    utm_epsg_airports       = db.relationship(
        "AirportUTMEPSG",
        back_populates = "airport",
        uselist = True,
        cascade = "all, delete-orphan")

    def __repr__(self):
        return f"Airport<{self.name}>"

    @property
    def center(self):
        return self.polygon.centroid

    @classmethod
    def get_all_from_utm_epsg(cls, utm_epsg):
        return db.session.query(Airport)\
            .join(AirportUTMEPSG, AirportUTMEPSG.airport_hash == Airport.airport_hash)\
            .filter(AirportUTMEPSG.utmepsg_epsg == utm_epsg)\
            .all()

    @classmethod
    def get_by_hash(cls, airport_hash):
        return db.session.query(Airport)\
            .filter(Airport.airport_hash == airport_hash)\
            .first()

    @classmethod
    def find_by_name(cls, name):
        """Return the airport whose name sort've matches the given name."""
        return db.session.query(Airport)\
            .filter(Airport.name.ilike(f"%{name.lower()}%"))\
            .first()


class User(db.Model, UserMixin):
    __tablename__ = "user_"

    PRIVILEGE_USER = 0
    PRIVILEGE_OWNER = 5

    id                      = db.Column(db.Integer, primary_key = True)

    username                = db.Column(db.String(32), nullable = False, unique = True)
    password_hash           = db.Column(db.String(128), nullable = False)
    privilege               = db.Column(db.Integer, nullable = False, default = PRIVILEGE_USER)

    def __repr__(self):
        return f"User<{self.username},p={self.privilege}>"

    def set_username(self, username):
        self.username = username

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def set_privilege(self, privilege):
        self.privilege = privilege

    @classmethod
    def get_by_username(cls, username):
        return db.session.query(User)\
            .filter(User.username == username)\
            .first()


class RadarWorkerErrorReport(db.Model):
    __tablename__ = "radar_worker_error_report"

    id                      = db.Column(db.Integer, primary_key = True)
    radar_worker_name       = db.Column(db.String(64), db.ForeignKey("radar_worker.name"))

    error_code              = db.Column(db.String(64), nullable = False)
    created                 = db.Column(db.DateTime(), nullable = False, default = datetime.now)
    description             = db.Column(db.Text, nullable = True)
    stack_trace             = db.Column(db.Text, nullable = True)
    extra_information_      = db.Column(db.Text, nullable = True)

    radar_worker            = db.relationship(
        "RadarWorker",
        back_populates = "error_reports",
        uselist = False)

    def __repr__(self):
        return f"RadarWorkerError<{self.radar_worker},r={self.error_code}>"

    @property
    def extra_information(self):
        if not self.extra_information_:
            return dict()
        return json.loads(self.extra_information_)

    @extra_information.setter
    def extra_information(self, value):
        if not value:
            self.extra_information_ = None
        else:
            self.extra_information_ = json.dumps(value)


class RadarWorker(db.Model, UserMixin):
    """
    A RadarWorker represents a single NodeJS instance running a Puppeteer bot. This model will also contain all configuration
    that will guide how the worker runs including whether to use proxies, whether to run headless, target address for reverse
    connection, whether to use RESTful HTTP or SocketIO for reverse connection etc.
    """
    __tablename__ = "radar_worker"

    STATUS_READY = 0
    STATUS_INITIALISING = 1
    STATUS_RUNNING = 2
    STATUS_SHUTDOWN = 3
    STATUS_ERROR = 4
    STATUS_UNKNOWN = -1

    ### Some configuration values, this info is read from the configuration file. ###
    # The worker's name.
    name                    = db.Column(db.String(64), primary_key = True)
    # A UUID for the worker.
    unique_id               = db.Column(db.String(32), unique = True)
    # The type of worker.
    worker_type             = db.Column(db.String(32), nullable = False)
    # If True, this worker will be included in the auto-start procedure, otherwise it'll be ignored.
    enabled                 = db.Column(db.Boolean, default = False)
    # What URL should this worker use as a base for connecting back?
    phone_home_url          = db.Column(db.String(128), default = "http://127.0.0.1:5000/")
    # Should this worker run headless?
    run_headless            = db.Column(db.Boolean, default = False)
    # Should this worker connect via a proxy?
    use_proxy               = db.Column(db.Boolean, default = False)
    # A JSON proxy list for this worker.
    proxy_url_list_         = db.Column(db.String(1024), default = None)
    # Should this radar worker save all outgoing payloads as JSON?
    should_save_payloads    = db.Column(db.Boolean, default = False)
    # The filename for this worker.
    worker_filename         = db.Column(db.String(64), nullable = False, default = config.WORKER_FILE_NAME)

    ### Live process info ###
    # The current process' PID.
    pid                     = db.Column(db.Integer, default = None)

    ### General live info ###
    # The datetime at which this worker was last updated. This is anytime the worker communicates with the master server at all.
    last_update             = db.Column(db.DateTime(), default = None)
    # For storing an error. This will ensure the worker will remain under the ERROR status until set None again.
    # This is a string, but is actually a JSON object.
    error_json_str_         = db.Column(db.Text)
    # Whether this worker is currently running, according to our records.
    running                 = db.Column(db.Boolean, default = False)
    # Time at which the worker was started.
    executed_at             = db.Column(db.DateTime(), default = None)
    # Time at which the worker was last shutdown. This is cleared upon execution of the Worker.
    shutdown_at             = db.Column(db.DateTime(), default = None)
    # Failsafe: if the radar worker is started, these two attributes will be set, then when celery beat
    # attempts to scan for aircraft, and the worker has been initialising for longer than 5 minutes, this state can be reset.
    initialising            = db.Column(db.Boolean, default = False)
    init_started_at         = db.Column(db.DateTime(), default = None)

    # All the aircraft-day work assigned to this RadarWorker.
    aircraft_day_work       = db.relationship(
        "AircraftPresentDay",
        back_populates = "assigned_worker",
        secondary = "worker_lock_aircraft_day",
        uselist = True)
    # All error reports created by this worker.
    error_reports           = db.relationship(
        "RadarWorkerErrorReport",
        back_populates = "radar_worker",
        uselist = True)

    def __repr__(self):
        return f"Worker<{self.name},t={self.worker_type},e={self.enabled},status={self.status_str}>"

    @property
    def is_active(self):
        return self.enabled

    def get_id(self):
        return self.name

    @property
    def init_started_at_str(self):
        if not self.init_started_at:
            return None
        # TODO 0x06
        """TODO 0x16"""
        utc_dt = datetime.utcfromtimestamp(int(self.init_started_at)).replace(tzinfo = pytz.utc)
        au_tz = pytz.timezone(config.TIMEZONE)
        aware_datetime = au_tz.normalize(utc_dt.astimezone(au_tz))
        am_pm = aware_datetime.strftime("<span class='ampm-font'>%p</span>").lower()
        return aware_datetime.strftime(f"%a, %d %B %Y at %I:%M{am_pm}")

    @property
    def executed_at_str(self):
        if not self.executed_at:
            return None
        # TODO 0x06
        """TODO 0x16"""
        utc_dt = datetime.utcfromtimestamp(int(self.executed_at)).replace(tzinfo = pytz.utc)
        au_tz = pytz.timezone(config.TIMEZONE)
        aware_datetime = au_tz.normalize(utc_dt.astimezone(au_tz))
        am_pm = aware_datetime.strftime("<span class='ampm-font'>%p</span>").lower()
        return aware_datetime.strftime(f"%a, %d %B %Y at %I:%M{am_pm}")

    @property
    def shutdown_at_str(self):
        if not self.shutdown_at:
            return None
        # TODO 0x06
        """TODO 0x16"""
        utc_dt = datetime.utcfromtimestamp(int(self.shutdown_at)).replace(tzinfo = pytz.utc)
        au_tz = pytz.timezone(config.TIMEZONE)
        aware_datetime = au_tz.normalize(utc_dt.astimezone(au_tz))
        am_pm = aware_datetime.strftime("<span class='ampm-font'>%p</span>").lower()
        return aware_datetime.strftime(f"%a, %d %B %Y at %I:%M{am_pm}")

    @property
    def num_assigned_aircraft_day_work(self):
        return len(self.aircraft_day_work)

    @property
    def num_error_reports(self):
        return len(self.error_reports)

    @property
    def proxy_url_list(self):
        if not self.proxy_url_list_:
            return []
        return json.loads(self.proxy_url_list_)

    @proxy_url_list.setter
    def proxy_url_list(self, value):
        if not value or (isinstance(value, list) and len(value) == 0):
            self.proxy_url_list_ = None
        else:
            self.proxy_url_list_ = json.dumps(value)

    @property
    def status(self):
        if self.running == False and self.executed_at == None and self.shutdown_at == None and self.initialising == False and self.init_started_at == None:
            return RadarWorker.STATUS_READY
        elif self.running == False and self.executed_at == None and self.shutdown_at == None and self.initialising == True and self.init_started_at != None:
            return RadarWorker.STATUS_INITIALISING
        elif self.running == True and self.executed_at != None and self.shutdown_at == None and self.initialising == False and self.init_started_at != None:
            return RadarWorker.STATUS_RUNNING
        elif self.running == False and self.initialising == False and self.error_json_str_ != None:
            return RadarWorker.STATUS_ERROR
        elif self.running == False and self.initialising == False and (self.executed_at != None or self.init_started_at != None):
            return RadarWorker.STATUS_SHUTDOWN
        return RadarWorker.STATUS_UNKNOWN

    @property
    def status_str(self):
        if self.status == RadarWorker.STATUS_READY:
            return "ready"
        elif self.status == RadarWorker.STATUS_INITIALISING:
            return "initialising"
        elif self.status == RadarWorker.STATUS_RUNNING:
            return "running"
        elif self.status == RadarWorker.STATUS_ERROR:
            return "error"
        elif self.status == RadarWorker.STATUS_SHUTDOWN:
            return "shutdown"
        return "unknown"

    @property
    def error_json(self):
        if not self.error_json_str_:
            return None
        return json.loads(self.error_json_str_)

    @error_json.setter
    def error_json(self, value):
        if not value:
            self.error_json_str_ = None
        else:
            self.error_json_str_ = json.dumps(value)

    def set_process(self, process):
        self.pid = process.pid

    def remove_process_info(self):
        self.pid = None

    def add_error_report(self, error_report):
        self.error_reports.append(error_report)

    def update_from_object(self, radar_worker, **kwargs):
        """
        """
        self.enabled = radar_worker.enabled
        self.unique_id = radar_worker.unique_id
        self.phone_home_url = radar_worker.phone_home_url
        self.run_headless = radar_worker.run_headless
        self.use_proxy = radar_worker.use_proxy
        self.proxy_url_list = radar_worker.proxy_url_list

    def reset_status_attrs(self):
        """Reset all status related attributes for this worker to default values. After this, the worker will qualify as one in a READY state."""
        self.running = False
        self.executed_at = None
        self.shutdown_at = None
        self.initialising = False
        self.init_started_at = None
        self.error_json_str_ = None
        self.remove_process_info()

    def set_last_update(self):
        datetime_now = g.get("datetime_now", None) or datetime.now()
        self.last_update = datetime_now

    @classmethod
    def get_by_name(cls, name):
        return db.session.query(RadarWorker)\
            .filter(RadarWorker.name == name)\
            .first()


class AircraftMaster(db.Model):
    """
    A model for joining an aircraft record to a master record, this will identify the aircraft as an actively tracked aircraft.
    """
    __tablename__ = "aircraft_master"

    aircraft_icao           = db.Column(db.String(12), db.ForeignKey("aircraft.icao"), primary_key = True)
    master_id               = db.Column(db.Integer, db.ForeignKey("master.id"), primary_key = True)


class Master(db.Model):
    """
    Central control table. There can only be one instance of this type, and that instance contains persistent configuration
    for the running of the server. This makes it easy to update the ongoing execution of the server in real time.
    """
    __tablename__ = "master"

    id                      = db.Column(db.Integer, primary_key = True)

    # The current day, at UTC. This will be used to bypass workers operating on the current day.
    current_day             = db.Column(db.Date, nullable = False)
    # All tracked vehicles.
    tracked_aircraft        = db.relationship(
        "Aircraft",
        back_populates = "master",
        secondary = "aircraft_master",
        uselist = True
    )

    @property
    def num_tracked_aircraft(self):
        return len(self.tracked_aircraft)

    @classmethod
    def new(cls, current_day, tracked_aircraft):
        # Create a new Master, set attributes, add to session and return it.
        new_master = Master(
            current_day = current_day,
            tracked_aircraft = tracked_aircraft
        )
        db.session.add(new_master)
        # Also flush it.
        db.session.flush()
        return new_master

    @classmethod
    def get(cls):
        # Get the first entry, always.
        master = db.session.query(Master).first()
        if not master:
            # Raise an exception, as creating the master must be done BEFORE ever calling get.
            # This should be done in a manage function.
            LOG.error(f"Failed to get the Master instance! One does not yet exist.")
            raise error.NoMasterError()
        # Otherwise return it.
        return master
