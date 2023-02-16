"""
A module for reading, updating and managing Radar worker configuration.
"""
import re
import os
import time
import uuid
import logging
import signal
import base64
import json
import psutil
from subprocess import Popen, DEVNULL
from datetime import datetime, date, timedelta

from flask import g
from marshmallow import Schema, fields, EXCLUDE, post_load, pre_load, ValidationError

from . import db, config, models, error

LOG = logging.getLogger("aireyes.radarworker")
LOG.setLevel( logging.DEBUG )

STATUS_READY = models.RadarWorker.STATUS_READY
STATUS_INITIALISING = models.RadarWorker.STATUS_INITIALISING
STATUS_RUNNING = models.RadarWorker.STATUS_RUNNING
STATUS_SHUTDOWN = models.RadarWorker.STATUS_SHUTDOWN
STATUS_ERROR = models.RadarWorker.STATUS_ERROR
STATUS_UNKNOWN = models.RadarWorker.STATUS_UNKNOWN


class RadarWorkerErrorReportSchema(Schema):
    """A schema for defining the structure for an error report from a radar worker."""
    error_code              = fields.Str(data_key = "errorCode")
    description             = fields.Str(allow_none = True, required = False, load_default = None, data_key = "friendlyDescription")
    stack_trace             = fields.Str(allow_none = True, required = False, load_default = None, data_key = "stackTrace")
    extra_information       = fields.Dict(keys = fields.Str(), values = fields.Str(), allow_none = True, required = False, load_default = dict(), data_key = "extraInformation")


class RadarWorkerSignalBaseSchema(Schema):
    """
    A schema for loading radar worker state updates.
    This is the base, and only presents a 'state' attribute, which should always equal the signal sent alongside the request.
    """
    state                   = fields.Str()


class RadarWorkerSignalInitialisedSchema(RadarWorkerSignalBaseSchema):
    """
    A schema for loading radar worker 'initialised' updates.
    This will essentially inform the server that the worker is ready to accept work.
    """
    pass


class RadarWorkerSignalHeartbeatSchema(RadarWorkerSignalBaseSchema):
    """
    A schema for loading radar worker 'heartbeat' updates.
    This will let the master server know the worker is not stuck.
    """
    pass


class RadarWorkerSignalShutdownSchema(RadarWorkerSignalBaseSchema):
    """
    A schema for loading radar worker 'shutdown' updates.
    This will inform the server of a worker shutdown, potentially why and some data that will maybe assist in understanding why.
    """
    reason                  = fields.Str(required = False, load_default = "No reason.")


class RadarWorkerConfigurationBaseSchema(Schema):
    """Forms a base for radar worker configuration, to identify general config values."""
    run_headless            = fields.Bool()
    use_proxy               = fields.Bool()
    should_save_payloads    = fields.Bool()
    phone_home_url          = fields.Str()
    proxy_url_list          = fields.List(fields.Str(), required = False, load_default = [])
    worker_filename         = fields.Str()


class RadarWorkerSchema(RadarWorkerConfigurationBaseSchema):
    """A schema for loading & dumping radar workers."""
    class Meta:
        unknown = EXCLUDE
    name                    = fields.Str()
    unique_id               = fields.Str()
    worker_type             = fields.Str()
    enabled                 = fields.Bool()


class RadarWorkersConfigurationSchema(RadarWorkerConfigurationBaseSchema):
    """A schema for the overarching configuration file. This contains defaults for all general values."""
    workers                 = fields.List(fields.Nested(RadarWorkerSchema, many = False))


class RadarWorkerStartCommandSchema(RadarWorkerSchema):
    pass


