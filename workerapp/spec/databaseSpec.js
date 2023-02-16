const { Sequelize, Model, DataTypes } = require("sequelize");
const conf = require("../conf");
const database = require("../database");
const data = require("../data");
const com = require("../com");
const path = require("path");


describe("updateFlightPointReceipts", () => {
    beforeAll(async () => {
        conf.NODE_ENV = "test";
    });

    beforeEach(async () => {
        originalTimeout = jasmine.DEFAULT_TIMEOUT_INTERVAL;
        jasmine.DEFAULT_TIMEOUT_INTERVAL = 30000;

        await database.createDatabase();
    });

    afterEach(async () => {
        jasmine.DEFAULT_TIMEOUT_INTERVAL = originalTimeout;

        await database.closeDatabase();
    });

    it("should correctly update all flight points in a receipt array to be synchronised.", async () => {
        let recentTrace31 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "complete", "pol35_live_trace_bin", "trace"), "trace_recent_7c4ee8_31032022.json"));
        let mergedTrace = recentTrace31;

        // Read an aircraft state and create a model from it.
        let aircraftState = await data.aircraftStateFromTrace(mergedTrace);
        let [aircraftModel, created] = await database.createAircraft(aircraftState);
        // Extract all FlightPoints from the merged trace.
        let flightPoints = await data.flightPointsFromTrace(mergedTrace);
        // Now, build flight points.
        let builtFlightPoints = database.buildFlightPointsModels(flightPoints);
        expect(builtFlightPoints.flightPoints.length).toBe(85);
        // Save to this aircraft.
        await builtFlightPoints.saveToAircraft(aircraftModel);
        // Now, we'll get the aircraft by its icao, with all unsynchronised points. Ensure we find 85.
        const targetAircraft = await database.getUnsynchronisedPointsFor(aircraftModel.icao);
        // Ensure is not null.
        expect(targetAircraft).not.toBe(null);
        // Expect icao to match.
        expect(targetAircraft.icao).toBe(aircraftModel.icao);
        // Expect 85 flight points found.
        expect(targetAircraft.FlightPoints.length).toBe(85);
        // Set the first 5 points to synchronised.
        for(var i = 0; i < 5; i++) {
            targetAircraft.FlightPoints[i].synchronised = true;
            await targetAircraft.FlightPoints[i].save();
        }
        // Get this aircraft again with unsynchronised points. This time, we should find 80.
        const targetAircraftA = await database.getUnsynchronisedPointsFor(aircraftModel.icao);
        expect(targetAircraftA.FlightPoints.length).toBe(80);
    });
});

describe("getAircraftDaysWithNumPoints", () => {
    beforeAll(async () => {
        conf.NODE_ENV = "test";
    });

    beforeEach(async () => {
        originalTimeout = jasmine.DEFAULT_TIMEOUT_INTERVAL;
        jasmine.DEFAULT_TIMEOUT_INTERVAL = 30000;

        await database.createDatabase();
    });

    afterEach(async () => {
        jasmine.DEFAULT_TIMEOUT_INTERVAL = originalTimeout;

        await database.closeDatabase();
    });

    it("should correctly query all unique days from the database.", async () => {
        let recentTrace31 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "complete", "pol35_live_trace_bin", "trace"), "trace_recent_7c4ee8_31032022.json"));
        let fullTrace29 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "trace", "pol35_broken_flight"), "trace_full_7c4ee8_29032022.json"));
        let traces = [recentTrace31, fullTrace29];
        for(let trace of traces) {
            let mergedTrace = trace;
            let aircraftState = await data.aircraftStateFromTrace(mergedTrace);
            let [aircraftModel, created] = await database.createAircraft(aircraftState);
            let flightPoints = await data.flightPointsFromTrace(mergedTrace);
            let builtFlightPoints = database.buildFlightPointsModels(flightPoints);
            await builtFlightPoints.saveToAircraft(aircraftModel);
        }
        // Now, get all unique days from the database.
        let aircraftDaysWithNumPoints = await database.getAircraftDaysWithNumPoints("7c4ee8")
        for(let aircraftDayWithNumPoints of aircraftDaysWithNumPoints) {
            console.log(`7c4ee8 on ${aircraftDayWithNumPoints.date} created ${aircraftDayWithNumPoints.get("numFlightPoints")} flight points.`);
        }
    });
});

