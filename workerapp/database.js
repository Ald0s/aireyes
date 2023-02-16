const { Sequelize, Model, DataTypes, Op } = require("sequelize");
const schema = require("./schema");
const conf = require("./conf");
const com = require("./com");
const blake2 = require("blake2");

var sequelize = null;

/*
Given an AircraftState, from either aircraftStateFromTrace/aircraftStateFromBinary, build a new instance of
the Flight model ready for database insertion and return it.

Arguments:
:aircraftState: An AircraftState object, from either aircraftStateFromTrace or aircraftStateFromBinary.

Returns:
A built instance of the Aircraft database model.
*/
function buildAircraftModel(aircraftState) {
    return sequelize.models.Aircraft.build({
        icao: aircraftState.icao,
        type: aircraftState.type,
        registration: aircraftState.registration,

        flightName: aircraftState.flightName,
        year: aircraftState.year,
        description: aircraftState.description,
        ownerOperator: aircraftState.ownerOperator,
        comprehensive: aircraftState.comprehensive
    });
}

/*
Given a single FlightPoint object, from either flightPointFromBinary/flightPointsFromTrace, build a new instance
of the FlightPoint database model and return it.

Arguments:
:flightPoint: A FlightPoint object, from either flightPointFromBinary or flightPointsFromTrace.

Returns:
A built instance of the FlightPoint database model.
*/
function newFlightPointModel(flightPoint) {
    return sequelize.models.FlightPoint.build({
        /* Full datetime of occurance, this requires timestamp in seconds. */
        timestamp: flightPoint.timestamp,
        /* An ISO format date, for comparison reasons; this is now a string on order of YYYY-MM-DD
        Old code is here; new Date(flightPoint.timestamp * 1000)*/
        date: com.dateToUtc(new Date(flightPoint.timestamp * 1000)),

        latitude: flightPoint.latitude,
        longitude: flightPoint.longitude,

        altitude: flightPoint.altitude,
        groundSpeed: flightPoint.groundSpeed,
        rotation: flightPoint.rotation,
        verticalRate: flightPoint.verticalRate,
        dataSource: flightPoint.dataSource,

        isOnGround: flightPoint.isOnGround,
        isAscending: flightPoint.isAscending,
        isDescending: flightPoint.isDescending
    });
}

/*
Builds an array of FlightPoint database models from the given list of FlightPoints. A FlightPoint hash will also be generated from the key data that uniquely identifies a single flight point;
that is, Aircraft ICAO and flight point timestamp. These are unique together as a single aircraft can ONLY be in ONE place at a particular timestamp. A BuiltFlightPoints result will be returned
that will allow async code to save the built points to the database, along with an association to a particular aircraft.

Arguments
---------
:flightPoints: An array of FlightPoint objects procured by the data module.

Returns
-------
A BuiltFlightPoints object, that will allow async code to associate these points with an aircraft.
*/
function buildFlightPointsModels(flightPoints) {
    let resultFlightPoints = [];
    // Now, iterate all these flight points, and build a new FlightPoint model for each.
    var lastFlightPoint = null;
    for(var flightPointIdx = 0; flightPointIdx < flightPoints.length; flightPointIdx++) {
        let currentFlightPoint = flightPoints [flightPointIdx];
        let nextFlightPoint = (flightPointIdx+1 < flightPoints.length) ? flightPoints[ flightPointIdx+1 ] : null;
        // Build a FlightPoint model.
        let flightPointModel = newFlightPointModel(currentFlightPoint);
        resultFlightPoints.push(flightPointModel);
        // Update last flight point.
        lastFlightPoint = currentFlightPoint;
    }
    // Create a BuiltFlightPoints object.
    return {
        flightPoints: resultFlightPoints,
        saveToAircraft: async function(aircraft) {
            // Now, we will iterate each flightPoint, to create the hash for each.
            for(let flightPoint of this.flightPoints) {
                // Set this flight point as referring to the given aircraft by its ICAO. This aircraft MUST already exist.
                flightPoint.AircraftIcao = aircraft.icao;
                // A hash for a flight point is dependant on two values; the aircraft icao, and the timestamp at which the point was created.
                // Furthermore, flight point hash has been extended by position and altitude. See python __init__.py for details.
                let positionForHash = (flightPoint.latitude !== null && flightPoint.longitude !== null)
                    ? flightPoint.latitude.toString()+flightPoint.longitude.toString() : "0";
                let altitudeForHash = (flightPoint.altitude !== null)
                    ? flightPoint.altitude.toString() : "na";
                let flightPointHash = blake2.createHash("blake2b", {digestLength: 16})
                    .update(Buffer.from(aircraft.icao + (flightPoint.timestamp).toString() + positionForHash + altitudeForHash))
                    .digest("hex");
                // Update the flight point's hash.
                flightPoint.flightPointHash = flightPointHash;
            }
            // Now that we've updated all flight points to have both a parent aircraft and a flight point hash, we will save them all to the database.
            await Promise.allSettled(this.flightPoints.map(flightPoint => flightPoint.save()));
            return aircraft;
        }
    };
}

