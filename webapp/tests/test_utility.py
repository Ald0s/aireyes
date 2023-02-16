import os
import base64
import json
import time
import decimal

from subprocess import Popen, PIPE
from datetime import date, datetime, timedelta

from sqlalchemy import asc, desc, func

from tests.conftest import BaseCase

from app import db, config, models, utility


class TestUtility(BaseCase):
    def test_time_delta_descriptor(self):
        t = utility.TimeDeltaDescriptor(timedelta(weeks = 6, hours = 4, minutes = 48, seconds = 9))
        # Ensure display is '1 month, 1 week'
        self.assertEqual(t.display(), "1 month, 1 week")
        # Ensure display, with minified keys is '1m, 1w'
        self.assertEqual(t.display(minified_keys = True), "1m, 1w")
        # Ensure display, with minified keys and WITHOUT commas is '1m 1w'
        self.assertEqual(t.display(minified_keys = True, use_commas = False), "1m 1w")
        # Ensure display, without minified keys and without commas is '1 month 1 week'
        self.assertEqual(t.display(minified_keys = False, use_commas = False), "1 month 1 week")
