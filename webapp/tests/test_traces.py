import os
import base64
import json
import time
import aiofiles
import decimal
import unittest
import asyncio
import aiofiles

from subprocess import Popen, PIPE
from datetime import date, datetime, timedelta

from sqlalchemy import asc
from sqlalchemy import func, and_, or_

from tests.conftest import BaseCase

from app import db, config, models, traces, airvehicles, error


class TestTraces(BaseCase):
    def test_ensure_days_created(self):
        # Create 3 date instances.
        dates = [date(2022, 1, 10), date(2022, 1, 11), date(2022, 1, 12)]
        # Use traces module to ensure created.
        traces.ensure_days_created(dates)
        db.session.flush()
        # Now, query each from the database, ensure they are not none.
        for date_ in dates:
            self.assertIsNotNone(models.Day.get_by_date(date_))

    def test_ensure_days_created_from_aircraft(self):
        # Load an aircraft's trace manually.
        with open(os.path.join(os.getcwd(), config.TESTDATA_DIR, "native_testdata", "aircraft_7c6bcf_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Load an aircraft schema.
        aircraft_d = airvehicles.AircraftSchema().load(aircraft_json)
        # Now, use traces module to ensure all days created associated with this aircraft dictionary.
        traces.ensure_days_created_from_aircraft(aircraft_d)
        db.session.flush()
        # Ensure all following dates are created.
        dates = [date(2022, 6, 25), date(2022, 6, 26)]
        for date_ in dates:
            self.assertIsNotNone(models.Day.get_by_date(date_))


class TestGenerateFlights(BaseCase):
    def _normalise_trace_timestamps(self, trace_json, **kwargs):
        """
        Given a trace in JSON form, normalise all timestamps by iterating the 'trace' attribute, and for each, adding the outer 'timestamp'
        attribute to the first index. The return value is the same JSON object, but normalised.

        Arguments
        ---------
        :trace_json: A JSON object containing a relative trace.

        Returns
        -------
        The same JSON, normalised.
        """
        try:
            # Save our outer timestamp, this is in seconds.
            timestamp = trace_json["timestamp"]
            # Iterate all items in the 'trace' attribute, adding timestamp to the first index on each.
            for trace_object in trace_json["trace"]:
                trace_object[0] += timestamp
            # Return the trace json.
            return trace_json
        except Exception as e:
            raise e

    def test_generate(self):
        # Read a basic trace from one of the dirs.
        with open(os.path.join(os.getcwd(), config.TRACES_DIR, "2021-08-31", "7c4ef5_full.json"), "r") as f:
            daily_json = json.loads(f.read())
        # Normalise daily json.
        normalised = self._normalise_trace_timestamps(daily_json)
        # Once normalised, collect a list of all different dates in here.
        dates = []
        for trace in normalised["trace"]:
            timestamp = trace[0]
            # Get from utcfromtimestamp.
            dt = datetime.utcfromtimestamp(timestamp)
            d = dt.date()
            if not d in dates:
                dates.append(d)

        for x in dates:
            print(x.isoformat())


class TestTrackingReports(BaseCase):
    def test_report_aircraft(self):
        """
        Test traces module's capability to report an aircraft's presence on a particular day.
        Load an example aircraft into database.
        Manually determine all unique days in which this aircraft was active (out of scope)
        Use traces module to report presence on all these days.
        Ensure relationship exists on both sides.
        """
        with open(os.path.join(os.getcwd(), config.TESTDATA_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Load the stored object with AircraftSchema; get back a dict for Aircraft.
        aircraft_d = airvehicles.AircraftSchema().load(aircraft_json)
        # Make the aircraft.
        aircraft = models.Aircraft(**aircraft_d)
        # Add the aircraft to database, and flush.
        db.session.add(aircraft)
        db.session.flush()
        # Ensure there are 1708 points.
        self.assertEqual(aircraft.num_flight_points, 1708)

        # Now, manually discover a list of days this aircraft was flying on, given their flight points.
        days_active = []
        for x in aircraft.flight_points:
            d = date.fromtimestamp(int(x.timestamp))
            if not d in days_active:
                days_active.append(d)
        # Ensure we have 1.
        self.assertEqual(len(days_active), 1)
        # Now, for each day active, use traces module to report this aircraft as present.
        for day in days_active:
            traces.report_aircraft_presence(aircraft, day)
        # Flush.
        db.session.flush()
        # Now, ensure relationships on both sides are good.
        # Get all aircraft's active days.
        aircraft_active_days = aircraft.days_active.all()
        # Ensure all entries in this list, their 'day' attribute is present in days_active.
        for day_active in aircraft_active_days:
            self.assertIn(day_active.day, days_active)
        # Now, get all aircraft active on both days active. Ensure that both times, aircraft is present.
        for day_ in days_active:
            # Locate a day model from database for this day.
            day = db.session.query(models.Day)\
                .filter(models.Day.day == day_)\
                .first()
            self.assertIsNotNone(day)
            # Now, get all aircraft present on the day.
            all_present_aircraft = day.active_aircraft.all()
            # Ensure aircraft is in this list.
            self.assertIn(aircraft, all_present_aircraft)
