from flask import Blueprint
from .. import db

frontend = Blueprint("frontend", __name__)

from . import routes
