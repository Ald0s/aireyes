import os
import re
import json
import time
import logging
import numpy
from datetime import date, datetime

from flask import request, render_template, redirect, flash, url_for, send_from_directory, abort, jsonify, make_response
from flask_socketio import emit
from flask_login import login_required, current_user, login_user, logout_user
from marshmallow import Schema, fields, validate, ValidationError, post_load, pre_load

from .. import db, config, login_manager, socketio, models, airvehicles, flights, traces, radarworker, error, decorators, geospatial, viewmodel

from . import api

LOG = logging.getLogger("aireyes.api.routes")
LOG.setLevel( logging.DEBUG )


@api.route("/api/suburbs", methods = [ "GET" ])
def suburbs():
    """
    A public API route that allows on-demand fetching of GeoJSON information for suburbs within a bounding box extent.
    Within query string arguments, srsname must be provided, along with the client's bounding box under bbox. This function will then, on the basis of
    the supplied bounding box, reply with a GeoJSON collection of suburbs that apply, configured to be coloured appropriately.
    """
    try:
        srs_name = request.args.get("srsname", None)
        bbox = request.args.get("bbox", None)
        zoom = request.args.get("zoom", None)
        show_aircraft = request.args.get("aircraft", "all")
        """
        TODO 0x08
        Please provide a lot of validation to these arguments.
        """
        # Ensure we were supplied the appropriate arguments.
        if not srs_name or not bbox or not zoom:
            LOG.error(f"Failed to query suburbs for client; they did not supply the correct arguments.")
            """
            TODO: proper error
            """
            raise Exception("args")
        # Process bbox into an array, floor zoom and convert to an int, convert aircraft to a list of flight names.
        bounding_box_extent = [ float(x) for x in bbox.split(",")[0:4] ]
        zoom = int(numpy.floor(float(zoom)))
        if show_aircraft == "none":
            # If this is 'none', the user as requested none.
            show_aircraft = []
        elif show_aircraft == "all":
            pass
        else:
            show_aircraft = show_aircraft.split(",")
        # Now, prepare to query suburbs.
        LOG.debug(f"User querying suburbs with SRS {srs_name}, bbox {bbox} and zoom level {zoom} ...")
        # With the client's SRS and bounding box, find all suburbs we must send to the client.
        all_suburbs_geojson = geospatial.geojson_suburbs_within_view(srs_name, bounding_box_extent, zoom,
            show_only_aircraft = show_aircraft)
        return make_response(all_suburbs_geojson, 200, { "Content-Type": "application/json" })
    except Exception as e:
        raise e


@api.route("/api/worker/authenticate", methods = [ "POST" ])
@decorators.workers_only(require_logged_in = False)
def authenticate():
    """
    Here, we'll authenticate the incoming worker. The request body should be JSON, containing the worker's name. We'll simply get the name
    then set the worker as active, in production.
    """
    try:
        body_json = request.json
        worker_name = body_json.get("workerName", None)
        worker_unique_id = body_json.get("workerUniqueId", None)
        """TODO: ensure request comes from 127.0.0.1"""
        # Locate worker based on this unique ID.
        radar_worker = db.session.query(models.RadarWorker)\
            .filter(models.RadarWorker.unique_id == worker_unique_id)\
            .first()
        # If worker can't be found, raise an exception.
        if not radar_worker:
            LOG.error(f"Failed to find worker with ID {worker_unique_id}, and reported name {worker_name}; this combination doesn't exist!")
            raise Exception("no-worker")
        # Otherwise, log the worker in!
        LOG.debug(f"Radar worker {worker_name} ({worker_unique_id}) has reported in!")
        login_user(radar_worker)
        radar_worker.reset_status_attrs()
        radarworker.worker_initialising(radar_worker)
        db.session.commit()
        return "OK", 200
    except Exception as e:
        raise e


@api.route("/api/worker/master", methods = [ "GET" ])
@decorators.workers_only(require_logged_in = False)
def master_status():
    """
    Prior to running a worker execution pass, management code will first query this route to determine whether the master server
    is alive and running. This route will simply respond with OK 200 if so.
    """
    try:
        return "OK", 200
    except Exception as e:
        raise e


