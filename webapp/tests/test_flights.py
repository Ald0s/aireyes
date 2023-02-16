import os
import base64
import json
import uuid
import decimal

from subprocess import Popen, PIPE
from datetime import date, datetime, timedelta, time, timezone

from sqlalchemy import asc
from sqlalchemy.orm import aliased

from tests.conftest import BaseCase

from app import db, config, models, flights, error, traces, airvehicles, calculations

"""
WARNING
-------
When we correct deterministic issue with flight data aircraft_7c6bcf_t1, that is, we make legs number 2 and 3 on 25/06 part of two separate flights,
all checks for the number of partial flights on that day, currently asserting 3 will fail on the basis of 4 being determined. Just keep this in mind.
"""
class TestFlightRevisionBasics(BaseCase):
    def test_make_timeline(self):
        """
        """
        aircraft, day_date, flight_points = self._setup_native_test_data_for("aircraft_7c4ee8_t1.json", 1456, date(2021, 7, 19), 1297)
        # Instantiate a DailyPartialFlightFactory with this data.
        partial_flight_factory = flights.DailyFlightsView(aircraft, day_date, flight_points)
        # Make the factory's timeline.
        partial_flight_factory.make_timeline()
        timeline = partial_flight_factory.timeline
        # Ensure that the start object is of type FlightPointStartDescriptor.
        self.assertIsInstance(timeline[0], flights.FlightPointStartDescriptor)
        # Ensure that the end object is of type FlightPointEndDescriptor.
        self.assertIsInstance(timeline[len(timeline)-1], flights.FlightPointEndDescriptor)
        # Now, get all points from timeline, but shave off the first and last values.
        all_but_first_last = timeline[1:len(timeline)-1]
        # Ensure that every second index is of type FlightPointChangeDescriptor.
        for idx in range(len(all_but_first_last)):
            if idx > 0 and idx % 2 == 0:
                self.assertIsInstance(all_but_first_last[idx-1], flights.FlightPointChangeDescriptor)
        # Ensure flight point associated with FlightPointStartDescriptor is equal to the first flight point.
        self.assertEqual(timeline[0].flight_point.flight_point_hash, flight_points[0].flight_point_hash)
        # Ensure flight point associated with FlightPointEndDescriptor is equal to the last flight point.
        self.assertEqual(timeline[len(timeline)-1].flight_point.flight_point_hash, flight_points[len(flight_points)-1].flight_point_hash)
        # From the 'all but first and last' list, filter all items that are not a flight point change descriptor.
        # Then, iterate all these items, whilst iterating flight points in pairs. Ensure that flight point1 matches first item and second etc.
        just_descriptors = list(filter(lambda x: isinstance(x, flights.FlightPointChangeDescriptor), all_but_first_last))
        idx = 0
        for point1, point2 in zip(flight_points, flight_points[1:]):
            descriptor = just_descriptors[idx]
            # Ensure that points1 and 2 correspond respectively to the decriptor.
            self.assertEqual(descriptor.flight_point1.flight_point_hash, point1.flight_point_hash)
            self.assertEqual(descriptor.flight_point2.flight_point_hash, point2.flight_point_hash)
            idx += 1

    def test_calculate_surrounding_points(self):
        """
        Test DailyPartialFlightFactory calculate_surrounding_points function.
        Instantiate a new DailyPartialFlightFactory given an aircraft and all its points from a particular day.
        """
        aircraft, day_date, flight_points = self._setup_native_test_data_for("aircraft_7c68b7_t1.json", 1708, date(2022, 7, 29), 1708)
        # Instantiate a DailyPartialFlightFactory with this data.
        partial_flight_factory = flights.DailyFlightsView(aircraft, day_date, flight_points)
        # Now, get the surrounding environment of flight point index 0.
        previous_point, current_point, next_point = partial_flight_factory.calculate_surrounding_points(0)
        # Previous should be None, current should not be None, next should not be None.
        self.assertIsNone(previous_point)
        self.assertIsNotNone(current_point)
        self.assertIsNotNone(next_point)
        # Now, get surrounding environment on index 1. None of the three should be None.
        previous_point, current_point, next_point = partial_flight_factory.calculate_surrounding_points(1)
        self.assertIsNotNone(previous_point)
        self.assertIsNotNone(current_point)
        self.assertIsNotNone(next_point)
        # Now, get surrounding environment on the final index. The next point should be None.
        previous_point, current_point, next_point = partial_flight_factory.calculate_surrounding_points(len(flight_points)-1)
        self.assertIsNotNone(previous_point)
        self.assertIsNotNone(current_point)
        self.assertIsNone(next_point)


