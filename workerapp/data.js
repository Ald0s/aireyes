const wqi = require("./adsbexchange/wqi.js");
const { ZSTDDecoder } = require("./adsbexchange/zstddec.js");

const com = require("./com.js");
const error = require("./error.js");

var zstdDecoder = null;


/*
Decode the given base64 string and return the result; this is a startup requirement object from the master.
*/
async function parseStartupData(base64Data) {

}

/*
Given an AircraftObject item from the 'aircraft' array within a decoded binary update, extract all information about that
aircraft and return an AircraftState object.

Arguments:

Returns:

*/
async function aircraftStateFromBinary(aircraftObject) {
    return {
        icao: aircraftObject.hex.trim(),
        registration: aircraftObject.r.trim(),
        type: aircraftObject.t.trim(),
        flightName: aircraftObject.flight.trim(),
        year: undefined,
        description: undefined,
        ownerOperator: undefined,
        comprehensive: true
    };
}

/*
Given a Trace object, extract all information about that aircraft and return an AircraftState object.

Arguments:

Returns:

*/
async function aircraftStateFromTrace(trace) {
    // Find a 'flight' value from all traceObjects (at data, index 8) points within trace.
    // We'll do this by first searching for a valid traceObject, this is one that is firstly not null or undefined, and secondly has the 'flight' key.
    let validTraceObject = trace.trace.find(traceObject => (traceObject [8] !== null && traceObject [8] !== undefined) && "flight" in traceObject [8]);
    let flightName = (validTraceObject !== undefined) ? validTraceObject[8].flight.trim() : undefined;
    return {
        icao: trace.icao,
        registration: trace.r,
        type: trace.t,
        flightName: flightName,
        year: parseInt(trace.year),
        description: trace.desc,
        ownerOperator: trace.ownOp,
        comprehensive: true
    };
}

async function aircraftStateFromTargetedVehicle(targetedVehicle) {
    return {
        icao: targetedVehicle.icao,
        flightName: targetedVehicle.name,
        comprehensive: false
    };
}

/*
Given a decoded binary object, augment the return value
with convenience methods to better locate specific aircraft and details.

Arguments:
:binaryUpdateObject: An object, freshly decoded, representing a binary update.

Returns:
A DecodedBinaryUpdate object, which contains the binary update data and augmented with several functions
for locating aircraft update data. All aircraft data returned will be data layer form; AircraftObject.
*/
async function decodedBinaryUpdateFromObject(binaryUpdateObject) {
    binaryUpdateObject.findAircraftObjectByIcao = function(icao) {
        for(let aircraft of this.aircraft) {
            if(aircraft.hex.toLowerCase().trim() === icao.toLowerCase().trim()) {
                return aircraft;
            }
        }
        return null;
    };

    binaryUpdateObject.findAircraftObjectByRego = function(registration) {
        for(let aircraft of this.aircraft) {
            if(aircraft.r.toLowerCase().trim() === registration.toLowerCase().trim()) {
                return aircraft;
            }
        }
        return null;
    };
    return binaryUpdateObject;
}

/*
This function will first decompress the buffer with ZSTD, then it will use wqi to decode to an object,
and will then augment the return value with convenience methods to better locate specific aircraft and details.

Arguments
---------
:buffer: A Uint8Array buffer to decode as a single binary update.

Returns:
A DecodedBinaryUpdate object, which contains the binary update data and augmented with several functions
for locating aircraft update data. All aircraft data returned will be data layer form; AircraftObject.
*/
async function decodeBinaryUpdate(buffer) {
    // If our decoder is not initialised yet, perform that now.
    if(zstdDecoder === null) {
        zstdDecoder = new ZSTDDecoder();
    }
    await zstdDecoder.init();
    // Attempt to decompress the buffer.
    let decompressedBuffer;
    try {
        decompressedBuffer = zstdDecoder.decode(buffer, 0);
    } catch(err) {
        if(err instanceof error.ZSTDDecompressionFailed) {
            switch(err.error_code) {
                case "find-decompressed-size":
                    // Probably not a ZSTD compressed buffer.
                    decompressedBuffer = buffer;
                    break;

                default:
                    com.logger.warn(`Failed to decode binary update, ZSTD decompression failed! We'll try just decoding the data. Reason: ${err.reason}, error code: ${err.error_code}`);
                    decompressedBuffer = buffer;
                    break;
            }
        } else {
            com.logger.debug(`Failed to decode binary update! Error: ${err}`);
            throw err;
        }
    }
    // Now, retrieve the inner buffer, create a data result from it and decode the raw binary array into our resulting object.
    let resultData = { buffer: decompressedBuffer.buffer };
    try {
        // Attempt to decode this result data, which may or may not be valid.
        wqi.wqiDecode(resultData);
    } catch(err) {
        if(err instanceof TypeError) {
            // Failed to decode the buffer. Decompressing a non-ZSTD buffer will simply use the original buffer anyway, but this could also be thrown
            // if a legitimately ZSTD compressed buffer could not be wqi-decoded.
            com.logger.error(`Failed to decode binary update; wqiDecode threw a TypeError.`);
            /*TODO: write this buffer as a physical error log for review.*/
            return null;
        } else {
            com.logger.error(`Failed to decode binary update. Error: ${err}`);
            return null;
        }
    }
    // Now, augment the result data object with a few functions.
    return await decodedBinaryUpdateFromObject(resultData);
}