@api.route("/api/worker/aircraft", methods = [ "POST" ])
@decorators.workers_only(require_logged_in = True)
def aircraft():
    """
    Either a single Aircraft object is incoming, or a list of them is incoming. Either way, the incoming data will be aggregated to a list, then processed one by one.
    Each Aircraft object is sent by the aircraft-tracker type radar worker when it has locked onto an aircraft and extracted its recent trace data, or when an aircraft
    currently being tracked has recorded more flight data. This means the size of these requests can vary greatly. This route will first extract the aircraft model &
    flight points from the request, and ensure all this data is currently synchronised with the database.

    This route will then access the flights module to submit whatever new flight data has been sent. This will handle the creation or updating of any relevant Flight models.
    This route responds by sending back a dictionary containing a list of flight point receipts for each aircraft; which contain identifying information for the flight points
    that have been accepted, informing the radar worker that the submitted information does not need to be resubmitted.
    """
    try:
        # A master dictionary for resulting receipt information. This will map an aircraft icao to (at the moment) a list of flight point receipt.
        aircraft_flight_points_receipts = {}
        aircraft_list_json = request.json
        # If type is not list, make a list.
        if not isinstance(aircraft_list_json, list):
            aircraft_list_json = [aircraft_list_json]
        for aircraft_json in aircraft_list_json:
            # With this aircraft JSON, we will load an Aircraft schema and all its FlightPoints.
            try:
                # We will attempt to load the aircraft schema, but, this may fail sometimes.
                aircraft_d = airvehicles.AircraftSchema().load(aircraft_json)
            except ValidationError as ve:
                # Raise a schema validation fail for the aircraft.
                raise error.SchemaValidationFail("aircraftschema", aircraft_json)
            # Give traces an opportunity to ensure all referenced Days are created on the server. In a production environment, this is helpful if the Day ticks over, as check-days only runs at init.
            traces.ensure_days_created_from_aircraft(aircraft_d)
            # Inform system via airvehicles. We'll also specify that we only want new flight points returned after submission, to take some of the weight off our flights module.
            aircraft, new_flight_points, synchronised_flight_points = airvehicles.aircraft_submitted(aircraft_d)
            LOG.debug(f"Successfully parsed update from {aircraft}, {len(new_flight_points)} points never existed. {len(synchronised_flight_points)} points are unsynchronised on the worker.")
            # We must get a day to associate these flight points with. For now, use the latest flight point in new_flight_points.
            if len(new_flight_points) > 0:
                day = new_flight_points[len(new_flight_points)-1].day_day
                LOG.debug(f"From submitted points, we will use {day} as the anchoring day.")
            else:
                day = None
                LOG.warning(f"Unable to locate a day to anchor latest flight point submission to!")
            # Report this aircraft as being active on this day, history & flights verified false though; since a flight tracker worker is reporting it.
            # We'll supply this call with the anchor day we've determined above. If this is None, it will be later generated.
            aircraft_day, was_created = traces.report_aircraft_presence(aircraft, day,
                history_verified = False, flights_verified = False)
            # Now, if we should, submit the new flight points as a partial flight to the flights module to create/update flights.
            if config.SHOULD_SUBMIT_PARTIAL_FLIGHTS:
                try:
                    """
                    TODO: for a non-blocking strategy, we can enqueue the call to flight_partial_submitted to be executed on a background worker instead.
                    This may be beneficial. Something to consider.
                    """
                    # Call out to flights module to inform this partial has just been submitted on this day.
                    submission_receipt = flights.flight_partial_submitted(aircraft, aircraft_day.day_day, new_flight_points, radar_worker = current_user)
                    flight = submission_receipt.flight
                    LOG.debug(f"Submitted flight data from this update to {flight}! (created={submission_receipt.was_created})")
                except error.InsufficientPartialFlightError as ipfe:
                    LOG.warning(f"Did not submit new flight points for {aircraft} on {aircraft_day.day_day} to revision, this aircraft does not have enough points on this day yet.")
                    flight = None
                except error.NoFlightPointsToAssimilateError as nfptae:
                    # Submission did not contain any new flight points, no flights were updated.
                    LOG.warning(f"Did not update any flights for {aircraft} - no new flight points were detected.")
                    flight = None
                except error.NoPartialFlightFoundForSubmission as npfffs:
                    LOG.warning(f"Did not update any flights for {aircraft} - no flight partials were calculated.")
                    flight = None
                except error.NoFlightsAssimilatedError as nfae:
                    # An assimilation error occurred. Get the reason and print an output based on that.
                    error_code = nfae.error_code
                    if error_code == "zero-created-updated":
                        LOG.warning(f"Did not assimilate any flights for {aircraft} - created+updated is 0. Perhaps a flight given is too small?")
                    else:
                        LOG.warning(f"Did not assimilate any flights for {aircraft} - no good reason given.")
                    flight = None
            # Commit to database.
            db.session.commit()
            # Now, if we should, submit a socket update for both this aircraft has a whole and for the flight we updated, if 'flight' isn't None.
            if config.SHOULD_SEND_SOCKETIO_UPDATES:
                """
                TODO: submit SocketIO aircraft update
                TODO: submit SocketIO flight update
                """
                # Now that we've committed, serialise the aircraft as AircraftViewModelSchema.
                aircraft_summary = viewmodel.AircraftViewModelSchema().dump(aircraft)
                emit("aircraft-update", aircraft_summary, to = config.SOCKETIO_ROOM_AIRCRAFT_REALTIME, namespace = "/aircraft")
            # We will serialise each FlightPoint with our receipt schema.
            receipts = [ airvehicles.FlightPointReceiptSchema().dump(flight_point) for flight_point in synchronised_flight_points ]
            LOG.debug(f"Received {len(receipts)} new points for {aircraft.flight_name} from this update.")
            # Now, add this list of receipts, under the aircraft's icao, to our resulting dictionary.
            aircraft_flight_points_receipts[aircraft.icao] = receipts
        # Finally, return this resulting dictionary.
        return aircraft_flight_points_receipts, 200
    except Exception as e:
        raise e