class TestFlightPartial(BaseCase):
    """
    """
    def test_aircraft_collect_future_partials_no_data(self):
        """
        Test that should we attempt to collect future partials (locate landing) when there is no data on next days, we are able
        to catch a raised FlightDataRevisionRequired exception.
        """
        # First, we must read native test data.
        aircraft, dates = self._load_native_test_data("aircraft_7c6bcf_t1.json")
        db.session.flush()

        # But, we must delete all flight points from 26/06.
        db.session.query(models.FlightPoint)\
            .filter(models.FlightPoint.day_day == date(2022, 6, 26))\
            .delete(synchronize_session = False)
        db.session.flush()

        # Get all flight points from 25/06.
        flight_points_2506 = aircraft.flight_points_from_day(date(2022, 6, 25))
        # Make a daily flights view for 25/06.
        daily_flights_view_2506 = flights.DailyFlightsView.from_args(aircraft, dates[0], flight_points_2506)
        # Just ensure we have 4 partial flights on this day.
        self.assertEqual(daily_flights_view_2506.num_partial_flights, 4)
        # Get the last partial.
        flight1_start = daily_flights_view_2506.last_partial_flight

        # Now, we'll attempt to collect points toward a landing. We'll specify False for should_handle_revision_req so that we can ensure
        # this action results in the appropriate error.
        with self.assertRaises(error.FlightDataRevisionRequired) as fdrr:
            flight1_start.collect_partials_until_landing(should_handle_revision_req = False)

    def test_aircraft_collect_past_partials_no_data(self):
        """
        Test that should we attempt to collect past partials (locate takeoff) when there is no data on previous days, we are able
        to catch a raised FlightDataRevisionRequired exception.
        """
        # First, we must read native test data.
        aircraft, dates = self._load_native_test_data("aircraft_7c6bcf_t1.json")
        db.session.flush()

        # But, we must delete all flight points from 25/06.
        db.session.query(models.FlightPoint)\
            .filter(models.FlightPoint.day_day == date(2022, 6, 25))\
            .delete(synchronize_session = False)
        db.session.flush()

        # Get all flight points from 26/06.
        flight_points_2606 = aircraft.flight_points_from_day(date(2022, 6, 26))
        # Now, construct a daily flights view for this data.
        daily_flights_view_2606 = flights.DailyFlightsView.from_args(aircraft, dates[1], flight_points_2606)
        # Just ensure we have 1 partial flight on this day.
        self.assertEqual(daily_flights_view_2606.num_partial_flights, 1)
        # Get that partial.
        flight1_end = daily_flights_view_2606.first_partial_flight

        # Now, we'll attempt to collect points back to a take off. We'll specify False for should_handle_revision_req so that we can ensure
        # this action results in the appropriate error.
        with self.assertRaises(error.FlightDataRevisionRequired) as fdrr:
            flight1_end.collect_partials_until_takeoff(should_handle_revision_req = False)

    def test_aircraft_collect_bidirectional_partials(self):
        """
        """
        aircraft, dates = self._load_native_test_data("aircraft_7c6bcf_t1.json")
        db.session.flush()
        # Get all flight points from 25/06.
        flight_points_2506 = aircraft.flight_points_from_day(date(2022, 6, 25))
        # Get all flight points from 26/06.
        flight_points_2606 = aircraft.flight_points_from_day(date(2022, 6, 26))

        # Make a daily flights view for 25/06.
        daily_flights_view_2506 = flights.DailyFlightsView.from_args(aircraft, dates[0], flight_points_2506)
        # Just ensure we have 4 partial flights on this day.
        self.assertEqual(daily_flights_view_2506.num_partial_flights, 4)
        # Make a daily flights view for 26/06.
        daily_flights_view_2606 = flights.DailyFlightsView.from_args(aircraft, dates[1], flight_points_2606)
        # Just ensure we have 1 partial flight on this day.
        self.assertEqual(daily_flights_view_2606.num_partial_flights, 1)

        # Get the very last partial flight on daily_flights_view_2506, and the very first on daily_flights_view_2606. This is a single flight.
        flight1_start = daily_flights_view_2506.last_partial_flight
        flight1_end = daily_flights_view_2606.first_partial_flight

        # Now, if we were to collect all future partials for flight1_start, we should get 1.
        flight1_start_future_partials = flight1_start.collect_partials_until_landing()
        self.assertEqual(len(flight1_start_future_partials), 1)
        # Now, if we were to collect all past partials for flight1_start, we should get 0.
        flight1_start_past_partials = flight1_start.collect_partials_until_takeoff()
        self.assertEqual(len(flight1_start_past_partials), 0)

        # Now, if we were to collect all future partials for flight1_end, we should get 0.
        flight1_end_future_partials = flight1_end.collect_partials_until_landing()
        self.assertEqual(len(flight1_end_future_partials), 0)
        # Now, if we were to collect all past partials for flight1_end, we should get 1.
        flight1_end_past_partials = flight1_end.collect_partials_until_takeoff()
        self.assertEqual(len(flight1_end_past_partials), 1)

    def test_aircraft_day_iterator(self):
        """
        """
        aircraft, dates = self._load_native_test_data("aircraft_7c6bcf_t1.json")
        db.session.flush()

        # Start on 25/06/2022 and iterate backwards. Ensure we locate 0 aircraft present day.
        backwards = list(iter(flights.AircraftDayIterator(aircraft, dates[0], flights.AircraftDayIterator.BACKWARD)))
        self.assertEqual(len(backwards), 0)
        # Start on 25/06/2022 and iterate forwards. Ensure we locate 1 aircraft present day.
        forwards = list(iter(flights.AircraftDayIterator(aircraft, dates[0], flights.AircraftDayIterator.FORWARD)))
        self.assertEqual(len(forwards), 1)
        # Ensure this is 26/06/2022.
        self.assertEqual(forwards[0].day_day, date(2022, 6, 26))

        # Start on 26/06/2022 and iterate backwards. Ensure we locate 1 aircraft present day.
        backwards = list(iter(flights.AircraftDayIterator(aircraft, dates[1], flights.AircraftDayIterator.BACKWARD)))
        self.assertEqual(len(backwards), 1)
        # Ensure this is 25/06/2022.
        self.assertEqual(backwards[0].day_day, date(2022, 6, 25))
        # Start on 26/06/2022 and iterate forwards. Ensure we locate 0 aircraft present day.
        forwards = list(iter(flights.AircraftDayIterator(aircraft, dates[1], flights.AircraftDayIterator.FORWARD)))
        self.assertEqual(len(forwards), 0)

    def test_attempt_find_suitable_partial_for(self):
        """
        Point index 469 is the last point for the first flight on this day.
        """
        # We will load this data manually.
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Subsect flight points into 4 parts.
        # Part 1, the entire first flight MINUS a single point: 0 -> 468
        flight_points_sub1 = aircraft_json["FlightPoints"][:469]
        self.assertEqual(len(flight_points_sub1), 469)
        # Part 2, the last point in the first flight for this day: 469 -> 470
        flight_points_sub2 = aircraft_json["FlightPoints"][469:470]
        self.assertEqual(len(flight_points_sub2), 1)
        # Part 3, the first 6 points in the next flight for the day: 470 -> 476
        flight_points_sub3 = aircraft_json["FlightPoints"][470:476]
        self.assertEqual(len(flight_points_sub3), 6)
        # Part 4, another few points in the second flight for the day: 476 -> 500
        flight_points_sub4 = aircraft_json["FlightPoints"][476:501]
        self.assertEqual(len(flight_points_sub4), 25)

        # Submit the first sub.
        aircraft, flight_points, synchronised_flight_points = self._submit_flight_point_dicts(aircraft_json, flight_points_sub1)
        # Ensure we have both a Day for date(2022, 7, 29) created, and an AircraftPresentDay.
        aircraft_present_day, was_created = traces.ensure_aircraft_day_junction_exists(aircraft, date(2022, 7, 29),
            flights_verified = False, history_verified = False)
        db.session.flush()
        # Ensure this aircraft, so far, has just 469 points.
        self.assertEqual(len(flight_points), 469)
        # Now, create a daily flights view from the aircraft present day.
        daily_flights_view = flights.DailyFlightsView.from_aircraft_present_day(aircraft_present_day)
        # Ensure we have 1 partial.
        self.assertEqual(daily_flights_view.num_partial_flights, 1)
        # If we attempt to locate a suitable partial for this set of flight points, we should get the first partial flight.
        located_partial = daily_flights_view.attempt_find_suitable_partial_for(flight_points)
        self.assertIsInstance(located_partial, flights.PartialFlight)
        self.assertEqual(located_partial, daily_flights_view.partial_flights[0])

        # Now, submit the second sub to the database. This should complete the very first flight, but should still be included in that first flight.
        aircraft, flight_points, synchronised_flight_points = self._submit_flight_point_dicts(aircraft_json, flight_points_sub2)
        # Create a new daily flights view, we should still have 1 partial.
        daily_flights_view = flights.DailyFlightsView.from_aircraft_present_day(aircraft_present_day)
        self.assertEqual(daily_flights_view.num_partial_flights, 1)
        # Now, take those received flight points, and attempt to locate a partial for them. We should get back an instance of type PartialFlight, should be the first partial flight.
        located_partial = daily_flights_view.attempt_find_suitable_partial_for(flight_points)
        self.assertIsInstance(located_partial, flights.PartialFlight)
        self.assertEqual(located_partial, daily_flights_view.partial_flights[0])

        # Next, we will now load sub3. This is the beginning of a new flight on the same day. We should expect back an entity of type NewPartialFlight.
        aircraft, flight_points, synchronised_flight_points = self._submit_flight_point_dicts(aircraft_json, flight_points_sub3)
        # Create a new daily flights view, we should now have 2 partial.
        daily_flights_view = flights.DailyFlightsView.from_aircraft_present_day(aircraft_present_day)
        self.assertEqual(daily_flights_view.num_partial_flights, 2)
        # Now, take those received flight points, and attempt to locate a partial for them. We should get back an instance of type PartialFlight.
        located_partial = daily_flights_view.attempt_find_suitable_partial_for(flight_points)
        # Ensure the located partial is equal to the second partial in daily flights view.
        self.assertIsInstance(located_partial, flights.PartialFlight)
        self.assertEqual(located_partial, daily_flights_view.partial_flights[1])

    def test_attempt_find_suitable_partial_for_v1(self):
        """
        """
        # We will load this data manually.
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())
        # Get our three subs; the first 0 -> 1000, second 1000 -> 1200 and the third 1200 -> 1205.
        flight_points_sub1 = aircraft_json["FlightPoints"][:1000]
        self.assertEqual(len(flight_points_sub1), 1000)
        flight_points_sub2 = aircraft_json["FlightPoints"][:1200]
        self.assertEqual(len(flight_points_sub2), 1200)
        flight_points_sub3 = aircraft_json["FlightPoints"][1200:1205]
        self.assertEqual(len(flight_points_sub3), 5)

        # Submit the first sub.
        aircraft, flight_points, synchronised_flight_points = self._submit_flight_point_dicts(aircraft_json, flight_points_sub1)
        # Ensure we have both a Day for date(2022, 7, 29) created, and an AircraftPresentDay.
        aircraft_present_day, was_created = traces.ensure_aircraft_day_junction_exists(aircraft, date(2022, 7, 29),
            flights_verified = False, history_verified = False)
        db.session.flush()
        # Ensure this aircraft, so far, has just 1000 points.
        self.assertEqual(len(flight_points), 1000)
        # Now, create a daily flights view from the aircraft present day.
        daily_flights_view = flights.DailyFlightsView.from_aircraft_present_day(aircraft_present_day)
        # Ensure we have 2 partials.
        self.assertEqual(daily_flights_view.num_partial_flights, 2)
        # We should not be able to locate a suitable partial for these flight points - as this is a multi-partial list.
        located_partial = daily_flights_view.attempt_find_suitable_partial_for(flight_points)
        self.assertIsNone(located_partial)

        # Now, submit the second sub to the database.
        aircraft, flight_points, synchronised_flight_points = self._submit_flight_point_dicts(aircraft_json, flight_points_sub2)
        # Create a new daily flights view, we should have 3 partials.
        daily_flights_view = flights.DailyFlightsView.from_aircraft_present_day(aircraft_present_day)
        self.assertEqual(daily_flights_view.num_partial_flights, 3)
        # When we submit sub2, since this is yet again a multi-partial list, we should not be able to find a suitable partial.
        located_partial = daily_flights_view.attempt_find_suitable_partial_for(flight_points)
        self.assertIsNone(located_partial)

        # Next, we will now load sub3.
        aircraft, flight_points, synchronised_flight_points = self._submit_flight_point_dicts(aircraft_json, flight_points_sub3)
        # Create a new daily flights view, we should still have 3 partials.
        daily_flights_view = flights.DailyFlightsView.from_aircraft_present_day(aircraft_present_day)
        self.assertEqual(daily_flights_view.num_partial_flights, 3)
        # Now, take those received flight points, and attempt to locate a partial for them. We should get back an instance of type PartialFlight; since this extends the third flight.
        located_partial = daily_flights_view.attempt_find_suitable_partial_for(flight_points)
        self.assertIsInstance(located_partial, flights.PartialFlight)
        # Ensure located partial belongs to the third partial.
        self.assertEqual(located_partial, daily_flights_view.partial_flights[2])


