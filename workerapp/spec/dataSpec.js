const path = require("path");

const com = require("../com");
const data = require("../data");
const wqi = require("../adsbexchange/wqi");


describe("decodeBinaryUpdate", () => {
    var binary1 = null;

    beforeAll(async () => {
        binary1 = await com.readFileAsBinary(path.join("spec", "testdata", "binary"), "1648433758135.bin");
    });

    it("should correctly decompress, then decode a binary update using ZSTD (new default)", async () => {
        // Read from a ZSTD compressed example; we will need to read as UTF8 instead of as binary.
        let zstdCompressedBinary = await com.readFileAsUtf8(path.join("spec", "testdata", "binary_zstd"), "pol35-02.zstd");
        // Create a Buffer from the base64 string given here.
        let compressedBuffer = new Buffer.from(zstdCompressedBinary, "base64");
        // Now, generate a binary update.
        let decodedBinaryUpdate = await data.decodeBinaryUpdate(compressedBuffer);
        expect(decodedBinaryUpdate.now).toBeCloseTo(1663925164.77, 1);
        expect(decodedBinaryUpdate.aircraft.length).toEqual(24);
    });

    it("should correctly decompress, then decode a binary update using ZSTD (new default)", async () => {
        // Read from a ZSTD compressed example; we will need to read as UTF8 instead of as binary.
        let zstdCompressedBinary = await com.readFileAsUtf8(path.join("spec", "testdata", "binary_zstd"), "1658842607.zstd");
        // Create a Buffer from the base64 string given here.
        let compressedBuffer = new Buffer.from(zstdCompressedBinary, "base64");
        // Now, generate a binary update.
        let decodedBinaryUpdate = await data.decodeBinaryUpdate(compressedBuffer);
        expect(decodedBinaryUpdate.now).toBeCloseTo(1658842607.91, 1);
        expect(decodedBinaryUpdate.aircraft.length).toEqual(11);
    });

    it("should correctly find and read a comprehensive version of POL31 from the given binary update", async () => {
        // Decode the first binary update.
        let binaryUpdate = await data.decodeBinaryUpdate(binary1);
        // Get the AircraftObject for POL31.
        let pol31AircraftObject = await binaryUpdate.findAircraftObjectByIcao("7c4ef4");
        // Convert it to an AircraftState.
        let pol31 = await data.aircraftStateFromBinary(pol31AircraftObject);
        expect(pol31.comprehensive).toBe(true);
        expect(pol31.icao).toBe("7c4ef4");
        expect(pol31.registration).toBe("VH-PVQ");
        expect(pol31.type).toBe("A139");
        expect(pol31.flightName).toBe("POL31");
    });

    it("should correctly find and read a flight point from the first binary update for POL31", async () => {
        // Decode the first binary update.
        let binaryUpdate = await data.decodeBinaryUpdate(binary1);
        // Get the AircraftObject for POL31.
        let pol31AircraftObject = await binaryUpdate.findAircraftObjectByIcao("7c4ef4");
        let flightPoint = await data.flightPointFromBinary(binaryUpdate.now, pol31AircraftObject);
        expect(flightPoint.latitude).toBe(-37.618314);
        expect(flightPoint.longitude).toBe(145.539289);
        expect(flightPoint.altitude).toBe(2450);
        expect(flightPoint.verticalRate).toBe(-288);

        expect(flightPoint.isOnGround).toBe(false);
        expect(flightPoint.isAscending).toBe(false);
        expect(flightPoint.isDescending).toBe(true);
    });
});

describe("trace", () => {
    /*
    Full trace 28 supplement & recent contain a FULL all-day voyage for POL31 from start to finish.
    */
    var fullTrace28Supplement = null;
    var recentTrace28Supplement = null;

    var fullTrace28 = null;
    var recentTrace28 = null;
    var fullTrace27 = null;
    var fullTrace25 = null;

    beforeEach(async () => {
        // Read FULL and RECENT for 28 March, supplemented.
        fullTrace28Supplement = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "trace"), "trace_full_7c4ef4_28032022_supplement.json"));
        recentTrace28Supplement = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "trace"), "trace_recent_7c4ef4_28032022_supplement.json"));

        // Read FULL and RECENT trace for test aircraft on 28 March.
        fullTrace28 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "trace"), "trace_full_7c4ef4_28032022.json"));
        recentTrace28 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "trace"), "trace_recent_7c4ef4_28032022.json"));
        // Read FULL trace for test aircraft on 27 March.
        fullTrace27 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "trace"), "trace_full_7c4ef4_27032022.json"));
        // 26 March, POL31 had 0 flights.
        // Read FULL trace for test aircraft on 25 March.
        fullTrace25 = JSON.parse(await com.readFileAsUtf8(path.join("spec", "testdata", "trace"), "trace_full_7c4ef4_25032022.json"));
    });

    it("flightPointsFromTrace should extract a list of FlightPoint from a trace.", async () => {
        let mergedTrace = await data.mergeAllTraces(fullTrace28Supplement, recentTrace28Supplement);
        let flightPoints = await data.flightPointsFromTrace(mergedTrace);
        // TODO: flightPointsFromTrace
    });

    it("should correctly read all traces, and merge into a single trace", async () => {
        let mergedTrace = await data.mergeAllTraces(fullTrace28, recentTrace28, fullTrace27, fullTrace25);
        // Expect the oldest timestamp to be...
        expect(mergedTrace.timestamp).toBe(1648166403.941);
        // Expect the newest timestamp to be...
        let newestTracePoint = mergedTrace.trace[ mergedTrace.trace.length - 1 ];
        let newestPointTimestamp = newestTracePoint [0] + mergedTrace.timestamp;
        expect(newestPointTimestamp).toBeCloseTo(1648433791.27, 2);
    });

    it("should correctly read a full trace and a null recent trace", async () => {
        let mergedTrace = await data.mergeAllTraces(fullTrace28, null);
        // Expect the oldest timestamp to be...
        expect(mergedTrace.timestamp).toBeCloseTo(1648420051.70, 1);
    });

    it("should correctly extract the POL31 aircraft (comprehensively) from full 28 trace", async () => {
        let pol31 = await data.aircraftStateFromTrace(fullTrace28);
        expect(pol31.comprehensive).toBe(true);
        expect(pol31.icao).toBe("7c4ef4");
        expect(pol31.registration).toBe("VH-PVQ");
        expect(pol31.type).toBe("A139");

        expect(pol31.flightName).toBe("POL31");
        expect(pol31.ownerOperator).toBe("STARFLIGHT VICTORIA PTY LTD");
        expect(pol31.description).toBe("AGUSTA AW-139");
        expect(pol31.year).toBe(2019);
    });
});
