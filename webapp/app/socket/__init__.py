import logging

from . import handler

LOG = logging.getLogger("aireyes.socket")
LOG.setLevel( logging.DEBUG )


def setup_socketio(socketio):
    """Run operations to setup the given instance of SocketIO with all handlers defined here."""
    # Register all socket namespaces.
    socketio.on_namespace(handler.AircraftNamespace("/aircraft"))
    socketio.on_namespace(handler.RadarWorkerNamespace("/worker"))

    # Setup error handlers for the socket server.
    @socketio.on_error()
    def error_handler(e):
        # Setup error handler for the default namespace.
        handler.handle_default_namespace_error()

    @socketio.on_error("/aircraft")
    def error_handler_aircraft(e):
        # Setup error handler for the '/aircraft' namespace.
        handler.handle_aircraft_error(e)

    @socketio.on_error("/worker")
    def error_handler_worker(e):
        # Setup error handler for the '/worker' namespace.
        handler.handle_worker_error(e)

    @socketio.on_error_default  # handles all namespaces without an explicit error handler
    def default_error_handler(e):
        # Default error handler for namespaces without error handler.
        handler.handle_error(e)