class TestFlightPointsManager(BaseCase):
    def test_flight_points_manager_basics(self):
        """
        """
        # Load test data from native.
        aircraft, dates = self._load_native_test_data("aircraft_7c6bcf_t1.json")
        db.session.flush()
        # Get all flight points from 26/06.
        flight_points_2606 = aircraft.flight_points_from_day(date(2022, 6, 26))

        # Now, make a flight points manager from this.
        manager = flights.FlightPointsManager(flight_points_2606)
        # Ensure there are multiple points.
        self.assertNotEqual(manager.num_flight_points, 0)
        # Ensure we can get the first point.
        self.assertIsNotNone(manager.first_point)
        # And last point.
        self.assertIsNotNone(manager.last_point)
        # Ensure we can index the manager directly to access underlying flight points.
        self.assertEqual(manager[0], manager.first_point)

    def test_flight_points_manager_derive_time_range(self):
        """
        """
        # Load test data from native.
        aircraft, dates = self._load_native_test_data("aircraft_7c6bcf_t1.json")
        db.session.flush()

        # Get all flight points from 25/06.
        flight_points_2506 = aircraft.flight_points_from_day(date(2022, 6, 25))
        # Get all flight points from 26/06.
        flight_points_2606 = aircraft.flight_points_from_day(date(2022, 6, 26))

        # Make a daily flights view for 25/06.
        daily_flights_view_2506 = flights.DailyFlightsView.from_args(aircraft, dates[0], flight_points_2506)
        # Just ensure we have 4 partial flights on this day.
        self.assertEqual(daily_flights_view_2506.num_partial_flights, 4)
        # Make a daily flights view for 26/06.
        daily_flights_view_2606 = flights.DailyFlightsView.from_args(aircraft, dates[1], flight_points_2606)
        # Just ensure we have 1 partial flight on this day.
        self.assertEqual(daily_flights_view_2606.num_partial_flights, 1)

        # For first partial flight in 2506, this is WITHIN prohibited range (8pm -> 7am)
        prohibited_flight = daily_flights_view_2506.partial_flights[0]
        # For first partial flight in 2606, this is OUTSIDE prohibited range (8pm -> 7am)
        ok_flight = daily_flights_view_2606.partial_flights[0]

        # Create a timezone for GMT+10, our local timezone.
        timezone_gmt10 = timezone(timedelta(hours = 10))
        # Now, construct a time range for our prohibited hours. (8pm -> 7am)
        prohibited_hours = (
            time(hour=20, minute=0, second=0, tzinfo = timezone_gmt10),
            time(hour=7, minute=0, second=0, tzinfo = timezone_gmt10),
        )
        # First, check that we are returned ALL points if we derive a flight points manager, requesting all points INSIDE prohibited hours if we provide prohibited_flight.
        prohibited_flight_pts_manager = flights.FlightPointsManager(prohibited_flight.flight_points)
        prohibited_flight_pts_manager = prohibited_flight_pts_manager.derive_manager(within_hours_range = prohibited_hours)
        # Ensure number of flight points is equal.
        self.assertEqual(prohibited_flight_pts_manager.num_flight_points, prohibited_flight.num_flight_points)
        # Second, check that we are returned NONE of the points if we derive a flight points manager, requesting all points INSIDE prohibited hours from the OK flight.
        ok_flight_pts_manager = flights.FlightPointsManager(ok_flight.flight_points)
        ok_flight_pts_manager = ok_flight_pts_manager.derive_manager(within_hours_range = prohibited_hours)
        # Ensure number of flight points is 0.
        self.assertEqual(ok_flight_pts_manager.num_flight_points, 0)
        """This proves that we can filter points down by providing a timezone aware range."""


class TestFlightAssimilator(BaseCase):
    def test_flight_assimilator(self):
        """
        """
        aircraft, dates = self._load_native_test_data("aircraft_7c6bcf_t1.json")
        db.session.flush()

        # Get all flight points from 25/06.
        flight_points_2506 = aircraft.flight_points_from_day(date(2022, 6, 25))
        # Get all flight points from 26/06.
        flight_points_2606 = aircraft.flight_points_from_day(date(2022, 6, 26))

        # Make a daily flights view for 25/06.
        daily_flights_view_2506 = flights.DailyFlightsView.from_args(aircraft, dates[0], flight_points_2506)
        # Just ensure we have 4 partial flights on this day.
        self.assertEqual(daily_flights_view_2506.num_partial_flights, 4)
        # Make a daily flights view for 26/06.
        daily_flights_view_2606 = flights.DailyFlightsView.from_args(aircraft, dates[1], flight_points_2606)
        # Just ensure we have 1 partial flight on this day.
        self.assertEqual(daily_flights_view_2606.num_partial_flights, 1)
        # Collect the last partial from 25/06.
        flight1_start = daily_flights_view_2506.last_partial_flight
        # Collect the first partial from 26/06.
        flight1_end = daily_flights_view_2606.first_partial_flight
        # Now, construct an assimilator given these two partial flights.
        flight_assimilator = flights.FlightAssimilator.from_args(aircraft, [flight1_start, flight1_end])
        # Get the very last point from flight1 end, and the very first point from flight1 start.
        first_point = flight1_start.flight_points[0]
        last_point = flight1_end.flight_points[flight1_end.num_flight_points-1]
        # Now, we will ensure the timestamps for both first points are the same, and also the last points are the same.
        self.assertEqual(first_point.timestamp, flight_assimilator.flight_points[0].timestamp)
        self.assertEqual(last_point.timestamp, flight_assimilator.flight_points[flight_assimilator.num_flight_points-1].timestamp)
        # Also, that totals add up.
        self.assertEqual(flight1_start.num_flight_points+flight1_end.num_flight_points, flight_assimilator.num_flight_points)
        """This proves that flight1 can be consolidated correctly, provided we give both partials explicitly."""

        # Now, we will implcitly require both the future and past partials at different times, to prove we can collect either way.
        # Begin by creating a new assimilator from just a single partial; flight start.
        flight_assimilator_from_start = flights.FlightAssimilator.from_partial_flight(aircraft, flight1_start)
        # Now, perform the same checks.
        self.assertEqual(first_point.timestamp, flight_assimilator_from_start.flight_points[0].timestamp)
        self.assertEqual(last_point.timestamp, flight_assimilator_from_start.flight_points[flight_assimilator_from_start.num_flight_points-1].timestamp)
        self.assertEqual(flight1_start.num_flight_points+flight1_end.num_flight_points, flight_assimilator_from_start.num_flight_points)
        # Now, continue by creating a new assimilator from the other single partial; flight end.
        flight_assimilator_from_end = flights.FlightAssimilator.from_partial_flight(aircraft, flight1_end)
        # Now, perform the same checks.
        self.assertEqual(first_point.timestamp, flight_assimilator_from_end.flight_points[0].timestamp)
        self.assertEqual(last_point.timestamp, flight_assimilator_from_end.flight_points[flight_assimilator_from_end.num_flight_points-1].timestamp)
        self.assertEqual(flight1_start.num_flight_points+flight1_end.num_flight_points, flight_assimilator_from_end.num_flight_points)
        """This proves that flight1 can be consolidated even across days, without explicitly providing those days' data."""

    def test_flight_assimilator_data_not_available(self):
        """
        """
        # First, we must read native test data.
        aircraft, dates = self._load_native_test_data("aircraft_7c6bcf_t1.json")
        db.session.flush()

        # But, we must delete all flight points from 26/06.
        db.session.query(models.FlightPoint)\
            .filter(models.FlightPoint.day_day == date(2022, 6, 26))\
            .delete(synchronize_session = False)
        db.session.flush()

        # Get all flight points from 25/06.
        flight_points_2506 = aircraft.flight_points_from_day(date(2022, 6, 25))
        # Now, construct a daily flights view for this data.
        daily_flights_view_2506 = flights.DailyFlightsView.from_args(aircraft, dates[0], flight_points_2506)
        # Just ensure we have 4 partial flights on this day.
        self.assertEqual(daily_flights_view_2506.num_partial_flights, 4)
        # Get that partial.
        flight1_start = daily_flights_view_2506.last_partial_flight

        # Now, we will create an assimilator from just this start partial.
        # Though we are missing the end flight data, we should still get back an assimilator who's last flight point is equal to the last flight point in the start data.
        flight_assimilator = flights.FlightAssimilator.from_partial_flight(aircraft, flight1_start)
        # Get the last point from our flight1 start.
        last_point = flight1_start.flight_points[flight1_start.num_flight_points-1]
        # Ensure this point's timestamp is equal to the assimilator's last point's timestamp.
        self.assertEqual(last_point.timestamp, flight_assimilator.flight_points[flight_assimilator.num_flight_points-1].timestamp)
        """This proves that flight1 can be partially constructed, though it is missing a signficant portion of future points."""

    def test_flight_assimilator_assimilate(self):
        """
        """
        # First, we must read native test data.
        aircraft, dates = self._load_native_test_data("aircraft_7c6bcf_t1.json")
        db.session.flush()
        # Next, update fuel figures to provide VKX with figures.
        airvehicles.update_fuel_figures(directory = "test_data")
        db.session.flush()
        # Let's say we've just committed an update to the database for all flight data on the 25/06.
        # All flight data on the 26/06 is already present- for whatever reason.
        # Get all flight points from 25/06.
        flight_points_2506 = aircraft.flight_points_from_day(date(2022, 6, 25))
        # Now, construct a daily flights view for this data.
        daily_flights_view_2506 = flights.DailyFlightsView.from_args(aircraft, dates[0], flight_points_2506)
        # Just ensure we have 4 partial flights on this day.
        self.assertEqual(daily_flights_view_2506.num_partial_flights, 4)
        # Get that partial.
        flight1_start = daily_flights_view_2506.last_partial_flight

        # Now, create a flight assimilator for this partial.
        flight_assimilator = flights.FlightAssimilator.from_partial_flight(aircraft, flight1_start)
        # And assimilate it.
        flight, created = flight_assimilator.assimilate()
        db.session.flush()
        # Ensure was created.
        self.assertEqual(created, True)
        # Ensure this flight is not None.
        self.assertIsNotNone(flight)
        # Ensure the number of flight points is equal to the number of flight points in flight1_start PLUS 317 (acquired via inspect-flight-data)
        self.assertEqual(flight.num_flight_points, flight1_start.num_flight_points+317)
        # Ensure this flight has a total co2 emissions value of 20576.
        self.assertEqual(int(flight.total_co2_emissions), 20576)
        """This proves that a single new Flight can be created."""