describe("getAllAircraft", () => {
    beforeAll(async () => {
        conf.NODE_ENV = "test";
    });

    beforeEach(async () => {
        originalTimeout = jasmine.DEFAULT_TIMEOUT_INTERVAL;
        jasmine.DEFAULT_TIMEOUT_INTERVAL = 30000;

        await database.createDatabase();
    });

    afterEach(async () => {
        jasmine.DEFAULT_TIMEOUT_INTERVAL = originalTimeout;

        await database.closeDatabase();
    });

    it("should correctly query all unique days from the database.", async () => {
        let recentTrace31 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "complete", "pol35_live_trace_bin", "trace"), "trace_recent_7c4ee8_31032022.json"));
        let fullTrace29 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "trace", "pol35_broken_flight"), "trace_full_7c4ee8_29032022.json"));
        let traces = [recentTrace31, fullTrace29];
        for(let trace of traces) {
            let mergedTrace = trace;
            let aircraftState = await data.aircraftStateFromTrace(mergedTrace);
            let [aircraftModel, created] = await database.createAircraft(aircraftState);
            let flightPoints = await data.flightPointsFromTrace(mergedTrace);
            let builtFlightPoints = database.buildFlightPointsModels(flightPoints);
            await builtFlightPoints.saveToAircraft(aircraftModel);
        }
        // Now, get all unique days from the database.
        let allAircraft = await database.getAllAircraft();
        console.log(allAircraft);
    });
});

describe("getUniqueDays", () => {
    beforeAll(async () => {
        conf.NODE_ENV = "test";
    });

    beforeEach(async () => {
        originalTimeout = jasmine.DEFAULT_TIMEOUT_INTERVAL;
        jasmine.DEFAULT_TIMEOUT_INTERVAL = 30000;

        await database.createDatabase();
    });

    afterEach(async () => {
        jasmine.DEFAULT_TIMEOUT_INTERVAL = originalTimeout;

        await database.closeDatabase();
    });

    it("should correctly query all unique days from the database.", async () => {
        let recentTrace31 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "complete", "pol35_live_trace_bin", "trace"), "trace_recent_7c4ee8_31032022.json"));
        let fullTrace29 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "trace", "pol35_broken_flight"), "trace_full_7c4ee8_29032022.json"));
        let traces = [recentTrace31, fullTrace29];
        for(let trace of traces) {
            let mergedTrace = trace;
            let aircraftState = await data.aircraftStateFromTrace(mergedTrace);
            let [aircraftModel, created] = await database.createAircraft(aircraftState);
            let flightPoints = await data.flightPointsFromTrace(mergedTrace);
            let builtFlightPoints = database.buildFlightPointsModels(flightPoints);
            await builtFlightPoints.saveToAircraft(aircraftModel);
        }
        // Now, get all unique days from the database.
        let allUniqueDays = await database.getUniqueDays()
            .then((flightPointsArray) => flightPointsArray.map(flightPoint => flightPoint.date));
        expect(allUniqueDays.length).toBe(3);
    });
});

describe("getUnsynchronisedPointsFromDayFor", () => {
    beforeAll(async () => {
        conf.NODE_ENV = "test";
    });

    beforeEach(async () => {
        originalTimeout = jasmine.DEFAULT_TIMEOUT_INTERVAL;
        jasmine.DEFAULT_TIMEOUT_INTERVAL = 30000;

        await database.createDatabase();
    });

    afterEach(async () => {
        jasmine.DEFAULT_TIMEOUT_INTERVAL = originalTimeout;

        await database.closeDatabase();
    });

    it("should correctly query the target aircraft, along with all its unsynchronised points; from a specific ISO format day..", async () => {
        let recentTrace31 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "complete", "pol35_live_trace_bin", "trace"), "trace_recent_7c4ee8_31032022.json"));
        let mergedTrace = recentTrace31;

        // Read an aircraft state and create a model from it.
        let aircraftState = await data.aircraftStateFromTrace(mergedTrace);
        let [aircraftModel, created] = await database.createAircraft(aircraftState);
        // Extract all FlightPoints from the merged trace.
        let flightPoints = await data.flightPointsFromTrace(mergedTrace);
        // Now, build flight points.
        let builtFlightPoints = database.buildFlightPointsModels(flightPoints);
        expect(builtFlightPoints.flightPoints.length).toBe(85);
        // Save to this aircraft.
        await builtFlightPoints.saveToAircraft(aircraftModel);
        // Now, we'll get the aircraft by its icao, with all unsynchronised points from day 2022-03-31. Ensure we find 85.
        const targetAircraft = await database.getUnsynchronisedPointsFromDayFor(aircraftModel.icao, "2022-03-31");
        expect(targetAircraft.FlightPoints.length).toBe(85);

        // Ensure that the first point's timestamp is less than the last point's timestamp.
        expect(targetAircraft.FlightPoints[0].timestamp).toBeLessThan(targetAircraft.FlightPoints[targetAircraft.FlightPoints.length-1].timestamp);
    });
});

