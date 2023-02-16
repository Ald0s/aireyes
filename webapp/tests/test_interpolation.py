import os
import base64
import json
import time
import decimal

from subprocess import Popen, PIPE
from datetime import date, datetime, timedelta

from sqlalchemy import asc, desc, func

from tests.conftest import BaseCase

from app import db, config, models, error, interpolation


class TestInterpolation(BaseCase):
    def test_prepare_flight_points(self):
        pass