class TestFlight(BaseCase):
    """
    Test all aspects of the Flight model.
    This database model will persist each flight, and hold references to their flight data. This model will also be responsible for holding
    statically calculated statistics on the flight.
    """
    def test_flight_days(self):
        """
        Test the flight model's ability to report all days it spans.
        """
        # Read the aircraft, with its points, added to the database, and also all dates in this test data.
        aircraft, dates = self._load_native_test_data("aircraft_7c6bcf_t1.json")
        db.session.flush()
        # Get flight points.
        flight_points = aircraft.all_flight_points
        # Create a new flight with ALL flight models.
        flight = models.Flight(
            flight_hash = uuid.uuid4().hex.lower(),
            aircraft = aircraft
        )
        db.session.add(flight)
        db.session.flush()
        # Set flight points to the entire flight data from 7c6bcf, this includes 25/06 and 26/06.
        flight.set_flight_points(flight_points)
        db.session.flush()

        days_to_check = [date(2022, 6, 25), date(2022, 6, 26)]
        # Ensure resulting flight spans two days.
        self.assertEqual(flight.num_days_across, 2)
        # Ensure that, in the result, both 25/06 and 26/06 is available.
        for day in flight.days:
            self.assertIn(day.day, days_to_check)
        # Now, iterate days to check, get the Day instance for each, and ensure both days have a single flight.
        for day_date in days_to_check:
            day = models.Day.get_by_date(day_date)
            # Ensure day is non-None.
            self.assertIsNotNone(day)
            # Ensure the Day has 1 flight.
            self.assertEqual(day.num_flights, 1)
        """This proves that we can extract from a Flight model, all Day models the Flight has movement logged on, and from a Day model, all Flights that has movement logged on that Day."""

    def test_flight(self):
        """
        Test the basic aspects and properties of the Flight model.
        """
        # Read the aircraft, with its points, added to the database, and also all dates in this test data.
        aircraft, dates = self._load_native_test_data("aircraft_7c6bcf_t1.json")
        db.session.flush()
        # Get flight points.
        flight_points = aircraft.all_flight_points

        # Now, grab 20 of those.
        flight_points_for_flight = flight_points[20:40]
        # Create a new flight.
        flight = models.Flight(
            flight_hash = uuid.uuid4().hex.lower(),
            aircraft = aircraft
        )
        db.session.add(flight)
        db.session.flush()
        # Attach these 20 to a new Flight.
        flight.set_flight_points(flight_points_for_flight)
        # Ensure 20 points.
        self.assertEqual(flight.num_flight_points, 20)

        # Now, get the point at index 19, and the point at index 41. These are the points directly before and after the flight.
        one_before_flight_start = flight_points[19]
        one_after_flight_start = flight_points[40]
        # Ensure these, respectively, are not equal to the first point and last point via instance reference.
        # This test ensures that our test points here are outside of the range.
        self.assertNotEqual(one_before_flight_start, flight.first_point)
        self.assertNotEqual(one_after_flight_start, flight.last_point)
        # But, ensure the indicies at 20, 40 respectively are equal to the first and last point.
        self.assertEqual(flight_points[20], flight.first_point)
        self.assertEqual(flight_points[39], flight.last_point)
        # Query this Flight, with both the timestamp it starts at and ends at, ensure these timestamps match those in the first and last points respectively.
        flight, starts_at, ends_at = db.session.query(models.Flight, models.Flight.starts_at, models.Flight.ends_at)\
            .join(models.Flight.flight_points_)\
            .filter(models.Flight.flight_hash == flight.flight_hash)\
            .first()
        self.assertAlmostEqual(starts_at, flight.first_point.timestamp, places = 2)
        self.assertAlmostEqual(ends_at, flight.last_point.timestamp, places = 2)


class FlightRevisionBase(BaseCase):
    def _make_daily_flights_view_for(self, filename, total_points_num, points_date_filter, points_from_date_num, **kwargs):
        aircraft, day_date, flight_points = self._setup_native_test_data_for(filename, total_points_num, points_date_filter, points_from_date_num)
        # Instantiate a DailyPartialFlightFactory with this data.
        partial_flight_factory = flights.DailyFlightsView(aircraft, day_date, flight_points)
        # Make the factory's timeline.
        partial_flight_factory.make_timeline()
        # Now, make partial flights.
        partial_flight_factory.make_partial_flights()
        # Return daily view.
        return partial_flight_factory