describe("getUnsynchronisedPointsFor", () => {
    beforeAll(async () => {
        conf.NODE_ENV = "test";
    });

    beforeEach(async () => {
        originalTimeout = jasmine.DEFAULT_TIMEOUT_INTERVAL;
        jasmine.DEFAULT_TIMEOUT_INTERVAL = 30000;

        await database.createDatabase();
    });

    afterEach(async () => {
        jasmine.DEFAULT_TIMEOUT_INTERVAL = originalTimeout;

        await database.closeDatabase();
    });

    it("should correctly query the target aircraft, and an empty list for flight points, if none present", async() => {
        let recentTrace31 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "complete", "pol35_live_trace_bin", "trace"), "trace_recent_7c4ee8_31032022.json"));
        let mergedTrace = recentTrace31;

        // Read an aircraft state and create a model from it.
        let aircraftState = await data.aircraftStateFromTrace(mergedTrace);
        let [aircraftModel, created] = await database.createAircraft(aircraftState);
        // Get all unsynchronised points for this aircraftModel.
        let aircraftWithNoPoints = await database.getUnsynchronisedPointsFor(aircraftModel.icao);
        // Ensure this is not null.
        expect(aircraftWithNoPoints).not.toEqual(null);
        // Ensure has icao.
        expect(aircraftWithNoPoints.icao).toBe("7c4ee8");
    });

    it("should correctly query the target aircraft, along with all its unsynchronised points.", async () => {
        let recentTrace31 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "complete", "pol35_live_trace_bin", "trace"), "trace_recent_7c4ee8_31032022.json"));
        let mergedTrace = recentTrace31;

        // Read an aircraft state and create a model from it.
        let aircraftState = await data.aircraftStateFromTrace(mergedTrace);
        let [aircraftModel, created] = await database.createAircraft(aircraftState);
        // Extract all FlightPoints from the merged trace.
        let flightPoints = await data.flightPointsFromTrace(mergedTrace);
        // Now, build flight points.
        let builtFlightPoints = database.buildFlightPointsModels(flightPoints);
        expect(builtFlightPoints.flightPoints.length).toBe(85);
        // Save to this aircraft.
        await builtFlightPoints.saveToAircraft(aircraftModel);
        // Now, we'll get the aircraft by its icao, with all unsynchronised points. Ensure we find 85.
        const targetAircraft = await database.getUnsynchronisedPointsFor(aircraftModel.icao);
        expect(targetAircraft.FlightPoints.length).toBe(85);
        // For 5 of these flight points, create a receipt setting each to synchronised.
        let flightPointReceiptsArray = [];
        for(var i = 0; i < 5; i++) {
            let flightPoint = targetAircraft.FlightPoints[i];
            flightPointReceiptsArray.push({
                AircraftIcao: flightPoint.AircraftIcao,
                flightPointHash: flightPoint.flightPointHash,
                synchronised: true
            });
        }
        // Now, update the points referred to by each receipt.
        await database.updateFlightPointReceipts(flightPointReceiptsArray);

        // Get this aircraft again with unsynchronised points. This time, we should find 80.
        const targetAircraftA = await database.getUnsynchronisedPointsFor(aircraftModel.icao);
        expect(targetAircraftA.FlightPoints.length).toBe(80);

        // Ensure that the first point's timestamp is less than the last point's timestamp.
        expect(targetAircraft.FlightPoints[0].timestamp).toBeLessThan(targetAircraft.FlightPoints[targetAircraft.FlightPoints.length-1].timestamp);
    });
});

