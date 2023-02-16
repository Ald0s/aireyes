"""
A module for coordinating User based activities.
"""
import re
import os
import time
import uuid
import logging
import json
from datetime import datetime, date, timedelta

from flask import g
from marshmallow import Schema, fields, EXCLUDE, post_load, pre_load

from . import db, config, models, error

LOG = logging.getLogger("aireyes.user")
LOG.setLevel( logging.DEBUG )

PRIVILEGE_USER = models.User.PRIVILEGE_USER
PRIVILEGE_OWNER = models.User.PRIVILEGE_OWNER


def create_user(username, password, **kwargs):
    """
    """
    try:
        privilege = kwargs.get("privilege", PRIVILEGE_USER)

        # Ensure the user does not already exist, raise an error if so.
        if models.User.get_by_username(username):
            LOG.error(f"Failed to create user: {username}, one with that username already exists.")
            raise Exception("user-already-exists")
        # Otherwise, create a new user instance.
        LOG.debug(f"Creating a new User '{username}' with privilege level {privilege}")
        user = models.User()
        # Set username, privilege and password.
        user.set_username(username)
        user.set_password(password)
        user.set_privilege(privilege)
        # Add to db session and return the user instance.
        db.session.add(user)
        return user
    except Exception as e:
        raise e