@api.route("/api/worker/targets", methods = [ "GET" ])
@decorators.workers_only(require_logged_in = True)
@decorators.get_master()
def request_tracker_targets(master, **kwargs):
    """
    Requests that the server provide the currently configured list of vehicle targets for the tracker. This route will serialise the TARGET_VEHICLES dictionary
    in the current configuration and reply with that.
    """
    try:
        # Create a new array of serialised target vehicle dictionaries, via the TargetVehicleSchema.
        LOG.debug(f"{current_user} is requesting tracker targets, replying with {master.num_tracked_aircraft} vehicles...")
        tracker_targets = [airvehicles.TargetVehicleSchema().dump(target_vehicle) for target_vehicle in master.tracked_aircraft]
        tracker_targets_json = jsonify(tracker_targets)
        return make_response(tracker_targets_json, 200, { "Content-Type": "application/json" })
    except Exception as e:
        raise e


@api.route("/api/worker/aircraft/<aircraft_icao>/timeout", methods = [ "POST" ])
@decorators.workers_only(require_logged_in = True)
@decorators.get_aircraft()
def report_aircraft_timeout(aircraft, **kwargs):
    """
    A route for handling the timing out of an aircraft. A time out can result from many things relevant to us; the aircraft has landed, network has dropped out,
    the aircraft can no longer be accurately tracked. It is up to this route to determine what course of action the worker should take. Generally, if the flight
    reports that the aircraft has landed, we can safely assume this was a landing. Whereas, a general disappearance may result in a hold-out style response.

    The request body should be JSON, and should be in the form of AircraftTimeoutReportSchema. The response body will also be a JSON object in the form of
    AircraftTimeoutResponseSchema.
    """
    try:
        # Get the body as AircraftTimeoutReportSchema.
        aircraft_timeout_report = airvehicles.AircraftTimeoutReportSchema().load(request.json)
        # Attempt to determine why this happened.
        report_receipt = airvehicles.aircraft_timeout_reported(aircraft, aircraft_timeout_report)
        # Check determination. If this was a landing,; if it was, and we must send socketio updates, send an update to the aircraft.
        if report_receipt.determination == "landing" and config.SHOULD_SEND_SOCKETIO_UPDATES:
            """
            TODO: submit SocketIO aircraft update
            TODO: submit SocketIO flight update
            """
            # Now that we've committed, serialise the aircraft as AircraftViewModelSchema.
            aircraft_summary = viewmodel.AircraftViewModelSchema().dump(aircraft)
            emit("aircraft-landed", aircraft_summary, to = config.SOCKETIO_ROOM_AIRCRAFT_REALTIME, namespace = "/aircraft")
        # Serialise to a response type.
        return airvehicles.AircraftTimeoutResponseSchema().dump(report_receipt), 200
    except Exception as e:
        raise e


