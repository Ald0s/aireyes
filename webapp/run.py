import logging
from app import create_app, config, socketio

logging.basicConfig( level = logging.DEBUG )

if __name__ == "__main__":
    app = create_app()
    socketio.run(app,
        host = config.HOST, port = config.PORT, debug = config.DEBUG, use_reloader = config.USE_RELOADER)
