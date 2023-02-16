from app import compat
compat.monkey_patch_sqlite()

import unittest

from tests.test_utility import *
from tests.test_frontend import *
from tests.test_radarworker import *
from tests.test_traces import *
from tests.test_aiotraces import *
from tests.test_aiogeospatial import *
from tests.test_flights import *
from tests.test_calculations import *
from tests.test_airvehicles import *
from tests.test_api import *
from tests.test_socket import *
from tests.test_geospatial import *
from tests.test_interpolation import *
from tests.test_user import *
from tests.test_viewmodel import *

if __name__ == '__main__':
    unittest.main()