@api.route("/api/worker/trace", methods = [ "POST" ])
@decorators.workers_only(require_logged_in = True)
def trace():
    """
    A route that will handle the receiving of history trace data for a particular aircraft. It's expected that the body
    be JSON of type AircraftDayTraceSchema. This route will return an AircraftDayTraceResponseSchema, representing a new
    command for the worker.
    """
    try:
        # Attempt to read JSON from request body, and load it as an AircraftDayTraceSchema.
        try:
            aircraft_day_trace_json = request.json
            aircraft_day_trace_d = traces.AircraftDayTraceHistorySchema().load(aircraft_day_trace_json)
            # Command traces to create all dates associated with this flight log. We'll only do this if we were given an aircraft.
            if aircraft_day_trace_d["aircraft"]:
                traces.ensure_days_created_from_aircraft(aircraft_day_trace_d["aircraft"])
            # We were able to successfully load this trace. We'll therefore invoke the airvehicles module to handle the historical trace.
            aircraft, day, synchronised_flight_points = traces.aircraft_trace_history_submitted(current_user, aircraft_day_trace_d)
            LOG.debug(f"Successfully received trace history from {str(aircraft)} on {day.isoformat()}, verifying this aircraft's presence on this day...")
            # Report this aircraft as being active on this day, verified true this time!
            traces.report_aircraft_presence(aircraft, day,
                history_verified = True)
            # Commit to database.
            db.session.commit()
            # Now, build a receipts list.
            receipts = [ airvehicles.FlightPointReceiptSchema().dump(flight_point) for flight_point in synchronised_flight_points ]
        except error.RequestWorkError as rwe:
            # If we have a RequestWorkError raised, this could mean the worker is requesting new work.
            LOG.warning(f"{current_user} is requesting history trawling work...")
            # By default, create an empty receipts list.
            receipts = []
        except ValidationError as ve:
            # Just get this handled by our ValidationError handler.
            raise ve
        # Construct a dictionary, compatible with AircraftDayTraceResponseSchema, set at least our receipts attribute, which will be processed by
        # the worker irrespective of the command we're about to give.
        trace_response_d = dict(
            command = None,
            receipts = receipts,
            requested_trace_history = None
        )
        # Now, we'll assign further work to the worker. Invoke traces module to assign.
        try:
            assigned_work_d = traces.assign_trace_history_work(current_user)
            # If we have managed to find more work, set response's command to 'trawl', and set the requested trace history attribute.
            trace_response_d["command"] = "trawl"
            trace_response_d["requested_trace_history"] = assigned_work_d
            # Commit to database.
            db.session.commit()
        except error.NoAssignableWorkLeft as nawl:
            # No work found. Set command to 'shutdown'
            LOG.warning(f"Found no further work to assign to radar worker {current_user}, shutting them down.")
            trace_response_d["command"] = "shutdown"
        # Now, dump our trace response dictionary.
        return traces.AircraftDayTraceResponseSchema().dump(trace_response_d), 200
    except Exception as e:
        raise e


