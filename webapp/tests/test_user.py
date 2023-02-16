import os
import base64
import json
import time
import decimal

from subprocess import Popen, PIPE
from datetime import date, datetime, timedelta

from sqlalchemy import asc, desc, func

from tests.conftest import BaseCase

from app import db, config, models, user


class TestUser(BaseCase):
    def test_create_user(self):
        # Ensure we can successfully create a new user.
        new_user = user.create_user("aldos", "password")
        db.session.flush()
        # Ensure if we try this again, an exception is raised.
        with self.assertRaises(Exception) as e:
            user.create_user("aldos", "password")