/*
Builds an array of FlightPoint database models from the given FlightPoint. A FlightPoint hash will also be generated from the key data that uniquely identifies a single flight point;
that is, Aircraft ICAO and flight point timestamp. These are unique together as a single aircraft can ONLY be in ONE place at a particular timestamp. A BuiltFlightPoints result will be returned
that will allow async code to save the built points to the database, along with an association to a particular aircraft.

Arguments
---------
:flightPoint: A FlightPoint objects procured by the data module.

Returns
-------
A BuiltFlightPoints object, that will allow async code to associate these points with an aircraft.
*/
function buildFlightPointModel(flightPoint) {
    return buildFlightPointsModels([flightPoint]);
}

/*
Given an AircraftState object, find or create an aircraft and return it.

Arguments
---------
:aircraftState: An AircraftState object.

Returns
-------
An array; the first item is the Aircraft model, the second is a boolean indicating whether or not the Aircraft had to be created.
*/
async function createAircraft(aircraftState) {
    // Locates existing aircraft, if there is one.
    let existingAircraft = await sequelize.models.Aircraft.findOne({
        where: { icao: aircraftState.icao }
    });
    if(existingAircraft === null) {
        // If the aircraft doesn't exist, we'll need to create it.
        let aircraftModel = buildAircraftModel(aircraftState);
        await aircraftModel.save();
        return [aircraftModel, true];
    }
    // Found this aircraft existing.
    return [existingAircraft, false];
}

/*
*/
async function getAircraftDaysWithNumPoints(aircraftIcao) {
    return await sequelize.models.FlightPoint.findAll({
        attributes: {
            include: [
                [
                    sequelize.literal(`(
                        SELECT COUNT(fp.flightPointHash)
                        FROM FlightPoints AS fp
                        WHERE fp.date = FlightPoint.date
                    )`),
                    "numFlightPoints"
                ]
            ]
        },
        where: {
            AircraftIcao: aircraftIcao
        },
        group: "date"
    });
}

/*
*/
async function getAllAircraft() {
    return await sequelize.models.Aircraft.findAll();
}

/*
*/
async function getUniqueDays() {
    const allUniqueDays = await sequelize.models.FlightPoint.findAll({
        attributes: ["date"],
        group: "date"
    });
    return allUniqueDays;
}

/*
Given an aircraft's ICAO, return all points logged by that aircraft where the point has not yet been synchronised. This function will
return the aircraft model with all points nested. The flight points will be ordered in ascending order by their timestamp.

Arguments
---------
:aircraftIcao: The ICAO for which we should query unsynchronised points.

Returns
-------
The target aircraft model, with points nested.
*/
async function getUnsynchronisedPointsFor(aircraftIcao) {
    const aircraftWithPoints = await sequelize.models.Aircraft.findOne({
        where: { icao: aircraftIcao },
        include: {
            model: sequelize.models.FlightPoint,
            required: false,
            where: { synchronised: false }
        },
        order: [
            [sequelize.models.FlightPoint, "timestamp", "ASC"]
        ]
    });
    return aircraftWithPoints;
}