@api.route("/api/worker/update/<signal>", methods = [ "POST" ])
@decorators.workers_only(require_logged_in = True)
def worker_signal(signal):
    """
    Signals an update to the state of a Radar worker. The request body is expected to be JSON, containing a
    single RadarWorkerSignalBaseSchema derivative.
    """
    try:
        # Get request body as JSON.
        signal_json = request.json
        # Based on the 'signal' attribute given here, we will utilise a different signal schema.
        if signal == "initialised":
            # Load as an initialised signal.
            signal_d = radarworker.RadarWorkerSignalInitialisedSchema().load(signal_json)
        elif signal == "heartbeat":
            # Load as a heartbeat signal.
            signal_d = radarworker.RadarWorkerSignalHeartbeatSchema().load(signal_json)
        elif signal == "shutdown":
            # Load as a shutdown signal.
            signal_d = radarworker.RadarWorkerSignalShutdownSchema().load(signal_json)
        else:
            LOG.error(f"Invalid signal type received from worker {current_user}; {signal}")
            raise NotImplementedError()
        # Invoke radarworker module to perform this update.
        radar_worker = radarworker.worker_signal_received(current_user, signal_d)
        # Simply commit, and return OK, 200.
        db.session.commit()
        return "OK", 200
    except Exception as e:
        raise e


@api.route("/api/worker/error", methods = [ "POST" ])
@decorators.workers_only(require_logged_in = True)
def worker_report_error():
    """
    Allows a worker to report an error they've encountered. All error information should be passed as JSON text in the
    body of the request.
    """
    try:
        # Get request body as JSON.
        error_report_json = request.json
        # Get an error report object from this.
        error_report_d = radarworker.RadarWorkerErrorReportSchema().load(error_report_json)
        error_report = models.RadarWorkerErrorReport(**error_report_d)
        # Finally, add this error report to the worker.
        current_user.add_error_report(error_report)
        # Simply commit, and return OK, 200.
        db.session.commit()
        return "OK", 200
    except Exception as e:
        raise e


@api.errorhandler(ValidationError)
def validation_error(e):
    """
    Validation of a schema has failed - this is the general validation error handler.
    """
    LOG.error(f"Request encountered a ValidationError")
    output_error_filename = f"validation-{int(time.time())}.json"
    with open(os.path.join(os.getcwd(), config.ERRORS_DIR, output_error_filename), "w") as w:
        w.write(str(e))
    raise NotImplementedError()


@api.errorhandler(error.InvalidCRSError)
def invalid_crs_error(e):
    """
    Handle the printing of an invalid CRS error.
    This should create a report explaining what happened, along with all flight points involved in the error case.
    """
    raise NotImplementedError(f"Request encountered a InvalidCRSError.")


@api.errorhandler(error.SchemaValidationFail)
def schema_validation_fail(e):
    """
    Validation of a schema has failed.
    We will write this to the errors directory.
    """
    LOG.error(f"Request encountered a SchemaValidationFail of type {e.schema_type_name}")
    output_error_filename = f"{e.schema_type_name}-{int(time.time())}.json"
    with open(os.path.join(os.getcwd(), config.ERRORS_DIR, output_error_filename), "w") as w:
        w.write(json.dumps(e.original_source_json, indent = 4))
    raise NotImplementedError()


@api.errorhandler(error.PageEvaluationFail)
def page_evaluation_fail(e):
    """
    """
    LOG.error(f"Request encountered a PageEvaluationFail upon function identifier {e.function_identifier}")
    """
    TODO: log this properly.
    """
    raise NotImplementedError()


@api.errorhandler(Exception)
def handle_exception(e):
    """
    An API exception handler for ALL uncaught exceptions.
    """
    # Otherwise, its some other exception that's unhandled. We'll log this, then force the User to logout.
    LOG.error(f"Handle exception called for {e}, this type is not yet supported!")
    LOG.error(e, exc_info = True)
    return NotImplementedError()


@api.errorhandler(404)
def handle_not_found(e):
    """
    Handle a not found error.
    """
    """TODO: properly log"""
    raise NotImplementedError()
