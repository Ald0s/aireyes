import os
import base64
import json
import time
import decimal
import pyproj
import uuid
from datetime import date, datetime, timedelta, timezone, time as dttime

from sqlalchemy import asc

from tests.conftest import BaseCase

from app import db, config, models, error, calculations, flights, airvehicles, geospatial


class CalculationBaseCase(BaseCase):
    def setUp(self):
        super().setUp()
        """For testing purpose, we'll ensure STAT_COUNT_GROUND_TIME_AS_FLIGHT_TIME is enabled."""
        config.STAT_COUNT_GROUND_TIME_AS_FLIGHT_TIME = True

    def _get_7c6bcf_manager(self):
        # First, we must read native test data.
        aircraft, dates = self._load_native_test_data("aircraft_7c6bcf_t1.json")
        db.session.flush()
        # We'll only use the small flight on the 26th for this.
        flight_points_2606 = aircraft.flight_points_from_day(date(2022, 6, 26))
        # Make a manager from it.
        return aircraft, flights.FlightPointsManager(flight_points_2606)


class TestCalculations(CalculationBaseCase):
    def test_total_flight_time_from(self):
        """
        Given a list of flight points, ensure we can determine the total number of minutes the aircraft was active for.
        """
        aircraft, flight_points_manager = self._get_7c6bcf_manager()
        # Ensure we get 51 minutes.
        self.assertEqual(calculations.total_flight_time_from(flight_points_manager), 51)
        # Now, derive flight points manager requiring airborn only.
        flight_points_manager = flight_points_manager.derive_manager(airborne_only = True)
        # Ensure we get 45 minutes.
        self.assertEqual(calculations.total_flight_time_from(flight_points_manager), 45)

    def test_total_distance_travelled_from(self):
        """
        """
        aircraft, flight_points_manager = self._get_7c6bcf_manager()
        # Ensure we get 522899 meters.
        self.assertEqual(calculations.total_distance_travelled_from(flight_points_manager), 517034)

    def test_average_speed_from(self):
        """
        """
        aircraft, flight_points_manager = self._get_7c6bcf_manager()
        # Ensure we get 336 knots.
        self.assertEqual(calculations.average_speed_from(flight_points_manager), 336)

    def test_average_altitude_from(self):
        """
        """
        aircraft, flight_points_manager = self._get_7c6bcf_manager()
        # Ensure we get 19778 feet.
        self.assertEqual(calculations.average_altitude_from(flight_points_manager), 19778)

    def test_estimate_total_fuel_used_by(self):
        """
        """
        # Load our test aircraft.
        aircraft, flight_points_manager = self._get_7c6bcf_manager()
        # Load fuel data for this aircraft, too.
        airvehicles.update_fuel_figures(aircraft)
        db.session.flush()
        # Ensure we get X gallons.
        print(calculations.estimate_total_fuel_used_by(aircraft, flight_points_manager))
        #self.assertEqual(calculations.average_altitude_from(flight_points_manager), 19778)

    def test_find_airport_via_epsg_for(self):
        """
        """
        # Load all airports.
        airvehicles.read_airports_from(config.AIRPORTS_CONFIG)
        db.session.flush()
        # Load all aircraft.
        aircraft = airvehicles.read_aircraft_from(config.KNOWN_AIRCRAFT_CONFIG)
        db.session.flush()

        # New transformer to transform each of these coordinate pairs to 3112.
        transformer = pyproj.Transformer.from_crs(4326, 3112, always_xy = True)
        geodetic_transformer = pyproj.Transformer.from_crs(transformer.target_crs.to_epsg(), transformer.target_crs.geodetic_crs.to_epsg(), always_xy = True)
        # Now, create a number of positions.
        # The first is Essendon, second is Melbourne, third is Brisbane West Wellcamp Airport
        nearest_positions = [
            [144.958, -37.731],
            [144.842, -37.692],
            [151.815506, -27.569459]
        ]
        nearest_positions = [transformer.transform(*position) for position in nearest_positions]
        nearest_epsgs = [calculations.epsg_code_for(*position, transformer = geodetic_transformer) for position in nearest_positions]
        # Create flight points for each.
        flight_points = []
        for position, epsg in zip(nearest_positions, nearest_epsgs):
            flight_point = models.FlightPoint(flight_point_hash = uuid.uuid4().hex.lower(), timestamp = time.time())
            flight_point.set_crs(3112)
            flight_point.set_position(position)
            flight_point.set_utm_epsg(epsg)
            flight_points.append(flight_point)
        # Now, use calculations module to retrieve closest airport, using our first aircraft.
        # Check nearest essendon.
        airport = calculations.find_airport_via_epsg_for(aircraft[0], flight_points[0])
        self.assertIsNotNone(airport)
        self.assertEqual(airport.name, "Melbourne Essendon")

        # Check nearest melbourne.
        airport = calculations.find_airport_via_epsg_for(aircraft[0], flight_points[1])
        self.assertIsNotNone(airport)
        self.assertEqual(airport.name, "Melbourne Intl")

        # Check nearest toowoomba.
        airport = calculations.find_airport_via_epsg_for(aircraft[0], flight_points[2])
        self.assertIsNotNone(airport)
        self.assertEqual(airport.name, "Brisbane West Wellcamp Airport")

    def test_find_airport_via_search_for(self):
        """
        """
        # Load all airports.
        airvehicles.read_airports_from(config.AIRPORTS_CONFIG)
        db.session.flush()
        # Load all aircraft.
        aircraft = airvehicles.read_aircraft_from(config.KNOWN_AIRCRAFT_CONFIG)
        db.session.flush()

        # Now, create a number of positions.
        nearest_essendon = [144.958, -37.731]
        nearest_melbourne = [144.842, -37.692]
        nearest_toowoomba = [151.869, -27.573]
        # New transformer to transform each of these coordinate pairs to 3112.
        transformer = pyproj.Transformer.from_crs(4326, 3112, always_xy = True)
        nearest_essendon = transformer.transform(*nearest_essendon)
        nearest_melbourne = transformer.transform(*nearest_melbourne)
        nearest_toowoomba = transformer.transform(*nearest_toowoomba)

        # Now, use calculations module to retrieve closest airport, using our first aircraft.
        # Check nearest essendon.
        airport = calculations.find_airport_via_search_for(aircraft[0], nearest_essendon)
        self.assertIsNotNone(airport)
        self.assertEqual(airport.name, "Melbourne Essendon")

        # Check nearest melbourne.
        airport = calculations.find_airport_via_search_for(aircraft[0], nearest_melbourne)
        self.assertIsNotNone(airport)
        self.assertEqual(airport.name, "Melbourne Intl")

        # Check nearest toowoomba.
        airport = calculations.find_airport_via_search_for(aircraft[0], nearest_toowoomba)
        self.assertIsNotNone(airport)
        self.assertEqual(airport.name, "Toowoomba")

    def test_calculate_co2_emissions(self):
        total_co2_used = calculations.calculate_co2_emissions_per_hour(5556, 910, 333, 59.6, 3.15)
        self.assertEqual(total_co2_used, 30636)
