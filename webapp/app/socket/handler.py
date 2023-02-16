import re
import logging
from flask import request
from flask_socketio import Namespace, emit, join_room, leave_room, disconnect
from flask_login import login_required, current_user

from .. import db, config, login_manager, socketio, models, airvehicles, flights, traces, radarworker, error, viewmodel

LOG = logging.getLogger("aireyes.socket.handler")
LOG.setLevel( logging.DEBUG )


class RadarWorkerNamespace(Namespace):
    """
    A namespace specifically for radar worker types. In order to connect to this namespace, some strict criteria must be satisfied including; namespace
    should match the required worker's namespace value, the incoming worker should also authenticate properly to match a current User value of type worker
    and for extra security, the worker must originate from the loopback/localhost address.
    """
    def on_connect(self):
        try:
            # Get the remote addr and user agent from the current request.
            remote_addr = request.remote_addr
            if config.APP_ENV == "Test":
                remote_addr = "127.0.0.1"
            if remote_addr != "127.0.0.1" and remote_addr != "localhost":
                LOG.error(f"Denied SocketIO connection from {request}. It does not come from localhost! (was {remote_addr})")
                raise error.RadarWorkerRequiredError("external-host")
            user_agent = request.headers.get("User-Agent", None)
            if not user_agent or not re.match(r"^aireyes/slave", user_agent):
                LOG.error(f"Denied SocketIO connection from {request}. It does not carry the worker user agent.")
                raise error.RadarWorkerRequiredError("bad-user-agent")
            # Ensure this user is authenticated.
            if not current_user.is_authenticated:
                LOG.error(f"Denied SocketIO connection from {request}. It is NOT authenticated.")
                raise error.RadarWorkerRequiredError("not-authenticated")
            # Finally, ensure the User model type is RadarWorker.
            if not isinstance(current_user, models.RadarWorker):
                LOG.error(f"Denied SocketIO connection from {request} ({current_user}.) It's type is not RadarWorker!")
                raise error.RadarWorkerRequiredError("invalid-user-type")
            LOG.debug(f"Radar worker has connected via SocketIO! Name: {current_user.name}")
        except error.RadarWorkerRequiredError as rwre:
            """ TODO: perhaps log this malicious attempt? """
            """ TODO: perhaps block this host? """
            disconnect()
        except Exception as e:
            LOG.error(e, exc_info = True)
            disconnect()

    def on_heartbeat(self, signal_json):
        signal_d = radarworker.RadarWorkerSignalHeartbeatSchema().load(signal_json)
        # Invoke radarworker module to perform this update.
        radar_worker = radarworker.worker_signal_received(current_user, signal_d)
        # Simply commit, and return OK, 200.
        db.session.commit()
        return "OK"

    def on_disconnect(self):
        try:
            if current_user.is_authenticated and isinstance(current_user, models.RadarWorker):
                # If current user is authenticated and is a RadarWorker, shut the worker down.
                LOG.debug(f"Radar worker has disconnected via SocketIO! Name: {current_user.name}")
                radarworker.worker_shutdown(current_user)
                db.session.commit()
        except Exception as e:
            LOG.error(e, exc_info = True)
            disconnect()


class AircraftNamespace(Namespace):
    """
    TODO: setup a get info function to extract remote identifying information from the incoming User - if they are not authenticated.

    A namespace for all viewers/users of the tracker. All pages invoke a connection to this namespace, and then channel within the namespace controls
    the type of content sent to all users. The list of channels are;
    1. <no room>; nothing.
    2. SOCKETIO_ROOM_AIRCRAFT_REALTIME; the User requires, upon joining, a summary of each aircraft, then continuous realtime updates for each aircraft if applicable.
    """
    def on_connect(self):
        try:
            LOG.debug(f"A new User has connected via SocketIO to the Aircraft namespace! Info: {current_user}")
        except Exception as e:
            raise e

    def on_aircraft_realtime(self):
        """
        Add the connection to the room SOCKETIO_ROOM_AIRCRAFT_REALTIME, then enumerate and serialise a summary for all aircraft, and submit this to just
        the incoming connection's room.
        """
        try:
            LOG.debug(f"User {current_user} is requesting access to realtime aircraft...")
            # Join to room SOCKETIO_ROOM_AIRCRAFT_REALTIME.
            join_room(config.SOCKETIO_ROOM_AIRCRAFT_REALTIME)
            # Finally, send a summary update to just this user.
            monitored_aircraft = airvehicles.get_monitored_aircraft(active_first = True)
            aircraft_summary_list = [viewmodel.AircraftViewModelSchema().dump(aircraft) for aircraft in monitored_aircraft]
            # Now that we have our summary list, serialised and ready to go, emit this to the current connection's room.
            emit("aircraft-summary", aircraft_summary_list, to = request.sid)
        except Exception as e:
            raise e

    def on_aircraft_flights_realtime(self, aircraft_icao):
        """
        Add the connection to the room associated with the given Aircraft's flights.
        """
        try:
            pass
        except Exception as e:
            raise e

    def on_disconnect(self):
        LOG.debug(f"A User has disconnected from Aircraft namespace! Info: {current_user}")


"""
TODO: proper management of errors here, please.
"""
def handle_default_namespace_error(error):
    """
    """
    LOG.error(f"Unhandled error occurred in default namespace; {error}")


def handle_aircraft_error(error):
    """
    """
    LOG.error(f"Unhandled error occurred in aircraft namespace; {error}")


def handle_worker_error(error):
    """
    """
    LOG.error(f"Unhandled error occurred in worker namespace; {error}")


def default_error_handler(error):
    """
    """
    LOG.error(f"Unhandled error occurred (global); {error}")