/*
Given an AircraftTrace object, turn the trace array into a FlightPoint array.

Arguments:
:aircraftTrace: An AircraftTrace object.

Returns:
An array of FlightPoint objects, representing each point in the 'trace' array.
*/
async function flightPointsFromTrace(aircraftTrace) {
    let flightPoints = [];
    for(let traceObject of aircraftTrace.trace) {
        let absoluteTimestamp = traceObject[0] + aircraftTrace.timestamp;
        let altitude = (traceObject[3] !== null) ? (traceObject[3] === "ground") ? 0 : parseInt(traceObject[3]) : null;
        let altitudeRate = (traceObject[7] !== null) ? parseInt(traceObject[7]) : null;
        let dataSource = traceObject[9] || null;

        let point = {
            timestamp: absoluteTimestamp,
            timestampMillis: absoluteTimestamp * 1000,
            latitude: parseFloat(traceObject[1]),
            longitude: parseFloat(traceObject[2]),
            altitude: altitude,
            groundSpeed: traceObject[4],
            rotation: traceObject[5],
            verticalRate: altitudeRate,
            dataSource: dataSource,

            time: com.formatTime(absoluteTimestamp),
            position: [traceObject[1], traceObject[2]],
            isAscending: (altitudeRate !== null) ? altitudeRate > 0 : false,
            isDescending: (altitudeRate !== null) ? altitudeRate < 0 : false,
            isOnGround: traceObject[3] === "ground"
        };
        flightPoints.push(point);
    }
    return flightPoints;
}

/*
Given a update timestamp and a BinaryAircraftObject, taken from the 'aircraft' array in a binary update,
assemble a FlightPoint for the point.

Arguments:
:updateAt: A timestamp, should be the value of the 'now' key in a binary update.
:binaryAircraftObject: The object at a particular index within the 'aircraft' array, in a binary update.

Returns:
A FlightPoint object.
*/
async function flightPointFromBinary(updateAt, binaryAircraftObject) {
    let altitude = (binaryAircraftObject["alt_baro"] !== undefined) ? (binaryAircraftObject["alt_baro"] === "ground") ? 0 : parseInt(binaryAircraftObject["alt_baro"]) : null;
    let altitudeRate = (binaryAircraftObject["baro_rate"] !== undefined) ? parseInt(binaryAircraftObject["baro_rate"]) : null;
    let groundSpeed = (binaryAircraftObject["gs"] !== undefined) ? parseFloat(binaryAircraftObject["gs"]) : null;
    let rotation = (binaryAircraftObject["track"] !== undefined) ? parseFloat(binaryAircraftObject["track"]) : null;
    let dataSource = binaryAircraftObject.type || null;

    return {
        timestamp: updateAt,
        timestampMillis: updateAt * 1000,
        latitude: parseFloat(binaryAircraftObject ["lat"]),
        longitude: parseFloat(binaryAircraftObject ["lon"]),
        altitude: altitude,
        groundSpeed: groundSpeed,
        rotation: rotation,
        verticalRate: altitudeRate,
        dataSource: dataSource,

        time: com.formatTime(updateAt),
        position: [binaryAircraftObject ["lat"], binaryAircraftObject ["lon"]],
        isAscending: (altitudeRate !== null) ? altitudeRate > 0 : false,
        isDescending: (altitudeRate !== null) ? altitudeRate < 0 : false,
        isOnGround: binaryAircraftObject["alt_baro"] === "ground"
    };
}

/*
Modify the given aircraft trace object, normalising all trace point time offsets by adding
the trace timestamp. This will make each trace point an absolute timestamp for when it occurred.

Arguments:
:aircraftTrace: An AircraftTrace object.

Returns:
The AircraftTrace object passed in.
*/
async function normaliseTimestamps(aircraftTrace) {
    for(var i = 0; i < aircraftTrace.trace.length; i++) {
        aircraftTrace.trace [i] [0] += aircraftTrace.timestamp;
    }
    return aircraftTrace;
}

/*
Modify the given aircraft trace object, reversing normalisation done on all trace points by subtracting
the aircraft trace's timestamp. This will make each trace's absolute timestamp an offset relative to the
aircraft trace's timestamp.

Arguments:
:aircraftTrace: An AircraftTrace object.

Returns:
The AircraftTrace object passed in.
*/
async function relativeTimestamps(aircraftTrace) {
    for(var i = 0; i < aircraftTrace.trace.length; i++) {
        aircraftTrace.trace [i] [0] -= aircraftTrace.timestamp;
    }
    return aircraftTrace;
}