describe("buildFlightPointsModels", () => {
    beforeAll(async () => {
        conf.NODE_ENV = "test";
    });

    beforeEach(async () => {
        originalTimeout = jasmine.DEFAULT_TIMEOUT_INTERVAL;
        jasmine.DEFAULT_TIMEOUT_INTERVAL = 30000;

        await database.createDatabase();
    });

    afterEach(async () => {
        jasmine.DEFAULT_TIMEOUT_INTERVAL = originalTimeout;

        await database.closeDatabase();
    });

    it("should correctly build a full list of flight points and commit it to the database, under the given aircraft.", async () => {
        let fullTrace31 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "complete", "pol35_live_trace_bin", "trace"), "trace_full_7c4ee8_31032022.json"));
        let recentTrace31 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "complete", "pol35_live_trace_bin", "trace"), "trace_recent_7c4ee8_31032022.json"));
        let mergedTrace = await data.mergeAllTraces(fullTrace31, recentTrace31);

        // Read an aircraft state and create a model from it.
        let aircraftState = await data.aircraftStateFromTrace(mergedTrace);
        let [aircraftModel, created] = await database.createAircraft(aircraftState);
        // Extract all FlightPoints from the merged trace.
        let flightPoints = await data.flightPointsFromTrace(mergedTrace);
        // Now, build flight points.
        let builtFlightPoints = database.buildFlightPointsModels(flightPoints);
        expect(builtFlightPoints.flightPoints.length).toBe(2443);
        // Save to this aircraft.
        await builtFlightPoints.saveToAircraft(aircraftModel);
        // Now, when we count the number of flight points associated with aircraftModel, we should get 2443 again.
        let flightPointCount = await aircraftModel.countFlightPoints();
        expect(flightPointCount).toBe(2443);
    });
});

describe("newFlightPointModel", () => {
    beforeAll(async () => {
        conf.NODE_ENV = "test";
    });

    beforeEach(async () => {
        await database.createDatabase();
    });

    afterEach(async () => {
        await database.closeDatabase();
    });

    it("should correctly build a FlightPoint object for the database, from trace", async () => {
        let fullTrace29 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "trace", "pol35_broken_flight"), "trace_full_7c4ee8_29032022.json"));
        let recentTrace29 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "trace", "pol35_broken_flight"), "trace_recent_7c4ee8_29032022.json"));
        let mergedTrace = await data.mergeAllTraces(fullTrace29, recentTrace29);
        let flightPoints = await data.flightPointsFromTrace(mergedTrace);

        let flightPointModel = database.newFlightPointModel(flightPoints[0]);
        expect(flightPointModel.latitude).toBe(-37.727363);
        expect(flightPointModel.longitude).toBe(145.037618);
        expect(flightPointModel.altitude).toBe(2125);
        expect(flightPointModel.verticalRate).toBe(-864);

        expect(flightPointModel.isOnGround).toBe(false);
        expect(flightPointModel.isAscending).toBe(false);
        expect(flightPointModel.isDescending).toBe(true);
    });

    it("should correctly build a FlightPoint object for the database, from binary", async () => {
        let binary1 = await com.readFileAsBinary(path.join("spec", "testdata", "binary"), "1648433758135.bin");
        // Decode the first binary update.
        let binaryUpdate = await data.decodeBinaryUpdate(binary1);
        let pol31AircraftObject = await binaryUpdate.findAircraftObjectByIcao("7c4ef4");
        let flightPoint = await data.flightPointFromBinary(binaryUpdate.now, pol31AircraftObject);

        // Now, ensure we can build a FlightPointModel.
        let flightPointModel = database.newFlightPointModel(flightPoint);
        expect(flightPointModel.latitude).toBe(-37.618314);
        expect(flightPointModel.longitude).toBe(145.539289);
        expect(flightPointModel.altitude).toBe(2450);
        expect(flightPointModel.verticalRate).toBe(-288);

        expect(flightPointModel.isOnGround).toBe(false);
        expect(flightPointModel.isAscending).toBe(false);
        expect(flightPointModel.isDescending).toBe(true);
    });
});

