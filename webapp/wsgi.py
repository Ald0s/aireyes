# Monkey patch for eventlet, this enables us to emit from external servers with SocketIO.
import eventlet
eventlet.monkey_patch()

from app import create_app
application = create_app()