def parse_as_start_command(command_line_args):
    """
    Given an array of strings, which shoud come from calling cmdline() on a psutil process instance, attempt to parse the input as a radar worker start
    command and, if successful, return the result. Otherwise, return None.

    Arguments
    ---------
    :command_line_args: An array containing command line arguments for the process.

    Returns
    -------
    A loaded instance of RadarWorkerStartCommandSchema.
    """
    try:
        worker_file_absolute = os.path.join(os.getcwd(), config.WORKER_RELATIVE_PATH, config.WORKER_FILE_NAME)
        # We must be able to find WORKER_FILE_NAME in the second argument.
        if len(command_line_args) != 3 or not re.search(re.escape(config.WORKER_FILE_NAME), command_line_args[1]):
            # An unrelated node process, or a non-slave worker.
            return None
        # Now, attempt to load the third argument as a start command. If this raises a validation error, this is either a corrupt worker or something else.
        start_command_decoded = base64.b64decode(command_line_args[2].encode("utf-8"))
        start_command_json = json.loads(start_command_decoded)
        # Now, load it as a RadarWorkerStartCommandSchema. Return this result.
        start_command_d = RadarWorkerStartCommandSchema().load(start_command_json)
        return start_command_d
    except ValidationError as ve:
        # Not a proper start command. Probably another, unrelated node process.
        return None
    except Exception as e:
        raise e


def query_running_workers(**kwargs):
    """
    Locate all running instances of the aireyes executable, and from the command line args, load a RadarWorkerStartCommandSchema. This function will
    then return a list of tuples, in the form of (radar_worker, start_command) for each active radar worker and the parsed start command.

    Keyword arguments
    -----------------
    :names: The names to filter returning running workers against. Optional. Provide an empty list or None to apply no filter.

    Returns
    -------
    A list of tuples, on the order of (radar_worker, start_command)
    """
    try:
        names = kwargs.get("names", None)

        running_worker_commands = []
        # Iterate all processes, grabbing the names from them especially.
        for proc in psutil.process_iter(["pid", "name", "username"]):
            # If the process' name is node, this is potentially a running radar worker.
            if proc.name() == "node":
                # Get the PID also.
                pid = proc.pid
                # Now, get the cmdline from the process. Ensure the second arg is identical to the WORKER_FILE_NAME and the number of command line args is exactly 3.
                command_line_args = proc.cmdline()
                # Attempt to parse these args as a start command.
                start_command_d = parse_as_start_command(command_line_args)
                if not start_command_d:
                    continue
                # Get the name from the start command.
                name = start_command_d.get("name")
                # If names list is a list and has len greater than 0, ensure name is in it before adding it to results.
                if names and len(names) > 0 and not name in names:
                    continue
                # Now, get the associated radar worker.
                radar_worker = models.RadarWorker.get_by_name(name)
                # Ensure pid on file matches the current one.
                if pid != radar_worker.pid:
                    LOG.warning(f"Radar worker {radar_worker} has PID {radar_worker.pid} on file, but is running under PID {pid}! Setting it now.")
                    radar_worker.set_process(proc)
                    db.session.commit()
                # Add this worker and the start command to the list.
                running_worker_commands.append((radar_worker, start_command_d,))
        return running_worker_commands
    except Exception as e:
        raise e


def is_worker_physically_running(name):
    running_workers = query_running_workers(names = [name])
    if not len(running_workers):
        return False
    for radar_worker, start_command in running_workers:
        if radar_worker.name == name:
            return True
    return False


