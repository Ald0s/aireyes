import os
import re
import json
import time
import logging
from datetime import datetime

from flask import g, request, render_template, redirect, flash, url_for, send_from_directory, abort, jsonify, current_app
from flask_login import login_required, current_user, login_user, logout_user, user_loaded_from_request
from flask.sessions import SecureCookieSessionInterface
from marshmallow import Schema, fields

from . import db, config, login_manager, models

LOG = logging.getLogger("aireyes.handler")
LOG.setLevel( logging.DEBUG )


def configure_app_handlers(app):
    @login_manager.request_loader
    def load_radar_worker_from_request(request):
        """
        Authenticate radar workers from a request. This is insecure and probably should not be made production.
        This should actually be done with OAuth or something. TODO 0x14
        """
        worker_id = request.headers.get("WorkerUniqueId")
        if worker_id:
            radar_worker = db.session.query(models.RadarWorker)\
                .filter(models.RadarWorker.unique_id == worker_id)\
                .first()
            if radar_worker:
                return radar_worker
        return None

    @login_manager.user_loader
    def load_user(user_id):
        """Loading User the suggested way by flask login."""
        try:
            user_id = int(user_id)
            return db.session.query(models.User)\
                .filter(models.User.id == user_id)\
                .first()
        except ValueError as ve:
            return None
