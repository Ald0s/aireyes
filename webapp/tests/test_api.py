import os
import time
import json

from datetime import date, datetime, timedelta
from flask import url_for
from tests.conftest import BaseWorkerAPICase, BaseUserAPICase

from app import db, config, models, airvehicles, radarworker, traces, flights, user


class TestUserAPI(BaseUserAPICase):
    def test_user(self):
        # Create a new user.
        new_user = user.create_user("aldos", "password")
        db.session.flush()


class TestRadarWorkerAPI(BaseWorkerAPICase):
    def test_error_report(self):
        # Read all radar workers, get the first one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]

        # Now, authenticate the radar worker, ensure this is a success.
        with self.app.test_client() as client:
            response = client.post(url_for("api.authenticate"),
                data = json.dumps(dict(
                    workerName = radar_worker.name,
                    workerUniqueId = radar_worker.unique_id
                )),
                content_type = "application/json")
            self.assertEqual(response.status_code, 200)
            # We will now perform a full error report to the server.
            error_report_response = client.post(url_for("api.worker_report_error"),
                data = json.dumps(dict(
                    errorCode = "example-error",
                    friendlyDescription = "This is a friendly description of what happened",
                    stackTrace = "STACK TRACE HERE",
                    extraInformation = dict( extra1 = "XO", extra2 = "XA" )
                )),
                content_type = "application/json")
            # Ensure this was successful.
            self.assertEqual(error_report_response.status_code, 200)
            # Now, get from the radar worker's object. Ensure there is a single report.
            self.assertEqual(radar_worker.num_error_reports, 1)
            # Ensure this single report's error code is 'example-error'
            self.assertEqual(radar_worker.error_reports[0].error_code, "example-error")

    def test_worker_only(self):
        # Read all radar workers, get the first one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]

        # First, ensure attempting to access even a workers-only route that does not require login, from an external IP, fails.
        # Create a request toward the master status route.
        with self.app.test_client(override_user_agent = "Firefox/Something") as client:
            response = client.get(url_for("api.master_status"))
            # Ensure we get a 404.
            self.assertEqual(response.status_code, 404)
        # Next, we'll use a valid user agent, but our remote address will be public.
        with self.app.test_client() as client:
            response = client.get(url_for("api.master_status"), environ_base={"REMOTE_ADDR": "115.98.54.100"})
            # Ensure we get a 404.
            self.assertEqual(response.status_code, 404)
        # Finally, if we attempt to access this route with all the valid criteria for a proper radar worker, this should not fail.
        with self.app.test_client() as client:
            response = client.get(url_for("api.master_status"))
            self.assertEqual(response.status_code, 200)

        # This time, if we attempt to query a logged-in only route, even though we're a radar worker, we are not yet authenticated. This should still fail, but with 403.
        with self.app.test_client() as client:
            response = client.post(url_for("api.aircraft"))
            # Ensure we get 403.
            self.assertEqual(response.status_code, 403)
        # Now, authenticate the radar worker, ensure this is a success.
        with self.app.test_client() as client:
            response = client.post(url_for("api.authenticate"),
                data = json.dumps(dict(
                    workerName = radar_worker.name,
                    workerUniqueId = radar_worker.unique_id
                )),
                content_type = "application/json")
            self.assertEqual(response.status_code, 200)
            # We will save the time of last update.
            last_updated_at = radar_worker.last_update
            # Now, we will wait 3 seconds.
            time.sleep(3)
            # We will now send a heartbeat.
            response = client.post(url_for("api.worker_signal", signal = "heartbeat"),
                data = json.dumps(dict(
                    state = "heartbeat"
                )),
                content_type = "application/json")
            self.assertEqual(response.status_code, 200)
            # Now, get the latest last update, and ensure it is greater than the saved last update.
            new_last_updated_at = radar_worker.last_update
            self.assertGreater(new_last_updated_at, last_updated_at)