/*
Given a variable number of AircraftTrace objects, they will all be merged into a single result trace.
The first trace given will be used as the reference for all other major data points about the aircraft.

Arguments:
:traces: A variable number of AircraftTrace objects.

Returns:
A new AircraftTrace object, which is a merger of all the given traces.
The return trace has relative trace point timestamps, not absolute.
*/
async function mergeAllTraces(...traces) {
    let referenceTrace = traces [0];
    if(referenceTrace === null || referenceTrace === undefined) {
        throw "Failed to merge traces - trace1 can't be null or undefined.";
    }
    let resultingTrace = {
        icao: referenceTrace.icao,
        r: referenceTrace.r,
        t: referenceTrace.t,
        desc: referenceTrace.desc,
        ownOp: referenceTrace.ownOp,
        year: referenceTrace.year,
        timestamp: referenceTrace.timestamp,
        trace: []
    };
    // Create a dictionary to associate all TraceObjects with an absolute timestamp, across ALL traces.
    let absoluteTimestampDict = {};
    // Now, iterate all traces given.
    for(var aircraftTraceIndex = 0; aircraftTraceIndex < traces.length; aircraftTraceIndex++) {
        let trace = traces[ aircraftTraceIndex ];
        // If trace is null, just continue.
        if(trace === null || trace === undefined) {
            continue;
        }
        // Normalise the timestamps.
        let normalisedTrace = await normaliseTimestamps(trace);
        // Now, iterate each trace point within the normalised trace, and set that TraceObject in absoluteTimestampDict, where it does not already exist.
        for(var traceObjectIdx = 0; traceObjectIdx < normalisedTrace.trace.length; traceObjectIdx++) {
            let traceObject = normalisedTrace.trace [traceObjectIdx];
            let absoluteTimestamp = parseFloat(traceObject [0]);
            if(!(absoluteTimestamp in absoluteTimestampDict)) {
                absoluteTimestampDict [absoluteTimestamp] = traceObject;
            }
        }
    }
    // Now, to assemble our resulting trace, we must determine the oldest timestamp to use as our new trace begin.
    // Sort all keys in absoluteTimestampDict in ascending order.
    let sortedKeys = Object.keys(absoluteTimestampDict).sort();
    // Oldest timestamp is the first key.
    resultingTrace.timestamp = parseFloat(sortedKeys[0]);
    for(let key of sortedKeys) {
        // Get our trace object by key.
        let traceObject = absoluteTimestampDict [key];
        // Push to resulting trace, in order dictated by sortedKeys.
        resultingTrace.trace.push(traceObject);
    }
    // Now, invert normalisation to get relative timestamps once again and return.
    let relativeResultingTrace = await relativeTimestamps(resultingTrace);
    return relativeResultingTrace;
}

/*
*/
async function aircraftWithPointsFromTraces(targetedVehicleTrace) {
    // Begin by merging traces from our targeted vehicle trace object.
    var mergedTrace = null;
    if(targetedVehicleTrace.recentTrace !== null && targetedVehicleTrace.fullTrace === null) {
        // If we have no full trace, but we have a recent trace, use the recent trace as reference.
        com.logger.debug(`Full trace is null, but we have a recent trace...`);
        mergedTrace = await mergeAllTraces(targetedVehicleTrace.recentTrace, targetedVehicleTrace.fullTrace);
    } else if(targetedVehicleTrace.fullTrace !== null && targetedVehicleTrace.recentTrace === null) {
        // If we have no recent trace, but we have a full trace, use the full trace as reference.
        com.logger.debug(`We have a full trace but recent trace is null...`);
        mergedTrace = await mergeAllTraces(targetedVehicleTrace.fullTrace, targetedVehicleTrace.recentTrace);
    } else if(targetedVehicleTrace.fullTrace !== null && targetedVehicleTrace.recentTrace !== null) {
        // If we have both a full trace and a recent trace, use the full trace as reference.
        com.logger.debug(`We have both a full trace and a recent trace for the new vehicle.`);
        mergedTrace = await mergeAllTraces(targetedVehicleTrace.fullTrace, targetedVehicleTrace.recentTrace);
    } else {
        // Otherwise, return an incomprehensive aircraft state.
        com.logger.warn(`Targeted vehicle ${targetedVehicleTrace.targetedVehicle.name} (${targetedVehicleTrace.targetedVehicle.icao}) does not have ANY trace. Returning an incomprehensive state for it.`);
        let incompAircraftState = await aircraftStateFromTargetedVehicle(targetedVehicleTrace.targetedVehicle);
        return [incompAircraftState, null];
    }
    // Continue by creating an AircraftState on the basis of the merged trace.
    let aircraftState = await aircraftStateFromTrace(mergedTrace);
    return [aircraftState, mergedTrace];
}


exports.aircraftWithPointsFromTraces = aircraftWithPointsFromTraces;
exports.flightPointFromBinary = flightPointFromBinary;
exports.flightPointsFromTrace = flightPointsFromTrace;
exports.decodeBinaryUpdate = decodeBinaryUpdate;
exports.decodedBinaryUpdateFromObject = decodedBinaryUpdateFromObject;
exports.aircraftStateFromTrace = aircraftStateFromTrace;
exports.aircraftStateFromBinary = aircraftStateFromBinary;
exports.aircraftStateFromTargetedVehicle = aircraftStateFromTargetedVehicle;
exports.mergeAllTraces = mergeAllTraces;