class TestFlightRevisionPartialFlights(FlightRevisionBase):
    """
    Given various native flight data inputs, generated by the aireyes worker, test the daily flights view's capability of constructing
    partial flights. A partial flight is a subsection of an aircraft's daily flight data separated into full flights, within the bounds
    of the given day. We'll apply some conditional checks and calculations to determine any new flights throughout the day's timeline.

    This means that, for an unreliable aircraft, we may find that two points that satisfy TIME_DIFFERENCE_NEW_FLIGHT requirement may both
    still be in the air.

    Aircraft Summaries
    ------------------

    7c68b7
    ------
    7c68b7's is an ADSB enabled aircraft, making its flight data very reliable.

    7c4ee8
    ------
    7c4ee8 is an MLAT reported vehicle, making its flight data very unreliable depending on location. To compound this, it's destinations are usually
    rural, distant or otherwise usually out of reach and its flight times are persistent, often flying between days. As such, a more complex approach
    is needed to assemble flights for this aircraft.

    7c6bcf
    ------
    7c6bcf is an ADSB reported vehicle. However, some notes have been made of flight inaccuracies on some days.
    """
    def test_make_partial_flights_7c68b7_t1(self):
        """
        On this date, this aircraft flew 3 separate flights. One from Sydney to Canberra, another from Canberra to Melbourne, and a final
        one from Melbourne back to Sydney. As such, we will expect three partial flights to be constructed; each beginning airborn and
        ending airborn.
        """
        partial_flight_factory = self._make_daily_flights_view_for("aircraft_7c68b7_t1.json", 1708, date(2022, 7, 29), 1708)
        partial_flights = partial_flight_factory.partial_flights

        # Expect there to be 3 partial flights.
        self.assertEqual(len(partial_flights), 3)
        # Each partial flight starts in takeoff and ends in landing.
        # None of these flights have incomplete pasts or futures.
        # Each is actually a complete flight.
        for partial_flight in partial_flights:
            self.assertEqual(partial_flight.started_with_takeoff, True)
            self.assertEqual(partial_flight.ended_with_landing, True)
            self.assertEqual(partial_flight.incomplete_past, False)
            self.assertEqual(partial_flight.incomplete_future, False)
            self.assertEqual(partial_flight.is_complete_flight, True)

    def test_make_partial_flights_7c4ee8_t1(self):
        """
        On this date, according to data, the aircraft begins its flight in middair. Shortly after flight data commences, the aircraft ceases to be reported
        correctly. When reporting reappears, approximately 40 minutes later, the aircraft is heading toward Albury airport and has decreased altitude to 7875 ft.
        The aircraft does not land, this is obvious as time between points is consistent with a continues flight from here. The aircraft flies back to Essendon from
        here. The final flight commences, and the flight data ends at 30000ft.

        From this, we expect 2 partial flights. The first should start mid air but should end on the ground.
        The second should begin grounded air but should end airborn.
        """
        partial_flight_factory = self._make_daily_flights_view_for("aircraft_7c4ee8_t1.json", 1456, date(2021, 7, 19), 1297)
        partial_flights = partial_flight_factory.partial_flights

        # Expect there to be 2 partial flights.
        self.assertEqual(len(partial_flights), 2)
        # First does not begin with a takeoff, but ends with a landing.
        # First has incomplete past, but complete future.
        # First is not a complete flight.
        self.assertEqual(partial_flights[0].started_with_takeoff, False)
        self.assertEqual(partial_flights[0].ended_with_landing, True)
        self.assertEqual(partial_flights[0].incomplete_past, True)
        self.assertEqual(partial_flights[0].incomplete_future, False)
        self.assertEqual(partial_flights[0].is_complete_flight, False)
        # Second begins with a takeoff but does not end with a landing
        # Second has complete past but incomplete future.
        # Second is not a complete flight either.
        self.assertEqual(partial_flights[1].started_with_takeoff, True)
        self.assertEqual(partial_flights[1].ended_with_landing, False)
        self.assertEqual(partial_flights[1].incomplete_past, False)
        self.assertEqual(partial_flights[1].incomplete_future, True)
        self.assertEqual(partial_flights[1].is_complete_flight, False)

    def test_make_partial_flights_7c4ee8_t2(self):
        """
        On this date, according to data, the aircraft begins on the ground. It then takes off and flies, ADSB data breaks off around 9:38, but our partial
        flight continues. Data ceases being reported correctly at about 10:08, and remains this way for around 6 minutes. This is about double or TIME_DIFFERENCE_NEW_FLIGHT,
        and so the partial flight is broken off here. This, due to altitude change & time difference, is obviously not a new flight. After this, the aircraft lands back
        at Essendon. Finally, the aircraft takes off again and flies around for a bit until the end of the day, which is also the last flight. Another unreliable aspect of
        this aircraft is shown here - the last partial begins mid air, but this should be regarded as a new flight, as time difference + altitude is sufficient.

        There must be 2 flight partials. The first shall begin and end on the ground. But the second, has that mid-air glitch; this will begin mid air.
        """
        partial_flight_factory = self._make_daily_flights_view_for("aircraft_7c4ee8_t2.json", 3974, date(2022, 7, 30), 3974)
        partial_flights = partial_flight_factory.partial_flights

        # Expect there to be 2 partial flights.
        self.assertEqual(len(partial_flights), 2)
        # First begins with a takeoff and ends with a landing.
        # First has neither incomplete past nor future.
        # First is a complete flight.
        self.assertEqual(partial_flights[0].started_with_takeoff, True)
        self.assertEqual(partial_flights[0].ended_with_landing, True)
        self.assertEqual(partial_flights[0].incomplete_past, False)
        self.assertEqual(partial_flights[0].incomplete_future, False)
        self.assertEqual(partial_flights[0].is_complete_flight, True)
        # Second begins with a takeoff and ends with a landing.
        # Second has neither incomplete past nor future.
        # Second is also a complete flight.
        self.assertEqual(partial_flights[1].started_with_takeoff, True)
        self.assertEqual(partial_flights[1].ended_with_landing, True)
        self.assertEqual(partial_flights[1].incomplete_past, False)
        self.assertEqual(partial_flights[1].incomplete_future, False)
        self.assertEqual(partial_flights[1].is_complete_flight, True)

    def test_make_partial_flights_7c4ee8_t3(self):
        """
        This test data contains two separate dates; 13/01/2022 and 14/01/2022. There are several flights over these two days that are of interest, but for the purposes of this function, we are interested
        purely in a flight taken from Melbourne airport to Tasmania as the final leg for 13/01. The aircraft completes a few flights around the Melbourne area for the first 2 partials. The third partial
        is a flight (and landing) to Tasmania. It does not look like a landing, because final seen altitude is 6000ft+. But, that said, next partial begins almost 6 hours later; so its safe to assume the
        landing/takeoff. As aforementioned, the flight also takes off at a high altitude on 14/01. This must also be considered a takeoff however. The flight then heads back to Melbourne. After a refueling,
        the aircraft completes another tour of Melbourne and lands. It's important to note, that in almost all landings for this aircraft, the aircraft is not 'on ground', but is instead still in the air.

        In total, there must be 5 partials in total; 3 on 13/01 and 2 on 14/01.
        """
        # Set config's INACCURACY_SOLVENCY_ENABLED to True.
        config.INACCURACY_SOLVENCY_ENABLED = True

        aircraft, dates = self._load_native_test_data("aircraft_7c4ee8_t3.json")
        # Comprehend a list of daily flights view for each date received.
        daily_flights_views = [ flights.DailyFlightsView.from_args(aircraft, date, aircraft.flight_points_from_day(date)) for date in dates ]
        # Ensure there are 2 in total, since we have 2 days.
        self.assertEqual(len(daily_flights_views), 2)

        # Now, get the first daily flights view.
        first_flights_view = daily_flights_views[0]
        # On this day, we must have 3 partial flights.
        self.assertEqual(first_flights_view.num_partial_flights, 3)
        # First should start without a takeoff, but end in a landing. Have incomplete past, and be an incomplete flight.
        partial_flights = first_flights_view.partial_flights
        self.assertEqual(partial_flights[0].started_with_takeoff, False)
        self.assertEqual(partial_flights[0].ended_with_landing, True)
        self.assertEqual(partial_flights[0].incomplete_past, True)
        self.assertEqual(partial_flights[0].incomplete_future, False)
        self.assertEqual(partial_flights[0].is_complete_flight, False)
        # Second should be a full flight.
        self.assertEqual(partial_flights[1].started_with_takeoff, True)
        self.assertEqual(partial_flights[1].ended_with_landing, True)
        self.assertEqual(partial_flights[1].incomplete_past, False)
        self.assertEqual(partial_flights[1].incomplete_future, False)
        self.assertEqual(partial_flights[1].is_complete_flight, True)
        # Third and final, the trip to Tasmania, should be an incomplete flight- because its cut off by days.
        self.assertEqual(partial_flights[2].started_with_takeoff, True)
        self.assertEqual(partial_flights[2].ended_with_landing, False)
        self.assertEqual(partial_flights[2].incomplete_past, False)
        self.assertEqual(partial_flights[2].incomplete_future, True)
        self.assertEqual(partial_flights[2].is_complete_flight, False)

        # Now, get the second daily flights view.
        second_flights_view = daily_flights_views[1]
        # On this day, we must have 2 partial flights.
        self.assertEqual(second_flights_view.num_partial_flights, 2)
        # First should not have a proper takeoff.
        self.assertEqual(partial_flights[0].started_with_takeoff, False)
        self.assertEqual(partial_flights[0].ended_with_landing, True)
        self.assertEqual(partial_flights[0].incomplete_past, True)
        self.assertEqual(partial_flights[0].incomplete_future, False)
        self.assertEqual(partial_flights[0].is_complete_flight, False)
        # The second is a complete flight.
        self.assertEqual(partial_flights[1].started_with_takeoff, True)
        self.assertEqual(partial_flights[1].ended_with_landing, True)
        self.assertEqual(partial_flights[1].incomplete_past, False)
        self.assertEqual(partial_flights[1].incomplete_future, False)
        self.assertEqual(partial_flights[1].is_complete_flight, True)

    def test_make_partial_flights_7c4ef2_t1(self):
        """
        On this date, POL30 flies in total 6 separate flights. All contained within one day. The issue is, legs 3 & 4 appear to be two totally different flights since leg 3 ends mid air at 2900ft, then leg 4
        starts at 2000ft. The difference in time is only two hours. We may have to consider AW139's range statistics in this determination. We will happily call 3 & 4 one flight because the time difference, though
        significant, is not well past the helicopter's range.

        There should be 6 partials overall.
        """
        partial_flight_factory = self._make_daily_flights_view_for("aircraft_7c4ef2_t1.json", 5590, date(2022, 8, 18), 5590)
        partial_flights = partial_flight_factory.partial_flights

        # Expect there to be 6 partial flights.
        self.assertEqual(len(partial_flights), 6)
        # Each partial flight starts in takeoff and ends in landing.
        # None of these flights have incomplete pasts or futures.
        # Each is actually a complete flight.
        for partial_flight in partial_flights:
            self.assertEqual(partial_flight.started_with_takeoff, True)
            self.assertEqual(partial_flight.ended_with_landing, True)
            self.assertEqual(partial_flight.incomplete_past, False)
            self.assertEqual(partial_flight.incomplete_future, False)
            self.assertEqual(partial_flight.is_complete_flight, True)

    def test_make_partial_flights_7c6bcf_t1(self):
        """
        This flight data consists of two separate dates; 25/06/2022 and 26/06/2022. There are 4 separate flights on this day. One from Brisbane to Sydney, from Sydney
        to Orange[*], from Orange back to Sydney[*], and finally, from Sydney towards Brisbane. The flight to Brisbane lands the next day, on the 26th.

        [*] This flight to orange has a strange ending; the aircraft is flying toward the airfield, but drops off map at alt 5000, regains presence on map a significant time
        later, and at similar altitude. This is most definitely a landing, but it has been cut off half way through. Devise a way to verify this as a full flight. Perhaps by
        taking into account first the significant amount of time between updates, then checking whether there may be an airport nearby/within heading of aircraft?

        Either way, there must be 4 partials on 25/06. The first three should begin and end on the ground. The fourth should begin on the ground but end mid air.

        There must be one partial on 26/06. This should commence middair but finish on the ground.
        """
        # Set config's INACCURACY_SOLVENCY_ENABLED to True.
        config.INACCURACY_SOLVENCY_ENABLED = True

        partial_flight_factory = self._make_daily_flights_view_for("aircraft_7c6bcf_t1.json", 2100, date(2022, 6, 25), 1783)
        partial_flights = partial_flight_factory.partial_flights

        # There should be 4 partials.
        self.assertEqual(len(partial_flights), 4)
        # First three should begin on the ground and end on the ground; complete flights.
        for x in partial_flights[:3]:
            self.assertEqual(x.started_with_takeoff, True)
            self.assertEqual(x.ended_with_landing, True)
            self.assertEqual(x.incomplete_past, False)
            self.assertEqual(x.incomplete_future, False)
            self.assertEqual(x.is_complete_flight, True)
        # The fourth should begin on the ground but not end with a landing.
        # This flight is not complete.
        # This flight has an incomplete future.
        self.assertEqual(partial_flights[3].started_with_takeoff, True)
        self.assertEqual(partial_flights[3].ended_with_landing, False)
        self.assertEqual(partial_flights[3].incomplete_past, False)
        self.assertEqual(partial_flights[3].incomplete_future, True)
        self.assertEqual(partial_flights[3].is_complete_flight, False)