class TestAircraftAPI(BaseWorkerAPICase):
    """
    From the perspective of a Radar worker.
    """
    '''def test_submit_new_aircraft(self):
        """
        Read JSON from aircrafts_7c68b7.json, this simulates exactly what the worker will generate.
        Grab the first aircraft object.
        Make a POST request to the aircraft route, with this Aircraft JSON object as the request body.
        Ensure we receive back a JSON array with 1708 items in it.
        Ensure each item has synchronised set to True.
        """
        # Read all radar workers, get the second one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        # Read an example aircraft data.
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        self.set_date_today(date(2022, 7, 29))
        # Open test client with this worker.
        with self.app.test_client(user = radar_worker) as client:
            # Make a POST request to the api_aircraft route.
            aircraft_request = client.post(url_for("api.aircraft"),
                data = json.dumps(aircraft_json),
                content_type = "application/json"
            )
            # Ensure request was successful.
            self.assertEqual(aircraft_request.status_code, 200)
            # Ensure we have the aircraft's ICAO in the response.
            aircrafts_result = aircraft_request.json
            self.assertIn(aircraft_json["icao"], aircrafts_result)
            # Get the receipts for this aircraft.
            flight_point_receipts = aircrafts_result[aircraft_json["icao"]]
            # Ensure there are 1708 objects.
            self.assertEqual(len(flight_point_receipts), 1708)
            # Ensure all objects have 'synchronised' attribute set to True.
            for receipt in flight_point_receipts:
                self.assertEqual(receipt["synchronised"], True)

        # Now, ensure actual flight point data is correct right now.
        control = [
            ("bca41ed27106b10714ad6b74b3035a8d", "1887bc077cc504fdbab74d15503247f8",),
            ("4882c2f4f9340f64595f249e72595859", "d135c593dbb911ecc6c0462b81a9d551",),
            ("7f835899314dfad4409712dcded9fcee", "9147de832b15dc45d40da9b7e9b92e7e",)
        ]
        # In sequence, these should match.
        for flight, first_last_points_hash in zip(models.Flight.query.all(), control):
            self.assertEqual(flight.first_point.flight_point_hash, first_last_points_hash[0])
            self.assertEqual(flight.last_point.flight_point_hash, first_last_points_hash[1])

    def test_submit_new_aircraft_update_partials(self):
        """
        Read JSON from aircrafts_7c68b7_t1.json, this simulates exactly what the worker will generate.
        Sub the aircraft's flight points three times; the first with 1000 points, the second with 1200 points, the third with 5 points FROM the 1200th point.
        Make a POST request to the aircraft route, with this Aircraft JSON object as the request body.
        Ensure we receive back a JSON array with 2111 items in it.
        Ensure each item has synchronised set to True.
        """
        # Read all radar workers, get the second one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        # Read an example aircraft data.
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Set day as 2022-07-29
        self.set_date_today(date(2022, 7, 29))
        # Get our three subs; the first 0 -> 1000, second 1000 -> 1200 and the third 1200 -> 1205.
        flight_points_sub1 = aircraft_json["FlightPoints"][:1000]
        self.assertEqual(len(flight_points_sub1), 1000)
        flight_points_sub2 = aircraft_json["FlightPoints"][:1200]
        self.assertEqual(len(flight_points_sub2), 1200)
        flight_points_sub3 = aircraft_json["FlightPoints"][1200:1205]
        self.assertEqual(len(flight_points_sub3), 5)

        # Open test client with this worker.
        with self.app.test_client(user = radar_worker) as client:
            # Set the aircraft json's flight points to sub1.
            aircraft_json["FlightPoints"] = flight_points_sub1
            # Make a POST request to the api_aircraft route.
            aircraft_request = client.post(url_for("api.aircraft"),
                data = json.dumps(aircraft_json),
                content_type = "application/json"
            )
            # Ensure request was successful.
            self.assertEqual(aircraft_request.status_code, 200)
            # Ensure we have the aircraft's ICAO in the response.
            aircrafts_result = aircraft_request.json
            self.assertIn(aircraft_json["icao"], aircrafts_result)
            # Get the receipts for this aircraft.
            flight_point_receipts = aircrafts_result[aircraft_json["icao"]]
            # Ensure there are 1000 objects.
            self.assertEqual(len(flight_point_receipts), 1000)
            # Ensure all objects have 'synchronised' attribute set to True.
            for receipt in flight_point_receipts:
                self.assertEqual(receipt["synchronised"], True)
            # Now, set the aircraft json's flight points to sub2.
            aircraft_json["FlightPoints"] = flight_points_sub2
            # Make a POST request to the api_aircraft route.
            aircraft_request = client.post(url_for("api.aircraft"),
                data = json.dumps(aircraft_json),
                content_type = "application/json"
            )
            # Ensure request was successful.
            self.assertEqual(aircraft_request.status_code, 200)
            # Ensure we have the aircraft's ICAO in the response.
            aircrafts_result = aircraft_request.json
            self.assertIn(aircraft_json["icao"], aircrafts_result)
            # Get the receipts for this aircraft.
            flight_point_receipts = aircrafts_result[aircraft_json["icao"]]
            # Ensure there are 1200 receipts, that is, we get back ALL sent flight points.
            self.assertEqual(len(flight_point_receipts), 1200)
            # Ensure all objects have 'synchronised' attribute set to True.
            for receipt in flight_point_receipts:
                self.assertEqual(receipt["synchronised"], True)
            # Now, set the aircraft json's flight points to sub3.
            aircraft_json["FlightPoints"] = flight_points_sub3
            # Make a POST request to the api_aircraft route.
            aircraft_request = client.post(url_for("api.aircraft"),
                data = json.dumps(aircraft_json),
                content_type = "application/json"
            )
            # Ensure request was successful.
            self.assertEqual(aircraft_request.status_code, 200)
            # Ensure we have the aircraft's ICAO in the response.
            aircrafts_result = aircraft_request.json
            self.assertIn(aircraft_json["icao"], aircrafts_result)
            # Get the receipts for this aircraft.
            flight_point_receipts = aircrafts_result[aircraft_json["icao"]]
            # Ensure there are 5 objects; since we get back all those we sent.
            self.assertEqual(len(flight_point_receipts), 5)
            # Ensure all objects have 'synchronised' attribute set to True.
            for receipt in flight_point_receipts:
                self.assertEqual(receipt["synchronised"], True)
            # Now, ensure actual flight point data is correct right now.
            control = [
                ("bca41ed27106b10714ad6b74b3035a8d", "1887bc077cc504fdbab74d15503247f8",),
                ("4882c2f4f9340f64595f249e72595859", "d135c593dbb911ecc6c0462b81a9d551",),
                ("7f835899314dfad4409712dcded9fcee", "45d63ba44866e2be42c249c1f68748f6",)
            ]
            # In sequence, these should match.
            for flight, first_last_points_hash in zip(models.Flight.query.all(), control):
                self.assertEqual(flight.first_point.flight_point_hash, first_last_points_hash[0])
                self.assertEqual(flight.last_point.flight_point_hash, first_last_points_hash[1])

    def test_aircraft_timeout_reported(self):
        """
        First, import all aircraft. Of those, we will focus on two; 7c68b7 and 7c6bcf.
        For each aircraft, import the following traces;
            aircraft_7c68b7_t1, 7c68b7: there are 3 flights in here; all finish.
            TODO: load something here that does NOT finish.
        Verify this data is correct and loaded.

        Set our current timestamp to the first flight in all flights for 7c68b7 + 100 seconds.
        Make a POST
        Call out to airvehicles module to report a timeout for that aircraft, giving the current timestamp as timeOfReport.
        Ensure we get back 'landing' as our determination.
        """
        # Read all radar workers, get the second one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        # Load 7c68b7.
        aircraft_7c68b7, aircraft_7c68b7_flights = self._import_all_flights("aircraft_7c68b7_t1.json", 3)
        db.session.flush()
        # TODO: the second load.

        # Get all flights from both aircraft, sorted newest first.
        all_flights_7c68b7 = flights.query_flights_from(aircraft_7c68b7, newest_first = True).all()
        self.assertEqual(len(all_flights_7c68b7), 3)
        # TODO: the second load.

        # Calculate our example timestamp.
        current_timestamp = all_flights_7c68b7[0].ends_at+100
        # Set current timestamp.
        self.set_current_timestamp(current_timestamp)

        # Open test client with this worker.
        with self.app.test_client(user = radar_worker) as client:
            # Now, report a timeout for that vehicle, giving our timeOfReport as current timestamp, set last binary update to 100 seconds PRIOR to current timestamp; when the last point was sent.
            # We'll do this with a POST request.
            report_receipt_request = client.post(url_for("api.report_aircraft_timeout", aircraft_icao = aircraft_7c68b7.icao),
                data = json.dumps(dict(
                    aircraftIcao = aircraft_7c68b7.icao,
                    timeOfReport = int(current_timestamp),
                    lastBinaryUpdate = int(current_timestamp-100),
                    currentConfigAircraftTimeout = 60
                )),
                content_type = "application/json"
            )
            # Ensure request was successful.
            self.assertEqual(report_receipt_request.status_code, 200)
            # Get the receipt.
            report_receipt = report_receipt_request.json
            # Ensure our receipt determines this as a 'landing.'
            self.assertEqual(report_receipt["determination"], "landing")

            """
            Locate a second testdata, one that does NOT end in a landing, and complete this test to ensure we get 'hold'
            """
            self.assertEqual(True, False)'''

    def test_request_tracker_targets(self):
        """
        Read in all radar workers.
        Set TARGET_VEHICLES in current config to a predictable list.
        Use one of the tracker workers to submit a request toward the server for its list.
        Ensure this request is successful
        Ensure the details of the response matches the predictable list.
        """
        # Read all radar workers, get the second one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        # Set the target vehicles in config to a small and predictable list.
        '''config.TARGET_VEHICLES = [
            { "icao": "7c4ee8", "name": "POL35", "airport_code": "e8" },
            { "icao": "7c4ef2", "name": "POL30", "airport_code": "f2" },
            { "icao": "7c4ef4", "name": "POL31", "airport_code": "f4" }
        ]'''

        # Open test client with this worker.
        with self.app.test_client(user = radar_worker) as client:
            # Make a POST request for all target vehicles.
            target_vehicles_request = client.get(url_for("api.request_tracker_targets"))
            # Ensure request was successful.
            self.assertEqual(target_vehicles_request.status_code, 200)
            # Get the resulting data.
            target_vehicles = target_vehicles_request.json
            # Ensure there are 4 results.
            self.assertEqual(len(target_vehicles), 4)
            # Ensure an entry has the 'airportCode' key, this ensures serialisation works.
            self.assertIn("airportCode", target_vehicles[0])