def execute_radar_worker(radar_worker):
    try:
        # The radar worker must be in the ready status.
        if radar_worker.status != STATUS_READY and radar_worker.status != STATUS_SHUTDOWN:
            LOG.error(f"Failed to execute {radar_worker}, it is NOT in a ready or shutdown state!")
            raise error.RadarWorkerError(radar_worker, "not-ready-or-shutdown", "Could not execute this worker; it must be READY or SHUTDOWN.")
        LOG.debug(f"Beginning initialisation process for {radar_worker}...")
        # Set the worker to initialising.
        worker_initialising(radar_worker)
        # We will now assemble a start command for this worker. This is done by dumping the worker through the RadarWorkerStartCommandSchema, to produce a dictionary.
        start_command = RadarWorkerStartCommandSchema().dump(radar_worker)
        # Now, convert the dictionary to a JSON string, and base64 encode that string.
        start_command_encoded = base64.b64encode(json.dumps(start_command).encode("utf-8")).decode("utf-8")
        # Construct our start command line args.
        command_line_args = [
            config.NODE_EXECUTABLE_PATH,
            os.path.join(os.getcwd(), config.WORKER_RELATIVE_PATH, config.WORKER_FILE_NAME),
            start_command_encoded
        ]
        # Use popen to start this process up in a detached fashion.
        start_worker_popen = Popen(command_line_args,
            shell = False,
            stdin = DEVNULL,
            stdout = DEVNULL,
            stderr = DEVNULL,
            close_fds = True,
            start_new_session = True
        )
        # Collect the PID for this subprocess.
        new_process_pid = start_worker_popen.pid
        LOG.debug(f"Started {radar_worker} under PID {new_process_pid}...")
        # Instantiate a new psutil instance for this PID, and set required info in radar worker.
        process = psutil.Process(new_process_pid)
        radar_worker.set_process(process)
        LOG.debug(f"{radar_worker} has successfully been physically started. Saved PID to db: {radar_worker.pid}")
        return process
    except Exception as e:
        raise e


def shutdown_worker(radar_worker, reason = "No reason given.", **kwargs):
    """

    Arguments
    ---------
    :radar_worker: The RadarWorker instance to shut down.
    :reason: The reason for the shutdown.

    Keyword arguments
    -----------------
    :reset: A boolean; True if the worker should be reset to a 'Ready' state just after shutdown, False if it should be set to a 'Shutdown' state. Default is False.
    """
    try:
        reset = kwargs.get("reset", False)

        LOG.debug(f"Attempting to shutdown {radar_worker}")
        # If the radar worker has a valid PID, we can attempt to close.
        if radar_worker.pid:
            try:
                # Now, create a Process instance, terminate and wait for it to close.
                process = psutil.Process(radar_worker.pid)
                #process.terminate()
                process.send_signal(signal.SIGINT)
                process.wait()
                LOG.debug(f"Terminated process associated with {radar_worker}")
            except psutil.NoSuchProcess as nsp:
                # Process already closed?
                LOG.debug(f"No process terminated for {radar_worker}, it does not exist any longer, but database still points to it.")
        # Before continuing, ensure the worker is currently no longer running.
        if is_worker_physically_running(radar_worker.name):
            LOG.error(f"Failed to shutdown worker {radar_worker}, it is still physically running! Pid: {radar_worker.pid}")
            raise error.RadarWorkerError(radar_worker, "process-sigint-fail", "The process is still running!")
        if reset:
            # Either way, if reset is True, set this worker to a ready state.
            LOG.debug(f"Shutdown of {radar_worker} complete, resetting attributes...")
            radar_worker.reset_status_attrs()
        else:
            LOG.debug(f"Shutdown of {radar_worker} complete.")
            worker_shutdown(radar_worker)
    except Exception as e:
        raise e


def is_worker_stuck(radar_worker):
    try:
        datetime_now = g.get("datetime_now", None) or datetime.now()

        if radar_worker.initialising and (datetime_now - radar_worker.init_started_at).total_seconds() > config.WORKER_STUCK_TIMEOUT:
            # Is the worker initialising? If so, and has been doing so for longer than 5 minutes, run failsafe against it.
            LOG.warning(f"Radar worker {radar_worker} appears to be stuck initialising...")
            return True, "Worker appears stuck while INITIALISING, shutdown/reset/restart failsafe triggered..."
        elif radar_worker.running and (datetime_now - radar_worker.last_update).total_seconds() > config.WORKER_STUCK_TIMEOUT:
            # If the worker is running, but time since last update is WORKER_STUCK_TIMEOUT seconds, we'll activate failsafe on the RadarWorker.
            LOG.warning(f"Radar worker {radar_worker} appears to be stuck active...")
            return True, "Worker appears stuck while RUNNING, shutdown/reset/restart failsafe triggered..."
        # Otherwise, not stuck.
        return False, None
    except Exception as e:
        raise e


