import os
import base64
import json
import time
import decimal

from subprocess import Popen, PIPE
from datetime import date, datetime, timedelta

from flask import url_for
from sqlalchemy import asc, desc, func

from tests.conftest import BaseBrowserCase

from app import db, config, models, user


class TestBrowser(BaseBrowserCase):
    def test_aircraft_flights(self):
        # Load in an example aircraft.
        aircraft, dates = self._load_native_test_data("aircraft_7c4ee8_t1.json",
            history_verified = True, flights_verified = False)
        db.session.flush()
        # Attempt to get a page of flights for this aircraft
        get_flights_for = self.client.get(url_for("frontend.aircraft_flights", aircraft_icao = "7c4ee8"), follow_redirects = True)
        # Ensure this was successful.
        self.assertEqual(get_flights_for.status_code, 200)
