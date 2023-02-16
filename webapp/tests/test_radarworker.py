import os
import base64
import json
import time
import decimal

from subprocess import Popen, PIPE
from datetime import date, datetime, timedelta

from sqlalchemy import asc

from tests.conftest import BaseCase

from app import db, config, models, airvehicles, radarworker, traces, error


class TestWorkerProcess(BaseCase):
    def test_parse_as_start_command(self):
        # Build a fake command line args.
        command_line_args = [
            config.NODE_EXECUTABLE_PATH,
            os.path.join(os.getcwd(), config.WORKER_RELATIVE_PATH, config.WORKER_FILE_NAME),
            "eyJydW5faGVhZGxlc3MiOiB0cnVlLCAic2hvdWxkX3NhdmVfcGF5bG9hZHMiOiBmYWxzZSwgIndvcmtlcl9maWxlbmFtZSI6ICJhaXJleWVzdGVzdGVyLmpzIiwgIm5hbWUiOiAidHVuZ3N0ZW50ZWQiLCAiZW5hYmxlZCI6IHRydWUsICJwcm94eV91cmxfbGlzdCI6IFtdLCAicGhvbmVfaG9tZV91cmwiOiAiaHR0cDovLzEyNy4wLjAuMTo1MDAwIiwgIndvcmtlcl90eXBlIjogImFpcmNyYWZ0LXRyYWNrZXIiLCAidW5pcXVlX2lkIjogIjk3NzFkZDYxYjFhYzkwNmQxMTgxYzYwNTQxMjVjNTU4IiwgInVzZV9wcm94eSI6IGZhbHNlfQ=="
        ]
        # Now, attempt to parse as a start command.
        start_command_d = radarworker.parse_as_start_command(command_line_args)
        # Ensure this worker's name is tungstented and it is an aircraft-tracker.
        self.assertEqual(start_command_d["name"], "tungstented")
        self.assertEqual(start_command_d["worker_type"], "aircraft-tracker")

    def test_execute_workera(self):
        # Read all the radar workers in.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        start_command = radarworker.RadarWorkerStartCommandSchema().dump(radar_worker)
        # Now, convert the dictionary to a JSON string, and base64 encode that string.
        start_command_encoded = base64.b64encode(json.dumps(start_command).encode("utf-8")).decode("utf-8")
        print(start_command_encoded)

    def test_execute_worker(self):
        # Read all the radar workers in.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        # Use radarworker to execute the first radar worker.
        process = radarworker.execute_radar_worker(radar_worker)
        # Ensure this process is running.
        self.assertEqual(process.is_running(), True)
        # Ensure the radar worker is set as initialising in database.
        self.assertEqual(radar_worker.status, radarworker.STATUS_INITIALISING)
        # Ensure the PID matches between radar worker and the process.
        self.assertEqual(process.pid, radar_worker.pid)
        # Shut this worker down, do not reset it.
        radarworker.shutdown_worker(radar_worker, reset = False)
        db.session.flush()
        # Ensure this is shutdown now.
        self.assertEqual(radar_worker.status, radarworker.STATUS_SHUTDOWN)
        # Ensure the process is not running.
        self.assertEqual(process.is_running(), False)

    def test_handle_stuck_worker(self):
        """
        Set WORKER_STUCK_TIMEOUT to 10 seconds.
        Set current datetime to be WORKER_STUCK_TIMEOUT+5 seconds prior to now.
        Set a worker to be in the initialising state.
        Reset current datetime.
        Ensure is_worker_stuck returns True.
        Call shutdown_worker for this radar worker, with reset to True.
        Ensure upon return the worker is in the READY state.
        """
        config.WORKER_STUCK_TIMEOUT = 10
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        # Use the first.
        radar_worker = radar_workers[0]
        # Set current datetime to be back in time.
        self.set_current_timestamp(time.time()-(config.WORKER_STUCK_TIMEOUT+5))
        # Set worker initialising.
        radarworker.worker_initialising(radar_worker)
        db.session.flush()
        # Reset timenow.
        self.reset_current_datetimenow()
        # Now, ensure is_worker_stuck returns True for this worker.
        is_stuck, reason = radarworker.is_worker_stuck(radar_worker)
        self.assertEqual(is_stuck, True)
        # Call force shutdown on this worker.
        radarworker.shutdown_worker(radar_worker)
        db.session.flush()
        # Ensure status for this worker is now shutdown.
        self.assertEqual(radar_worker.status, radarworker.STATUS_SHUTDOWN)

        # Set current datetime to be back in time.
        self.set_current_timestamp(time.time()-(config.WORKER_STUCK_TIMEOUT+5))
        # Now, initialise the worker successfully, to a running position.
        radarworker.worker_initialising(radar_worker)
        db.session.flush()
        radarworker.worker_running(radar_worker)
        db.session.flush()
        # Reset timenow.
        self.reset_current_datetimenow()
        # Now, ensure is_worker_stuck returns True for this worker.
        is_stuck, reason = radarworker.is_worker_stuck(radar_worker)
        self.assertEqual(is_stuck, True)
        # Call shutdown on this worker, this time, reset the worker.
        radarworker.shutdown_worker(radar_worker, reset = True)
        db.session.flush()
        # Ensure status for this worker is now ready, not shutdown.
        self.assertEqual(radar_worker.status, radarworker.STATUS_READY)


