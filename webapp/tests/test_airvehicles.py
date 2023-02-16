import os
import base64
import json
import time
import decimal

from subprocess import Popen, PIPE
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import asc, desc, func

from tests.conftest import BaseCase

from app import db, config, models, airvehicles, error, flights, geospatial, viewmodel


class TestAircraftModel(BaseCase):
    def test_total_carbon_emissions(self):
        # Create all aircraft from the states.
        aircraft = airvehicles.read_aircraft_from("aircraft_states.json")
        db.session.flush()
        # Import all flights.
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.flush()
        # Use instance to get the total emissions.
        total_emissions = aircraft_7c4ee8.total_carbon_emissions
        # Now, we'll query for total emissions from the aircraft directly.
        total_emissions_q = db.session.query(models.Aircraft.total_carbon_emissions)\
            .join(models.Aircraft.flights_)\
            .filter(models.Aircraft.icao == aircraft_7c4ee8.icao)\
            .scalar()
        self.assertEqual(total_emissions, total_emissions_q)

    def test_latest_flight(self):
        # Load 3 different native traces.
        aircraft_7c68b7, aircraft_7c68b7_flights = self._import_all_flights("aircraft_7c68b7_t1.json", 3)
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        aircraft_7c6bcf, aircraft_7c6bcf_flights = self._import_all_flights("aircraft_7c6bcf_t1.json", 4)
        db.session.flush()
        # Get the latest flight for each aircraft.
        aircraft_7c68b7_latest = aircraft_7c68b7.latest_flight
        aircraft_7c4ee8_latest = aircraft_7c4ee8.latest_flight
        aircraft_7c6bcf_latest = aircraft_7c6bcf.latest_flight
        # Ensure each first point's datetime iso matches the first point of the final partial.
        self.assertEqual(aircraft_7c68b7_latest.first_point.datetime_iso, "2022-07-29 05:54:09")
        self.assertEqual(aircraft_7c4ee8_latest.first_point.datetime_iso, "2021-07-19 22:26:05")
        self.assertEqual(aircraft_7c6bcf_latest.first_point.datetime_iso, "2022-06-25 23:17:06")


class TestAirport(BaseCase):
    def test_create_all_airports(self):
        """
        Test airvehicles module's capability to create all airports.
        """
        # Use the module to now load all airports, and add them to the database.
        airports = airvehicles.read_airports_from("airports.json")
        db.session.flush()
        # Ensure we can find Melbourne Essendon from the database.
        melbourne_essendon = models.Airport.find_by_name("Melbourne essendon")
        self.assertIsNotNone(melbourne_essendon)
        # Ensure this airport has 1 epsg.
        self.assertEqual(len(melbourne_essendon.epsgs), 1)
        # Ensure that EPSG is 32755.
        self.assertEqual(melbourne_essendon.epsgs[0], 32755)
        # Find the airport with name containing 'noonkanbah'.
        # In source data, this airport's name is prepended with '[DUPLICATE] ', ensure this is no longer the case.
        noonkanbah = models.Airport.find_by_name("noonkanbah")
        self.assertIsNotNone(noonkanbah)
        # Ensure name is 'Noonkanbah Airport'.
        self.assertEqual(noonkanbah.name, "Noonkanbah Airport")

    def test_create_airport(self):
        """
        Test AirportSchema's ability to correctly load an airport from our input JSON file.
        """
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "airports.json"), "r") as f:
            airports_json = json.loads(f.read())
        # Locate Melbourne Essendon.
        located_airports = list(filter(lambda airport_d: airport_d["name"] == "MELBOURNE ESSENDON", airports_json))
        # Ensure we found 1.
        self.assertEqual(len(located_airports), 1)
        # Use the schema to load this Airport and verify some attributes.
        airport_d = airvehicles.AirportSchema().load(located_airports[0])
        # Now, get an Airport model.
        airport = models.Airport(**airport_d)
        # Ensure airport has is 21eb4b8a8159be5db58db7870019037d
        self.assertEqual(airport.airport_hash, "21eb4b8a8159be5db58db7870019037d")
        # Ensure name is 'Melbourne Essendon'.
        self.assertEqual(airport.name, "Melbourne Essendon")
        # Ensure latitude is close to -4284583.882341818.
        self.assertAlmostEqual(int(airport.center.y), -4284583)
        # Ensure longitude is close to 965073.
        self.assertAlmostEqual(int(airport.center.x), 965073)


