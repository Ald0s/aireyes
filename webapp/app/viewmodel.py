"""
"""
import re
import os
import time
import uuid
import decimal
import hashlib
import logging
import math
import pyproj
import json
import pytz
from datetime import datetime, date, timedelta, time as time_, timezone

from shapely import geometry, ops

from .compat import insert

from flask import g
from sqlalchemy import func, and_, or_, asc, desc
from sqlalchemy.exc import IntegrityError
from marshmallow import Schema, fields, EXCLUDE, post_load, pre_load

from . import db, config, models, error, airvehicles, utility

LOG = logging.getLogger("aireyes.viewmodel")
LOG.setLevel( logging.DEBUG )


class ProjectViewModel():
    """An object that provides view functions for the entire project as a whole."""
    @property
    def num_vehicles_monitored_str(self):
        """Returns a formatted integer indicating how many vehicles are currently monitored by this system."""
        return f"{self.num_monitored_aircraft:,}"

    @property
    def date_recording_started_str(self):
        """Return a date, formatted like Wednesday, 08 July 2020, for the 'from' date."""
        data_recording_started = config.DATA_SOURCE_DAY_RANGE[0]
        return data_recording_started.strftime("%A, %d %B %Y")

    @property
    def since_recording_started_timespan_str(self):
        """Returns a delta time descriptor formatted string for the amount of time since recording of data began."""
        # Get the first date in the range, combine it with time.
        from_datetime = datetime.combine(config.DATA_SOURCE_DAY_RANGE[0], time_.min)
        to_datetime = datetime.utcnow()
        time_delta_descriptor = utility.TimeDeltaDescriptor(to_datetime-from_datetime)
        return f"{time_delta_descriptor.display(minified_keys = False, use_commas = False, use_and = True)}"

    @property
    def total_flight_time_hours_str(self):
        """Returns a formatted total of all flight time, in hours."""
        num_hours_flown = round(self.flight_time_total/60)
        return f"{num_hours_flown:,}"

    @property
    def total_num_flights_str(self):
        """Returns the total number of Flights, formatted."""
        return f"{self.num_flights_total:,}"

    @property
    def total_fuel_consumed_str(self):
        """Returns the total amount of fuel, in gallons, consumed by all flights, formatted."""
        return f"{round(self.total_fuel_used):,}"

    @property
    def total_co2_produced_str(self):
        """Returns the total amount of co2, in kilograms, produced by all flights, formatted."""
        return f"{round(self.total_co2_emissions):,}"

    @property
    def num_people_yearly_co2_quota_str(self):
        """Returns the number of people that produce the equivalent co2 in an entire year, formatted."""
        # Source: https://data.worldbank.org/indicator/EN.ATM.CO2E.PC?end=2019&name_desc=false&start=1990
        yearly_co2_emissions_per_capita_australia = config.YEARLY_CO2_EMISSIONS_PER_CAPITA_AUSTRALIA
        # Convert this to kilograms.
        yearly_co2_emissions_per_capita_australia = yearly_co2_emissions_per_capita_australia * 1000
        num_people_yearly_quota = self.total_co2_emissions / yearly_co2_emissions_per_capita_australia
        return f"{int(num_people_yearly_quota):,}"

    def __init__(self):
        self._refresh_summary()

    def _refresh_summary(self):
        """(Re)run the query for all sum stats from the database. We'll persistently use these values over the course of this instance."""
        self.num_flights_total, self.flight_time_total, self.total_fuel_used, self.total_co2_emissions = db.session.query(
            func.count(models.Flight.flight_hash),
            func.sum(func.coalesce(models.Flight.flight_time_total, 0)),
            func.sum(func.coalesce(models.Flight.fuel_used, 0)),
            func.sum(func.coalesce(models.Flight.total_co2_emissions, 0))
        ).first()
        self.num_monitored_aircraft = db.session.query(func.count(models.Aircraft.icao))\
            .filter(models.Aircraft.is_enabled == True)\
            .scalar()


