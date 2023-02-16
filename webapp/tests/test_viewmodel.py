import os
import base64
import json
import time
import decimal

from subprocess import Popen, PIPE
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import asc, desc, func

from tests.conftest import BaseCase

from app import db, config, models, utility, viewmodel, airvehicles


class TestAircraftViewModel(BaseCase):
    def test_percentage_flight_time_prohibited(self):
        # Now, create a summary where total flight time is 2849172 minutes, and prohibited flight time is 284917 minutes.
        aircraft_summary = viewmodel.AircraftViewModel(flight_time_total = 2849172, flight_time_prohibited = 284917)
        # Ensure this is 10% os the total value.
        self.assertEqual(aircraft_summary.percentage_flight_time_prohibited, 10)

    def test_since_seen_timespan_str(self):
        # Set current date to 10 Jan 2022
        self.set_datetimenow(datetime(2022, 1, 10, hour = 5, minute = 43, second = 4))
        # Now, create a summary with date_first_seen set to 7 July 2020
        aircraft_summary = viewmodel.AircraftViewModel(timestamp_first_seen = datetime(2020, 7, 7, hour = 2, minute = 13, second = 4, tzinfo = timezone(timedelta(hours = 11))).timestamp())
        # Ensure since_seen_timespan_str is equal to '1 year and 6 months'
        self.assertEqual(aircraft_summary.since_seen_timespan_str, "1 year and 6 months")

    def test_first_seen_str(self):
        # Set current date to 10 Jan 2022
        self.set_datetimenow(datetime(2022, 1, 10, hour = 5, minute = 43, second = 4))
        # Now, create a summary with date_first_seen set to 7 July 2020
        aircraft_summary = viewmodel.AircraftViewModel(timestamp_first_seen = datetime(2020, 7, 7, hour = 2, minute = 13, second = 4).timestamp())
        # Ensure first_seen_str is equal to 'Tue, 07 July 2020'
        self.assertEqual(aircraft_summary.first_seen_str, "Tue, 07 July 2020")

    def test_num_people_yearly_co2_quota_str(self):
        # Now, make an aircraft summary object.
        aircraft_summary = viewmodel.AircraftViewModel(total_carbon_emissions = 102708)
        # Ensure that, when we get num_people_yearly_co2_quota_str, the result is equal to "6"
        self.assertEqual(aircraft_summary.num_people_yearly_co2_quota_str, "6")

    def test_last_seen_str(self):
        # Now, make an aircraft summary object.
        aircraft_summary = viewmodel.AircraftViewModel(seconds_since_last_seen = None)
        # Last seen str should be 'Not seen yet'
        self.assertEqual(aircraft_summary.last_seen_str, "Not seen yet")
        # Now, add a flight point, 8 minutes ago.
        aircraft_summary.seconds_since_last_seen = 514
        # Ensure last seen str is 'Last seen 8m 34s ago'
        self.assertEqual(aircraft_summary.last_seen_str, "Last seen 8m 34s ago")
        # Now, change timestamp to 5 seconds ago.
        aircraft_summary.seconds_since_last_seen = 5
        # Ensure last seen str is 'Last seen just now'
        self.assertEqual(aircraft_summary.last_seen_str, "Last seen just now")
        # Change timestamp to 35 seconds ago.
        aircraft_summary.seconds_since_last_seen = 35
        # Ensure last seen str is 'Last seen 35 seconds ago'
        self.assertEqual(aircraft_summary.last_seen_str, "Last seen 35s ago")
        # Change timestamp to 4 hours and 34 minutes ago.
        aircraft_summary.seconds_since_last_seen = timedelta(hours = 4, minutes = 34).total_seconds()
        # Ensure last seen str is 'Last seen 4h 34m ago'
        self.assertEqual(aircraft_summary.last_seen_str, "Last seen 4h 34m ago")
        # Change timestamp to 100 days ago.
        aircraft_summary.seconds_since_last_seen = timedelta(days = 100).total_seconds()
        # Ensure last seen str is now 'Last seen 100 days ago'
        self.assertEqual(aircraft_summary.last_seen_str, "Last seen 3m 1w ago")
        # Change timestamp to 500 days ago.
        aircraft_summary.seconds_since_last_seen = timedelta(days = 500).total_seconds()
        # Ensure last seen str is now 'Last seen a long time ago'
        self.assertEqual(aircraft_summary.last_seen_str, "Last seen a long time ago")

    def test_total_flight_time_str(self):
        # Now, make an aircraft summary object.
        aircraft_summary = viewmodel.AircraftViewModel(flight_time_total = None)
        # Flight time total str should be 'Not flown yet'
        self.assertEqual(aircraft_summary.flight_time_total_str, "Not flown yet")
        # Flown for 8 minutes
        aircraft_summary.flight_time_total = 514
        # Flight time total str should be 'Flown for 8 minutes'
        self.assertEqual(aircraft_summary.flight_time_total_str, "Flown for 8 minutes and 34 seconds")
        # Flown for 5 seconds
        aircraft_summary.flight_time_total = 5
        # Flight time total str should be 'Flown very little'
        self.assertEqual(aircraft_summary.flight_time_total_str, "Flown very little")

    def test_total_flight_time_prohibited_str(self):
        # Now, make an aircraft summary object.
        aircraft_summary = viewmodel.AircraftViewModel(flight_time_prohibited = None)
        # Flight time total str should be 'Not flown yet'
        self.assertEqual(aircraft_summary.flight_time_prohibited_str, "Not flown during prohibited hours yet")
        # Flown for 8 minutes
        aircraft_summary.flight_time_prohibited = 514
        # Flight time total str should be 'Flown for 8 minutes'
        self.assertEqual(aircraft_summary.flight_time_prohibited_str, "Flown for 8 minutes and 34 seconds during prohibited hours")
        # Flown for 5 seconds
        aircraft_summary.flight_time_prohibited = 5
        # Flight time total str should be 'Flown very little'
        self.assertEqual(aircraft_summary.flight_time_prohibited_str, "Flown very little during prohibited hours")