class TestComprehensiveSubmissions(BaseWorkerAPICase):
    def test_7c4ef5_t1(self):
        # Read all radar workers, get the second one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        # Read an example aircraft data.
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c4ef4_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Set day as 2022-09-25
        #self.set_date_today(date(2022, 9, 25))
        # TODOODODODODOD
        flight_points_sub1 = aircraft_json["FlightPoints"]

        # Open test client with this worker.
        with self.app.test_client(user = radar_worker) as client:
            # Set the aircraft json's flight points to sub1.
            aircraft_json["FlightPoints"] = flight_points_sub1
            # Make a POST request to the api_aircraft route.
            aircraft_request = client.post(url_for("api.aircraft"),
                data = json.dumps(aircraft_json),
                content_type = "application/json"
            )
            # Ensure request was successful.
            self.assertEqual(aircraft_request.status_code, 200)
            # Ensure we have the aircraft's ICAO in the response.
            aircrafts_result = aircraft_request.json
            self.assertIn(aircraft_json["icao"], aircrafts_result)


class TestPreparedSubmissions(BaseWorkerAPICase):
    def test_t1(self):
        # Read all radar workers, get the second one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        # Read an example aircraft data.
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "prepared_submissions", "1664078768719.json"), "r") as f:
            submission_json = f.read()

        # Open test client with this worker.
        with self.app.test_client(user = radar_worker) as client:
            # Make a POST request to the api_aircraft route.
            aircraft_request = client.post(url_for("api.aircraft"),
                data = submission_json,
                content_type = "application/json"
            )
            # Ensure request was successful.
            self.assertEqual(aircraft_request.status_code, 200)
            # Ensure we have the aircraft's ICAO in the response.
            aircrafts_result = aircraft_request.json

    def test_t2(self):
        # Read all radar workers, get the second one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        # List the files in the following directory, sort by ascending and one by one, read and submit them.
        target_dir = os.path.join(os.getcwd(), config.IMPORTS_DIR, "prepared_submissions", "group-1")
        target_dir_listed = os.listdir(target_dir)
        # Resulting list to contain all JSONs.
        resulting_json_submissions = []
        for target_file in target_dir_listed:
            absolute_target = os.path.join(target_dir, target_file)
            # Now, read the contents.
            with open(absolute_target, "r") as f:
                submission_json = f.read()
                resulting_json_submissions.append(submission_json)
        # Open test client with this worker.
        with self.app.test_client(user = radar_worker) as client:
            # Now for each raw json in the resulting json submissions list, submit each.
            for submission_json in resulting_json_submissions:
                # Make a POST request to the api_aircraft route.
                aircraft_request = client.post(url_for("api.aircraft"),
                    data = submission_json,
                    content_type = "application/json"
                )
                # Ensure request was successful.
                self.assertEqual(aircraft_request.status_code, 200)
                # Ensure we have the aircraft's ICAO in the response.
                aircrafts_result = aircraft_request.json

    def test_t3(self):
        # Read all radar workers, get the second one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        # List the files in the following directory, sort by ascending and one by one, read and submit them.
        target_dir = os.path.join(os.getcwd(), config.IMPORTS_DIR, "prepared_submissions", "group-1")
        target_dir_listed = os.listdir(target_dir)
        # Resulting list to contain all JSONs.
        resulting_json_submissions = []
        for target_file in target_dir_listed:
            absolute_target = os.path.join(target_dir, target_file)
            # Now, read the contents.
            with open(absolute_target, "r") as f:
                submission_json = f.read()
                resulting_json_submissions.append(submission_json)
        # Open test client with this worker.
        with self.app.test_client(user = radar_worker) as client:
            # Now for each raw json in the resulting json submissions list, submit each.
            for submission_json in resulting_json_submissions[1:]:
                # Make a POST request to the api_aircraft route.
                aircraft_request = client.post(url_for("api.aircraft"),
                    data = submission_json,
                    content_type = "application/json"
                )
                # Ensure request was successful.
                self.assertEqual(aircraft_request.status_code, 200)
                # Ensure we have the aircraft's ICAO in the response.
                aircrafts_result = aircraft_request.json

    def test_t3(self):
        """
        Strange issue; this was an error to do with ground speed being out of range (kept getting 1193), fixed by installing code that will
        first check to see if the ground speed for each flight point is outta range, and none it out if so.
        """
        # Read all radar workers, get the second one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        # List the files in the following directory, sort by ascending and one by one, read and submit them.
        target_dir = os.path.join(os.getcwd(), config.IMPORTS_DIR, "prepared_submissions", "group-2")
        target_dir_listed = os.listdir(target_dir)
        # Resulting list to contain all JSONs.
        resulting_json_submissions = []
        for target_file in target_dir_listed:
            absolute_target = os.path.join(target_dir, target_file)
            # Now, read the contents.
            with open(absolute_target, "r") as f:
                submission_json = f.read()
                resulting_json_submissions.append(submission_json)
        # Open test client with this worker.
        with self.app.test_client(user = radar_worker) as client:
            # Now for each raw json in the resulting json submissions list, submit each.
            for submission_json in resulting_json_submissions[1:]:
                # Make a POST request to the api_aircraft route.
                aircraft_request = client.post(url_for("api.aircraft"),
                    data = submission_json,
                    content_type = "application/json"
                )
                # Ensure request was successful.
                self.assertEqual(aircraft_request.status_code, 200)
                # Ensure we have the aircraft's ICAO in the response.
                aircrafts_result = aircraft_request.json

    def test_t4(self):
        """
        Pol32's first flight within this at 05:18 begins with a None position. Ensure we can still locate takeoff airport.
        """
        # We require airports for this.
        airvehicles.read_airports_from(config.AIRPORTS_CONFIG)
        db.session.flush()
        # Read all radar workers, get the second one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        # List the files in the following directory, sort by ascending and one by one, read and submit them.
        target_file = os.path.join(os.getcwd(), config.IMPORTS_DIR, "prepared_submissions", "payload-log-4.json")
        with open(target_file, "r") as f:
            prepared_submissions_obj = json.loads(f.read())
            resulting_json_submissions = prepared_submissions_obj["preparedSubmissions"]
        # Open test client with this worker.
        with self.app.test_client(user = radar_worker) as client:
            # Now for each raw json in the resulting json submissions list, submit each.
            for submission_json in resulting_json_submissions:
                # Make a POST request to the api_aircraft route.
                aircraft_request = client.post(url_for("api.aircraft"),
                    data = json.dumps(submission_json),
                    content_type = "application/json"
                )
                # Ensure request was successful.
                self.assertEqual(aircraft_request.status_code, 200)
                # Ensure we have the aircraft's ICAO in the response.
                aircrafts_result = aircraft_request.json


