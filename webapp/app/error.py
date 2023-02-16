
class NoSuburbFoundError(Exception):
    pass


class NoStateFoundError(Exception):
    pass


class SuburbSearchExhausted(Exception):
    def __init__(self, _error_code):
        self.error_code = _error_code


class SchemaValidationFail(Exception):
    """
    An exception type for reporting unexpected marshmallow ValidationErrors. For example, when loading an Aircraft schema, sometimes
    the source data may present a case we aren't aware of. This Exception will demand that the error be written to disk for debug.
    """
    def __init__(self, _schema_type_name, _original_source_json):
        super().__init__()
        self.schema_type_name = _schema_type_name
        self.original_source_json = _original_source_json


class PageEvaluationFail(Exception):
    """
    An exception type for reporting worker Page evaluation failures. For example, the target Page's code has changed and as such, when
    we evaluate our own Javascript, that code fails.
    """
    def __init__(self, _function_identifier, _error):
        super().__init__()
        self.function_identifier = _function_identifier
        self.error = _error


class NoAirportsLoaded(Exception):
    """An exception that reports when find_airport_for has been called, but there are no airports loaded into the database."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class NoAirportFound(Exception):
    """Reports that no airport could be found."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class InsufficientPartialFlightError(Exception):
    """
    """
    def __init__(self, _partial_flight_fragments, **kwargs):
        self.partial_flight_fragments = _partial_flight_fragments


class NoFlightPointsToAssimilateError(Exception):
    """
    An exception that lets calling code know, that an attempt to submit a partial flight failed because there were no new flight points
    to actually submit.
    """
    def __init__(self):
        pass


class FlightChangeInaccuracySolvencyRequired(Exception):
    """
    Flags a particular flight change descriptor as being anomalous such that further investigation is required to determine exactly what
    happened, interpolate if necessary, or at the very least; to log the issue.
    """
    def __init__(self, _flight_point_change_descriptor, **kwargs):
        self.flight_point_change_descriptor = _flight_point_change_descriptor


class FlightDataRevisionRequired(Exception):
    """
    An exception that will queue the requested aircraft/day combination for flight data revision. This is usually raised whilst revising a day
    who's data has just been uploaded, but which requires another day as well. Optionally, the programmer can elect to have either the history
    revised, just the flight data revised or both. By default, this is a hard revision order; the referred aircraft/day will be queued for both
    history and flight data verification.
    """
    def __init__(self, _aircraft, _day, **kwargs):
        self.requires_history = kwargs.get("requires_history", True)
        self.requires_flight = kwargs.get("requires_flight", True)

        self.aircraft = _aircraft
        self.day = _day


class NoFlightsAssimilatedError(Exception):
    """
    An exception for reporting a total lack of success in assimilating a daily flights view instance. This is not raised when no partial flights exist,
    only when no flights are either created or updated in the process.
    """
    def __init__(self, _error_code = "no-error-given", **kwargs):
        self.error_code = _error_code


class FlightAssimilationError(Exception):
    """
    An exception for handling the failure of a single flight assimilation instance.
    """
    pass


class NoPartialFlightFoundForSubmission(Exception):
    """
    When a radar worker submits new flight points, and after those points have been committed to the database, this exception will be used if
    no partial flight can be found from the current day.
    """
    def __init__(self, _aircraft, _day, _flight_points, **kwargs):
        self.aircraft = _aircraft
        self.day = _day
        self.flight_points = _flight_points


class MissingFuelFiguresError(Exception):
    """
    An exception that will be raised when an aircraft is used to calculate flight fuel used, but that aircraft does not yet have fuel consumption
    data, and it can't be found in any new data files.
    """
    def __init__(self, _aircraft, **kwargs):
        self.aircraft = _aircraft


class NoFuelFiguresDataFound(Exception):
    """
    The aircraft fuel figures JSON file can't be found.
    """
    def __init__(self, **kwargs):
        pass


class MultiplePotentialFlightsFoundError(Exception):
    def __init__(self, _flight_assimilator, **kwargs):
        super().__init__()
        self.flight_assimilator = _flight_assimilator