class TestFlightRevisionComprehensive(BaseCase):
    """
    Testing for comprehensive revisions of flight data; that is, using the flights module indirectly via the revise_flight_data_for function.
    This is the top-most level of functionality. Each test should mirror a test within the TestFlightRevisionPartialFlights class.
    """
    def test_comprehensive_7c4ef4_t3(self):
        """
        """
        # Read the aircraft, with its points, added to the database, and also all dates in this test data; we will not require flights to be verified,
        # but history must be verified.
        aircraft, dates = self._load_native_test_data("aircraft_7c4ef4_t3.json", history_verified = True, flights_verified = False)
        db.session.flush()
        # Ensure there's 1 date in this case.
        self.assertEqual(len(dates), 1)
        # Now, call out to flights module and request a revision for this aircraft and first day.
        revise_flights_receipt = flights.revise_flight_data_for(aircraft, date(2022, 9, 24))
        db.session.flush()
        # Ensure not None.
        self.assertIsNotNone(revise_flights_receipt)

    def test_comprehensive_7c4ef4_t2(self):
        """
        """
        # Read the aircraft, with its points, added to the database, and also all dates in this test data; we will not require flights to be verified,
        # but history must be verified.
        aircraft, dates = self._load_native_test_data("aircraft_7c4ef4_t2.json", history_verified = True, flights_verified = False)
        db.session.flush()
        # Ensure there's 1 date in this case.
        self.assertEqual(len(dates), 1)
        # Now, call out to flights module and request a revision for this aircraft and first day.
        revise_flights_receipt = flights.revise_flight_data_for(aircraft, date(2022, 9, 24))
        db.session.flush()
        # Ensure not None.
        self.assertIsNotNone(revise_flights_receipt)

    def test_comprehensive_7c68b7_t1(self):
        """
        On this date, this aircraft flew 3 separate flights. One from Sydney to Canberra, another from Canberra to Melbourne, and a final
        one from Melbourne back to Sydney. As such, we will expect three flights to be constructed.
        """
        # Read the aircraft, with its points, added to the database, and also all dates in this test data; we will not require flights to be verified,
        # but history must be verified.
        aircraft, dates = self._load_native_test_data("aircraft_7c68b7_t1.json", history_verified = True, flights_verified = False)
        db.session.flush()
        # Ensure there's 1 date in this case.
        self.assertEqual(len(dates), 1)
        # Now, call out to flights module and request a revision for this aircraft and first day.
        revise_flights_receipt = flights.revise_flight_data_for(aircraft, dates[0])
        db.session.flush()
        # Ensure not None.
        self.assertIsNotNone(revise_flights_receipt)
        # Ensure there are 3 flights.
        self.assertEqual(revise_flights_receipt.num_flights, 3)
        # Ensure, overall, there are only 3 flights.
        self.assertEqual(models.Flight.query.count(), 3)
        # For some reason, we will revise flight data again. This time, provide the 'force' attr so it goes through.
        revise_flights_receipt = flights.revise_flight_data_for(aircraft, dates[0], force = True)
        db.session.flush()
        # Irrespective, we should have the same output AND we should still have only 3 flights in total.
        self.assertEqual(revise_flights_receipt.num_flights, 3)
        self.assertEqual(models.Flight.query.count(), 3)

    def test_comprehensive_7c4ee8_t1(self):
        """
        On this date, according to data, the aircraft begins its flight in middair. Shortly after flight data commences, the aircraft ceases to be reported
        correctly. When reporting reappears, approximately 40 minutes later, the aircraft is heading toward Albury airport and has decreased altitude to 7875 ft.
        The aircraft does not land, this is obvious as time between points is consistent with a continues flight from here. The aircraft flies back to Essendon from
        here. The final flight commences, and the flight data ends at 30000ft. The following day, the flight continues until it lands.

        From this, we expect 2 flights.
        """
        # Read the aircraft, with its points, added to the database, and also all dates in this test data; we will not require flights to be verified,
        # but history must be verified.
        aircraft, dates = self._load_native_test_data("aircraft_7c4ee8_t1.json", history_verified = True, flights_verified = False)
        db.session.flush()
        # Ensure there's 2 date in this case.
        self.assertEqual(len(dates), 2)
        # Now, call out to flights module and request a revision for this aircraft and first day.
        revise_flights_receipt = flights.revise_flight_data_for(aircraft, dates[0])
        db.session.flush()
        # Ensure not None.
        self.assertIsNotNone(revise_flights_receipt)
        # Ensure there are 2 flights.
        self.assertEqual(revise_flights_receipt.num_flights, 2)
        # Ensure, overall, there are only 2 flights.
        self.assertEqual(models.Flight.query.count(), 2)

    def test_comprehensive_7c4ee8_t1_across_two_updates(self):
        """
        Exact same as above. But this time, we we load only the first day (19/07/2021) first, develop flights from that, then we'll load the remaining data from
        20/07/2021, and supplement the existing flight with that data. We should still have two.

        All in all, there should be 2 flights to come out of this.
        """
        # Read the aircraft, with only points from 19/07/2021.
        aircraft, dates = self._load_native_test_data("aircraft_7c4ee8_t1.json", history_verified = True, flights_verified = False, only_load_points_from = [date(2021, 7, 19)])
        db.session.flush()
        # Ensure there's 1 dates in this case.
        self.assertEqual(len(dates), 1)
        # Now, call out to flights module and request a revision for this aircraft and first day.
        revise_flights_receipt = flights.revise_flight_data_for(aircraft, dates[0])
        db.session.flush()
        # Ensure not None.
        self.assertIsNotNone(revise_flights_receipt)
        # Ensure there are 2 flights.
        self.assertEqual(revise_flights_receipt.num_flights, 2)
        # Ensure, overall, there are only 2 flights.
        self.assertEqual(models.Flight.query.count(), 2)
        # Get the second flight.
        second_flight = revise_flights_receipt.flights[1]
        # Ensure this spans across 1 day.
        self.assertEqual(second_flight.num_days_across, 1)

        # Now, load test data, only from 20/07/2021.
        aircraft, dates = self._load_native_test_data("aircraft_7c4ee8_t1.json", history_verified = True, flights_verified = False, only_load_points_from = [date(2021, 7, 20)])
        db.session.flush()
        # Ensure there's 1 dates in this case.
        self.assertEqual(len(dates), 1)
        # Now, call out to flights module and request a revision for this aircraft and first day.
        revise_flights_receipt = flights.revise_flight_data_for(aircraft, dates[0])
        db.session.flush()
        # Ensure not None.
        self.assertIsNotNone(revise_flights_receipt)
        # Ensure is one flight in this receipt.
        self.assertEqual(revise_flights_receipt.num_flights, 1)
        # Ensure, overall, there are still only 2 flights.
        self.assertEqual(models.Flight.query.count(), 2)
        # Get the first flight.
        first_flight = revise_flights_receipt.flights[0]
        # Ensure this spans across 2 days now.
        self.assertEqual(first_flight.num_days_across, 2)

    def test_comprehensive_7c4ee8_t2(self):
        """
        On this date, according to data, the aircraft begins on the ground. It then takes off and flies, ADSB data breaks off around 9:38, but our partial
        flight continues. Data ceases being reported correctly at about 10:08, and remains this way for around 6 minutes. This is about double our TIME_DIFFERENCE_NEW_FLIGHT,
        and so the partial flight is broken off here. This, due to altitude change & time difference, is obviously not a new flight. After this, the aircraft lands back
        at Essendon. Finally, the aircraft takes off again and flies around for a bit until the end of the day, which is also the last flight. Another unreliable aspect of
        this aircraft is shown here - the last partial begins mid air, but this should be regarded as a new flight, as time difference + altitude is sufficient.

        There must be 2 flights.
        """
        # Read the aircraft, with its points, added to the database, and also all dates in this test data; we will not require flights to be verified,
        # but history must be verified.
        aircraft, dates = self._load_native_test_data("aircraft_7c4ee8_t2.json", history_verified = True, flights_verified = False)
        db.session.flush()
        # Ensure there's 1 date in this case.
        self.assertEqual(len(dates), 1)
        # Now, call out to flights module and request a revision for this aircraft and first day.
        revise_flights_receipt = flights.revise_flight_data_for(aircraft, dates[0])
        db.session.flush()
        # Ensure not None.
        self.assertIsNotNone(revise_flights_receipt)
        # Ensure there are 2 flights.
        self.assertEqual(revise_flights_receipt.num_flights, 2)
        # Ensure, overall, there are only 2 flights.
        self.assertEqual(models.Flight.query.count(), 2)

    def test_comprehensive_7c4ee8_t3(self):
        """
        This test data contains two separate dates; 13/01/2022 and 14/01/2022. There are several flights over these two days that are of interest, but for the purposes of this function, we are interested
        purely in a flight taken from Melbourne airport to Tasmania as the final leg for 13/01. The aircraft completes a few flights around the Melbourne area for the first 2 partials. The third partial
        is a flight (and landing) to Tasmania. It does not look like a landing, because final seen altitude is 6000ft+. But, that said, next partial begins almost 6 hours later; so its safe to assume the
        landing/takeoff. As aforementioned, the flight also takes off at a high altitude on 14/01. This must also be considered a takeoff however. The flight then heads back to Melbourne. After a refueling,
        the aircraft completes another tour of Melbourne and lands. It's important to note, that in almost all landings for this aircraft, the aircraft is not 'on ground', but is instead still in the air.

        There must be 5 flights.
        """
        # Read the aircraft, with its points, added to the database, and also all dates in this test data; we will not require flights to be verified,
        # but history must be verified.
        aircraft, dates = self._load_native_test_data("aircraft_7c4ee8_t3.json", history_verified = True, flights_verified = False)
        db.session.flush()
        # Ensure there's 2 dates in this case.
        self.assertEqual(len(dates), 2)
        # Now, call out to flights module and request a revision for this aircraft and first day.
        revise_flights_receipt = flights.revise_flight_data_for(aircraft, dates[0])
        db.session.flush()
        # Ensure not None.
        self.assertIsNotNone(revise_flights_receipt)
        # Ensure there are 3 flights on this day.
        self.assertEqual(revise_flights_receipt.num_flights, 3)
        # Ensure, overall, there are 3 flights so far.
        self.assertEqual(models.Flight.query.count(), 3)

        # Now call out to flights module again and request a revision for this aircraft and second day.
        revise_flights_receipt = flights.revise_flight_data_for(aircraft, dates[1])
        db.session.flush()
        # Ensure not None.
        self.assertIsNotNone(revise_flights_receipt)
        # Ensure there are 2 flights on this day.
        self.assertEqual(revise_flights_receipt.num_flights, 2)
        # Ensure, overall, there are 5 flights overall.
        self.assertEqual(models.Flight.query.count(), 5)

    def test_comprehensive_7c4ef2_t1(self):
        """
        On this date, POL30 flies in total 6 separate flights. All contained within one day. The issue is, legs 3 & 4 appear to be two totally different flights since leg 3 ends mid air at 2900ft, then leg 4
        starts at 2000ft. The difference in time is only two hours. We may have to consider AW139's range statistics in this determination. We will happily call 3 & 4 one flight because the time difference, though
        significant, is not well past the helicopter's range.

        There should be 6 flights overall.
        """
        # Read the aircraft, with its points, added to the database, and also all dates in this test data; we will not require flights to be verified,
        # but history must be verified.
        aircraft, dates = self._load_native_test_data("aircraft_7c4ef2_t1.json", history_verified = True, flights_verified = False)
        db.session.flush()
        # Ensure there's 1 date in this case.
        self.assertEqual(len(dates), 1)
        # Now, call out to flights module and request a revision for this aircraft and first day.
        revise_flights_receipt = flights.revise_flight_data_for(aircraft, dates[0])
        db.session.flush()
        # Ensure not None.
        self.assertIsNotNone(revise_flights_receipt)
        # Ensure there are 6 flights on this day.
        self.assertEqual(revise_flights_receipt.num_flights, 6)
        # Ensure, overall, there are 6 flights so far.
        self.assertEqual(models.Flight.query.count(), 6)

    def test_comprehensive_7c6bcf_t1(self):
        """
        This flight data consists of two separate dates; 25/06/2022 and 26/06/2022. There are 4 separate flights on this day. One from Brisbane to Sydney, from Sydney
        to Orange[*], from Orange back to Sydney[*], and finally, from Sydney towards Brisbane. The flight to Brisbane lands the next day, on the 26th.

        [*] This flight to orange has a strange ending; the aircraft is flying toward the airfield, but drops off map at alt 5000, regains presence on map a significant time
        later, and at similar altitude. This is most definitely a landing, but it has been cut off half way through. Devise a way to verify this as a full flight. Perhaps by
        taking into account first the significant amount of time between updates, then checking whether there may be an airport nearby/within heading of aircraft?

        There should be three flights total, across 2 days.
        """
        aircraft, dates = self._load_native_test_data("aircraft_7c6bcf_t1.json", history_verified = True, flights_verified = False)
        db.session.flush()
        # Ensure there's 2 dates in this case.
        self.assertEqual(len(dates), 2)
        # Now, call out to flights module and request a revision for this aircraft and first day.
        revise_flights_receipt = flights.revise_flight_data_for(aircraft, dates[0])
        db.session.flush()
        # Ensure not None.
        self.assertIsNotNone(revise_flights_receipt)
        # Ensure there are 4 flights.
        self.assertEqual(revise_flights_receipt.num_flights, 4)
        # Ensure, overall, there are only 4 flights.
        self.assertEqual(models.Flight.query.count(), 4)

    def test_comprehensive_7c4ee8_20200816(self):
        """
        This flight data consists of a dodgy point-to-point flight at the very start, along with 2 full flights and one taxi-only flight. This is a fringe issue data log, because
        that dodgy point-to-point flight is just that; a single point at index 0, with a 5000+ second jump to the very next point. This should be discluded by daily flights view
        prior to going anywhere. We CAN potentially report this issue.

        We expect 3 flights for now, though we should come up with something special for that taxiing only flight.
        """
        # For these old traces, we must import aircraft states.
        aircraft = airvehicles.read_aircraft_from("aircraft_states.json")
        db.session.flush()
        # Import the trace.
        aircraft, dates = self._load_native_test_data("aircraft_7c4ee8_20200816.json", history_verified = True, flights_verified = False)
        db.session.flush()
        # Ensure there's 1 date in this case.
        self.assertEqual(len(dates), 1)
        # Now, call out to flights module and request a revision for this aircraft and first day.
        revise_flights_receipt = flights.revise_flight_data_for(aircraft, dates[0])
        db.session.flush()
        # Ensure not None.
        self.assertIsNotNone(revise_flights_receipt)
        # Ensure there are 3 flights.
        self.assertEqual(revise_flights_receipt.num_flights, 3)
        # Ensure, overall, there are only 3 flights.
        self.assertEqual(models.Flight.query.count(), 3)


