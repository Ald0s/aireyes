import os
import base64
import json
import time
import aiofiles
import decimal
import unittest
import tarfile
import asyncio
import aiofiles

from subprocess import Popen, PIPE
from datetime import date, datetime, timedelta

from sqlalchemy import asc
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import create_async_engine, async_scoped_session, AsyncSession
from sqlalchemy import func, and_, or_

from tests.conftest import BaseCase

from app import db, config, models, airvehicles, aiotraces as traces, error, aio


class TestImportTraces(unittest.IsolatedAsyncioTestCase):
    async def test_import_aircraft_state(self):
        """
        Ensure we can get a desired aircraft state from the aircrafts JSON file.
        First, ensure we get a NoAircraftStateInFile error if we pass a non-existent ICAO.
        Second, ensure we get a valid aircraft state for 7c4ee8
        Ensure all data in it is correct.
        """
        # Ensure we can't import with a bad icao.
        with self.assertRaises(error.NoAircraftStateInFile):
            await traces.import_aircraft_state("none")
        # Ensure we can locate an aircraft state for icao 7c4ee8.
        _7c4ee8_state = await traces.import_aircraft_state("7c4ee8")
        # Ensure all info is correct.
        self.assertEqual(_7c4ee8_state["icao"], "7c4ee8")
        self.assertEqual(_7c4ee8_state["flightName"], "POL35")
        self.assertEqual(_7c4ee8_state["registration"], "VH-PVE")
        self.assertEqual(_7c4ee8_state["type"], "B350")
        self.assertEqual(_7c4ee8_state["description"], "B300 King Air 350")
        self.assertEqual(_7c4ee8_state["year"], "2019")
        self.assertEqual(_7c4ee8_state["ownerOperator"], "SKYTRADERS PTY LTD")

    async def test_extract_aircraft_state(self):
        """
        Read a JSON trace from a test file; test-traces/2021-06-29/7c4ef5_full.json
        Use traces module to extract aircraft information from the read JSON object.
        Ensure icao is 7c4ef5
        Ensure registration is VH-PVR
        Ensure type is A139
        Ensure desc is AgustaWestland 139
        Ensure owner operator is STARFLIGHT VICTORIA PTY LTD
        Ensure year is 2019

        Now, read a JSON trace from another test file, but one that doesn't contain aircraft state info (old style.)
        Specifically from test-traces/2020-08-16/7c4ee8_full.json
        Now, when we extract aircraft state from this trace, ensure we get an exception back.
        """
        # Read the JSON trace, decode as JSON.
        async with aiofiles.open(os.path.join(os.getcwd(), config.TRACES_DIR, "2021-06-29", "7c4ef5_full.json"), "r") as f:
            _7c4ef5_full_contents = await f.read()
            _7c4ef5_full = json.loads(_7c4ef5_full_contents)
        # Now, use traces module to extract aircraft state from the trace.
        aircraft_state = await traces.extract_aircraft_state(_7c4ef5_full)
        # Ensure aircraft data is all correct.
        self.assertEqual(aircraft_state["icao"], "7c4ef5")
        self.assertEqual(aircraft_state["flightName"], "POL32")
        self.assertEqual(aircraft_state["registration"], "VH-PVR")
        self.assertEqual(aircraft_state["type"], "A139")
        self.assertEqual(aircraft_state["description"], "AgustaWestland 139")
        self.assertEqual(aircraft_state["year"], "2019")
        self.assertEqual(aircraft_state["ownerOperator"], "STARFLIGHT VICTORIA PTY LTD")
        # Read the JSON trace, decode as JSON.
        async with aiofiles.open(os.path.join(os.getcwd(), config.TRACES_DIR, "2020-08-16", "7c4ee8_full.json"), "r") as f:
            _7c4ee8_full_contents = await f.read()
            _7c4ee8_full = json.loads(_7c4ee8_full_contents)
        # Attempt to extract aircraft state, ensure we get an exception in response.
        with self.assertRaises(error.NoAircraftStateInTrace):
            await traces.extract_aircraft_state(_7c4ee8_full)

    async def test_normalise_timestamps(self):
        """
        Read a JSON trace from a test file; test-traces/2020-08-16/7c4ee8_full.json
        Ensure the now timestamp for the trace is 1597561132.709
        Ensure the first trace's timestamp is 0, ensure the last trace's timestamp is 57525.9
        Now, use the traces module to normalise the entire trace.
        Ensure the first trace object's timestamp is equal to the trace's timestamp.
        Ensure the last trace object's timestamp is almost equal to 1597618658.609
        """
        # Read the JSON trace, decode as JSON.
        async with aiofiles.open(os.path.join(os.getcwd(), config.TRACES_DIR, "2020-08-16", "7c4ee8_full.json"), "r") as f:
            _7c4ee8_full_contents = await f.read()
            _7c4ee8_full = json.loads(_7c4ee8_full_contents)
        # Now get the timestamp for the trace, ensure it is 1597561132.709
        timestamp = _7c4ee8_full["timestamp"]
        self.assertAlmostEqual(timestamp, 1597561132.709)
        # Ensure the timestamp for the first trace object is 0.
        self.assertEqual(_7c4ee8_full["trace"][0][0], 0)
        # Ensure the timestamp for the last trace object is 57525.9
        self.assertAlmostEqual(_7c4ee8_full["trace"][len(_7c4ee8_full["trace"])-1][0], 57525.9)
        # Pass this trace through to normalise timestamps.
        normalised_trace = await traces.normalise_trace_timestamps(_7c4ee8_full)
        # Ensure object 0's timestamp is 1597561132.709
        self.assertAlmostEqual(normalised_trace["trace"][0][0], 1597561132.709)
        # Ensure the last object's timestamp is 1597618658.609
        self.assertAlmostEqual(normalised_trace["trace"][len(normalised_trace["trace"])-1][0], 1597618658.609, places = 2)

    async def test_merge_traces(self):
        """
        Read two traces from JSON files; test_data/test-traces/7c4ef4_full.json, test_data/test-traces/7c4ef4_recent.json
        Next, merge all traces in input traces.
        In the result, expect the aircraft's information is correct.
        Expect the first trace object's timestamp to be 1648420051.709
        Expect the last trace object's timestamp to be 1648433791.275
        """
        all_input_traces = []
        for trace_filename in [
            os.path.join(os.getcwd(), config.TRACES_DIR, "2022-03-28", "7c4ef4_full.json"),
            os.path.join(os.getcwd(), config.TRACES_DIR, "2022-03-28", "7c4ef4_recent.json")
        ]:
            # Read the contents, decode as JSON and add to input list.
            async with aiofiles.open(trace_filename, "r") as f:
                contents = await f.read()
                read_json = json.loads(contents)
                all_input_traces.append(read_json)
        # Merge all traces in the input list, set normalise to False so this attempt raises an exception.
        with self.assertRaises(Exception) as e:
            await traces.merge_traces(all_input_traces, normalise = False)
        # Merge all traces in the input list, set normalise to True so the function handles timestamp normalisation.
        merged_trace = await traces.merge_traces(all_input_traces, normalise = True)
        # Now that traces are merged, ensure that the first trace's object's timestamp is 1648420051.709
        self.assertAlmostEqual(merged_trace["trace"][0][0], 1648420051.709, places = 2)
        # Then, ensure the final trace object's timestamp is 1648433791.275
        self.assertAlmostEqual(merged_trace["trace"][len(merged_trace["trace"])-1][0], 1648433791.275, places = 2)

    async def test_import_aircraft_on_day(self):
        """
        Test the importation of trace data for aircraft 7c4ef5, on day 2021-06-29.
        Read a JSON trace from a test file; test-traces/2021-06-29/7c4ef5_full.json
        Use traces module to execute importation, we should get back the target Aircraft instance.
        Ensure this Aircraft has a total of 59 flight points after creation.

        Test the importation of trace data for aircraft 7c4ef4, on day 2020-08-18.
        This trace does not contain a comprehensive aircraft state, but we should be able to still import it thanks to aircraft_states JSON.
        """
        # Create a new database engine.
        async with aio.open_db(recreate = True) as engine:
            async_session_factory = sessionmaker(engine,
                expire_on_commit = False, class_ = AsyncSession)
            # Now, scoped session.
            async with aio.session_scope(async_session_factory) as session:
                # Create a profile for our test data.
                aircraft_icao = "7c4ef5"
                day = date(2021, 6, 29)
                files = [ os.path.join(os.getcwd(), config.TRACES_DIR, "2021-06-29", "7c4ef5_full.json") ]
                # Use traces module to execute importation.
                await traces.import_aircraft_trace_on_day(session, aircraft_icao, day, files)
                await session.flush()
                aircraft = await session.get(models.Aircraft, aircraft_icao)
                # Get number of flight points in this aircraft.
                num_flight_points_result = await session.execute((
                    select(func.count(models.FlightPoint.flight_point_hash))
                    .where(models.FlightPoint.aircraft_icao == aircraft.icao)
                ))
                # Ensure the aircraft is not None.
                self.assertIsNotNone(aircraft)
                # Ensure aircraft has 59 points.
                self.assertEqual(num_flight_points_result.scalar(), 59)
                # Ensure attempting to import this aircraft's trace on this day once again raises HistoryVerifiedError.
                with self.assertRaises(error.HistoryVerifiedError) as hve:
                    await traces.import_aircraft_trace_on_day(session, aircraft_icao, day, files)

                # Create a profile for our test data.
                aircraft_icao = "7c4ef4"
                day = date(2020, 8, 16)
                files = [ os.path.join(os.getcwd(), config.TRACES_DIR, "2020-08-16", "7c4ef4_full.json") ]
                # Use traces module to execute importation.
                await traces.import_aircraft_trace_on_day(session, aircraft_icao, day, files)
                await session.flush()
                aircraft1 = await session.get(models.Aircraft, aircraft_icao)
                # Get number of flight points in this aircraft.
                num_flight_points_result = await session.execute((
                    select(func.count(models.FlightPoint.flight_point_hash))
                    .where(models.FlightPoint.aircraft_icao == aircraft1.icao)
                ))
                # Ensure the aircraft is not None.
                self.assertIsNotNone(aircraft1)
                # Ensure aircraft has 487 points.
                self.assertEqual(num_flight_points_result.scalar(), 487)
                # Ensure attempting to import this aircraft's trace on this day once again raises HistoryVerifiedError.
                with self.assertRaises(error.HistoryVerifiedError) as hve:
                    await traces.import_aircraft_trace_on_day(session, aircraft_icao, day, files)

    async def test_duplicate_primary_key(self):
        """
        """
        _7c4ef2_state = await traces.import_aircraft_state("7c4ef2")
        # Create a new database engine.
        async with aio.open_db(recreate = True) as engine:
            async_session_factory = sessionmaker(engine,
                expire_on_commit = False, class_ = AsyncSession)
            # Now, scoped session.
            async with aio.session_scope(async_session_factory) as session:
                # Create a profile for our test data.
                aircraft_icao = "7c4ef2"
                day_isos = [
                    "2020-07-08",
                    "2020-07-09",
                    "2020-07-10",
                    "2020-07-12",
                    "2020-07-13",
                    "2020-07-14",
                    "2020-07-16"
                ]
                file_days = [(date.fromisoformat(day_iso), os.path.join(os.getcwd(), config.TRACES_DIR, day_iso, "7c4ef2_full.json"),) for day_iso in day_isos]
                for day, path in file_days:
                    # Use traces module to execute importation.
                    await traces.import_aircraft_trace_on_day(session, aircraft_icao, day, [path])
                    await session.flush()
