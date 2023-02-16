#!/bin/bash
# Set app environment to testing.
export APP_ENV=Test
# Build the basics of this app.
#pipenv run flask init-db
# Run tests.
pipenv run python test.py
