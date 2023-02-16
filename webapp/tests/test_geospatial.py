import os
import base64
import uuid
import json
import time
import aiofiles
import decimal
import unittest
import asyncio
import aiofiles
import logging
import geopandas
import geojson
import fiona

import shapely
from shapely import geometry

import cProfile
import io
import pstats
import contextlib

from fiona.drvsupport import supported_drivers
from subprocess import Popen, PIPE
from datetime import date, datetime, timedelta

from sqlalchemy import asc, desc
from sqlalchemy.orm import joinedload, lazyload, subqueryload
from sqlalchemy import func, and_, or_

from tests.conftest import BaseCase

from app import db, config, models, error, geospatial, airvehicles, aiogeospatial, draw


class UsePhysicalGeospatialDatabase(BaseCase):
    """
    Forces the test app instance to use a physical sqlite database for test purposes.
    This ensures that we can read & query data between both sync and async function types.
    """
    def create_app(self):
        config.SQLALCHEMY_DATABASE_URI = "sqlite:///geospatial.db"
        config.AIOSQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///instance/geospatial.db"
        return super().create_app()


class TestGeospatial(UsePhysicalGeospatialDatabase):
    def test_read_suburbs_from(self):
        """
        Test geospatial's ability to import all suburbs.
        Read all suburbs from test-suburbs.
        Get victoria by code VIC.
        Ensure its not none and has 1549 suburbs.
        Find doncaster east by its name from vic.
        Ensure its not none and has 1208 coordinates.
        """
        geospatial.read_suburbs_from(config.SUBURBS_DIR)
        # Now, get Victoria.
        victoria_state = models.State.get_by_code("VIC")
        # Ensure not None.
        self.assertIsNotNone(victoria_state)
        # Now, locate Doncaster East.
        doncaster_east = victoria_state.find_suburb_by_name("Doncaster East")
        # Ensure we can find doncaster east.
        self.assertIsNotNone(doncaster_east)
        # Ensure doncaster east has 1208 coordinates.
        self.assertEqual(doncaster_east.num_coordinates, 1208)
        # Ensure postcode is 3109.
        self.assertEqual(doncaster_east.postcode, 3109)

    def test_determine_suburb_neighbourships_postgis(self):
        """
        Import all test suburbs from VIC.
        Locate doncaster east, assert that it exists.
        Determine suburb neighbour relationships for doncaster east.
        Ensure, given a static list, each neighbour is recognised.
        Ensure list of neighbour has the same length as static list.
        Ensure doncaster east instance to have 6 neighbours according to database.
        """
        # Import all test suburbs.
        geospatial.read_suburbs_from(config.SUBURBS_DIR)
        # Ensure we can find Doncaster East.
        doncaster_east = models.Suburb.get_by_name("Doncaster East")
        # Write doncaster east out.
        self.assertIsNotNone(doncaster_east)
        # Determine neighbours for this suburb, with precise boundaries.
        doncaster_east_neighbours = geospatial.determine_neighbours_for(doncaster_east)
        db.session.flush()
        # Expect each neighbour to be present in the following list of neighbours:
        neighbours = [
            "Templestowe",
            "Warrandyte",
            "Donvale",
            "Blackburn North",
            "Box Hill North",
            "Doncaster"
        ]
        for located_neighbour_name in [ suburb.name for suburb in doncaster_east_neighbours ]:
            self.assertIn(located_neighbour_name, neighbours)
        # Ensure size is the same.
        self.assertEqual(len(neighbours), len(doncaster_east_neighbours))
        # Also, expect doncaster east to have 6 neighbours in database.
        self.assertEqual(doncaster_east.num_neighbours, 6)

    def test_determine_suburb_neighbourships_precise(self):
        """
        Import all test suburbs from VIC.
        Locate doncaster east, assert that it exists.
        Determine suburb neighbour relationships for doncaster east.
        Ensure, given a static list, each neighbour is recognised.
        Ensure list of neighbour has the same length as static list.
        Ensure doncaster east instance to have 7 neighbours according to database.
        """
        # Import all test suburbs.
        geospatial.read_suburbs_from(config.SUBURBS_DIR)
        # Ensure we can find Doncaster East.
        doncaster_east = models.Suburb.get_by_name("Doncaster East")
        # Write doncaster east out.
        self.assertIsNotNone(doncaster_east)
        # Determine neighbours for this suburb, with precise boundaries.
        doncaster_east_neighbours = geospatial.determine_neighbours_for(doncaster_east,
            precise_suburb_boundaries = True, force_without_postgis = True)
        db.session.flush()
        # Expect each neighbour to be present in the following list of neighbours:
        neighbours = [
            "Templestowe",
            "Warrandyte",
            "Donvale",
            "Blackburn North",
            "Box Hill North",
            "Doncaster"
        ]
        for located_neighbour_name in [ suburb.name for suburb in doncaster_east_neighbours ]:
            self.assertIn(located_neighbour_name, neighbours)
        # Ensure size is the same.
        self.assertEqual(len(neighbours), len(doncaster_east_neighbours))
        # Also, expect doncaster east to have 6 neighbours in database.
        self.assertEqual(doncaster_east.num_neighbours, 6)

    def test_determine_suburb_neighbourships_inprecise(self):
        """
        Import all test suburbs from VIC.
        Locate doncaster east, assert that it exists.
        Determine suburb neighbour relationships for doncaster east.
        Ensure, given a static list, each neighbour is recognised.
        Ensure list of neighbour has the same length as static list.
        Ensure doncaster east instance to have 6 neighbours according to database.
        """
        # Import all test suburbs.
        geospatial.read_suburbs_from(config.SUBURBS_DIR)
        # Ensure we can find Doncaster East.
        doncaster_east = models.Suburb.get_by_name("Doncaster East")
        self.assertIsNotNone(doncaster_east)
        # Determine neighbours for this suburb, with inprecise boundaries.
        doncaster_east_neighbours = geospatial.determine_neighbours_for(doncaster_east,
            precise_suburb_boundaries = False, force_without_postgis = True)
        db.session.flush()
        # Expect each neighbour to be present in the following list of neighbours:
        neighbours = [
            "Templestowe",
            "Warrandyte",
            "Donvale",
            "Blackburn North",
            "Box Hill North",
            "Doncaster"
        ]
        for located_neighbour_name in [ suburb.name for suburb in doncaster_east_neighbours ]:
            self.assertIn(located_neighbour_name, neighbours)
        # Ensure size is the same.
        self.assertEqual(len(neighbours), len(doncaster_east_neighbours))
        # Also, expect doncaster east to have 6 neighbours in database.
        self.assertEqual(doncaster_east.num_neighbours, 6)

    def test_flight_point_geospatial_locator_without_postgis(self):
        """
        Test our object redefinition of the flight point geospatial locator.
        Import all test suburbs.
        Create 3 flight points; two in doncaster east and one in mitcham; note that doncaster east and mitcham are NOT neighbours.
        Instantiate a GeospatialFlightPointLocator, focused on the list of flight points; this is intended to simulate what our request handling code may do.
        Command the locator to geolocate all flight points.
        Assess the result object; in particular, methodology should be 'nowhere' for the first flight point, 'exact-last-suburb' for the second flight point and finally, 'neighbour-last-suburb' for the third and final point.

        Now, perform another test with another list, this time, starting on one suburb, and moving across two other suburbs.
        """
        # Import all test suburbs.
        geospatial.read_suburbs_from(config.SUBURBS_DIR, process_neighbourships = True)
        # Create a list fo each resulting instance of the flight points.
        flight_point_hashes = ["0x1", "0x2", "0x3"]
        coordinates = [(-37.758679, 145.171284), (-37.768721, 145.169020), (-37.813535, 145.196489)]
        timestamps = [time.time(), time.time()+10, time.time()+20]
        # Get points from these.
        points = list(self._build_flight_points(flight_point_hashes, coordinates, timestamps))
        # Instantiate a locator, and command geolocation of the above 3 points.
        flight_point_locator = geospatial.GeospatialFlightPointLocator(points,
            crs = 3112, force_without_postgis = True)
        flight_point_locator.geolocate_all()
        # Get the result's outcome dict.
        outcome_dictionary = flight_point_locator.last_result.outcome_dictionary
        # Now, the first flight point's methodology should be 'state-epsg.'
        self.assertEqual(outcome_dictionary[points[0]]["methodology"], "state-epsg")
        # The second should be 'exact-last-suburb'.
        self.assertEqual(outcome_dictionary[points[1]]["methodology"], "exact-last-suburb")
        # Third and final should be 'state-epsg'
        self.assertEqual(outcome_dictionary[points[2]]["methodology"], "state-epsg")

        # Build a new list. This time, there are two points in Doncaster East, 1 point in Donvale, then 1 point in Park Orchards.
        flight_point_hashes = ["0x4", "0x5", "0x6", "0x7"]
        coordinates = [(-37.75980299361616, 145.17030889852742), (-37.78007986149734, 145.1653486047535), (-37.78310621797378, 145.1839160455809), (-37.787665833529665, 145.2084450190611)]
        timestamps = [time.time(), time.time()+10, time.time()+20, time.time()+30]
        # Get points from these.
        points = list(self._build_flight_points(flight_point_hashes, coordinates, timestamps))
        # Instantiate a locator, and command geolocation of the above points list.
        flight_point_locator = geospatial.GeospatialFlightPointLocator(points,
            crs = 3112, force_without_postgis = True)
        flight_point_locator.geolocate_all()
        # Get the result's outcome dict.
        outcome_dictionary = flight_point_locator.last_result.outcome_dictionary
        # Now, the first flight point's methodology should be 'state-epsg.'
        self.assertEqual(outcome_dictionary[points[0]]["methodology"], "state-epsg")
        # The second should be 'exact-last-suburb'.
        self.assertEqual(outcome_dictionary[points[1]]["methodology"], "exact-last-suburb")
        # Third should be 'neighbour-last-suburb'
        self.assertEqual(outcome_dictionary[points[2]]["methodology"], "neighbour-last-suburb")
        # Fourth and final should be 'neighbour-last-suburb'
        self.assertEqual(outcome_dictionary[points[3]]["methodology"], "neighbour-last-suburb")
        # Get all suburbs.
        doncaster_east = models.Suburb.get_by_name("Doncaster East")
        donvale = models.Suburb.get_by_name("Donvale")
        park_orchards = models.Suburb.get_by_name("Park Orchards")
        # Ensure first and second points have been associated with Doncaster East.
        for point in points[:2]:
            self.assertEqual(point.suburb, doncaster_east)
        # Ensure third is donvale.
        self.assertEqual(points[2].suburb, donvale)
        # Ensure fourth is park orchards.
        self.assertEqual(points[3].suburb, park_orchards)

    def test_flight_point_geospatial_locator(self):
        """
        Test our object redefinition of the flight point geospatial locator.
        Import all test suburbs.
        Create 3 flight points; two in doncaster east and one in mitcham; note that doncaster east and mitcham are NOT neighbours.
        Instantiate a GeospatialFlightPointLocator, focused on the list of flight points; this is intended to simulate what our request handling code may do.
        Command the locator to geolocate all flight points.
        Assess the result object; in particular, methodology should be 'nowhere' for the first flight point, 'exact-last-suburb' for the second flight point and finally, 'neighbour-last-suburb' for the third and final point.

        Now, perform another test with another list, this time, starting on one suburb, and moving across two other suburbs.
        """
        # Import all test suburbs.
        geospatial.read_suburbs_from(config.SUBURBS_DIR, process_neighbourships = True)
        # Create a list fo each resulting instance of the flight points.
        flight_point_hashes = ["0x1", "0x2", "0x3"]
        coordinates = [(-37.758679, 145.171284), (-37.768721, 145.169020), (-37.813535, 145.196489)]
        timestamps = [time.time(), time.time()+10, time.time()+20]
        # Get points from these.
        points = list(self._build_flight_points(flight_point_hashes, coordinates, timestamps))
        # Instantiate a locator, and command geolocation of the above 3 points.
        flight_point_locator = geospatial.GeospatialFlightPointLocator(points, crs = 3112)
        flight_point_locator.geolocate_all()
        # Get the result's outcome dict.
        outcome_dictionary = flight_point_locator.last_result.outcome_dictionary
        # All points should be postgis.
        self.assertEqual(outcome_dictionary[points[0]]["methodology"], "postgis")
        self.assertEqual(outcome_dictionary[points[1]]["methodology"], "postgis")
        self.assertEqual(outcome_dictionary[points[2]]["methodology"], "postgis")

        # Build a new list. This time, there are two points in Doncaster East, 1 point in Donvale, then 1 point in Park Orchards.
        flight_point_hashes = ["0x4", "0x5", "0x6", "0x7"]
        coordinates = [(-37.75980299361616, 145.17030889852742), (-37.78007986149734, 145.1653486047535), (-37.78310621797378, 145.1839160455809), (-37.787665833529665, 145.2084450190611)]
        timestamps = [time.time(), time.time()+10, time.time()+20, time.time()+30]
        # Get points from these.
        points = list(self._build_flight_points(flight_point_hashes, coordinates, timestamps))
        # Instantiate a locator, and command geolocation of the above points list.
        flight_point_locator = geospatial.GeospatialFlightPointLocator(points, crs = 3112)
        flight_point_locator.geolocate_all()
        # Get the result's outcome dict.
        outcome_dictionary = flight_point_locator.last_result.outcome_dictionary
        # All points should be postgis.
        self.assertEqual(outcome_dictionary[points[0]]["methodology"], "postgis")
        self.assertEqual(outcome_dictionary[points[1]]["methodology"], "postgis")
        self.assertEqual(outcome_dictionary[points[2]]["methodology"], "postgis")
        self.assertEqual(outcome_dictionary[points[3]]["methodology"], "postgis")
        # Get all suburbs.
        doncaster_east = models.Suburb.get_by_name("Doncaster East")
        donvale = models.Suburb.get_by_name("Donvale")
        park_orchards = models.Suburb.get_by_name("Park Orchards")
        # Ensure first and second points have been associated with Doncaster East.
        for point in points[:2]:
            self.assertEqual(point.suburb, doncaster_east)
        # Ensure third is donvale.
        self.assertEqual(points[2].suburb, donvale)
        # Ensure fourth is park orchards.
        self.assertEqual(points[3].suburb, park_orchards)

    def test_geolocate_suburbs_for(self):
        """
        Test our ability to locate the suburbs for various lists of points, providing various degrees of hints and clues.
        Import all test suburbs.
        Create three separate flight points lists, each list representing a separate submission of partial data;
            List #1:
            A set of points, moving from doncaster east to donvale. Provide NO previous suburb. The first 2 points are in Doncaster East. The third is in Templestowe, fourth in Doncaster East and the last two are in Donvale.
            List #2:
            A set from donvale to ringwood north. Provide Donvale as previous suburb. The first point is in Donvale, second in Park Orchards, last point is in Ringwood North.
            List #3:
            A set from wantirna to vermont south (signal dropped out.) Provide Ringwood North as previous suburb. The first 3 points is in Wantirna. The last point is in Vermont South.
        The idea is that between lists 2 & 3, a signal dropout occured. This means that the geolocation function should drop back to comprehensive determination of
        suburb location, using any evidence only as advice.

        Call out to geolocate_suburbs_for targeting the first flight points list.
        Ensure the first four points now have Doncaster East as their suburb. Ensure the last two are Donvale.
        Geolocate second flights point list.
        Ensure the first 3 points now have Donvale as suburb. Ensure the last is Ringwood.
        Geolocate the third points list.
        Ensure the first 3 points has Wantirna as their suburb. Ensure the last has Vermont South.
        """
        # Import all test suburbs.
        geospatial.read_suburbs_from(config.SUBURBS_DIR, process_neighbourships = True)
        # Get all suburbs we'll be using.
        doncaster_east = models.Suburb.get_by_name("Doncaster East")
        templestowe = models.Suburb.get_by_name("Templestowe")
        donvale = models.Suburb.get_by_name("Donvale")
        ringwood_north = models.Suburb.get_by_name("Ringwood North")
        park_orchards = models.Suburb.get_by_name("Park Orchards")
        wantirna = models.Suburb.get_by_name("Wantirna")
        vermont_south = models.Suburb.get_by_name("Vermont South")
        # Build a timestamp base; so we can properly simulate a time constrained order.
        base_timestamp = time.time()
        def new_flight_point(point_tuple):
            nonlocal base_timestamp
            base_timestamp+=20
            new_flight_point = models.FlightPoint(
                flight_point_hash = uuid.uuid4().hex,
                timestamp = base_timestamp
            )
            # Transform point tuple, and set as geom.
            position = geometry.Point(self._transform_tuple(point_tuple))
            new_flight_point.set_crs(3112)
            new_flight_point.set_position(position)
            return new_flight_point

        # Now, setup list 1.
        point_list1 = [(-37.75416426483057, 145.16930494351396), (-37.76264677310995, 145.1655957984213), (-37.77266524869869, 145.1628470990249), (-37.78045437540643, 145.1622692925968), (-37.78041725745537, 145.1777617820421), (-37.77173533274967, 145.1873421719799)]
        # Now, setup list 2.
        point_list2 = [(-37.77239932395302, 145.1875699987444), (-37.78245016028276, 145.2001570974438), (-37.79657708068976, 145.20931924147845), (-37.793158586387776, 145.2286649748579)]
        # Finally, setup list 3.
        point_list3 = [(-37.842391540694294, 145.2371700868865), (-37.85091800143544, 145.23230779711665), (-37.8567474105836, 145.21672446055194), (-37.85514050329149, 145.19562310767392)]
        # Convert each to a flight point.
        flight_point_list1 = [ new_flight_point(point) for point in point_list1 ]
        flight_point_list2 = [ new_flight_point(point) for point in point_list2 ]
        # Between lists 2 & 3, chuck in a 300 second gap.
        base_timestamp+=300
        flight_point_list3 = [ new_flight_point(point) for point in point_list3 ]

        # Call out to geolocate the first flight points list. Provide no last suburb.
        #list1_results = geospatial.geolocate_suburbs_for(flight_point_list1)
        result = geospatial.geolocate_suburbs_for(flight_point_list1)
        list1_results = result.flight_points
        # Ensure there are 6 in results.
        self.assertEqual(len(list1_results), 6)
        # Ensure the first is in doncaster east.
        self.assertEqual(list1_results[0].suburb, doncaster_east)
        # From second to third, templestowe.
        for point in list1_results[1:3]:
            self.assertEqual(point.suburb, templestowe)
        # Fourth back to doncaster east
        self.assertEqual(list1_results[3].suburb, doncaster_east)
        # Ensure the last two are in donvale.
        for point in list1_results[4:]:
            self.assertEqual(point.suburb, donvale)
        # Now, geolocate list #2. This time, provide donvale as our last seen suburb.
        #list2_results = geospatial.geolocate_suburbs_for(flight_point_list2, last_seen_suburb = donvale)
        result = geospatial.geolocate_suburbs_for(flight_point_list2, last_seen_suburb = donvale)
        list2_results = result.flight_points
        # Ensure there are 4 in results.
        self.assertEqual(len(list2_results), 4)
        # Ensure the first is in donvale.
        self.assertEqual(list2_results[0].suburb, donvale)
        # Ensure the second is in park orchards.
        self.assertEqual(list2_results[1].suburb, park_orchards)
        # Ensure the third is in donvale.
        self.assertEqual(list2_results[2].suburb, donvale)
        # Ensure the last is in ringwood north.
        self.assertEqual(list2_results[3].suburb, ringwood_north)
        # Finally, geolocate list #3. Provide ringwood north as our last seen suburb.
        #list3_results = geospatial.geolocate_suburbs_for(flight_point_list3, last_seen_suburb = ringwood_north)
        result = geospatial.geolocate_suburbs_for(flight_point_list3, last_seen_suburb = ringwood_north)
        list3_results = result.flight_points
        # Ensure there are 4 in results.
        self.assertEqual(len(list3_results), 4)
        # Ensure the first 3 points are in wantirna.
        for point in list3_results[:3]:
            self.assertEqual(point.suburb, wantirna)
        # Ensure the last is in vermont south.
        self.assertEqual(list3_results[3].suburb, vermont_south)

    def test_revise_geolocation_for(self):
        """
        Import all test suburbs.
        Import an aircraft's trace.
        Call out to geospatial module to revise that aircraft/day's geolocation data.
        Ensure there are various results.
        Call out again for the same command; ensure the action raises FlightPointsGeolocatedError as it has already been done.
        """
        # Import all test suburbs.
        geospatial.read_suburbs_from(config.SUBURBS_DIR, process_neighbourships = True)
        # Load 7c4ee8.
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.flush()
        # Get the first day from the first flight.
        first_flight_day = aircraft_7c4ee8_flights[0].days.first()
        # Call out to geospatial module, requesting a revision for geolocation data on that day.
        receipt = geospatial.revise_geolocation_for(aircraft_7c4ee8, first_flight_day.day)
        db.session.flush()
        # There should be 177 successfully geolocated.
        self.assertEqual(receipt.num_geolocated, 177)
        # There should be 0 overwritten and 0 skipped.
        self.assertEqual(receipt.num_overwritten_geolocated, 0)
        self.assertEqual(receipt.num_skipped, 0)
        # There should be 208 failed.
        self.assertEqual(receipt.num_error, 1120)
        # Attempting to run this again should raise FlightPointsGeolocatedError.
        with self.assertRaises(error.FlightPointsGeolocatedError) as fpge:
            geospatial.revise_geolocation_for(aircraft_7c4ee8, first_flight_day.day)

    def test_revise_geolocation_multiday(self):
        """
        Import all test suburbs.
        Import an aircraft's trace, that spans across multiple days.
        Collect all days involved.
        Create a geospatial flight point locator.
        Revise geolocation data for all days involved for that aircraft.
        """
        # Import all test suburbs.
        geospatial.read_suburbs_from(config.SUBURBS_DIR, process_neighbourships = True)
        # Load 7c4ee8, and a trace that spans multiple days.
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t3.json", 5)
        db.session.flush()
        # Now, collect all days involved.
        all_days = []
        for flight in aircraft_7c4ee8_flights:
            for day in flight.days.all():
                # Now, iterate days. If they are not already in all_days, add.
                if not day.day in all_days:
                    all_days.append(day.day)
        # There should be 2 days overall.
        self.assertEqual(len(all_days), 2)
        # Now, create a locator.
        locator = geospatial.GeospatialFlightPointLocator()
        # Finally, use this locator for each revision call for the aircraft and day.
        # Collect receipts.
        receipts = []
        for day in all_days:
            geolocation_receipt = geospatial.revise_geolocation_for(aircraft_7c4ee8, day,
                locator = locator)
            # Now, with the receipt, add to our receipts list.
            receipts.append(geolocation_receipt)
        print(f"Geolocated {len(receipts)} different days:")
        for idx, receipt in enumerate(receipts):
            print(f"Day #{idx+1} took {receipt.time_taken} seconds.")


