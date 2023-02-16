import os
import base64
import json
import time
import aiofiles
import decimal
import unittest
import asyncio
import aiofiles

from subprocess import Popen, PIPE
from datetime import date, datetime, timedelta

from sqlalchemy import asc
from sqlalchemy.future import select
from sqlalchemy.orm import declarative_base, relationship, selectinload, sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine, async_scoped_session
from sqlalchemy import func, and_, or_

from tests.conftest import BaseCase
from fastkml import kml

from app import db, config, models, aiogeospatial, aio


class SuburbKMLBaseAsyncioCase(unittest.IsolatedAsyncioTestCase):
    def _get_placemarks_from_kml(self, absolute_state_directory, state_filename, suburbs_to_import = [], **kwargs):
        """
        Read a list of placemarks from a given KML. Provide the requested states, by title name (ex. Doncaster East) to the list import only those.
        If nothing provided, all KML placemarks will be returned from the file.
        """
        # Open the file.
        with open(os.path.join(absolute_state_directory, state_filename)) as f:
            file_contents = f.read()
        # Now, parse the state as KML.
        state_kml = kml.KML()
        # Populate from file contents.
        state_kml.from_string(file_contents)
        # Get the document from the state kml.
        state_document = list(state_kml.features())[0]
        # Get all placemarks.
        state_suburbs = list(state_document.features())
        # Now, filter those not requested, unless suburbs_to_import has 0 items.
        resulting_placemarks = []
        if len(suburbs_to_import):
            for placemark in state_suburbs:
                suburb_name = placemark.name.title()
                if suburb_name in suburbs_to_import:
                    resulting_placemarks.append(placemark)
        else:
            resulting_placemarks = state_suburbs
        return resulting_placemarks


class TestImportSuburbs(SuburbKMLBaseAsyncioCase):
    async def test_import_suburbs_from_placemarks(self):
        """
        """
        async with aio.open_db(recreate = True) as engine:
            async_session_factory = sessionmaker(engine,
                expire_on_commit = False, class_ = AsyncSession)
            async with aio.session_scope(async_session_factory) as session:
                suburb_directory = os.path.join(os.getcwd(), config.SUBURBS_DIR)
                # From the VIC state file, read just the 'Doncaster East' placemark.
                suburb_placemarks = self._get_placemarks_from_kml(suburb_directory, "VIC.kml", [ "Doncaster East" ])
                # Call out to aiogeospatial, loading these placemarks into the database.
                suburb_importation_results = await aiogeospatial.import_suburbs_from_placemarks(session, "VIC", suburb_placemarks)
                # Ensure we have 1 result.
                self.assertEqual(len(suburb_importation_results), 1)
                suburb = suburb_importation_results[0].suburb
                # Ensure the suburb name is Doncaster East.
                self.assertEqual(suburb.name, "Doncaster East")
                # Ensure state name is VIC.
                self.assertEqual(suburb.state_code, "VIC")
                # Ensure has 1208 points.
                self.assertEqual(suburb.num_coordinates, 1208)


class TestImportState(unittest.IsolatedAsyncioTestCase):
    async def test_import_state(self):
        """
        """
        # Set database to the local test database.
        #config.SQLALCHEMY_DATABASE_URI = "sqlite:///geospatial.db"
        #config.AIOSQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///instance/geospatial.db"
        # Open a connection, clearing the database prior to any work.
        async with aio.open_db(recreate = True) as engine:
            async_session_factory = sessionmaker(engine,
                expire_on_commit = False, class_ = AsyncSession)
            print(engine)
            async with aio.session_scope(async_session_factory) as session:
                states_absolute_path = os.path.join(os.getcwd(), config.SUBURBS_DIR)
                state_filename = "VIC.kml"
                # Now, import this entire state.
                state_import_result = await aiogeospatial.import_state(session, states_absolute_path, state_filename)
                await session.flush()