describe("createAircraft", () => {
    beforeAll(async () => {
        conf.NODE_ENV = "test";
    });

    beforeEach(async () => {
        await database.createDatabase();
    });

    afterEach(async () => {
        await database.closeDatabase();
    });

    it("should correctly create an Aircraft database model from trace", async () => {
        let fullTrace31 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "complete", "pol35_live_trace_bin", "trace"), "trace_full_7c4ee8_31032022.json"));
        let recentTrace31 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "complete", "pol35_live_trace_bin", "trace"), "trace_recent_7c4ee8_31032022.json"));
        let mergedTrace = await data.mergeAllTraces(fullTrace31, recentTrace31);

        // Get a comprehensive aircraft state from this trace.
        let aircraftState = await data.aircraftStateFromTrace(mergedTrace);
        // Now, create an aircraft model.
        let [aircraftModel, created] = await database.createAircraft(aircraftState);
        expect(aircraftModel.flightName).toBe("POL35");
        expect(aircraftModel.icao).toBe("7c4ee8");
        // Now, create the same model again; the same instance should be returned.
        let [recreateAircraftModelAttempt, recreated] = await database.createAircraft(aircraftState);
        expect(recreateAircraftModelAttempt.icao).toBe(aircraftModel.icao);
    });

    it("should correctly create an aircraft from binary", async () => {
        let binary1 = await com.readFileAsBinary(path.join("spec", "testdata", "binary"), "1648433758135.bin");
        // Decode the first binary update.
        let binaryUpdate = await data.decodeBinaryUpdate(binary1);
        // Get the AircraftObject for POL31.
        let pol31AircraftObject = await binaryUpdate.findAircraftObjectByIcao("7c4ef4");
        // Convert it to comprehensive AircraftState.
        let pol31AircraftStateBinary = await data.aircraftStateFromBinary(pol31AircraftObject);
        // Now, create an aircraft from this state.
        let [aircraftModel, created] = await database.createAircraft(pol31AircraftStateBinary);
        // Ensure this was successful.
        expect(aircraftModel.icao).toBe(aircraftModel.icao);
    });

    it("should correctly create an aircraft from a tracked vehicle object, but it must be incomprehensive.", async () => {
        // Use data to create an aircraft state from a tracked vehicle.
        let trackedVehicle = {
            icao: "7c4ee8",
            name: "POL35",
            airportCode: "e8"
        };
        let aircraftState = await data.aircraftStateFromTargetedVehicle(trackedVehicle);
        // Now, ensure that we can create a new aircraft model from this state.
        let [aircraftModel, created] = await database.createAircraft(aircraftState);
        // Ensure aircraft model is incomprehensive.
        expect(aircraftModel.comprehensive).toBe(false);
    });
});

describe("buildAircraftModel", () => {
    beforeAll(async () => {
        conf.NODE_ENV = "test";
    });

    beforeEach(async () => {
        await database.createDatabase();
    });

    afterEach(async () => {
        await database.closeDatabase();
    });

    it("should correctly build an Aircraft object for the database from binary", async () => {
        let binary1 = await com.readFileAsBinary(path.join("spec", "testdata", "binary"), "1648433758135.bin");
        // Decode the first binary update.
        let binaryUpdate = await data.decodeBinaryUpdate(binary1);
        // Get the AircraftObject for POL31.
        let pol31AircraftObject = await binaryUpdate.findAircraftObjectByIcao("7c4ef4");
        // Convert it to an AircraftState.
        let pol31AircraftStateBinary = await data.aircraftStateFromBinary(pol31AircraftObject);
        // Now create an Aircraft database row from this.
        let pol31FromBinary = database.buildAircraftModel(pol31AircraftStateBinary);
        expect(pol31FromBinary.comprehensive).toBe(true);
        expect(pol31FromBinary.icao).toBe("7c4ef4");
        expect(pol31FromBinary.registration).toBe("VH-PVQ");
        expect(pol31FromBinary.type).toBe("A139");
        expect(pol31FromBinary.flightName).toBe("POL31");
    });

    it("should correctly build an Aircraft object for the database from trace", async () => {
        // Now from trace.
        let fullTrace28Supplement = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "trace"), "trace_full_7c4ef4_28032022_supplement.json"));
        let pol31AircraftStateTrace = await data.aircraftStateFromTrace(fullTrace28Supplement);
        // Create an Aircraft database row from this.
        let pol31FromTrace = database.buildAircraftModel(pol31AircraftStateTrace);
        expect(pol31FromTrace.comprehensive).toBe(true);
        expect(pol31FromTrace.icao).toBe("7c4ef4");
        expect(pol31FromTrace.registration).toBe("VH-PVQ");
        expect(pol31FromTrace.type).toBe("A139");

        expect(pol31FromTrace.flightName).toBe("POL31");
        expect(pol31FromTrace.ownerOperator).toBe("STARFLIGHT VICTORIA PTY LTD");
        expect(pol31FromTrace.description).toBe("AGUSTA AW-139");
        expect(pol31FromTrace.year).toBe(2019);
    });
});