def radar_worker_execution_pass():
    """
    Runs a sweep of all enabled radar workers. Each will attempt an initialisation. If the radar worker is already running, no action will be taken. This task will
    also detect stuck/unresponsive or otherwise invalid radar workers and will attempt to rectify the situation automatically.

    First test performed, is whether each radar worker actually even has a running process. This is done by collecting all instances of the worker NodeJS script currently
    running, deserialising their start commands, and parsing those. The worker in question can then be deemed running or stopped from this point. If it is stopped, its status
    will immediately be cleared, and an initialisation will be attempted. Otherwise, tests will continue on to the virtual tests below.

    Logical procedure is as follows; first, if the worker is in an INITIALISING state, and has been so for longer than 5 minutes, the worker is determined to be stuck.
    We will attempt to kill the worker, reset its state and retry. Otherwise, if the worker is initialising, we will take no action this pass, as the worker is simply
    starting up. If the worker is in an ACTIVE state, but time since the last update has been longer than 5 minutes, the worker is determined to be inactive. We will
    attempt to kill the worker, reset its state and retry. Finally, if the worker is active, no action will be taken as it is already running.
    """
    try:
        # Locate all enabled workers.
        enabled_radar_workers = db.session.query(models.RadarWorker)\
            .filter(models.RadarWorker.enabled == True)\
            .all()
        # If there are no enabled workers, just spit a warning and don't continue.
        if not len(enabled_radar_workers):
            LOG.warning(f"No radar workers are currently enabled - skipping execution pass!")
            return
        LOG.debug(f"Executing/ensure running on {len(enabled_radar_workers)} workers at {str(datetime.now())}")
        # Iterate each enabled worker and run out execution pass logic.
        for radar_worker in enabled_radar_workers:
            # Determine whether the worker is at all stuck.
            is_stuck, reason = is_worker_stuck(radar_worker)
            if is_stuck:
                # If worker is stuck, force shut it down, with reset to True.
                shutdown_worker(radar_worker, reason, reset = True)
            else:
                # Otherwise, check if it is already in a state that prohibits any further attempts of execution.
                if radar_worker.initialising:
                    # Worker initialising, this is appropriate for this pass.
                    LOG.debug(f"We won't perform any actions upon radar worker {radar_worker.name} this round, its already currently starting up.")
                    continue
                elif radar_worker.running:
                    # Worker is running, this is appropriate for this pass.
                    LOG.debug(f"We won't perform any actions upon radar worker {radar_worker.name} this round, its currently running.")
                    continue
                else:
                    # Worker is not prohibited at all from being executed.
                    LOG.debug(f"{radar_worker} is in a position that requires it be EXECUTED...")
            # Finally, execute the worker.
            execute_radar_worker(radar_worker)
    except Exception as e:
        raise e


def worker_initialising(radar_worker, **kwargs):
    """
    Set the given worker to an initialising position. This will clear all previous log attributes for the worker's
    previous runs. This function can only be used if the Worker has a READY or SHUTDOWN status.
    """
    try:
        datetime_now = g.get("datetime_now", None) or datetime.now()

        if radar_worker.status == STATUS_INITIALISING:
            # If worker is initialising, do nothing.
            LOG.warning(f"Skipped initialising worker {radar_worker.name}, it is already initialising!")
            return radar_worker
        elif radar_worker.status != STATUS_READY and radar_worker.status != STATUS_SHUTDOWN:
            # Otherwise, if in any other status, raise an error.
            LOG.error(f"Failed to initialise radar worker {radar_worker.name}! Ready/shutdown status is required, but worker's status is {radar_worker.status_str}")
            raise Exception("not-ready")
        # Otherwise, reset attributes and set initialising.
        LOG.debug(f"Setting {radar_worker} to INITIALISING position...")
        radar_worker.reset_status_attrs()
        radar_worker.set_last_update()
        radar_worker.initialising = True
        radar_worker.init_started_at = datetime_now
        return radar_worker
    except Exception as e:
        raise e