class TestRadarWorker(BaseCase):
    def test_radar_worker_signals(self):
        """
        """
        self.assertEqual(True, False)

    def test_radar_worker_error(self):
        # Read all radar workers, we'll use the first.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]

        # Now, create 2 error reports for this worker, by loading them via the error report schema.
        error_report_ds = [
            radarworker.RadarWorkerErrorReportSchema().load(dict(
                errorCode = "example-error"
            )),
            radarworker.RadarWorkerErrorReportSchema().load(dict(
                errorCode = "serious-error",
                friendlyDescription = "This is a friendly description of what happened",
                stackTrace = "STACK TRACE HERE",
                extraInformation = dict( extra1 = "XO", extra2 = "XA" )
            ))
        ]
        # Now, create a radar worker error report type out of each and add to the radar worker.
        for error_report_d in error_report_ds:
            error_report = models.RadarWorkerErrorReport(**error_report_d)
            radar_worker.add_error_report(error_report)
        db.session.flush()
        # Ensure the worker has 2.
        self.assertEqual(radar_worker.num_error_reports, 2)
        # Now, get the second.
        error_report = radar_worker.error_reports[1]
        # Ensure all info matches.
        self.assertEqual(error_report.error_code, "serious-error")
        self.assertEqual(error_report.description, "This is a friendly description of what happened")
        self.assertEqual(error_report.stack_trace, "STACK TRACE HERE")
        self.assertEqual(error_report.extra_information, dict(extra1 = "XO", extra2 = "XA"))

    def test_create_radar_worker(self):
        """
        Test the RadarWorkerSchema's capability of loading a worker configuration.
        """
        # Read the raw JSON from the file.
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "worker.conf"), "r") as f:
            worker_conf = json.loads(f.read())
        # Read all radar workers from this conf.
        radar_workers = radarworker.read_radar_workers_from(conf_json = worker_conf)
        # Now, get the first worker.
        radar_worker = radar_workers[0]
        # Ensure name is 'tungstented'.
        self.assertEqual(radar_worker.name, "tungstented")
        # Ensure not using proxy.
        self.assertEqual(radar_worker.use_proxy, False)
        # Ensure we are running headless.
        self.assertEqual(radar_worker.run_headless, True)
        # Ensure we must not save payloads.
        self.assertEqual(radar_worker.should_save_payloads, False)
        # Ensure our phone home URL is correct.
        self.assertEqual(radar_worker.phone_home_url, "http://127.0.0.1:5000")
        # Ensure worker filename is aireyestester.
        self.assertEqual(radar_worker.worker_filename, "aireyestester.js")

        # Now, update the worker's configuration in read JSON object; use_proxy, phone_home_url.
        worker_conf["workers"][0]["use_proxy"] = True
        worker_conf["workers"][0]["phone_home_url"] = "http://127.0.0.1:6000"
        # Reload from this object once again.
        radar_workers = radarworker.read_radar_workers_from(conf_json = worker_conf)
        db.session.flush()
        # Ensure we are now using proxy, phone home URL is different.
        radar_worker = radar_workers[0]
        self.assertEqual(radar_worker.use_proxy, True)
        self.assertEqual(radar_worker.phone_home_url, "http://127.0.0.1:6000")

        # Ensure that when we verify information on the second worker, it is naturally different due to a diversion from the global configuration.
        radar_worker = radar_workers[1]
        # Ensure name is 'edmund'.
        self.assertEqual(radar_worker.name, "edmund")
        # Ensure using proxy.
        self.assertEqual(radar_worker.use_proxy, True)
        # Ensure we are not running headless.
        self.assertEqual(radar_worker.run_headless, False)
        # Ensure we must save payloads.
        self.assertEqual(radar_worker.should_save_payloads, True)
        # Ensure our phone home URL is correct.
        self.assertEqual(radar_worker.phone_home_url, "http://127.0.0.1:7000")
        # Ensure worker filename is aireyestester.
        self.assertEqual(radar_worker.worker_filename, "aireyestester.js")

    def test_radar_worker_history_assign_complete_work(self):
        """
        Test the functionality of assigning, editing, completing and cancelling work assigned to a radar worker.
        Read all radar workers from configuration.
        We will now simulate an aircraft-tracker reporting in a tracked aircraft on a particular day.
        Read in a single aircraft along with all its points.
        Now, add the aircraft to the database and perform a report that this aircraft, on this day, has not had its history verified.
        Then, use the traces module to assign this history worker some work. This should be successful.
        The worker should now have 1 assigned work.
        Attempting to further assign work (with multiple_assignments_allowed set True) should result in a NoAssignableWorkLeft error.
        Get the targeted aircraft day, ensure it is not None, and its assigned_work attr is not None.
        Shutdown the radar worker.
        Ensure the aircraft day's assigned_work attr is now None.
        Ensure the radar worker now has 0 work to complete.
        Initialise & run the worker once again.
        Assign work again. Then, attempt to assign work again, this time with multiple_assignments_allowed set False, we should just get back the same work.
        Ensure both instances of assigned work match, and ensure worker has only 1 assigned work.

        Now, we'll handle response from worker.
        Create an AircraftDayTraceHistorySchema describing the current aircraft, day.
        Invoke traces module to submit this trace history.
        Ensure aircraft we get back is not None, ensure day is not None, and ensure there are 2111 flight points returned.
        Ensure aircraft day's history_verified attr is now True, and its assigned_worker is None.
        Ensure worker's num_assigned_aircraft_day_work is 0 as they have completed the work.
        """
        # Read all radar workers, get the second one.
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[1]
        # Read in aircraft + points from this testdata.
        with open(os.path.join(os.getcwd(), config.TESTDATA_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
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
        # Ensure the radar worker has been assigned no work.
        self.assertEqual(radar_worker.num_assigned_aircraft_day_work, 0)
        # Now, use the traces module to assign this radar worker an aircraft-day.
        trace_history_work_d = traces.assign_trace_history_work(radar_worker, multiple_assignments_allowed = True)
        db.session.flush()
        # Ensure target aircraft is that aircraft above.
        self.assertEqual(trace_history_work_d["target_aircraft_icao"], aircraft.icao)
        # Ensure target day is 2022-7-29.
        self.assertEqual(trace_history_work_d["target_day"], date(2022, 7, 29))
        # Set the radar worker to initialising, then to running.
        radar_worker = radarworker.worker_initialising(radar_worker)
        db.session.flush()
        radar_worker = radarworker.worker_running(radar_worker)
        db.session.commit()
        # Now, ensure the radar worker has 1 assigned bit of work.
        self.assertEqual(radar_worker.num_assigned_aircraft_day_work, 1)
        # Now, if we attempt to be assigned further work, expect NoAssignableWorkLeft to be raised.
        with self.assertRaises(error.NoAssignableWorkLeft) as e:
            traces.assign_trace_history_work(radar_worker, multiple_assignments_allowed = True)
        # Get an association between this aircraft and this day; ensure the assigned worker is not None.
        aircraft_day = models.AircraftPresentDay.find(aircraft.icao, date(2022, 7, 29))
        # Ensure not None.
        self.assertIsNotNone(aircraft_day)
        # Now, ensure that the assigned worker is not None either.
        self.assertIsNotNone(aircraft_day.assigned_worker)
        # Now, shutdown the worker. This action should result in all assignments being dropped.
        radar_worker = radarworker.worker_shutdown(radar_worker)
        db.session.commit()
        # Ensure the aircraft day now does not have an assigned worker.
        self.assertIsNone(aircraft_day.assigned_worker)
        # Ensure the radar worker has 0 works to complete.
        self.assertEqual(radar_worker.num_assigned_aircraft_day_work, 0)
        # Set the radar worker to initialising, then to running.
        radar_worker = radarworker.worker_initialising(radar_worker)
        db.session.flush()
        radar_worker = radarworker.worker_running(radar_worker)
        db.session.flush()
        # Now, use the traces module to assign this radar worker an aircraft-day.
        trace_history_work_d = traces.assign_trace_history_work(radar_worker)
        db.session.commit()
        # Ensure that, if we now request more work, we'll get back the same assignment.
        trace_history_work_d_1 = traces.assign_trace_history_work(radar_worker)
        self.assertEqual(trace_history_work_d["target_aircraft_icao"], trace_history_work_d_1["target_aircraft_icao"])
        self.assertEqual(trace_history_work_d["target_day"], trace_history_work_d_1["target_day"])
        # Now, ensure the radar worker still has 1 assigned bit of work.
        self.assertEqual(radar_worker.num_assigned_aircraft_day_work, 1)

        # Now we have our assigned work, and it'll be sent to the worker for completion, lets just say that the worker has now sent back the trace for that day.
        # A report of trace history is in the form of the AircraftDayTraceHistorySchema, so initialise one, and in this case, we'll just provide the aircraft_d we read earlier.
        aircraft_day_trace_history = traces.AircraftDayTraceHistorySchema().load(dict(
            day = date(2022, 7, 29).isoformat(),
            aircraft = aircraft_json,
            intentionallyEmpty = False
        ))
        # Now, report this via the traces module.
        aircraft, day, flight_points = traces.aircraft_trace_history_submitted(radar_worker, aircraft_day_trace_history)
        db.session.commit()
        # Ensure aircraft, day not none.
        self.assertIsNotNone(aircraft)
        self.assertIsNotNone(day)
        # Ensure there are 1708 flight points.
        self.assertEqual(len(flight_points), 1708)
        # Ensure the worker now has 0 assigned work.
        self.assertEqual(radar_worker.num_assigned_aircraft_day_work, 0)
        # Ensure the aircraft day has history_verified set to True, and also that it has a None assigned worker.
        self.assertEqual(aircraft_day.history_verified, True)
        self.assertIsNone(aircraft_day.assigned_worker)

        # Ensure that, if we try and assign the worker more work now, even multiple_assignments_allowed set True, a NoAssignableWorkLeft is raised.
        with self.assertRaises(error.NoAssignableWorkLeft) as e:
            traces.assign_trace_history_work(radar_worker, multiple_assignments_allowed = True)

    def test_manage_radar_worker(self):
        """
        Test radarworker's capability for managing a worker's state. Upon shutdown, the previous run's configuration must not be cleared. This should only be cleared
        when the radar worker is being re-initialised.

        By default, the RadarWorker must be in a READY status.
        Use the radarworker module to initialise the RadarWorker.
        Ensure the worker is now in an INITIALISING status.
        Now run the worker. Ensure the worker is in a RUNNING status.
        Ensure we can run the worker again without any issue again.
        Shutdown the worker. Ensure worker is in a SHUTDOWN status.
        Ensure we can shut the worker down again without any issue.
        Set the worker initialising. Ensure worker is in an INITIALISING status.
        Ensure we can shut the worker down from this status. Ensure worker is in a SHUTDOWN status.
        """
        radar_workers = radarworker.read_radar_workers_from("worker.conf")
        radar_worker = radar_workers[0]
        # Ensure the radarworker is ready.
        self.assertEqual(radar_worker.status_str, "ready")
        # Now, use the radarworker to initialise the worker.
        radar_worker = radarworker.worker_initialising(radar_worker)
        db.session.flush()
        # Status should be initialising.
        self.assertEqual(radar_worker.status_str, "initialising")
        # Ensure we can call worker_initialising again without any issue.
        radarworker.worker_initialising(radar_worker)
        # Now, use radarworker module to set the worker to a running position.
        radarworker.worker_running(radar_worker)
        db.session.flush()
        # Status should be running.
        self.assertEqual(radar_worker.status_str, "running")
        # Ensure we can call worker_running again without any issue.
        radarworker.worker_running(radar_worker)
        # Now use radarworker module to set the worker to a shutdown position.
        radarworker.worker_shutdown(radar_worker)
        db.session.flush()
        # Status should be shutdown.
        self.assertEqual(radar_worker.status_str, "shutdown")
        # Ensure we can call worker_shutdown again without any issue.
        radarworker.worker_shutdown(radar_worker)
        # Now, ensure we can call worker_initialising once again.
        radar_worker = radarworker.worker_initialising(radar_worker)
        db.session.flush()
        # Status should be initialising.
        self.assertEqual(radar_worker.status_str, "initialising")
        # Use radarworker to shutdown the worker. It should succeed, though worker has an initialising status.
        radarworker.worker_shutdown(radar_worker)
        db.session.flush()
        # Status should be shutdown.
        self.assertEqual(radar_worker.status_str, "shutdown")