/*
Given an aircraft's ICAO, return points logged by that aircraft where the point has not yet been synchronised and where it
has occurred on the given day.. This function will return the aircraft model with all points nested. The flight points will
be ordered in ascending order by their timestamp.

Arguments
---------
:aircraftIcao: The ICAO for which we should query unsynchronised points.
:isoDay: The day, in ISO format, from which to search for points logged by the aircraft.

Returns
-------
The target aircraft model, with points nested.
*/
async function getUnsynchronisedPointsFromDayFor(aircraftIcao, isoDay) {
    const aircraftWithPoints = await sequelize.models.Aircraft.findOne({
        where: { icao: aircraftIcao },
        include: {
            model: sequelize.models.FlightPoint,
            required: false,
            where: {
                synchronised: false,
                date: isoDay
            }
        },
        order: [
            [sequelize.models.FlightPoint, "timestamp", "ASC"]
        ]
    });
    return aircraftWithPoints;
}

/*
Acknowledge processing of all flight points received back from the server. This will set the synchronised value for each located in the array to 'true'.

Arguments
---------
:flightPointReceiptsArray: An array of FlightPointReceipt objects.
*/
async function updateFlightPointReceipts(flightPointReceiptsArray) {
    // Now, iterate the flight point receipts array, and update each FlightPoint model referred to by the aircaft icao
    // and flight point hash; set synchronised to true. If the flight point can't be found, take no action.
    await Promise.allSettled(
        flightPointReceiptsArray.map(flightPointReceipt => sequelize.models.FlightPoint.update({ synchronised: true }, {
            where: { [Op.and]: [{AircraftIcao: flightPointReceipt.AircraftIcao}, {flightPointHash: flightPointReceipt.flightPointHash}] }
        }))
    );
    return flightPointReceiptsArray;
}

/*
Create a new memory based Sequelize database, and init our schemas on it.
If it exists, somehow, the entire database will be torn down and rebuilt.

Returns
-------
The Sequelize instance.
*/
async function createDatabase() {
    com.logger.debug(`Creating database connection in environment '${conf.NODE_ENV}'`);
    // Setup sequelize instance now. Only special case is if we're in the test environment, we need to load an sqlite memory database.
    if(conf.NODE_ENV === "test") {
        sequelize = new Sequelize({
            dialect: "sqlite",
            storage: ":memory:"
        });
    } else {
        console.log(conf.DATABASE_URI);
        sequelize = new Sequelize(conf.DATABASE_URI);
    }
    try {
        // Ensure we can successfully authenticate this connection.
        await sequelize.authenticate();
        com.logger.debug(`Database connection successfully authenticated!`);
    } catch(err) {
        com.logger.error(`Could not authenticate database connection:`, err);
    }
    // Initialise schema.
    await schema.initSchemas(sequelize);
    if(conf.NODE_END !== "production") {
        // If not in production, we will always force sync these tables.
        await sequelize.sync({
            force: true
        });
    } else {
        // Otherwise, just sync.
        await sequelize.sync();
    }
    return sequelize;
}

/* Close this database instance. */
async function closeDatabase() {
    if(sequelize) {
        com.logger.debug("Closing database...");
        return await sequelize.close();
    }
}

exports.newFlightPointModel = newFlightPointModel;
exports.buildAircraftModel = buildAircraftModel;
exports.buildFlightPointsModels = buildFlightPointsModels;
exports.buildFlightPointModel = buildFlightPointModel;

exports.createAircraft = createAircraft;
exports.getAllAircraft = getAllAircraft;
exports.getUniqueDays = getUniqueDays;
exports.getUnsynchronisedPointsFor = getUnsynchronisedPointsFor;
exports.getUnsynchronisedPointsFromDayFor = getUnsynchronisedPointsFromDayFor;
exports.updateFlightPointReceipts = updateFlightPointReceipts;

exports.getAircraftDaysWithNumPoints = getAircraftDaysWithNumPoints;

exports.createDatabase = createDatabase;
exports.closeDatabase = closeDatabase;
