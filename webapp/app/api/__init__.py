from flask import Blueprint
from .. import db

api = Blueprint("api", __name__)

from . import routes