def worker_running(radar_worker):
    """
    Set the given worker to a running position. In order for this to happen, the worker's status must be initialising. If the worker is
    already running, no action will be taken.
    """
    try:
        datetime_now = g.get("datetime_now", None) or datetime.now()

        if radar_worker.status == STATUS_RUNNING:
            # If worker is running, do nothing.
            LOG.warning(f"Skipped setting worker {radar_worker.name} to running, it is already running!")
            return radar_worker
        elif radar_worker.status != STATUS_INITIALISING:
            # Otherwise, if in any other status, raise an error.
            LOG.error(f"Failed to set radar worker {radar_worker.name} to RUNNING! Initialising status is required, but worker's status is {radar_worker.status_str}")
            raise Exception("not-initialising")
        # Otherwise, set running to True, initialising to False and log when we executed it.
        LOG.debug(f"Setting {radar_worker} to ACTIVE position")
        #radar_worker.reset_status_attrs()
        radar_worker.set_last_update()
        radar_worker.running = True
        radar_worker.initialising = False
        radar_worker.executed_at = datetime_now
        return radar_worker
    except Exception as e:
        raise e


def worker_shutdown(radar_worker):
    """
    Set the given worker to a shutdown position. In order for this to happen, the worker's status must be running. If the worker is already shutdown, no action
    will be taken. Setting the worker to shutdown will not clear its status attributes, so we have persistent logs.
    """
    try:
        datetime_now = g.get("datetime_now", None) or datetime.now()

        if radar_worker.status == STATUS_SHUTDOWN:
            # If worker is shutdown, do nothing.
            LOG.warning(f"Skipped shutting down worker {radar_worker.name}, it is already shutdown!")
            return radar_worker
        elif radar_worker.status != STATUS_RUNNING and radar_worker.status != STATUS_INITIALISING:
            # Otherwise, if in any other status, raise an error.
            LOG.error(f"Failed to set radar worker {radar_worker.name} to SHUTDOWN! Running/initialising status is required, but worker's status is {radar_worker.status_str}")
            raise Exception("not-running")
        # Otherwise, set running to False.
        LOG.debug(f"Setting {radar_worker} to SHUTDOWN position")
        # Clear all assignments.
        for assigned_work in radar_worker.aircraft_day_work:
            radar_worker.aircraft_day_work.remove(assigned_work)
        radar_worker.set_last_update()
        radar_worker.shutdown_at = datetime_now
        radar_worker.running = False
        radar_worker.initialising = False
        radar_worker.remove_process_info()
        return radar_worker
    except Exception as e:
        raise e


def worker_signal_received(radar_worker, signal_d, **kwargs):
    """
    Given a RadarWorker and a loaded RadarWorkerSignalBaseSchema derivative, change the worker's running status to match the new signal.

    Arguments
    ---------
    :radar_worker: An instance of RadarWorker.
    :signal_d: A loaded RadarWorkerSignalBaseSchema.

    Returns
    -------
    The RadarWorker.
    """
    try:
        # Set last update.
        radar_worker.set_last_update()
        # Now load the state update.
        LOG.debug(f"Received state update from a radar worker {radar_worker}")
        state = signal_d["state"]
        if state == "initialised":
            # The worker has succesfully started. Change state from initialising -> active.
            worker_running(radar_worker)
        elif state == "shutdown":
            # The worker has shutdown. Change state from active -> shutdown.
            reason = signal_d.get("reason", "No reason given.")
            LOG.debug(f"{radar_worker} has requested a shutdown. Reason: {reason}")
            worker_shutdown(radar_worker)
        elif state == "heartbeat":
            # Worker has sent a heartbeat.
            LOG.debug(f"{radar_worker} has sent a heartbeat.")
        else:
            LOG.error(f"Unrecognised worker state update: {state}")
            raise ValueError("unknown-update")
    except Exception as e:
        raise e


