#!/bin/bash

# Via boot.sh we can either run LiveDevelopment (by default) or Production.
if [ "$1" = "LiveDevelopment" ]; then
    echo "Running aireyes in LIVE DEVELOPMENT environment";
    export APP_ENV=LiveDevelopment
else
    echo "Running aireyes in PRODUCTION environment";
    export APP_ENV=Production
fi

# Build the basics of this app.
pipenv run flask init-db
# Now, create all days.
pipenv run flask check-days
# Import airports.
pipenv run flask import-airports
# Now, create all aircrafts found in aircraft_states.json.
pipenv run flask import-known-aircraft
# Ensure there is an AircraftDay junction created for all aircraft and all new days.
# Don't upsert/change data.
pipenv run flask verify-aircraft-day
# Import our most up-to-date radar worker data.
pipenv run flask import-radar-workers
# Finally, run the server via gunicorn, using our wsgi entry point and gunicorn config.
pipenv run gunicorn wsgi:application -c gunicorn.conf.py