class TestProjectViewModel(BaseCase):
    def test_date_recording_started_str(self):
        project_summary = viewmodel.ProjectViewModel()
        # Ensure date recording start str is equal to 'Wednesday, 08 July 2020'
        self.assertEqual(project_summary.date_recording_started_str, "Wednesday, 08 July 2020")

    def test_since_recording_started_timespan_str(self):
        project_summary = viewmodel.ProjectViewModel()
        # Ensure since recording started timespan str equal to '2 years and 3 months'
        self.assertEqual(project_summary.since_recording_started_timespan_str, "2 years and 3 months")

    def test_total_flight_time_hours_str(self):
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.flush()
        project_summary = viewmodel.ProjectViewModel()
        # Ensure there's '7' total flight time hours.
        self.assertEqual(project_summary.total_flight_time_hours_str, "7")

    def test_total_num_flights_str(self):
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.flush()
        project_summary = viewmodel.ProjectViewModel()
        # Ensure there's '2' total flights.
        self.assertEqual(project_summary.total_num_flights_str, "2")

    def test_total_fuel_consumed_str(self):
        aircraft = airvehicles.read_aircraft_from("aircraft_states.json")
        db.session.flush()
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.flush()
        project_summary = viewmodel.ProjectViewModel()
        # Ensure total fuel consumed is 834.
        self.assertEqual(project_summary.total_fuel_consumed_str, "834")

    def test_total_co2_produced_str(self):
        aircraft = airvehicles.read_aircraft_from("aircraft_states.json")
        db.session.flush()
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.flush()
        project_summary = viewmodel.ProjectViewModel()
        # Ensure total co2 produced is '102,708'
        self.assertEqual(project_summary.total_co2_produced_str, "102,708")

    def test_num_people_yearly_co2_quota_str(self):
        aircraft = airvehicles.read_aircraft_from("aircraft_states.json")
        db.session.flush()
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.flush()
        project_summary = viewmodel.ProjectViewModel()
        # Ensure the num people yearly co2 quota is '6'
        self.assertEqual(project_summary.num_people_yearly_co2_quota_str, "6")