def update_radar_worker(radar_worker, **kwargs) -> models.RadarWorker:
    """
    Persist this RadarWorker to the database. If it does not already exist, it will simply be added. Otherwise, if it exists, contents will be updated.
    Either way, the latest RadarWorker instance will be returned.

    Arguments
    ---------
    :radar_worker: An instance of RadarWorker.

    Returns
    -------
    The latest RadarWorker.
    """
    try:
        LOG.debug(f"Attempting to persist radar worker identified by {radar_worker.name} to database.")
        # Does this radar worker exist? If so, simply update.
        existing_radar_worker = models.RadarWorker.get_by_name(radar_worker.name)
        if existing_radar_worker:
            LOG.debug(f"Worker {radar_worker.name} already exists. Persisting potential changes to configuration now...")
            existing_radar_worker.update_from_object(radar_worker)
            # Return existing radar worker.
            return existing_radar_worker
        # Otherwise, we will simply add this to the database.
        LOG.debug(f"Worker {radar_worker.name} does not exist. Creating it now...")
        db.session.add(radar_worker)
        # Return new instance.
        return radar_worker
    except Exception as e:
        raise e


def read_radar_workers_from(filename = None, **kwargs):
    """
    Read all radar workers from the given file, identified by just its name. The file should be present within the imports/ directory
    to be located. This function, once all workers are read, will update each of them ensuring configuration is consistent. This function
    can also be used by simply supplying the full JSON configuration intended to be read. In this case, that will be used.

    Arguments
    ---------
    :filename: The name, relative to imports/ directory, of the file.

    Keyword arguments
    -----------------
    :conf_json: Bypass all file I/O by providing deserialised JSON for the configuration file.
    :directory: A directory, relative to the imports/ directory, to search for the file. By default, nothing is used.

    Returns
    -------
    A list of all RadarWorkers.
    """
    try:
        conf_json = kwargs.get("conf_json", None)
        directory = kwargs.get("directory", "")

        workers = []
        if conf_json:
            worker_conf_json = conf_json
        else:
            # If doesn't exist, raise an error.
            workers_absolute_path = os.path.join(os.getcwd(), config.IMPORTS_DIR, directory, filename)
            if not os.path.isfile(workers_absolute_path):
                LOG.error(f"Failed to locate requested workers configuration file at {workers_absolute_path}")
                raise Exception("no-workers-config")
            # Then, open and read JSON from the config.
            with open(workers_absolute_path, "r") as f:
                worker_conf_json = json.loads(f.read())
        # Now, load this dict as a RadarWorkersConfigurationSchema.
        radar_workers_configuration = RadarWorkersConfigurationSchema().load(worker_conf_json)
        # Get all default values from this configuration.
        run_headless = radar_workers_configuration.get("run_headless", True)
        use_proxy = radar_workers_configuration.get("use_proxy", False)
        should_save_payloads = radar_workers_configuration.get("should_save_payloads", False)
        phone_home_url = radar_workers_configuration.get("phone_home_url", None)
        if not phone_home_url:
            raise Exception(f"No default phone home URL given!")
        proxy_url_list = radar_workers_configuration.get("proxy_url_list", [])
        worker_filename = radar_workers_configuration.get("worker_filename", None)
        if not worker_filename:
            raise Exception(f"No default worker filename given!")

        # We can now load each radar worker one by one.
        for worker_d in radar_workers_configuration["workers"]:
            # Make a new radar worker.
            worker = models.RadarWorker(**worker_d)
            # For each of the configuration values above, attempt to get from the worker_d but use the equivalent above as default.
            worker.run_headless = worker_d.get("run_headless", run_headless)
            worker.use_proxy = worker_d.get("use_proxy", use_proxy)
            worker.should_save_payloads = worker_d.get("should_save_payloads", should_save_payloads)
            worker.phone_home_url = worker_d.get("phone_home_url", phone_home_url)
            worker.proxy_url_list = worker_d.get("proxy_url_list", proxy_url_list)
            worker.worker_filename = worker_d.get("worker_filename", worker_filename)
            # Now, update the worker.
            update_radar_worker(worker)
            # Add to result.
            workers.append(worker)
        # Return all read workers, up-to-date.
        return workers
    except Exception as e:
        raise e