class TestAircraft(BaseCase):
    def test_load_aircraft_with_schema(self):
        """
        Ensure the schema loads all required inputs of an aircraft.
        """
        aircraft_no_airport_code = {
            "icao": "7c5b4c",
            "flightName": "QLK58",
            "registration": "VH-SBI",
            "type": "DH8C",
            "description": "2004 DE HAVILLAND DHC-8-300 Dash 8",
            "ownerOperator": "EASTERN AUSTRALIA AIRLINES PTY. LIMITED",
            "year": "2004",
            "image": "",
            "FlightPoints": []
        }
        # Load this aircraft.
        aircraft_schema = airvehicles.AircraftSchema()
        aircraft = aircraft_schema.load(aircraft_no_airport_code)
        # Ensure airport code is '4c'
        self.assertEqual(aircraft["airport_code"], "4c")
        aircraft_airport_code = {
            "icao": "7c5b4c",
            "flightName": "QLK58",
            "registration": "VH-SBI",
            "type": "DH8C",
            "description": "2004 DE HAVILLAND DHC-8-300 Dash 8",
            "ownerOperator": "EASTERN AUSTRALIA AIRLINES PTY. LIMITED",
            "year": "2004",
            "airportCode": "xo",
            "image": "",
            "FlightPoints": []
        }
        # Load this aircraft.
        aircraft_schema = airvehicles.AircraftSchema()
        aircraft = aircraft_schema.load(aircraft_airport_code)
        # Ensure airport code is 'xo'
        self.assertEqual(aircraft["airport_code"], "xo")

    def test_create_known_aircraft(self):
        """
        Use the airvehicles module to invoke the creation of all known aircraft.
        We should then be able to find each of the key police aircraft in results; POL30, POL31, POL32, POL35
        """
        # Call out to airvehicles module to create them all.
        aircraft = airvehicles.read_aircraft_from("aircraft_states.json")
        db.session.flush()
        # Ensure there are more than 0 results.
        self.assertNotEqual(len(aircraft), 0)
        # Now, ensure we can find each of the aircraft's flight names in our reference list.
        # ENsure each aircraft also has valid fuel info.
        for a in aircraft:
            self.assertIn(a.flight_name, ["POL30", "POL31", "POL32", "POL35"])
            self.assertEqual(a.has_valid_fuel_data, True)

    def test_create_aircraft(self):
        """
        Test the AircraftSchema's ability to create new Aircraft models.
        Read JSON array from aircrafts_7c68b7.json
        There should be a single aircraft in the array
        Load the stored object with AircraftSchema, exclude the FlightPoints attribute, as that is out of this test's scope.
        Ensure the read aircraft model is correct.
        """
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Load the stored object with AircraftSchema, exclude FlightPoints; we will get back a dict for Aircraft.
        aircraft_d = airvehicles.AircraftSchema(exclude = ("flight_points",)).load(aircraft_json)
        # Make a new aircraft.
        aircraft = models.Aircraft(**aircraft_d)
        # Ensure instance of Aircraft.
        self.assertIsInstance(aircraft, models.Aircraft)
        # Ensure icao is correct.
        self.assertEqual(aircraft.icao, "7c68b7")
        # Ensure type is correct.
        self.assertEqual(aircraft.type, "E55P")
        # Ensure flight name is correct.
        self.assertEqual(aircraft.flight_name, "UYX")
        # Ensure registration is correct.
        self.assertEqual(aircraft.registration, "VH-UYX")
        # Ensure description is correct.
        self.assertEqual(aircraft.description, "EMBRAER EMB-505 Phenom 300")
        # Ensure year is correct.
        self.assertEqual(aircraft.year, 2019)
        # Ensure owner operator is correct.
        self.assertEqual(aircraft.owner_operator, "FLIGHT OPTIONS (AUSTRALIA) PTY LTD")

    def test_create_aircraft_flight_point(self):
        """
        Test the FlightPointSchema's ability to create new FlightPoint models.
        Read JSON array from aircrafts_7c68b7.json
        There should be a single aircraft in the array
        Load the first flight point with FlightPointSchema.
        Ensure the read flight point model is correct.
        Ensure the read flight point's 'day' attribute is equal to '2022-07-29'
        """
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
            flight_points_json = aircraft_json["FlightPoints"]
        # Load this flight point with FlightPointSchema, should get back a FlightPoint
        flight_point_json = flight_points_json[0]
        flight_point = airvehicles.FlightPointSchema().load(flight_point_json)
        # Ensure flight point hash is correct.
        self.assertEqual(flight_point.flight_point_hash, "bca41ed27106b10714ad6b74b3035a8d")
        # Ensure aircraft icao is correct.
        self.assertEqual(flight_point.aircraft_icao, "7c68b7")
        # Ensure timestamp is correct.
        self.assertAlmostEqual(flight_point.timestamp, decimal.Decimal(1659053010.21), 3)
        # Ensure latitude is correct.
        self.assertAlmostEqual(int(flight_point.position.y), -3929749)
        # Ensure longitude is correct.
        self.assertAlmostEqual(int(flight_point.position.x), 1576132)
        # Ensure altitude is correct.
        self.assertEqual(flight_point.altitude, 0)
        # Ensure ground speed is correct.
        self.assertAlmostEqual(flight_point.ground_speed, decimal.Decimal(0))
        # Ensure rotation is correct.
        self.assertAlmostEqual(flight_point.rotation, decimal.Decimal(108.4))
        # Ensure vertical rate is correct.
        self.assertEqual(flight_point.vertical_rate, None)
        # Ensure not on ground, not ascending and not descending.
        self.assertEqual(flight_point.is_on_ground, True)
        self.assertEqual(flight_point.is_ascending, False)
        self.assertEqual(flight_point.is_descending, False)
        # Ensure the flight point's 'day' attribute is equal to 2022-07-29; this was derived from the timestamp.
        self.assertEqual(flight_point.day_day, date(2022, 7, 29))

    def test_create_aircraft_flight_point_extraordinary(self):
        """
        Test the FlightPointSchema's ability to create new FlightPoint models, but flight point models of varying integrity.
        Read all JSON from native testdata aircraft_7c4ef4_t1
        There should be a single aircraft in the array
        Load the first flight point with FlightPointSchema.
        Ensure the read flight point model is correct.
        Ensure the read flight point's 'day' attribute is equal to '2022-07-29'
        """
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c4ef4_t2.json"), "r") as f:
            aircraft_json = json.loads(f.read())
            flight_points_json = aircraft_json["FlightPoints"]
        # Get the first item from this flight points list. It is summarised as follows;
        # The following data points are valid; longitude and latitude, a valid altitude, a valid groundspeed and a valid data source.
        # The following data points are NOT valid; track (rotation.)
        flight_point = flight_points_json[0]
        flight_point = airvehicles.FlightPointSchema().load(flight_point)
        # Ensure this point has a valid position.
        self.assertEqual(flight_point.is_position_valid, True)
        # Ensure we have a valid groundspeed, data source and altitude.
        self.assertIsNotNone(flight_point.ground_speed)
        self.assertIsNotNone(flight_point.altitude)
        self.assertIsNotNone(flight_point.data_source)
        # Ensure data source for this is adsb_icao
        self.assertEqual(flight_point.data_source, "adsb_icao")
        # Ensure track (rotation) is invalid, though.
        self.assertIsNone(flight_point.rotation)

        # Now, get the next flight point.
        # The following data points are valid; longitude, latitude, altitude, altitude rate, grounspeed, rotation (track) and data source.
        flight_point = flight_points_json[390]
        flight_point = airvehicles.FlightPointSchema().load(flight_point)
        # Ensure this point does have a valid position.
        self.assertEqual(flight_point.is_position_valid, True)
        # Ensure we have a valid groundspeed, data source and altitude and altitude rate.
        self.assertIsNotNone(flight_point.ground_speed)
        self.assertIsNotNone(flight_point.altitude)
        self.assertIsNotNone(flight_point.vertical_rate)
        self.assertIsNotNone(flight_point.data_source)
        self.assertIsNotNone(flight_point.rotation)
        # Ensure data source for this is mlat
        self.assertEqual(flight_point.data_source, "mlat")

        # Now, get the next flight point.
        # The following data points are valid; altitude, altitude rate, grounspeed, rotation (track) and data source.
        # The following data points are NOT valid; longitude, latitude
        flight_point = flight_points_json[411]
        flight_point = airvehicles.FlightPointSchema().load(flight_point)
        # Ensure this point does not have a valid position.
        self.assertEqual(flight_point.is_position_valid, False)
        # Ensure we have a valid groundspeed, data source and altitude and altitude rate.
        self.assertIsNotNone(flight_point.ground_speed)
        self.assertIsNotNone(flight_point.altitude)
        self.assertIsNotNone(flight_point.vertical_rate)
        self.assertIsNotNone(flight_point.data_source)
        self.assertIsNotNone(flight_point.rotation)
        # Ensure data source for this is mode_s
        self.assertEqual(flight_point.data_source, "mode_s")

    def test_create_aircraft_and_points(self):
        """
        Test the AircraftSchema's ability to create new Aircrafts & points for it.
        Read the flight information from testdata aircraft_7c68b7_t1.json
        Load the aircraft with the AircraftSchema type, do not exclude the points.
        There should be 1708 points.
        """
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Load the stored object with AircraftSchema; get back a dict for Aircraft.
        aircraft_d = airvehicles.AircraftSchema().load(aircraft_json)
        # Make the aircraft.
        aircraft = models.Aircraft(**aircraft_d)
        # Add the aircraft to database, and flush.
        db.session.add(aircraft)
        db.session.flush()
        # Ensure there are 1708 points.
        self.assertEqual(aircraft.num_flight_points, 1708)

    def test_new_aircraft(self):
        """
        Test the flight module's capability to receive aircraft updates.
        Load the aircraft JSON from aircrafts_7c68b7.json
        Use AircraftSchema to load this JSON.
        We will then use the airvehicles module to submit this aircraft.
        """
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Load the stored object with AircraftSchema.
        aircraft_d = airvehicles.AircraftSchema().load(aircraft_json)
        # Now, submit this with airvehicles module, do not update fuel figures.
        aircraft, flight_points, synchronised_flight_points = airvehicles.aircraft_submitted(aircraft_d, should_update_fuel_figures = False)
        db.session.flush()
        # Ensure we got 1708 points back.
        self.assertEqual(len(flight_points), 1708)
        # But, ensure this aircraft does not have valid fuel figures.
        self.assertEqual(aircraft.has_valid_fuel_data, False)

    def test_existing_aircraft(self):
        """
        Test the airvehicles module's capability to process flight point updates.
        Load the aircraft JSON from aircrafts_7c68b7.json
        Get the first aircraft. We will now subsection that JSON object's FlightPoints into 3 parts of differing length. Parts 1 and 2 will have overlapping items.
        Use AircraftSchema to load this JSON.
        We will then use the airvehicles module to submit this aircraft.
        """
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Subsect flight points into 3 parts.
        # Part 1: 0 -> 20
        flight_points_sub1 = aircraft_json["FlightPoints"][:20]
        # Part 2: 15 -> 100
        flight_points_sub2 = aircraft_json["FlightPoints"][15:100]
        # Part 3: 100 -> remainder
        flight_points_sub3 = aircraft_json["FlightPoints"][100:]

        # Set the aircraft_json's FlightPoints to sub1.
        aircraft_json["FlightPoints"] = flight_points_sub1

        # Load the stored object with the modified AircraftSchema.
        aircraft = airvehicles.AircraftSchema().load(aircraft_json)
        # Now, submit this with airvehicles module.
        aircraft, flight_points, synchronised_flight_points = airvehicles.aircraft_submitted(aircraft)
        db.session.flush()
        # Ensure we got 20 points back.
        self.assertEqual(len(synchronised_flight_points), 20)

        # Now, set aircraft json's flight points to sub2, then reload for a new Aircraft.
        aircraft_json["FlightPoints"] = flight_points_sub2
        aircraft = airvehicles.AircraftSchema().load(aircraft_json)
        # Submit this aircraft, get back 85 points.
        aircraft, flight_points, synchronised_flight_points = airvehicles.aircraft_submitted(aircraft)
        db.session.flush()
        # Ensure we got 85 points back.
        self.assertEqual(len(synchronised_flight_points), 85)

        # Now, set aircraft json's flight points to sub3, then reload for a new aircraft.
        aircraft_json["FlightPoints"] = flight_points_sub3
        aircraft = airvehicles.AircraftSchema().load(aircraft_json)
        # Submit this aircraft, get back 2011 points.
        aircraft, flight_points, synchronised_flight_points = airvehicles.aircraft_submitted(aircraft)
        db.session.flush()
        # Ensure we got 1608 points back.
        self.assertEqual(len(synchronised_flight_points), 1608)

    def test_new_aircraft_and_points_get_new_points(self):
        """
        Test the AircraftSchema's ability to create new Aircrafts & points for it.
        Read the flight information from testdata aircraft_7c68b7_t1.json
        Sub the flight points, get 1000.
        Create the aircraft with just these points.
        There should be 1000 points returned.
        Now, sub the flight points again, this time get 1200 (this is a combination of the first 1000 and a further 200)
        Submit this aircraft again with the 1200 points. We should get back aircraft and two lists. The first list should have 200 items, the second should have 1200.
        Ensure we get 200 points back.
        """
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Get our two subs; the first 0 -> 1000 and the second 1000 -> 1200.
        flight_points_sub1 = aircraft_json["FlightPoints"][:1000]
        self.assertEqual(len(flight_points_sub1), 1000)
        flight_points_sub2 = aircraft_json["FlightPoints"][:1200]
        self.assertEqual(len(flight_points_sub2), 1200)

        # Load the aircraft with the first sub.
        aircraft, flight_points, synchronised_flight_points = self._submit_flight_point_dicts(aircraft_json, flight_points_sub1)
        # Ensure there are 1000 points returned.
        self.assertEqual(len(flight_points), 1000)
        # Ensure aircraft has 1000 total points.
        self.assertEqual(aircraft.num_flight_points, 1000)

        # Now, load sub2, which is those 1000 points, plus another 200 points.
        aircraft, flight_points, synchronised_flight_points = self._submit_flight_point_dicts(aircraft_json, flight_points_sub2)
        # Ensure there are 200 points returned.
        self.assertEqual(len(flight_points), 200)
        # Ensure synchronised has 1200 total points.
        self.assertEqual(len(synchronised_flight_points), 1200)

    def test_aircraft_last_seen(self):
        """
        Tests both the instance level & expression level hybrid attributes for retrieving an aircraft's last seen property in seconds.
        This will test both the case that the aircraft has never been seen, and where they have.
        """
        # Get current timestamp. Set it for test purposes.
        current_time = time.time()
        self.set_current_timestamp(current_time)
        # Now, import 7c68b7's trace.
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Subsect flight points into 1 part.
        # Part 1: 0 -> 20
        flight_points_sub1 = aircraft_json["FlightPoints"][:20]
        # Set the aircraft_json's FlightPoints to an empty list.
        aircraft_json["FlightPoints"] = []
        # Load the stored object with the modified AircraftSchema.
        aircraft = airvehicles.AircraftSchema().load(aircraft_json)
        # Now, submit this with airvehicles module.
        aircraft, flight_points, synchronised_flight_points = airvehicles.aircraft_submitted(aircraft)
        db.session.flush()
        # Ensure we got 0 points back.
        self.assertEqual(len(flight_points), 0)

        # Now, get the number of seconds since last seen, via instance.
        instance_level_seconds_since_seen = aircraft.seconds_since_last_seen
        # Ensure this is None.
        self.assertIsNone(instance_level_seconds_since_seen)
        # Perform a query for this exact aircraft by its icao, retrieving just the seconds since last seen, via expression.
        expression_level_seconds_since_seen = db.session.query(models.Aircraft.seconds_since_last_seen)\
            .filter(models.Aircraft.icao == aircraft.icao)\
            .scalar()
        # Ensure this is also None.
        self.assertIsNone(expression_level_seconds_since_seen)

        # Set the aircraft_json's FlightPoints to sub1.
        aircraft_json["FlightPoints"] = flight_points_sub1
        # Load the stored object with the modified AircraftSchema.
        aircraft = airvehicles.AircraftSchema().load(aircraft_json)
        # Now, submit this with airvehicles module.
        aircraft, flight_points, synchronised_flight_points = airvehicles.aircraft_submitted(aircraft)
        db.session.commit()
        # Ensure we got 20 points back.
        self.assertEqual(len(flight_points), 20)
        # Now, get the number of seconds since last seen, via instance.
        instance_level_seconds_since_seen = aircraft.seconds_since_last_seen
        # Perform a query for this exact aircraft by its icao, retrieving just the seconds since last seen, via expression.
        expression_level_seconds_since_seen = db.session.query(models.Aircraft.seconds_since_last_seen)\
            .filter(models.Aircraft.icao == aircraft.icao)\
            .scalar()
        # Ensure the instance level & expression level values are equal.
        self.assertEqual(instance_level_seconds_since_seen, expression_level_seconds_since_seen)

    '''def test_aircraft_altitude(self):
        """
        Tests both the instance level & expression level hybrid attributes for retrieving an aircraft's last seen altitude property.
        This will test both the case that the aircraft has never been seen, and where they have.
        """
        # Get current timestamp. Set it for test purposes.
        current_time = time.time()
        self.set_current_timestamp(current_time)
        # Now, import 7c68b7's trace.
        with open(os.path.join(os.getcwd(), "testdata", "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Subsect flight points into 2 parts.
        # Part 1: 0 -> 20
        flight_points_sub1 = aircraft_json["FlightPoints"][:20]
        # Part 2: 20 -> 500
        flight_points_sub2 = aircraft_json["FlightPoints"][20:500]
        # Set the aircraft_json's FlightPoints to an empty list.
        aircraft_json["FlightPoints"] = []
        # Load the stored object with the modified AircraftSchema.
        aircraft = airvehicles.AircraftSchema().load(aircraft_json)
        # Now, submit this with airvehicles module.
        aircraft, flight_points, synchronised_flight_points = airvehicles.aircraft_submitted(aircraft)
        db.session.flush()
        # Ensure we got 0 points back.
        self.assertEqual(len(flight_points), 0)
        # Now, get the altitude since last seen, via instance.
        instance_level_last_seen_altitude = aircraft.last_seen_altitude
        # Ensure this is None.
        self.assertIsNone(instance_level_last_seen_altitude)
        # Perform a query for this exact aircraft by its icao, retrieving just the altitude since last seen, via expression.
        expression_level_last_seen_altitude = db.session.query(models.Aircraft.last_seen_altitude)\
            .join(models.Aircraft.flight_points_)\
            .filter(models.Aircraft.icao == aircraft.icao)\
            .scalar()
        # Ensure this is also None.
        self.assertIsNone(expression_level_last_seen_altitude)

        # Set the aircraft_json's FlightPoints to sub1.
        aircraft_json["FlightPoints"] = flight_points_sub1
        # Load the stored object with the modified AircraftSchema.
        aircraft = airvehicles.AircraftSchema().load(aircraft_json)
        # Now, submit this with airvehicles module.
        aircraft, flight_points, synchronised_flight_points = airvehicles.aircraft_submitted(aircraft)
        db.session.flush()
        # Ensure we got 20 points back.
        self.assertEqual(len(flight_points), 20)
        # Now, get the number of seconds since last seen, via instance.
        instance_level_last_seen_altitude = aircraft.last_seen_altitude
        # Perform a query for this exact aircraft by its icao, retrieving just the last seen altitude, via expression.
        expression_level_last_seen_altitude = db.session.query(models.Aircraft.last_seen_altitude)\
            .join(models.Aircraft.flight_points_)\
            .filter(models.Aircraft.icao == aircraft.icao)\
            .group_by(models.Aircraft.icao)\
            .scalar()
        # Ensure the instance level & expression level values are equal.
        self.assertEqual(instance_level_last_seen_altitude, expression_level_last_seen_altitude)

        # Now, submit sub2.
        aircraft, flight_points, synchronised_flight_points = self._submit_flight_point_dicts(aircraft_json, flight_points_sub2)
        # Now, get the number of seconds since last seen, via instance.
        instance_level_last_seen_altitude = aircraft.last_seen_altitude
        # Perform a query for this exact aircraft by its icao, retrieving just the last seen altitude, via expression.
        expression_level_last_seen_altitude = db.session.query(models.Aircraft.last_seen_altitude)\
            .join(models.Aircraft.flight_points_)\
            .filter(models.Aircraft.icao == aircraft.icao)\
            .group_by(models.Aircraft.icao)\
            .scalar()
        # Ensure the instance level & expression level values are equal.
        self.assertEqual(instance_level_last_seen_altitude, expression_level_last_seen_altitude)'''

    def test_aircraft_is_active_now(self):
        """
        """
        # Get current timestamp, minus 10 seconds. Set it for test purposes.
        current_time = time.time()
        self.set_current_timestamp(current_time-10)
        # Now, import 7c68b7's trace.
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Subsect flight points into 3 parts.
        # Part 1: 0 -> 20
        flight_points_sub1 = aircraft_json["FlightPoints"][:20]

        # Set the aircraft_json's FlightPoints to sub1.
        aircraft_json["FlightPoints"] = flight_points_sub1
        # Load the stored object with the modified AircraftSchema.
        aircraft = airvehicles.AircraftSchema().load(aircraft_json)
        # Now, submit this with airvehicles module.
        aircraft, flight_points, synchronised_flight_points = airvehicles.aircraft_submitted(aircraft)
        db.session.commit()
        # Ensure we got 20 points back.
        self.assertEqual(len(flight_points), 20)
        # Now, is the aircraft currently active?
        instance_level_is_active = aircraft.is_active_now
        # Perform a query for this exact aircraft by its icao, retrieving just whether the aircraft is currently active, via expression.
        expression_level_is_active = db.session.query(models.Aircraft.is_active_now)\
            .filter(models.Aircraft.icao == aircraft.icao)\
            .scalar()
        # Ensure the instance level & expression level values are equal.
        self.assertEqual(instance_level_is_active, expression_level_is_active)
        # Answer, overall, should be False.
        self.assertEqual(instance_level_is_active, False)

        # Set current timestamp to the timestamp+20 found in the last point in our sub1.
        self.set_current_timestamp(flight_points[len(flight_points)-1].timestamp+20)
        # Now, execute both instance level & expression level queries for is active now.
        instance_level_is_active = aircraft.is_active_now
        # Perform a query for this exact aircraft by its icao, retrieving just whether the aircraft is currently active, via expression.
        expression_level_is_active = db.session.query(models.Aircraft.is_active_now)\
            .filter(models.Aircraft.icao == aircraft.icao)\
            .scalar()
        # Ensure the instance level & expression level values are equal.
        self.assertEqual(instance_level_is_active, expression_level_is_active)
        # Answer, overall, should be True' now that we're only 20 seconds from our last aircraft update.
        self.assertEqual(instance_level_is_active, True)

    def test_get_monitored_aircraft(self):
        """
        """
        # Load 7c68b7.
        aircraft_7c68b7, aircraft_7c68b7_flights = self._import_all_flights("aircraft_7c68b7_t1.json", 3)
        db.session.flush()
        # Load 7c4ee8.
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.flush()

        all_summaries = airvehicles.get_monitored_aircraft(active_first = True)
        self.assertEqual(len(all_summaries), 2)

    def test_aircraft_flight_statistics(self):
        """
        Test instance & expression level results for all aggregate Flight functions, we'll use 2 aircraft with valid flight information, and the rest of the
        known 5 we'll use a control to ensure cartesian products are dealt with.

        First, import all aircraft. Of those, we will focus on two; 7c68b7 and 7c6bcf.
        For each aircraft, import the following traces;
            aircraft_7c68b7_t1, 7c68b7: there are 3 flights in here.
            aircraft_7c4ee8_t1, 7c4ee8, there are 2 flights in here.
        Verify this data is correct and loaded.
        """
        # Import all aircraft.
        aircraft = airvehicles.read_aircraft_from(config.KNOWN_AIRCRAFT_CONFIG)
        db.session.flush()
        # Load 7c68b7.
        aircraft_7c68b7, aircraft_7c68b7_flights = self._import_all_flights("aircraft_7c68b7_t1.json", 3)
        db.session.flush()
        # Load 7c4ee8.
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.flush()

        # First, ensure for both our loaded flights, the instance level information matches some static data.
        # First 7c68b7.
        self.assertEqual(aircraft_7c68b7.is_active_now, False)
        self.assertEqual(aircraft_7c68b7.num_flights, 3)
        self.assertEqual(aircraft_7c68b7.distance_travelled, 1542733)
        self.assertEqual(aircraft_7c68b7.flight_time_total, 184)
        # Now 7c4ee8.
        self.assertEqual(aircraft_7c68b7.is_active_now, False)
        self.assertEqual(aircraft_7c4ee8.num_flights, 2)
        self.assertEqual(aircraft_7c4ee8.distance_travelled, 2024024)
        self.assertEqual(aircraft_7c4ee8.flight_time_total, 410)
        # Next, perform a query, grabbing these informations via expression level for each aircraft. Ensure this information matches instance level information.
        # First, 7c68b7.
        aircraft_7c68b7_query_result = db.session.query(models.Aircraft.num_flights, models.Aircraft.distance_travelled, models.Aircraft.flight_time_total)\
            .outerjoin(models.Aircraft.flights_)\
            .filter(models.Aircraft.icao == aircraft_7c68b7.icao)\
            .first()
        self.assertEqual(aircraft_7c68b7_query_result[0], aircraft_7c68b7.num_flights)
        self.assertEqual(aircraft_7c68b7_query_result[1], aircraft_7c68b7.distance_travelled)
        self.assertEqual(aircraft_7c68b7_query_result[2], aircraft_7c68b7.flight_time_total)
        # Second 7c4ee8.
        aircraft_7c4ee8_query_result = db.session.query(models.Aircraft.num_flights, models.Aircraft.distance_travelled, models.Aircraft.flight_time_total)\
            .outerjoin(models.Aircraft.flights_)\
            .filter(models.Aircraft.icao == aircraft_7c4ee8.icao)\
            .first()
        self.assertEqual(aircraft_7c4ee8_query_result[0], aircraft_7c4ee8.num_flights)
        self.assertEqual(aircraft_7c4ee8_query_result[1], aircraft_7c4ee8.distance_travelled)
        self.assertEqual(aircraft_7c4ee8_query_result[2], aircraft_7c4ee8.flight_time_total)

        # Perform these exact checks above, but with an aircraft with absolutely no flight data whatsoever.
        aircraft_7c4ef2 = models.Aircraft.get_by_icao("7c4ef2")
        self.assertIsNotNone(aircraft_7c4ef2)
        # Ensure this information is correct on instance level.
        self.assertEqual(aircraft_7c4ef2.is_active_now, False)
        self.assertEqual(aircraft_7c4ef2.num_flights, 0)
        self.assertEqual(aircraft_7c4ef2.distance_travelled, 0)
        self.assertEqual(aircraft_7c4ef2.flight_time_total, 0)
        # Query for each by expression.
        aircraft_7c4ef2_query_result = db.session.query(models.Aircraft.num_flights, models.Aircraft.distance_travelled, models.Aircraft.flight_time_total)\
            .outerjoin(models.Aircraft.flights_)\
            .filter(models.Aircraft.icao == aircraft_7c4ef2.icao)\
            .first()
        self.assertEqual(aircraft_7c4ef2_query_result[0], aircraft_7c4ef2.num_flights)
        self.assertEqual(aircraft_7c4ef2_query_result[1], aircraft_7c4ef2.distance_travelled)
        self.assertEqual(aircraft_7c4ef2_query_result[2], aircraft_7c4ef2.flight_time_total)

    '''def test_enumerate_monitored_aircraft(self):
        """
        Read all aircraft's states to begin with. Then read flight data for 7c68b7. Read only 20 flight points.
        Set the current timestamp to 20 seconds post the latest flight point in that data. Enumerate monitored aircraft, with active first. We will expect
        to receive back 7c68b7 as the first entry in the response.
        """
        # Call out to airvehicles module to create them all.
        aircraft = airvehicles.read_aircraft_from("aircraft_states.json")
        db.session.flush()
        # Now, import 7c68b7's trace.
        with open(os.path.join(os.getcwd(), "testdata", "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Subsect flight points into 3 parts.
        # Part 1: 0 -> 20
        flight_points_sub1 = aircraft_json["FlightPoints"][:20]
        # Set the aircraft_json's FlightPoints to sub1.
        aircraft_json["FlightPoints"] = flight_points_sub1
        # Load the stored object with the modified AircraftSchema.
        aircraft = airvehicles.AircraftSchema().load(aircraft_json)
        # Now, submit this with airvehicles module.
        aircraft, flight_points, synchronised_flight_points = airvehicles.aircraft_submitted(aircraft)
        db.session.commit()
        # Ensure we got 20 points back.
        self.assertEqual(len(flight_points), 20)


        # Set current timestamp to the timestamp+20 found in the last point in our sub1.
        self.set_current_timestamp(flight_points[len(flight_points)-1].timestamp+20)
        enumerated_aircraft = airvehicles.enumerate_monitored_aircraft(active_first = True)
        # Ensure we get 5 results.
        self.assertEqual(len(enumerated_aircraft), 5)
        # Ensure the first entry is 7c68b7.
        self.assertEqual(enumerated_aircraft[0].icao, "7c68b7")

        # Enumerate monitored aircraft again, this time serialise them all.
        enumerated_aircraft_d = airvehicles.enumerate_monitored_aircraft(
            active_first = True, serialise = True, SerialiseAsSchema = viewmodel.AircraftViewModelSchema)
        self.assertEqual(len(enumerated_aircraft_d), 5)'''

    '''def test_summarise_monitored_aircraft(self):
        """
        Test summarisation of aircrafts, we'll use 2 aircraft with valid flight information, and the rest of the
        known 5 we'll use a control to ensure cartesian products are dealt with.

        First, import all aircraft. Of those, we will focus on two; 7c68b7 and 7c6bcf.
        For each aircraft, import the following traces;
            aircraft_7c68b7_t1, 7c68b7: there are 3 flights in here.
            aircraft_7c4ee8_t1, 7c4ee8, there are 2 flights in here.
        Verify this data is correct and loaded.
        """
        # There should be 0 summaries located.
        all_summaries = airvehicles.summarise_monitored_aircraft(active_first = True)
        self.assertEqual(len(all_summaries), 0)

        # Import all aircraft.
        aircraft = airvehicles.read_aircraft_from(config.KNOWN_AIRCRAFT_CONFIG)
        db.session.flush()

        # There should be 4 summaries located, since 7c68b7 is not in known aircraft config.
        all_summaries = airvehicles.summarise_monitored_aircraft(active_first = True)
        self.assertEqual(len(all_summaries), 4)

        # Load 7c68b7.
        aircraft_7c68b7, aircraft_7c68b7_flights = self._import_all_flights("aircraft_7c68b7_t1.json", 3)
        db.session.flush()
        # Load 7c4ee8.
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.flush()

        # There should be 5 summaries located.
        all_summaries = airvehicles.summarise_monitored_aircraft(active_first = True)
        self.assertEqual(len(all_summaries), 5)
        # Get most recent flight for 7c4ee8
        most_recent_flight = aircraft_7c4ee8.latest_flight
        # Get flight points from that flight.
        flight_points = most_recent_flight.flight_points.all()
        # Set current timestamp to 20 seconds AFTER the last point in the most recent flight.
        self.set_current_timestamp(flight_points[len(flight_points)-1].timestamp+20)

        # There should be 5 summaries located.
        all_summaries = airvehicles.summarise_monitored_aircraft(active_first = True)
        self.assertEqual(len(all_summaries), 5)
        # Get first vehicle.
        first_vehicle = all_summaries[0]
        # The first should be 7c4ee8.
        self.assertEqual(first_vehicle.icao, "7c4ee8")
        # Ensure this vehicle is active now.
        self.assertEqual(first_vehicle.is_active_now, True)
        # Ensure there are 2 flights.
        self.assertEqual(first_vehicle.num_flights, 2)
        # Ensure seconds since last seen is 20.
        self.assertEqual(first_vehicle.seconds_since_last_seen, 20)
        # Ensure last seen altitude 27000
        self.assertEqual(first_vehicle.last_seen_altitude, 27000)
        # Ensure distance travelled 2015884
        self.assertEqual(first_vehicle.distance_travelled, 2024024)
        # Ensure total fuel used 834.
        self.assertEqual(first_vehicle.total_fuel_used, 834)
        # Ensure flight time total is 410.
        self.assertEqual(first_vehicle.flight_time_total, 410)

        # Now, get the second vehicle; 7c4ef2.
        second_vehicle = all_summaries[1]
        # The first should be 7c4ef2.
        self.assertEqual(second_vehicle.icao, "7c4ef2")
        # Ensure this vehicle is not active now.
        self.assertEqual(second_vehicle.is_active_now, False)
        # Ensure there are 0 flights.
        self.assertEqual(second_vehicle.num_flights, 0)
        # Ensure seconds since last seen is None.
        self.assertEqual(second_vehicle.seconds_since_last_seen, None)
        # Ensure last seen altitude is None
        self.assertEqual(second_vehicle.last_seen_altitude, None)
        # Ensure distance travelled is 0
        self.assertEqual(second_vehicle.distance_travelled, 0)
        # Ensure total fuel used is 0.
        self.assertEqual(second_vehicle.total_fuel_used, 0)
        # Ensure flight time total is 0.
        self.assertEqual(second_vehicle.flight_time_total, 0)'''

    '''def test_aircraft_timeout_reported(self):
        """
        First, import all aircraft. Of those, we will focus on two; 7c68b7 and 7c6bcf.
        For each aircraft, import the following traces;
            aircraft_7c68b7_t1, 7c68b7: there are 3 flights in here; all finish.
            TODO: load something here that does NOT finish.
        Verify this data is correct and loaded.

        Set our current timestamp to the first flight in all flights for 7c68b7 + 100 seconds.
        Call out to airvehicles module to report a timeout for that aircraft, giving the current timestamp as timeOfReport.
        Ensure we get back 'landing' as our determination.
        """
        # Load 7c68b7.
        aircraft_7c68b7, aircraft_7c68b7_flights = self._import_all_flights("aircraft_7c68b7_t1.json", 3)
        db.session.flush()
        # TODO: the second load.

        # Get all flights from both aircraft, sorted newest first.
        all_flights_7c68b7 = flights.query_flights_from(aircraft_7c68b7, newest_first = True).all()
        self.assertEqual(len(all_flights_7c68b7), 3)
        # TODO: the second load.

        # Calculate our example timestamp.
        current_timestamp = all_flights_7c68b7[0].ends_at+100
        # Set current timestamp.
        self.set_current_timestamp(current_timestamp)
        # Now, report a timeout for that vehicle, giving our timeOfReport as current timestamp
        report_receipt = airvehicles.aircraft_timeout_reported(aircraft_7c68b7, airvehicles.AircraftTimeoutReportSchema().load(
            dict(
                aircraftIcao = aircraft_7c68b7.icao,
                timeOfReport = int(current_timestamp),
                lastBinaryUpdate = int(current_timestamp-100),
                currentConfigAircraftTimeout = 60
            )
        ))
        # Ensure our receipt determines this as a 'landing.'
        self.assertEqual(report_receipt.determination, "landing")

        """
        Locate a second testdata, one that does NOT end in a landing, and complete this test to ensure we get 'hold'
        """
        self.assertEqual(True, False)'''


"""
GROUP BY aircraft icao if not explicitly filtering to a particular aircraft.


for x in enumerated_aircraft:
    print(db.session.query(models.Aircraft.flight_name, func.max(models.FlightPoint.timestamp), models.Aircraft.seconds_since_last_seen, models.Aircraft.last_seen_altitude)\
        .outerjoin(models.Aircraft.flight_points_)\
        .filter(models.Aircraft.icao == x.icao)\
        .first())
    print(x.last_seen_altitude)
    print("")
    #print(x.is_active_now)
"""