class NoPartialFlightsError(Exception):
    def __init__(self, _flight_assimilator, **kwargs):
        super().__init__()
        self.flight_assimilator = _flight_assimilator


class NoFlightPointsError(Exception):
    def __init__(self, _aircraft, _reason = "No reason given.", **kwargs):
        super().__init__()
        self.aircraft = _aircraft
        self.reason = _reason

        self.flight_assimilator = kwargs.get("assimilator", None)
        self.partial_flights = kwargs.get("partial_flights", None)
        self.days = kwargs.get("days", None)


class NoAircraftStateInTrace(Exception):
    def __init__(self, _icao, _trace, **kwargs):
        self.icao = _icao
        self.trace = _trace


class NoAircraftStateInFile(Exception):
    def __init__(self, _icao, _reason, **kwargs):
        self.icao = _icao
        self.reason = _reason


class HistoryVerifiedError(Exception):
    def __init__(self, _icao, **kwargs):
        self.icao = _icao


class FlightsVerifiedError(Exception):
    def __init__(self, _aircraft_present_day, **kwargs):
        self.aircraft_icao = _aircraft_present_day.aircraft_icao
        self.day = _aircraft_present_day.day_day


class FlightPointsGeolocatorError(Exception):
    def __init__(self, error_code = "no-error", **kwargs):
        self.error_code = error_code
        self.flight_points = kwargs.get("flight_points", None)


class FlightPointsGeolocatedError(Exception):
    def __init__(self, _aircraft_present_day, **kwargs):
        self.aircraft_icao = _aircraft_present_day.aircraft_icao
        self.day = _aircraft_present_day.day_day
        self.error_code = kwargs.get("error_code", "No reason given.")


class RequestWorkError(Exception):
    def __init__(self, _radar_worker_name, **kwargs):
        super().__init__()
        self.radar_worker_name = _radar_worker_name


class NoAssignableWorkLeft(Exception):
    def __init__(self, _radar_worker_name, **kwargs):
        super().__init__()
        self.radar_worker_name = _radar_worker_name


class NoMasterError(Exception):
    def __init__(self, **kwargs):
        pass


class MasterServerOfflineError(Exception):
    def __init__(self, **kwargs):
        pass


class RadarWorkerError(Exception):
    def __init__(self, _radar_worker, _error_code, _reason = "No reason given.", **kwargs):
        self.radar_worker = _radar_worker
        self.error_code = _error_code
        self.reason = _reason


class RadarWorkerRequiredError(Exception):
    def __init__(self, _error_code = "", **kwargs):
        self.error_code = _error_code


class RadarWorkerIncompatibleError(Exception):
    def __init__(self, _radar_worker, _required_type, **kwargs):
        self.radar_worker = _radar_worker
        self.required_type = _required_type


class InvalidCRSError(Exception):
    @property
    def flight_points_json(self):
        """Return a JSON serialised list of all flight points."""
        raise NotImplementedError("InvalidCRSError::flight_points_json() not implemented yet")

    def __init__(self, reason, **kwargs):
        self.reason = reason
        # Attempt to get the flight points that triggered this error, by default, a blank list.
        self.flight_points = kwargs.get("flight_points", [])


class FlightPointsIntegrityError(InvalidCRSError):
    """An error for when an operation is attempted that requires the given flight points to be valid within specific criteria."""
    def __init__(self, operation, reason, **kwargs):
        super().__init__(reason, **kwargs)
        self.operation = operation


class FlightPointPositionIntegrityError(Exception):
    """An error for when a given flight point is not sufficiently detailed to perform an operation."""
    def __init__(self, aircraft, position, operation, reason, **kwargs):
        self.aircraft = aircraft
        self.position = position
        self.operation = operation
        self.reason = reason


class FlightPointIntegrityError(Exception):
    """An error for when a given flight point is not sufficiently detailed to perform an operation."""
    def __init__(self, aircraft, flight_point, error_code, **kwargs):
        self.aircraft = aircraft
        self.flight_point = flight_point
        self.error_code = error_code


class NoFlightPathError(Exception):
    def __init__(self):
        pass
