### Gunicorn eventlet web socket compatible configuration ###
loglevel = "info"
errorlog = ""
accesslog = ""

# Bind to port 8081 and allow up to 2048 pending connections.
bind = ["0.0.0.0:8081"]
backlog = 2048

# Configure gunicorn to use eventlet.
workers = 1
threads = 1
worker_connections = 1000
worker_class = "eventlet"

# 10,000 requests until the worker restarts (help with memory leaks.)
max_requests = 10000

# If radio silence from the worker exceeds this number of seconds, replace it with a fresh instance.
timeout = 20

# Maximum number of seconds to wait for the next request in a keep-alive connection.
keepalive = 5