'''class TestGeospatialFlightPoint(UsePhysicalGeospatialDatabase):
    def test_geospatial_flight_point_amounts(self):
        """
        Import all test suburbs.
        Import two aircraft, with no flight points at all.
        Find Doncaster East and Templestowe.
        For the first aircraft, generate 10 points in Doncaster East and 8 points in Templestowe.
        For the second aircraft, generate 20 points in Doncaster East and 12 points in Templestowe.
        Use instance level property to check that the total number of flight points in Doncaster East is equal to 30.
        Use instance level property to check that the total number of flight points in Templestowe is equal to 20.
        """
        # Import all test suburbs.
        geospatial.read_suburbs_from(config.SUBURBS_DIR, process_neighbourships = True)
        # Import all known aircraft, with no flight points at all.
        aircraft = airvehicles.read_aircraft_from(config.KNOWN_AIRCRAFT_CONFIG)
        db.session.flush()
        # We will be using POL35 (7c4ee8) and POL32 (7c4ef5).
        pol35 = models.Aircraft.get_by_icao("7c4ee8")
        pol32 = models.Aircraft.get_by_icao("7c4ef5")
        # Get all suburbs we'll be using.
        doncaster_east = models.Suburb.get_by_name("Doncaster East")
        templestowe = models.Suburb.get_by_name("Templestowe")
        # A point that occurs in Doncaster East.
        point_in_doncaster_east = (-37.761105, 145.172085,)
        # A point that occurs in Templestowe.
        point_in_templestowe = (-37.759642, 145.165290,)
        # Start with a base timestamp.
        base_timestamp = time.time()
        # For the first aircraft, pol35, generate 10 points in Doncaster East and 8 points in Templestowe.
        for it in range(10):
            # Increment timestamp by 10.
            base_timestamp+=10
            # Generate 10 points in Doncaster East.
            new_flight_point = models.FlightPoint(
                flight_point_hash = uuid.uuid4().hex,
                timestamp = base_timestamp,
                aircraft = pol35
            )
            # Transform point tuple, and set as geom.
            position = geometry.Point(self._transform_tuple(point_in_doncaster_east))
            new_flight_point.set_crs(3112)
            new_flight_point.set_position(position)
            doncaster_east.flight_points_.append(new_flight_point)
        for it in range(8):
            base_timestamp+=10
            # Generate 8 points in Templestowe.
            new_flight_point = models.FlightPoint(
                flight_point_hash = uuid.uuid4().hex,
                timestamp = base_timestamp,
                aircraft = pol35
            )
            # Transform point tuple, and set as geom.
            position = geometry.Point(self._transform_tuple(point_in_templestowe))
            new_flight_point.set_crs(3112)
            new_flight_point.set_position(position)
            templestowe.flight_points_.append(new_flight_point)
        # For the second aircraft, pol32, generate 20 points in Doncaster East and 12 points in Templestowe.
        for it in range(20):
            base_timestamp+=10
            # Generate 20 points in Doncaster East.
            # Generate 10 points in Doncaster East.
            new_flight_point = models.FlightPoint(
                flight_point_hash = uuid.uuid4().hex,
                timestamp = base_timestamp,
                aircraft = pol32
            )
            # Transform point tuple, and set as geom.
            position = geometry.Point(self._transform_tuple(point_in_doncaster_east))
            new_flight_point.set_crs(3112)
            new_flight_point.set_position(position)
            doncaster_east.flight_points_.append(new_flight_point)
        for it in range(12):
            base_timestamp+=10
            # Generate 12 points in Templestowe.
            # Generate 8 points in Templestowe.
            new_flight_point = models.FlightPoint(
                flight_point_hash = uuid.uuid4().hex,
                timestamp = base_timestamp,
                aircraft = pol32
            )
            # Transform point tuple, and set as geom.
            position = geometry.Point(self._transform_tuple(point_in_templestowe))
            new_flight_point.set_crs(3112)
            new_flight_point.set_position(position)
            templestowe.flight_points_.append(new_flight_point)
        # Flush all to db.
        db.session.flush()

        # Now, use instance level properties to ensure that doncaster east has, in total, 30 flight points.
        self.assertEqual(doncaster_east.num_flight_points, 30)
        # And templestowe has 20 in total.
        self.assertEqual(templestowe.num_flight_points, 20)
        # Now, get the number of flight points for both doncaster east and templestowe via expression. Ensure values are equal to 30 and 20 respectively again.
        doncaster_east_num_flight_points = db.session.query(models.Suburb.num_flight_points)\
            .filter(models.Suburb.suburb_hash == doncaster_east.suburb_hash)\
            .filter(models.FlightPoint.suburb_hash == models.Suburb.suburb_hash)\
            .scalar()
        self.assertEqual(doncaster_east_num_flight_points, 30)
        templestowe_num_flight_points = db.session.query(models.Suburb.num_flight_points)\
            .filter(models.Suburb.suburb_hash == templestowe.suburb_hash)\
            .filter(models.FlightPoint.suburb_hash == models.Suburb.suburb_hash)\
            .scalar()
        self.assertEqual(templestowe_num_flight_points, 20)

        highest_num_flight_points = db.session.query(
            models.Suburb.suburb_hash,
            models.Suburb.name,
            models.Suburb.num_flight_points
        )\
        .join(models.FlightPoint, models.FlightPoint.suburb_hash == models.Suburb.suburb_hash)\
        .group_by(models.Suburb.suburb_hash)\
        .order_by(desc(models.Suburb.num_flight_points))\
        .first()
        self.assertEqual(highest_num_flight_points[0], doncaster_east.suburb_hash)

        # Create a GeospatialSuburbViewIntersection, omit suburbs.
        suburb_view_intersection = geospatial.GeospatialSuburbViewIntersection()
        # Now, ensure the num flight points ceiling is 30.
        self.assertEqual(suburb_view_intersection.num_flight_points_ceiling, 30)'''


