import os
import time
import json
import flask_socketio

from datetime import date, datetime, timedelta
from flask import url_for
from tests.conftest import BaseSocketIOCase

from app import db, config, models, socketio, airvehicles, radarworker


class TestSocketIO(BaseSocketIOCase):
    def test_connect_aircraft(self):
        """
        Ensure we can connect to the aircraft namespace.
        """
        client = socketio.test_client(self.app, namespace = "/aircraft")
        client.connect()
        # Ensure we are connected to aircraft.
        self.assertTrue(client.is_connected())

    def test_cant_connect_radar_worker(self):
        """
        Ensure the security mechanisms protecting the radar worker namespace are operating correctly.
        Ensure a connecton is impossible if no user agent or login status is given.
        Ensure a connection is impossible if a user agent is given, but no login status is given.
        Ensure a connection is finally possible if the above two criteria are satisfied.
        """
        # Read all radar workers, get the first one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        # Ensure this fails if we try a connection with a socketio client with nothing special going on.
        client = socketio.test_client(self.app, namespace = "/worker")
        self.assertFalse(client.is_connected(namespace = "/worker"))
        # Ensure this fails if we try a connection with a valid user agent, but we're not logged in.
        client = socketio.test_client(self.app, namespace = "/worker", headers = { "user-agent": "aireyes/slave" })
        self.assertFalse(client.is_connected(namespace = "/worker"))
        # Ensure this finally succeeds if we try a connection with a valid user agent and we're logged in as a radar worker.
        with self.app.test_client(user = radar_worker) as flask_client:
            # We'll first authenticate the worker.
            response = flask_client.post(url_for("api.authenticate"),
                data = json.dumps(dict(
                    workerName = radar_worker.name,
                    workerUniqueId = radar_worker.unique_id
                )),
                content_type = "application/json")
            # Next, attempt to access protected socket handler.
            client = socketio.test_client(self.app,
                flask_test_client = flask_client, namespace = "/worker", headers = { "user-agent": "aireyes/slave" })
            self.assertTrue(client.is_connected(namespace = "/worker"))

    def test_join_realtime_aircraft(self):
        """
        Test instance & expression level results for all aggregate Flight functions, we'll use 2 aircraft with valid flight information, and the rest of the
        known 5 we'll use a control to ensure cartesian products are dealt with.

        First, import all aircraft. Of those, we will focus on two; 7c68b7 and 7c6bcf.
        For each aircraft, import the following traces;
            aircraft_7c68b7_t1, 7c68b7: there are 3 flights in here.
            aircraft_7c4ee8_t1, 7c4ee8, there are 2 flights in here.
        Verify this data is correct and loaded.

        Simulate us viewing the aircraft index page. So we will emit the aircraft_realtime event.
        We should get back an event with name 'aircraft-summary'
        There should be 1 arg contained within the event.
        The argument should be a list, and it should have 5 items.
        """
        # Import all aircraft.
        aircraft = airvehicles.read_aircraft_from(config.KNOWN_AIRCRAFT_CONFIG)
        db.session.flush()
        # Load 7c68b7.
        aircraft_7c68b7, aircraft_7c68b7_flights = self._import_all_flights("aircraft_7c68b7_t1.json", 3)
        db.session.flush()
        # Load 7c4ee8.
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.commit()

        client = socketio.test_client(self.app, namespace = "/aircraft")
        client.connect()
        # Ensure we are connected to aircraft.
        self.assertTrue(client.is_connected())
        # Request to join aircraft realtime room.
        client.get_received(namespace = "/aircraft")
        client.emit("aircraft_realtime", namespace = "/aircraft")
        received = client.get_received(namespace = "/aircraft")
        # Ensure we got one response.
        self.assertEqual(len(received), 1)
        received_event = received[0]
        # Ensure received event's name is 'aircraft-summary'
        self.assertEqual(received_event["name"], "aircraft-summary")
        # Get the very first argument. This should be an array.
        aircraft_summaries = received_event["args"][0]
        self.assertIsInstance(aircraft_summaries, list)
        # Ensure there are 5 entries.
        self.assertEqual(len(aircraft_summaries), 5)