class AircraftViewModelSchema(Schema):
    """
    A schema for dumping an aircraft viewmodel to a summary representation. This schema provides some basic identifying information, along with some data regarding the aircraft's
    most previous flight, and some overall statistics for the Aircraft.
    """
    class Meta:
        unknown = EXCLUDE
    ### Basic identifying information ###
    icao                    = fields.Str(data_key = "aircraftIcao")
    type                    = fields.Str(allow_none = True, data_key = "aircraftType")
    flight_name             = fields.Str(data_key = "aircraftName")
    registration            = fields.Str(allow_none = True, data_key = "aircraftRegistration")

    ### Statistics ###
    # Is this aircraft currently active?
    is_active_now           = fields.Bool(data_key = "isActiveNow")
    # Total amount of estimated fuel used.
    total_fuel_used         = fields.Int(data_key = "totalFuelUsed")
    # How many seconds ago was this aircraft last seen?
    seconds_since_last_seen = fields.Int(data_key = "lastSeenSeconds")
    # The total amount of time, in minutes, this aircraft has flown.
    flight_time_total       = fields.Int(data_key = "flightTimeTotal")
    # Total distance travelled by the aircraft, in meters.
    distance_travelled      = fields.Int(data_key = "distanceTravelled")
    # The number of flights so far.
    num_flights             = fields.Int(data_key = "numFlights")