class TestFlightDataSubmission(BaseCase):
    """
    Testing the flight_partial_submitted function's capability of correctly extending an existing Flight, given a partial subsection of
    points, sent by a radar worker in realtime. This should test the creation of new flights, and augmentation of old flights.

    TODO: to write a test, what if a submission of flight points contains points that themselves span across multiple days.
    """
    def test_attempt_submission_same_day_7c68b7(self):
        """
        Given this flight data log, which contains three separate flights each occuring on the same day, we will test the same day capability of the
        partial flight submission function. We'll begin by taking a large subsection of the very first flight; all points except the same. We'll then
        submit a single point; the final in that flight. We'll then create a whole new flight by providing the first few points of the next flight. We'll
        then submit a substantial number of points to that second flight. Ensure it grows.
        """
        # We require airports for this.
        airvehicles.read_airports_from(config.AIRPORTS_CONFIG)
        db.session.flush()
        # We will load this data manually.
        with open(os.path.join(os.getcwd(), config.IMPORTS_DIR, "native_testdata", "aircraft_7c68b7_t1.json"), "r") as f:
            aircraft_json = json.loads(f.read())

        # Subsect flight points into 4 parts.
        # Part 1, the entire first flight MINUS a single point: 0 -> 468
        flight_points_sub1 = aircraft_json["FlightPoints"][:469]
        self.assertEqual(len(flight_points_sub1), 469)
        # Part 2, the last point in the first flight for this day: 469 -> 470
        flight_points_sub2 = aircraft_json["FlightPoints"][469:470]
        self.assertEqual(len(flight_points_sub2), 1)
        # Part 3, the first 6 points in the next flight for the day: 470 -> 476
        flight_points_sub3 = aircraft_json["FlightPoints"][470:476]
        self.assertEqual(len(flight_points_sub3), 6)
        # Part 4, another few points in the second flight for the day: 476 -> 601
        flight_points_sub4 = aircraft_json["FlightPoints"][476:601]
        self.assertEqual(len(flight_points_sub4), 125)

        # Submit the first sub.
        aircraft, flight_points, synchronised_flight_points = self._submit_flight_point_dicts(aircraft_json, flight_points_sub1)
        # Ensure we have both a Day for date(2022, 7, 29) created, and an AircraftPresentDay.
        aircraft_present_day, was_created = traces.ensure_aircraft_day_junction_exists(aircraft, date(2022, 7, 29),
            flights_verified = False, history_verified = False)
        db.session.flush()
        # Ensure this aircraft, so far, has just 469 points.
        self.assertEqual(len(flight_points), 469)
        # Submit this list of points to the submission function.
        submission_receipt = flights.flight_partial_submitted(aircraft, date(2022, 7, 29), flight_points)
        db.session.flush()
        # Ensure this flight isn't None, and ensure it was created.
        self.assertIsNotNone(submission_receipt.flight)
        self.assertEqual(submission_receipt.was_created, True)
        # Ensure this flight has a take off airport, AND a landing airport.
        self.assertIsNotNone(submission_receipt.flight.takeoff_airport)
        self.assertIsNotNone(submission_receipt.flight.landing_airport)
        # Ensure we have 1 flight overall.
        self.assertEqual(models.Flight.query.count(), 1)

        # Now, submit the second sub to the database. This should complete the very first flight, but should still be included in that first flight.
        aircraft, flight_points, synchronised_flight_points = self._submit_flight_point_dicts(aircraft_json, flight_points_sub2)
        # Submit this section partial.
        submission_receipt = flights.flight_partial_submitted(aircraft, date(2022, 7, 29), flight_points)
        db.session.flush()
        # Ensure this flight isn't None, and ensure it was NOT created.
        self.assertIsNotNone(submission_receipt.flight)
        self.assertEqual(submission_receipt.was_created, False)
        # Ensure we have 1 flight overall.
        self.assertEqual(models.Flight.query.count(), 1)

        # Next, we will now load sub3. This is the beginning of a new flight on the same day.
        aircraft, flight_points, synchronised_flight_points = self._submit_flight_point_dicts(aircraft_json, flight_points_sub3)
        db.session.flush()
        # Submit this section partial.
        submission_receipt = flights.flight_partial_submitted(aircraft, date(2022, 7, 29), flight_points)
        db.session.flush()
        # Ensure this flight isn't None, and ensure it was created.
        self.assertIsNotNone(submission_receipt.flight)
        self.assertEqual(submission_receipt.was_created, True)
        # Flight is grounded at the moment.
        self.assertEqual(submission_receipt.flight.is_on_ground, True)
        # Ensure this flight has a take off airport, but not a landing airport.
        self.assertIsNotNone(submission_receipt.flight.takeoff_airport)
        self.assertIsNone(submission_receipt.flight.landing_airport)
        # Ensure we have 2 flight overall.
        self.assertEqual(models.Flight.query.count(), 2)

        # Now, we will now load sub4. This is a substantial portion of flight data from that second flight.
        aircraft, flight_points, synchronised_flight_points = self._submit_flight_point_dicts(aircraft_json, flight_points_sub4)
        # Submit this section partial.
        submission_receipt = flights.flight_partial_submitted(aircraft, date(2022, 7, 29), flight_points)
        db.session.flush()
        # Ensure this flight isn't None, and ensure it was NOT created.
        self.assertIsNotNone(submission_receipt.flight)
        self.assertEqual(submission_receipt.was_created, False)
        # Flight is no longer grounded.
        self.assertEqual(submission_receipt.flight.is_on_ground, False)
        # Ensure this flight has a take off airport, but not a landing airport.
        self.assertIsNotNone(submission_receipt.flight.takeoff_airport)
        self.assertIsNone(submission_receipt.flight.landing_airport)
        # Ensure we have 2 flight overall.
        self.assertEqual(models.Flight.query.count(), 2)