class TestGeospatialOptimisation(UsePhysicalGeospatialDatabase):
    def test_determine_epsg_codes(self):
        # Import all test suburbs.
        geospatial.read_suburbs_from(config.SUBURBS_DIR, process_neighbourships = True)
        # Ensure we can find Doncaster East.
        doncaster_east = models.Suburb.get_by_name("Doncaster East")
        # Create a list of each resulting instance of the flight points.
        flight_point_hashes = ["0x1", "0x2", "0x3"]
        coordinates = [(-37.758679, 145.171284), (-37.768721, 145.169020), (-37.813535, 145.196489)]
        timestamps = [time.time(), time.time()+10, time.time()+20]
        # Get points from these.
        points = list(self._build_flight_points(flight_point_hashes, coordinates, timestamps))
        db.session.add_all(points)
        db.session.flush()
        # Determine EPSGs for doncaster east suburb.
        geospatial.determine_epsg_codes_for_suburb(doncaster_east)
        db.session.flush()
        # Ensure there's one, 32755.
        self.assertEqual(len(doncaster_east.epsgs), 1)
        self.assertEqual(doncaster_east.epsgs[0], 32755)


class TestSuburbsViewIntersection(UsePhysicalGeospatialDatabase):
    '''def test_suburbs_in_view_postgis(self):
        """
        Import all PROPER VIC suburbs. This is many of them, like 900+ suburbs.
        """
        # Set suburbs dir to the NON test version.
        config.SUBURBS_DIR = os.path.join("testdata", "test-suburbs")
        # Import all VIC suburbs.
        geospatial.read_suburbs_from(config.SUBURBS_DIR, process_neighbourships = True)
        # Get the suburbs in view.
        all_suburbs_geojson = geospatial.geojson_suburbs_within_view("EPSG:4326", [144.9138996287245, -37.82490546599817, 145.36118165688129, -37.661349210835844], 0, should_dump = False)
        # Ensure there are 126 suburbs in this feature collection.
        self.assertEqual(len(all_suburbs_geojson["features"]), 126)'''

    def test_to_geojson(self):
        """
        Import all PROPER VIC suburbs. This is many of them, like 900+ suburbs.
        """
        # Import all test suburbs.
        geospatial.read_suburbs_from(config.SUBURBS_DIR, process_neighbourships = True)
        # Load 2 different native traces.
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        aircraft_7c4ef2, aircraft_7c4ef2_flights = self._import_all_flights("aircraft_7c4ef2_t1.json", 6)
        db.session.flush()
        # Geolocate all days in database.
        for day in db.session.query(models.Day).all():
            # Call out to geospatial module, requesting a revision for geolocation data on that day.
            receipt = geospatial.revise_geolocation_for(aircraft_7c4ee8, day.day, force = True)
            receipt = geospatial.revise_geolocation_for(aircraft_7c4ef2, day.day, force = True)
        db.session.flush()

        def ensure_num_flight_points(all_suburbs, should_be):
            num_flight_points = 0
            for suburb in all_suburbs["features"]:
                num_flight_points += suburb["properties"]["num_points"]
            self.assertEqual(num_flight_points, should_be)

        # Get the suburbs in view, with NO aircraft selected.
        all_suburbs_geojson = geospatial.geojson_suburbs_within_view("EPSG:4326", [144.9138996287245, -37.82490546599817, 145.36118165688129, -37.661349210835844], 0,
            should_dump = False, show_only_aircraft = [])
        # Ensure there are 126 suburbs in this feature collection.
        self.assertEqual(len(all_suburbs_geojson["features"]), 126)
        # Ensure this is 0.
        ensure_num_flight_points(all_suburbs_geojson, 0)
        # Get the suburbs in view, with ALL aircraft selected.
        all_suburbs_geojson = geospatial.geojson_suburbs_within_view("EPSG:4326", [144.9138996287245, -37.82490546599817, 145.36118165688129, -37.661349210835844], 0,
            should_dump = False)
        # Ensure there are 126 suburbs in this feature collection.
        self.assertEqual(len(all_suburbs_geojson["features"]), 126)
        # Ensure this is 1838.
        ensure_num_flight_points(all_suburbs_geojson, 1838)
        # Get the suburbs in view, with JUST 'POL35' selected.
        all_suburbs_geojson = geospatial.geojson_suburbs_within_view("EPSG:4326", [144.9138996287245, -37.82490546599817, 145.36118165688129, -37.661349210835844], 0,
            should_dump = False, show_only_aircraft = ["POL35"])
        # Ensure there are 126 suburbs in this feature collection.
        self.assertEqual(len(all_suburbs_geojson["features"]), 126)
        # Ensure this is 196.
        ensure_num_flight_points(all_suburbs_geojson, 196)


class TestAircraftSummaryGeospatialWork(UsePhysicalGeospatialDatabase):
    def test_most_frequented_suburbs(self):
        # Import all test suburbs.
        geospatial.read_suburbs_from(config.SUBURBS_DIR, process_neighbourships = True)
        # Load 3 different native traces.
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.flush()
        # Geolocate all days in database.
        for day in db.session.query(models.Day).all():
            # Call out to geospatial module, requesting a revision for geolocation data on that day.
            receipt = geospatial.revise_geolocation_for(aircraft_7c4ee8, day.day, force = True)
        db.session.flush()
        # Now, for 7c4ee8, the one most often
        summary = airvehicles.AircraftSummary(aircraft_7c4ee8)
        most_frequented = summary.most_frequented_suburbs
        # Ensure top 3 is 'Essendon Fields', 'Pascoe Value', 'Doncaster East'
        self.assertEqual(most_frequented[0][0].name, "Essendon Fields")
        self.assertEqual(most_frequented[1][0].name, "Pascoe Vale")
        self.assertEqual(most_frequented[2][0].name, "Doncaster East")