class AircraftViewModel():
    """
    An object that provides a few augmenting features to an aircraft model. This is intended to be passed to templates or serialised
    and sent back as an accurate summary of the aircraft.
    """
    @property
    def since_seen_timespan_str(self):
        """Returns a delta time descriptor formatted string for the amount of time since the aircraft was first seen, until now."""
        since_seen_td = self.since_seen_timedelta
        time_delta_descriptor = utility.TimeDeltaDescriptor(since_seen_td)
        return f"{time_delta_descriptor.display(minified_keys = False, use_commas = False, use_and = True)}"

    @property
    def first_seen_str(self):
        """Returns a formatted date for when this aircraft was first seen. (When logs begin.)"""
        first_seen_date = self.datetime_first_seen
        return first_seen_date.strftime(f"%a, %d %B %Y")

    @property
    def total_fuel_cost_str(self):
        """Returns the total amount of AUD ($) this aircraft has cost for fuel ALONE."""
        return f"{self.total_fuel_cost:,}"

    @property
    def total_fuel_cost(self):
        """Returns the total cost of all fuel for this aircraft."""
        return self.fuel_cost * self.total_fuel_used

    @property
    def total_servicing_cost_str(self):
        """Returns the total amount of AUD ($) this aircraft has cost for servicing ALONE."""
        return f"{self.total_servicing_cost:,}"

    @property
    def total_servicing_cost(self):
        """Returns the total cost of all servicing for this aircraft."""
        """
        TODO: aircraft servicing cost.
        """
        return 0

    @property
    def total_pilot_salary_cost_str(self):
        """Returns the total amount of AUD ($) this aircraft has cost in salaries ALONE."""
        return f"{self.total_pilot_salary_cost:,}"

    @property
    def total_pilot_salary_cost(self):
        """Returns the total cost of all salaries for this aircraft."""
        """
        TODO: aircraft pilot salary cost.
        """
        return 0

    @property
    def total_cost_str(self):
        """Returns the total amount of AUD ($) this aircraft has cost taxpayers."""
        return f"{self.total_cost:,}"

    @property
    def total_cost(self):
        """Returns the total cost of this aircraft."""
        return sum([self.total_fuel_cost, self.total_servicing_cost, self.total_pilot_salary_cost])

    @property
    def num_people_yearly_co2_quota_str(self):
        """Returns the total number of people that, yearly in Australia, produces the same amount of co2 as this aircraft has in total."""
        num_people_yearly_co2_quota = self.num_people_yearly_co2_quota
        # Format and return.
        return f"{num_people_yearly_co2_quota:,}"

    @property
    def num_people_yearly_co2_quota(self):
        """Returns the total number of people that, yearly in Australia, produces the same amount of co2 as this aircraft has in total."""
        # Source: https://data.worldbank.org/indicator/EN.ATM.CO2E.PC?end=2019&name_desc=false&start=1990
        yearly_co2_emissions_per_capita_australia = config.YEARLY_CO2_EMISSIONS_PER_CAPITA_AUSTRALIA
        # Convert this to kilograms.
        yearly_co2_emissions_per_capita_australia = yearly_co2_emissions_per_capita_australia * 1000
        # Number of people is the floored result of dividing total carbon emissions (kg) by emissions per capita (kg)
        return math.floor(self.total_carbon_emissions / yearly_co2_emissions_per_capita_australia)

    @property
    def total_carbon_emissions_str(self):
        """Returns the total carbon emissions produced by this aircraft, formatted with commas."""
        if not self.total_carbon_emissions:
            return "0"
        return f"{self.total_carbon_emissions:,}"

    @property
    def most_frequented_suburbs(self):
        """Returns the 15 suburbs with the most presence from this aircraft."""
        flight_point_frequency_sq = db.session.query(func.count(models.FlightPoint.flight_point_hash).label("frequency"))\
            .filter(models.FlightPoint.aircraft_icao == self._aircraft.icao)\
            .filter(models.FlightPoint.suburb_hash == models.Suburb.suburb_hash)\
            .scalar_subquery()
        return db.session.query(models.Suburb, flight_point_frequency_sq)\
            .order_by(desc(flight_point_frequency_sq))\
            .limit(15)\
            .all()

    @property
    def distance_travelled_str(self):
        """Returns the total distance travelled, in kilometers, by the aircraft; formatted with commas."""
        if not self.distance_travelled_kilometers:
            return "0"
        return f"{self.distance_travelled_kilometers:,}"

    @property
    def flight_time_total_str(self):
        if not self.flight_time_total:
            return "Not flown yet"
        flight_time_total_td = timedelta(seconds = self.flight_time_total)
        if flight_time_total_td.total_seconds() <= 60:
            return "Flown very little"
        # Make a time delta descriptor for this td.
        time_delta_descriptor = utility.TimeDeltaDescriptor(flight_time_total_td)
        return f"Flown for {time_delta_descriptor.display(minified_keys = False, use_commas = False, use_and = True)}"

    @property
    def percentage_flight_time_prohibited(self):
        """Returns a percentage value of this aircraft's flight time (during prohibited hours) against their total flight time."""
        return round((self.flight_time_prohibited / self.flight_time_total) * 100)

    @property
    def flight_time_prohibited_str(self):
        if not self.flight_time_prohibited:
            return "Not flown during prohibited hours yet"
        flight_time_prohibited_td = timedelta(seconds = self.flight_time_prohibited)
        if flight_time_prohibited_td.total_seconds() <= 60:
            return "Flown very little during prohibited hours"
        # Make a time delta descriptor for this td.
        time_delta_descriptor = utility.TimeDeltaDescriptor(flight_time_prohibited_td)
        return f"Flown for {time_delta_descriptor.display(minified_keys = False, use_commas = False, use_and = True)} during prohibited hours"

    @property
    def last_seen_str(self):
        if not self.seconds_since_last_seen:
            return "Not seen yet"
        # Make a timedelta from these seconds.
        last_seen_td = timedelta(seconds = self.seconds_since_last_seen)
        if last_seen_td.days > 365:
            return "Last seen a long time ago"
        elif last_seen_td.total_seconds() <= 10:
            return "Last seen just now"
        # Make a time delta descriptor out of this td.
        time_delta_descriptor = utility.TimeDeltaDescriptor(last_seen_td)
        return f"Last seen {time_delta_descriptor.display(minified_keys = True, use_commas = False)} ago"

    @property
    def since_seen_timedelta(self):
        """Returns a timedelta for the total amount of time since the aircraft was first seen, until now."""
        datetime_now = g.get("datetime_now", None) or datetime.now()
        return datetime_now-self.datetime_first_seen

    @property
    def datetime_first_seen(self):
        # TODO 0x06:
        utc_dt = datetime.utcfromtimestamp(int(self.timestamp_first_seen)).replace(tzinfo = pytz.utc)
        au_tz = pytz.timezone(config.TIMEZONE)
        return au_tz.normalize(utc_dt.astimezone(au_tz))

    def __init__(self, aircraft = None, **kwargs):
        # For each value given in keyword args, set that as an attribute in this instance. This is a natural override of values in aircraft.
        for key, value in kwargs.items():
            setattr(self, key, value)
        # Set the aircraft.
        self._aircraft = aircraft

    def __getattr__(self, attr):
        if self._aircraft and hasattr(self._aircraft, attr):
            return getattr(self._aircraft, attr)
        else:
            return super().__getattribute__(attr)


class FlightViewModel():
    @property
    def starts_at(self):
        return self._flight.starts_at

    @property
    def starts_at_friendly_datetime(self):
        return self._flight.starts_at_friendly_datetime

    @property
    def ends_at(self):
        return self._flight.ends_at

    @property
    def ends_at_friendly_datetime(self):
        return self._flight.ends_at_friendly_datetime

    @property
    def num_flight_points(self):
        return self._flight.num_flight_points

    @property
    def flight_number(self):
        return self._flight.flight_number

    @property
    def aircraft(self):
        return self._flight.aircraft

    def __init__(self, flight = None, **kwargs):
        # For each value given in keyword args, set that as an attribute in this instance. This is a natural override of values in flight.
        for key, value in kwargs.items():
            setattr(self, key, value)
        # Set the flight.
        self._flight = flight

    def __getattr__(self, attr):
        return super().__getattribute__(attr)
        if self._flight and hasattr(self._flight, attr):
            return getattr(self._flight, attr)
        else:
            return super().__getattribute__(attr)