class TestFlightQueries(BaseCase):
    def test_query_flights(self):
        # Import all aircraft.
        aircraft = airvehicles.read_aircraft_from(config.KNOWN_AIRCRAFT_CONFIG)
        db.session.flush()
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.flush()
        aircraft_7c68b7, aircraft_7c68b7_flights = self._import_all_flights("aircraft_7c68b7_t1.json", 3)
        db.session.flush()

        # Query all flights, sort by latest first.
        all_flights = flights.query_flights(newest_first = True)\
            .all()
        # Ensure there are 5 returned.
        self.assertEqual(len(all_flights), 5)

    def test_flight_number(self):
        """
        First, import all aircraft. Of those, we will focus on two; 7c68b7 and 7c6bcf.
        For each aircraft, import the following traces;
            aircraft_7c68b7_t1, 7c68b7: there are 3 flights in here.
            aircraft_7c4ee8_t1, 7c4ee8, there are 2 flights in here.
        Verify this data is correct and loaded.
        """
        # Import all aircraft.
        aircraft = airvehicles.read_aircraft_from(config.KNOWN_AIRCRAFT_CONFIG)
        db.session.flush()
        # Load 7c4ee8 FIRST, so the flight record indicies for 7c68b7 will be off by two.
        aircraft_7c4ee8, aircraft_7c4ee8_flights = self._import_all_flights("aircraft_7c4ee8_t1.json", 2)
        db.session.flush()
        # Load 7c68b7.
        aircraft_7c68b7, aircraft_7c68b7_flights = self._import_all_flights("aircraft_7c68b7_t1.json", 3)
        db.session.flush()

        # Query all flights from 7c68b7, ensure we get three.
        # We will request oldest flights first.
        all_flights_7c68b7 = flights.query_flights_from(aircraft_7c68b7, newest_first = False).all()
        self.assertEqual(len(all_flights_7c68b7), 3)
        # Now, verify that as we iterate through the resulting flights instance, the flight number is equal to the enumeration index + 1.
        # Also, ensure each flight name is equal to the following format; 'UYX Flight #X' where X is enumeration index + 1
        for flight_index, flight in enumerate(all_flights_7c68b7):
            self.assertEqual(flight.flight_number, flight_index+1)
            self.assertEqual(flight.flight_name, f"UYX Flight #{flight_index+1}")
        # Do the same for 7c4ee8.
        for flight_index, flight in enumerate(aircraft_7c4ee8_flights):
            self.assertEqual(flight.flight_number, flight_index+1)
            self.assertEqual(flight.flight_name, f"POL35 Flight #{flight_index+1}")

    def test_latest_flight(self):
        """
        First, import flights for 7c68b7. There are 3 flights in here.
        Verify this data is correct and loaded.
        """
        # Load 7c68b7.
        aircraft_7c68b7, aircraft_7c68b7_flights = self._import_all_flights("aircraft_7c68b7_t1.json", 3)
        db.session.flush()

        # Query all flights from 7c68b7, ensure we get three.
        all_flights_7c68b7 = flights.query_flights_from(aircraft_7c68b7, newest_first = True).all()
        self.assertEqual(len(all_flights_7c68b7), 3)
        # Get latest flight from the aircraft. Ensure the returned value matches the first item in all_flights_7c68b7.
        self.assertEqual(all_flights_7c68b7[0], aircraft_7c68b7.latest_flight)

    def test_distance_kilometers(self):
        """
        """
        # Import all aircraft.
        aircraft = airvehicles.read_aircraft_from(config.KNOWN_AIRCRAFT_CONFIG)
        db.session.flush()
        # Now, query total kilometers from the aircraft. It should be None.
        aircraft_distance_kms_q = db.session.query(models.Aircraft.distance_travelled_kilometers)\
            .outerjoin(models.Aircraft.flights_)\
            .filter(models.Aircraft.icao == aircraft[0].icao)\
            .scalar()
        self.assertEqual(aircraft_distance_kms_q, 0)

        # Load 7c68b7.
        aircraft_7c68b7, aircraft_7c68b7_flights = self._import_all_flights("aircraft_7c68b7_t1.json", 3)
        db.session.flush()

        # Get all flights, ensure we have 3.
        all_flights_7c68b7 = flights.query_flights_from(aircraft_7c68b7, newest_first = True).all()
        self.assertEqual(len(all_flights_7c68b7), 3)
        # Now, from the first flight, grab distance kilometers via the instance level func.
        flight = all_flights_7c68b7[0]
        distance_kms = flight.distance_travelled_kilometers
        # Now, query this distance KMS by expression. Ensure it matches the instance level.
        distance_kms_q = db.session.query(models.Flight.distance_travelled_kilometers)\
            .filter(models.Flight.flight_hash == flight.flight_hash)\
            .scalar()
        self.assertEqual(distance_kms_q, distance_kms)
        # Get all distance travelled (kilometers) from the aircraft.
        aircraft_distance_kms = aircraft_7c68b7.distance_travelled_kilometers
        # Now, get it from a query. Ensure they match.
        aircraft_distance_kms_q = db.session.query(models.Aircraft.distance_travelled_kilometers)\
            .join(models.Aircraft.flights_)\
            .filter(models.Aircraft.icao == aircraft_7c68b7.icao)\
            .scalar()
        self.assertEqual(aircraft_distance_kms_q, aircraft_distance_kms)
        # This sort've works, for some reason there's some rounding issues, but we gotta move on.

    def test_query_flights_from(self):
        """
        First, import all aircraft. Of those, we will focus on two; 7c68b7 and 7c6bcf.
        For each aircraft, import the following traces;
            aircraft_7c68b7_t1, 7c68b7: there are 3 flights in here.
            aircraft_7c4ee8_t1, 7c4ee8, there are 2 flights in here.
        Verify this data is correct and loaded.

        Now, we'll test our ability to query flights from each of these.
        Query ALL flights from 7c68b7, order by newest flights first, ensure we get three.
        Verify the first flights start is greater than the second flights start.
        Query ALL flights from 7c4ee8, order by newest flights first, ensure we get two.
        Verify the first flights start is greater than the second flights start.

        Query ALL flights from 7c68b7, order by oldest flights first, ensure we get three.
        Verify the first flights start is less than the second flights start.
        Query ALL flights from 7c4ee8, order by oldest flights first, ensure we get two.
        Verify the first flights start is less than the second flights start.
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

        # Query all flights from 7c68b7, ensure we get three.
        # We will request newest flights first.
        all_flights_7c68b7 = flights.query_flights_from(aircraft_7c68b7, newest_first = True).all()
        self.assertEqual(len(all_flights_7c68b7), 3)
        # Ensure first point's timestamp is greater than second points timestamp.
        self.assertGreater(all_flights_7c68b7[0].starts_at, all_flights_7c68b7[1].starts_at)
        # Query all flights from 7c4ee8, ensure we get two.
        # We will request newest flights first.
        all_flights_7c4ee8 = flights.query_flights_from(aircraft_7c4ee8, newest_first = True).all()
        self.assertEqual(len(all_flights_7c4ee8), 2)
        # Ensure first point's timestamp is greater than second points timestamp.
        self.assertGreater(all_flights_7c68b7[0].starts_at, all_flights_7c68b7[1].starts_at)

        # Query all flights from 7c68b7, ensure we get three.
        # We will request oldest flights first.
        all_flights_7c68b7 = flights.query_flights_from(aircraft_7c68b7, newest_first = False).all()
        self.assertEqual(len(all_flights_7c68b7), 3)
        # Ensure first point's timestamp is less than second points timestamp.
        self.assertLess(all_flights_7c68b7[0].starts_at, all_flights_7c68b7[1].starts_at)
        # Query all flights from 7c4ee8, ensure we get two.
        # We will request newest flights first.
        all_flights_7c4ee8 = flights.query_flights_from(aircraft_7c4ee8, newest_first = False).all()
        self.assertEqual(len(all_flights_7c4ee8), 2)
        # Ensure first point's timestamp is less than second points timestamp.
        self.assertLess(all_flights_7c68b7[0].starts_at, all_flights_7c68b7[1].starts_at)