class TestTraceAPI(BaseWorkerAPICase):
    """
    From the perspective of a Radar worker.
    """
    def test_history_trawler_assign_complete_work(self):
        """
        Read JSON from aircrafts_7c68b7.json, this simulates exactly what the worker will generate.
        """
        # Read all radar workers, get the second one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[2]
        # Read in aircraft + points from this testdata.
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Ensure this day is created.
        traces.ensure_days_created([date(2022, 7, 29)])
        db.session.flush()
        # Load the stored object with AircraftSchema; get back a dict for Aircraft.
        aircraft_d = airvehicles.AircraftSchema().load(aircraft_json)
        # Make the aircraft.
        aircraft = models.Aircraft(**aircraft_d)
        # Add the aircraft to database, and flush.
        db.session.add(aircraft)
        db.session.flush()
        # Now, use the traces module (though this is out of test scope) to report this aircraft-day as history NOT verified!
        traces.report_aircraft_presence(aircraft, date(2022, 7, 29), history_verified = False)
        db.session.flush()

        # Now, mock radar_worker being logged in.
        with self.app.test_client(user = radar_worker) as client:
            # Perform a POST request, with a None aircraft to request work.
            request_work_response = client.post(url_for("api.trace"),
                data = json.dumps(dict(
                    day = None,
                    aircraft = None,
                    intentionallyEmpty = False
                )),
                content_type = "application/json"
            )
            # Ensure we got 200.
            self.assertEqual(request_work_response.status_code, 200)
            # Get our response as JSON.
            response_json = request_work_response.json
            # Ensure command is 'trawl', ensure the target aircraft icao is 7c68b7, ensure the target day is 2022-07-29.
            self.assertEqual(response_json["command"], "trawl")
            self.assertEqual(response_json["requestedTraceHistory"]["targetAircraftIcao"], "7c68b7")
            self.assertEqual(response_json["requestedTraceHistory"]["targetDay"], "2022/07/29")
            # Ensure 0 items in receipts.
            self.assertEqual(len(response_json["receipts"]), 0)

            # Now, we will essentially trawl the request data. Prepare a response for the server and send it.
            send_aircraft_day_trace_response = client.post(url_for("api.trace"),
                data = json.dumps(dict(
                    day = date(2022, 7, 29).isoformat(),
                    aircraft = aircraft_json,
                    intentionallyEmpty = False
                )),
                content_type = "application/json"
            )
            # Ensure request was successful.
            self.assertEqual(send_aircraft_day_trace_response.status_code, 200)
            # Get our response as JSON.
            response_json = send_aircraft_day_trace_response.json
            # Ensure command is now shutdown, as there's no more work.
            self.assertEqual(response_json["command"], "shutdown")
            # Ensure requestedTraceHistory is None.
            self.assertIsNone(response_json["requestedTraceHistory"])
            # Ensure there are 1708 receipt objects.
            self.assertEqual(len(response_json["receipts"]), 1708)
