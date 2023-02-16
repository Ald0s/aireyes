/*
The module used by slaves of type aircraft-tracker.
*/
const process = require("process");
const conf = require("./conf");
const com = require("./com");
const data = require("./data");
const error = require("./error");
const master = require("./master");
const database = require("./database");

/*
Target vehicle objects. This will be used if no list is sent by the server upon connection, or the tracker is not being run in slave mode.
Whether flying or not, these vehicles will be selected and observed indefinitely. Entries should be in the form of:
{
    icao: "<MODE S ICAO 24 BIT CODE>",
    name: "<CALL SIGN>",
    airportCode: "<AIRPORT CODE (HEX)>"
},
*/
const TARGET_VEHICLES_FALLBACK = [

];
/* The number of seconds to wait between the while-hold loop. */
const NUM_SECONDS_LOOP = 10;

/* Storage for the command given to this tracker by the server via the command line. */
var slaveCommand = {};
/* State variable that, when true, will queue new flight points, identified by their timestamp in milliseconds. */
var awaitingUpdateResponse = false;
/* Are we in slave mode? If so, we should not run autonomously, consulting the master where applicable. */
var isSlaveMode = false;
/* This will execute tracker logic until set to false. */
var shouldRunTracker = false;
/* This will be true when all targeted aircraft are still being enumerated/submitted to server */
var stillTargetingAircraft = false;
/* If the target vehicles list is provided somehow, it will end up in this variable. */
var providedTargetVehicles = [];
/* A stateful object that tracks aircrafts currently being watched and accumulating updates that are not sent whilst waiting for an existing update query to complete.*/
let trackedAircraft = {
    aircraft: {},
    savedUpdates: {},
    missedUpdates: {},
    add: function(aircraftModel) {
        if(!(aircraftModel.icao in this.aircraft)) {
            this.aircraft[aircraftModel.icao] = aircraftModel;
            this.savedUpdates[aircraftModel.icao] = [];
        }
    },
    remove: function(aircraftModel) {
        if(aircraftModel.icao in this.aircraft) {
            delete this.aircraft[aircraftModel.icao];
        }
        if(aircraftModel.icao in this.missedUpdates) {
            delete this.missedUpdates[aircraftModel.icao];
        }
        if(aircraftModel.icao in this.savedUpdates) {
            delete this.savedUpdates[aircraftModel.icao];
        }
    },
    getAircraftWithQueuedUpdatesExcl: function(excluding = []) {
        let requiredAircraft = [];
        // If icao in excluding, even if that aircraft has queued updates, it will not be returned.
        // Otherwise, this function will simply return an array with those aircraft that have a saved update length greater than 0.
        for(let aircraftIcao of Object.keys(this.aircraft)) {
            // If excluded, cont.
            if(excluding.includes(aircraftIcao)) {
                continue;
            }
            // Otherwise, get number of saved updates. If this greater than 0, get and add the aircraft's model to resulting array.
            if((this.savedUpdates[aircraftIcao] || []).length > 0) {
                requiredAircraft.push(this.aircraft[aircraftIcao]);
            }
        }
        return requiredAircraft;
    },
    missingUpdate: function(aircraftModel) {
        // Get the counter integer from the missedUpdates, or get 0, then increment it and reset.
        this.missedUpdates[aircraftModel.icao] = (this.missedUpdates[aircraftModel.icao] || 0) + 1;
        // Now, is our missed update counter equal to our greater than out max in configuration? Then remove the aircraft.
        if(this.missedUpdates[aircraftModel.icao] >= conf.NUM_MISSED_UPDATES_AIRCRAFT_LANDED) {
            com.logger.debug(`Aircraft ${aircraftModel.flight_name} (${aircraftModel.icao}) has disappeared from binary updates - most likely landed?`);
            this.remove(aircraftModel);
        }
    },
    isVehicleTracked: function(icao) {
        return icao in this.aircraft;
    },
    numTracked: function() {
        return Object.keys(this.aircraft).length;
    }
};

/*
Handle the response from master server regarding aircraft update submissions. This will set all flight points referred to in each response to synchronised,
meaning the server has acknowledged and processed them, and we never need to resend.

Arguments
---------
:aircraftSubmissionReceipts: An array of (def in webapp) FlightPointReceiptSchema instances.
*/
async function handleAircraftSubmissionReceipts(aircraftSubmissionReceipts) {
    // We have submitted all aircraft to the server and have received a submission receipts object. Each object maps an aircraft ICAO to a list of flight point receipts. Therefore,
    // we'll execute a database update on each value sent to us. TODO: error reporting here.
    let updateReceiptsResult = await Promise.allSettled(
        Object.values(aircraftSubmissionReceipts)
            .map(flightPointReceiptsArray => database.updateFlightPointReceipts(flightPointReceiptsArray))
    );
    com.logger.debug(`Successfully submitted ${updateReceiptsResult.length} aircraft and their unsynchronised points to master.`);
    // Reset our gatekeeper variable to false, allowing updates again.
    awaitingUpdateResponse = false;
}

/*
Given an aircraft model, returns another Aircraft database record referencing the given aircraft, but with all unsynchronised points already
queried. This function, by default, will also save any waiting saved updates for the given aircraft to the database prior to executing the
query. This essentially prepares the aircraft for, and returns an object compatible with, updating the aircraft with master.

Arguments
---------
:aircraftModel: An instance of Aircraft.

Returns
-------
An Aircraft model, with unsynchronised points the result of update preparation.
*/
async function makeAircraftWithPoints(aircraftModel) {
    // Get some data points for this aircraft already.
    let icao = aircraftModel.icao;
    let name = aircraftModel.flightName;
    let numSavedUpdatesToSubmit = (trackedAircraft.savedUpdates[icao] || []).length;
    com.logger.debug(`Preparing aircraft ${name} (${icao}) for update with master...`);
    // If there are any waiting points for this aircraft, construct a database flight point model out of each; get back a BuiltFlightPoints object.
    let savedUpdatesToSubmit = trackedAircraft.savedUpdates[icao] || [];
    let builtSavedUpdateFlightPoints = database.buildFlightPointsModels(savedUpdatesToSubmit);
    // Save this BuiltFlightPoints to the given aircraft.
    await builtSavedUpdateFlightPoints.saveToAircraft(aircraftModel);
    // Remove all now-saved flight points from the saved updates for this aircraft.
    // TODO: improve this technique, do not use an array but a dictionary of some kind.
    for(let savedUpdate of savedUpdatesToSubmit) {
        // This works by removing each update common between savedUpdatesToSubmit and the tracked aircraft's savedUpdates array.
        (trackedAircraft.savedUpdates[icao] || []).splice((trackedAircraft.savedUpdates[icao] || []).indexOf(savedUpdate), 1);
    }
    com.logger.debug(`Persisted ${numSavedUpdatesToSubmit} saved update flight points to database for ${name} (${icao}), there's now ${(trackedAircraft.savedUpdates[icao] || []).length} saved updates waiting.`);
    com.logger.debug(`We will be submitting ${numSavedUpdatesToSubmit} saved updates for ${name} (${icao}) to master server.`);
    // Perform a query for all unsynchronised points for this aircraft.
    let aircraftWithPoints = await database.getUnsynchronisedPointsFor(aircraftModel.icao);
    // Return this value.
    return aircraftWithPoints;
}

/*
Submit flight points to the master server. This function is technically based around a single aircraft model, the specific aircraft that invoked its
action, but will also determine whether any other aircraft, with presence in savedUpdates requires an update. The rationale behind this is that there
is a race condition when using the gatekeeper; awaitingUpdateResponse between updates when there are multiple vehicles active.

The aircraft that invoked this action may pass a primary BuiltFlightPoints object, containing the point(s) that represent the need for the invocation of
the function. This is because, we wish to count database execution time as 'protected' time (that is, awaitingUpdateResponse is true, and thus no other
IO operations can take place.)

Arguments
---------
:aircraftModel: An instance of Aircraft.
:builtFlightPoints: A BuiltFlightPoints object.
:extraOpts: An object containing optional extras.

Returns
-------
An array containing each Aircraft instance that has been updated.
*/
async function performUpdatePass(aircraftModel, builtFlightPoints, extraOpts = {}) {
    let requireAircraftSetup = extraOpts.requireAircraftSetup || false;
    // We have begun submission logic. Set our gatekeeping variable to true.
    awaitingUpdateResponse = true;

    let aircraftArray = [];
    if(!requireAircraftSetup || aircraftModel.isSetup === true) {
        // Only perform if we either don't require setup aircraft, or aircraft is setup.
        // Now, retrieve an Aircraft instance, with points, for the primary aircraft model.
        com.logger.debug(`Performing update pass; preparing PRIMARY AIRCRAFT...`);
        // Save the given builtFlightPoints to database, then make aircraft with points.
        let primaryAircraftWithPoints = await builtFlightPoints.saveToAircraft(aircraftModel)
            .then((aircraftModel) => makeAircraftWithPoints(aircraftModel));
        aircraftArray.push(primaryAircraftWithPoints);
    } else {
        com.logger.debug(`Did not perform update pass involving PRIMARY aircraft ${aircraftModel.flightName} (${aircraftModel.icao}) - not setup yet.`);
    }
    // Acquire all secondary aircraft, again, as long as we're able to; give primary aircraft icao to ignore it. Immediately filter the returned aircraft
    // models to get rid of those that are not ready to be updated yet.
    let secondaryAircraft = trackedAircraft.getAircraftWithQueuedUpdatesExcl([aircraftModel.icao])
        .filter(aircraft => aircraft.isSetup || !requireAircraftSetup);
    if(secondaryAircraft.length > 0) {
        com.logger.debug(`Located ${secondaryAircraft.length} SECONDARY aircraft with queued updates we're going to send in this update pass.`);
        // Prepare each of these as well, but we will do so asynchronously via an all settled promise.
        let allSecondaryWithPoints = await Promise.allSettled(secondaryAircraft.map(aircraft => makeAircraftWithPoints(aircraft)))
            .then((allOutcomes) => {
                // If failed, we will simply error log the value (reason) for now.
                let allRejected = allOutcomes.filter(outcome => outcome.status === "rejected");
                for(let rejected of allRejected) {
                    com.logger.error(`Getting secondary with points was rejected. Reason; ${rejected.value}`);
                }
                // Return an array of all Aircraft instances, with prepared points.
                return allOutcomes.filter(outcome => outcome.status === "fulfilled")
                    .map(fulfilled => fulfilled.value);
            });
        // Push all in secondary with points to our array.
        aircraftArray.push(...allSecondaryWithPoints);
    }
    // Ready to perform update.
    com.logger.debug(`Update pass prepared. We have ${aircraftArray.length} aircraft to submit;`);
    for(let aircraftToSubmit of aircraftArray) {
        com.logger.debug(`\t${aircraftToSubmit.flightName} (${aircraftToSubmit.icao})\t${aircraftToSubmit.FlightPoints.length} points`);
    }
    if(isSlaveMode) {
        // Do it.
        com.logger.debug(`Now submitting update pass to master server...`);
        await master.sendAircraft(aircraftArray)
            .then((aircraftSubmissionReceipts) => handleAircraftSubmissionReceipts(aircraftSubmissionReceipts));
    } else {
        com.logger.warn(`Not running in slave mode, no need to submit to master server. We'll keep this to ourselves.`);
    }
    // Return our aircraft array.
    return aircraftArray;
}

/*
This function updates the given aircraft, given a new FlightPoint we've newly acquired via binary updates. Once it has been saved
to the aircraft, as an unsynchronised point, all unsynchronised points for this aircraft will be queried and sent as an update to
the master server.

Arguments
---------
:aircraft: The Aircraft instance to associate this point with.
:flightPoint: The new FlightPoint model to save to the database and send to the server.

Returns
-------

*/
function updateAircraft(aircraftModel, flightPoint) {
    return new Promise((resolve, reject) => {
        // Get or create a new array for saved updates, or get existing.
        let savedUpdatesArray = trackedAircraft.savedUpdates[aircraftModel.icao] || [];
        // Is the aircraft setup? If not, we'll simply add this FlightPoint, as it is, to an array within the savedUpdates dictionary in trackedAircraft.
        if(aircraftModel.isSetup === false || awaitingUpdateResponse === true) {
            if(aircraftModel.isSetup === false && awaitingUpdateResponse === true) {
                com.logger.debug(`Aircraft ${aircraftModel.flightName} (${aircraftModel.icao}) has received an update, but the aircraft is not setup AND we are currently awaiting response from master for a previous update. (${savedUpdatesArray.length} waiting.)`);
            } else if(aircraftModel.isSetup === false) {
                com.logger.debug(`Aircraft ${aircraftModel.flightName} (${aircraftModel.icao}) has received an update, but is not yet setup - saving it for later. (${savedUpdatesArray.length} waiting.)`);
            } else if(awaitingUpdateResponse === true) {
                com.logger.debug(`Aircraft ${aircraftModel.flightName} (${aircraftModel.icao}) has received an update, but we are currently awaiting response from master for a previous update. (${savedUpdatesArray.length} waiting.)`);
            }
            // Set this flight point within saved updates under the flight points hash. This ensures only 1 copy of each unique point is saved.
            savedUpdatesArray.push(flightPoint);
            // Now, set this dictionary as value under the aircraft's icao as key in savedUpdates.
            trackedAircraft.savedUpdates[aircraftModel.icao] = savedUpdatesArray;
            // And resolve.
            resolve(aircraftModel);
        } else {
            // Otherwise, build this raw flight point into a database model, then submit it.
            let builtFlightPoints = database.buildFlightPointModel(flightPoint);
            return performUpdatePass(aircraftModel, builtFlightPoints)
                .then((aircraftArray) => resolve(aircraftModel));
        }
    });
}

/*
Handle a reported aircraft timeout for the given aircraft. The master, if available, can better decide whether this is an actual timeout. We will pass
all determined properties for this timeout report to the server, and act based on the response. If master is not available, we will make the executive
decision to just remove the aircraft.

Arguments
---------
:aircraft: An instance of Aircraft to handle timeout for.
:timeoutProperties: An object with a snapshot of timeout information.

Returns
-------

*/
async function handleAircraftTimeout(aircraft, timeoutProperties) {
    /* TODO: relocate this function. */
    let inactivateAircraft = async function(_aircraft) {
        _aircraft.isActiveNow = false;
        await _aircraft.save();
    };

    // If we can't find the aircraft object, this means the aircraft is no longer in binary updates. Check whether the aircraft has timed out.
    if(isSlaveMode) {
        // Since we're in slave mode, ask the master for guidance; this places the responsibility for determining what is a landing, network issue or
        // data inaccuracies solely on the master. The master may either reply to inactivate the timed out aircraft; assuming a landing, or to issue a 'hold out' command.
        com.logger.debug(`Aircraft ${aircraft.flightName} (${aircraft.icao}) has timed out! Asking master for guidance...`);
        // Perform a query to inform the master of this aircraft's timeout.
        let aircraftTimeoutReport = await master.reportAircraftTimeout(aircraft, timeoutProperties);
        // If determination is 'landing', remove the aircraft.
        if(aircraftTimeoutReport.determination === "landing") {
            com.logger.warn(`Master has informed us that ${aircraft.flightName} (${aircraft.icao}) has most likely landed. Inactivating it now...`);
            // Set the aircraft as inactive, then save it.
            await inactivateAircraft(aircraft);
        } else {
            /* TODO: proper management here. */
            com.logger.warn(`Master has not given us specific guidance on the timeout for ${aircraft.flightName} (${aircraft.icao}) we will (FOR NOW) inactivate the aircraft.`);
            await inactivateAircraft(aircraft);
        }
    } else {
        // The aircraft has timed out. We can safely remove it from tracked aircraft.
        com.logger.warn(`Aircraft ${aircraft.flightName} (${aircraft.icao}) has timed out! Inactivating it...`);
        await inactivateAircraft(aircraft);
    }
}

/*
Waits for and processes all incoming binary updates from ADSBExchange. If we have one or more aircrafts we're tracking, and the update is type is a binCraft update,
we'll extract and decode it. Then, for each aircraft in our tracking list, we'll locate the specific update object for that aircraft from binary and update the tracker.
*/
function binaryUpdateReceived(response) {
    return new Promise((resolve, reject) => {
        if(trackedAircraft.numTracked() > 0 && conf.BINARY_UPDATE_ZSTD_BINCRAFT_REGEX.test(response.url())
            && conf.BINARY_UPDATE_BINCRAFT_TYPE_REGEX.test((response.headers()["content-type"] || ""))) {
            const dateReceived = response.headers()["date"];
            const contentLength = response.headers()["content-length"];
            if(!stillTargetingAircraft) {
                com.logger.debug(`Received aircraft binary update at ${dateReceived} of length ${contentLength}`);
            }

            // We will get the response data, convert from base64 to a buffer then initialise a uint8 array for that buffer.
            // We'll then decode the binary update, and with the resulting update object, distribute update objects to each of the tracked aircraft.
            response.buffer()
                .then(recvData => new Buffer.from(recvData, "base64"))
                .then(recvBuffer => new Uint8Array(recvBuffer))
                .then(compressedData => data.decodeBinaryUpdate(compressedData))
                .then(decodedBinaryUpdate => new Promise((resolve, reject) => {
                    // Distribute a located flight point binary object from this update to the destination tracked aircraft.
                    return Promise.allSettled(
                        Object.values(trackedAircraft.aircraft).map(aircraft => {
                            let aircraftObject = decodedBinaryUpdate.findAircraftObjectByIcao(aircraft.icao);
                            if(aircraftObject === null) {
                                return new Promise((resolve, reject) => {
                                    // Assemble an object of properties describing the report.
                                    let timeoutProperties = {
                                        timeOfReport: Math.floor(Date.now() / 1000),
                                        lastBinaryUpdate: aircraft.lastBinaryUpdate,
                                        currentConfigAircraftTimeout: conf.AIRCRAFT_TIMEOUT,
                                        isTimeout: function() {
                                            // If last binary update is undefined or null, we will return true; its still a timeout, just, somehow before updates could ever be received.
                                            if(!this.lastBinaryUpdate) {
                                                return true;
                                            }
                                            return (this.timeOfReport - this.lastBinaryUpdate) > conf.AIRCRAFT_TIMEOUT;
                                        }
                                    }
                                    // If judged as timed out, we will handle the aircraft timeout, pass the properties too. This function only need be called if the aircraft
                                    // is currently active according to its database model, as an inactive aircraft doesn't timeout.
                                    if(aircraft.isActiveNow && timeoutProperties.isTimeout()) {
                                        // If we've timed out, return a promise for handling this timeout.
                                        return handleAircraftTimeout(aircraft, timeoutProperties)
                                            .then(() => resolve());
                                    }
                                });
                            }

                            return new Promise((resolve, reject) => {
                                if(aircraft.comprehensive === false && aircraftObject !== null) {
                                    com.logger.debug(`Aircraft ${aircraft.flightName} (${aircraft.icao}) is currently not comprehensive, updating it as per binary object...`);
                                    return com.saveDecodedBinaryUpdate(decodedBinaryUpdate)
                                        .then(() => data.aircraftStateFromBinary(aircraftObject))
                                        .then((aircraftState) => {
                                            // With aircraft state, update all data points not currently held.
                                            aircraft.registration = aircraftState.registration;
                                            aircraft.type = aircraftState.type;
                                            aircraft.comprehensive = true;
                                        })
                                        .then(() => resolve());
                                } else {
                                    // Otherwise, just resolve.
                                    return resolve();
                                }
                            })
                            .then(() => data.flightPointFromBinary(decodedBinaryUpdate.now, aircraftObject))
                            .then((flightPoint) => updateAircraft(aircraft, flightPoint))
                            .then((aircraft) => new Promise((resolve, reject) => {
                                // Received a valid binary update, and sent it to the Aircraft. Now, update the aircraft's lastBinaryUpdate value.
                                aircraft.lastBinaryUpdate = Math.floor(Date.now() / 1000);
                                // If the aircraft is currently set as inactive, we'll now activate it.
                                if(!aircraft.isActiveNow) {
                                    com.logger.debug(`Aircraft ${aircraft.flightName} (${aircraft.icao}) has just (re)appeared. Reactivating it!`);
                                    aircraft.isActiveNow = true;
                                }
                                // Save and resolve.
                                aircraft.save()
                                    .then(() => resolve());
                            }))
                            .catch((err) => {
                                if(err instanceof TypeError) {
                                    com.logger.debug(`TypeError when received binary update; ${err}`);
                                } else {
                                    com.logger.error(`Failed to get flight point from binary! ${err}`);
                                }
                            });
                        })
                    )
                    .then(() => resolve());
                }))
                .then(() => resolve());
        }
    });
}

/*
Given a page and a targetedVehicle object, instruct Puppeteer to select the vehicle and retrieve its current trace data; either full, recent or both.
The return value is a VehicleTrace object if successful, which contains the targeted vehicle object given as input, the full trace (if applicable) given as JSON and
the recent trace (if applicable) given as JSON.

Arguments
---------
:page: An instance of Page.
:targetedVehicle: An entry from the TargetedVehicles object returned by searchTargetedVehicles()

Returns
-------
A TargetedVehicleTrace object, which contains the given targetedVehicle, the full trace (if applicable) and recent trace (if applicable.)
*/
async function selectVehicleTraceData(page, targetedVehicle, shouldRemoveTrace = true) {
    // Ensure targeted vehicle is valid.
    if(targetedVehicle.icao === null || targetedVehicle.airportCode === null || targetedVehicle.name === null) {
        com.logger.error(`Failed to launch tracker on targeted vehicle: ${JSON.stringify(targetedVehicle)}, one or more required arguments are null.`);
        throw new error.NoExternalTargetVehiclesError(`Invalid targeted vehicle given!`);
    }
    com.logger.debug(`Selecting targeted vehicle ${targetedVehicle.name} (${targetedVehicle.icao}) and retrieving trace...`);
    // Fill in the blanks for the target documents we wish to receive upon clicking the aircraft.
    const recentTraceDocument = `trace_recent_${targetedVehicle.icao}.json`;
    const fullTraceDocument = `trace_full_${targetedVehicle.icao}.json`;
    // Spawn an array of promises to finish when all have settled OR an error occurs first in any.
    let downloadResults = await Promise.allSettled([
        page.waitForResponse((response) => response.url() === `https://globe.adsbexchange.com/data/traces/${targetedVehicle.airportCode}/${recentTraceDocument}` && response.status() === 200, { timeout: 30000 }),
        page.waitForResponse((response) => response.url() === `https://globe.adsbexchange.com/data/traces/${targetedVehicle.airportCode}/${fullTraceDocument}` && response.status() === 200, { timeout: 30000 }),
        page.evaluate((hex) => {
            // Evaluates in page, this is a function provided by ADSBExchange, not us.
            selectPlaneByHex(hex, { follow: false });
        }, targetedVehicle.icao)
    ]);
    if(shouldRemoveTrace) {
        // Evaluate a call to deselectAllPlanes() since we no longer require active trace.
        await page.evaluate(() => {
            deselectAllPlanes();
        })
        .catch((err) => {
            // Failed to deselect all planes due to evaluation fail. ADSBExchange code may have change. We must report this to admin.
            throw new error.PageEvaluationError("deselectAllPlanes", err);
        });
    }
    // Now, get our results from the action.
    let recentTraceResult = downloadResults[0];
    let fullTraceResult = downloadResults[1];
    let selectPlaneByHexResult = downloadResults[2];
    // Is select plane by hex rejected? If so, throw a PageEvaluationError.
    if(selectPlaneByHexResult.status === "rejected") {
        // Value should be an Error.
        throw new error.PageEvaluationError("selectPlaneByHex", selectPlaneByHexResult.value);
    }
    // TODO: insert some error management here.
    // Get both traces as JSON.
    let recentJson = (recentTraceResult.status === "fulfilled") ? await recentTraceResult.value.json() : null;
    let fullJson = (fullTraceResult.status === "fulfilled") ? await fullTraceResult.value.json() : null;
    // Create and return our TargetedVehicleTrace object.
    let vehicleTraceDataResult = {
        targetedVehicle: targetedVehicle,
        fullTrace: fullJson,
        recentTrace: recentJson,

        gotFullTrace: fullJson !== null,
        gotRecentTrace: recentJson !== null
    }
    com.logger.debug(`Fetched trace data for vehicle ${targetedVehicle.name} (${targetedVehicle.icao}); full trace? ${vehicleTraceDataResult.gotFullTrace}, recent trace? ${vehicleTraceDataResult.gotRecentTrace}`);
    return vehicleTraceDataResult;
}

/*
Given a TargetedVehicleTrace, which contains a potential aircraft and all traces discovered for that aircraft, merge those traces, then submit all discovered points
to our local database. From there, all unsynchronised points are queried for the aircraft and sent to the master server. This will cache those points remotely. From
there, the aircraft will tracked via binary updates.

Arguments
---------
:targetedVehicleTrace: A TargetedVehicleTrace containing the target aircraft and its trace data.

Returns
-------
The now tracked Aircraft model.
*/
async function beginTrackingAircraft(targetedVehicleTrace) {
    // First, use the data module to get an aircraft state and merged trace from this targeted vehicle trace.
    let [aircraftState, mergedTrace] = await data.aircraftWithPointsFromTraces(targetedVehicleTrace);
    // Find or create the aircraft referenced by this state object. This is an existing database model.
    let [aircraftModel, wasCreated] = await database.createAircraft(aircraftState);
    if(wasCreated === true) {
        com.logger.debug(`Created a new aircraft ${aircraftModel.flightName} (${aircraftModel.icao}) and now tracking it...`);
    } else {
        com.logger.debug(`Now tracking existing aircraft; ${aircraftModel.flightName} (${aircraftModel.icao})`);
    }
    // Set the aircraft's isSetup to false.
    aircraftModel.isSetup = false;
    await aircraftModel.save();
    // Now, add the aircraft to tracked aircraft.
    trackedAircraft.add(aircraftModel);
    if(mergedTrace !== null) {
        // Now, only if we have a trace (and by inference, a full trace,) commit the flight points to database and run an update pass.
        let flightPoints = await data.flightPointsFromTrace(mergedTrace);
        // Get a BuiltFlightPoints object from building these flight points.
        // TODO: should I use trace for this... ?
        let builtFlightPoints = database.buildFlightPointsModels(flightPoints);
        // Submit these points.
        await performUpdatePass(aircraftModel, builtFlightPoints, { requireAircraftSetup: false });
    }
    // Finally, we can set the aircraft's isSetup to true.
    aircraftModel.isSetup = true;
    await aircraftModel.save();
    // And return the aircraft model.
    return aircraftModel;
}

/*
ADSBExchange specific setting. This allows us to have multiple aircrafts exclusively selected at the same time; this allows us to receive updates for just those we have
interest in, thus saving bandwidth for everyone.
*/
async function ensureMultiSelectEnabled(page) {
    // Get the status of multiselect from the page.
    let isMultiSelectEnabled = await page.evaluate(() => {
        if(!multiSelect) {
            toggleMultiSelect(true);
        }
        return multiSelect;
    });
    if(isMultiSelectEnabled) {
        com.logger.debug(`Multi select now enabled!`);
    } else {
        throw new Exception(`Multi select could not be enabled!`);
    }
}

/*
For all vehicles provided by either the server or our fallback list, this function will perform the selection logic; all recent & full trace will be downloaded and
submitted to the server, then the aircraft will be added to the tracked aircraft list.
*/
async function selectTargetVehicles(page) {
    stillTargetingAircraft = true;
    // Get our targets.
    com.logger.debug(`Getting target vehicles...`);
    let targetVehicles = await getTargetVehicles();
    com.logger.debug(`We will be tracking ${targetVehicles.length} vehicles.`);
    // Now, we must ensure that multiselect is enabled on the page.
    await ensureMultiSelectEnabled(page);
    // Now we can select all vehicles. For each entry in target vehicles icao, we'll collect all trace, then pass that result to beginTrackingAircraft to add it to the tracked aircraft list.
    for(let targetedVehicle of targetVehicles) {
        com.logger.debug(`Starting tracking of target ${targetedVehicle.name} (${targetedVehicle.icao})`);
        let targetedVehicleTrace = await selectVehicleTraceData(page, targetedVehicle, false);
        // Now, with the targeted vehicle trace, we can begin tracking this aircraft.
        let aircraftModel = await beginTrackingAircraft(targetedVehicleTrace);
        com.logger.debug(`Vehicle ${aircraftModel.flightName} (${aircraftModel.icao}) is now being tracked!`);
    }
    com.logger.debug(`Selection of targeted vehicles COMPLETE! We are now actively tracking the target aircraft!`);
    stillTargetingAircraft = false;
}

/*
Runs prior to tracker execution. This function ensures the environment of execution is well suited to the tools the tracker will try and use.
*/
async function ensureTrackerCanBeRun(page) {
    // An object containing all identifiers required by this tracker, and the type they should return as.
    let requiredIdentifiers = {
        selectPlaneByHex: "function",
        deselectAllPlanes: "function",
        toggleMultiSelect: "function",
        multiSelect: "boolean",
        testHide: "function",
        testUnhide: "function"
    };
    // Page should already be navigated and loaded to ADSBExchange.
    // Collect an object that describes the state of all required on-page functionality. If any do not exist, throw a WebControlsError.
    let requiredIdentifiersReport = await page.evaluate((identifiers) => {
        // Build a report showing the status of each identifier.
        return identifiers.map(identifier => ({[identifier]: typeof(eval(identifier))}))
            .reduce(((r, c) => Object.assign(r, c)), {});
    }, Object.keys(requiredIdentifiers));
    // Now, iterate the keys from the report.
    var shouldFail = false;
    for(const [reportKey, typeString] of Object.entries(requiredIdentifiersReport)) {
        // If typestring does not match the equivalent value in required identifiers, set should fail to true.
        if(typeString !== requiredIdentifiers[reportKey]) {
            com.logger.warn(`Tracker will NOT be able to run; identifier ${reportKey} is of type ${typeString} on page, instead of required ${requiredIdentifiers[reportKey]}`);
            shouldFail = true;
        }
    }
    // If should fail is true, throw an exception for WebControlsError.
    if(shouldFail) {
        throw new error.WebControlsError(requiredIdentifiersReport);
    }
    return true;
}

/*
This function must return an array of icaos.
These are the vehicles that will be selected.
*/
async function getTargetVehicles() {
    // We must first decide what our targets are. If we were not given any targets, use the fallback list. If that too is empty, throw an exception.
    if((providedTargetVehicles === null || (providedTargetVehicles.length === 0) && TARGET_VEHICLES_FALLBACK.length === 0)) {
        throw new Exception(`No target vehicles have been provided! Tracker can not run.`);
    }
    // Now, we have one of provided vehicles or fallback, at least. Attempt to use provided vehicles, but use fallback if unavailable.
    let targetVehicles;
    if(providedTargetVehicles !== null && providedTargetVehicles.length > 0) {
        targetVehicles = providedTargetVehicles;
    } else {
        targetVehicles = TARGET_VEHICLES_FALLBACK;
    }
    return targetVehicles;
}

async function setupFromOptions(extraOpts) {
    // Are we running in slave mode?
    isSlaveMode = extraOpts.isSlave || false;
    if(isSlaveMode) {
        com.logger.debug(`Tracker is running in slave mode - we will attempt constant contact with the master.`);
    } else {
        com.logger.debug(`Tracker not running in slave mode - no need for master contact.`);
    }
    // Get the command from the server, given via command-line.
    slaveCommand = extraOpts.slaveCommand || {};
    try {
        // From this command, attempt to get a targeted vehicles array.
        let targetVehicles = slaveCommand.target_vehicles || [];
        // If this array is NOT empty, this takes ultimate precedence. So set our provided target vehicles to this.
        if(targetVehicles.length > 0) {
            com.logger.debug(`Target vehicles were given by the command line. There are ${targetVehicles.length} vehicles to track.`);
            providedTargetVehicles = targetVehicles;
        } else {
            if(isSlaveMode) {
                // Otherwise, it was not given. If we are in slave mode, we'll attempt to fetch a list of target vehicles from the server.
                let remoteTargetVehicles = await master.requestTrackerTargets();
                // If we could not find any target vehicles (not including errors,) we will simply fallback to local.
                if(remoteTargetVehicles.length <= 0) {
                    com.logger.warn(`Master server did not reply with any target vehicles. Falling back to our LOCAL LIST of target vehicles (contains ${TARGET_VEHICLES_FALLBACK.length} vehicles.)`);
                    throw new error.NoExternalTargetVehiclesError("server-not-targeting-any");
                } else {
                    // Otherwise, we managed to find some vehicles. We'll use these.
                    com.logger.debug(`Master server replied with a list of ${remoteTargetVehicles.length} vehicles to target.`);
                    providedTargetVehicles = remoteTargetVehicles;
                }
            } else {
                // Not in slave mode. We'll simply set provided target vehicles to null, thus triggering a fallback to our local list.
                com.logger.warn(`No target vehicles given by command line, and we are NOT in slave mode. Falling back to our LOCAL LIST of target vehicles (contains ${TARGET_VEHICLES_FALLBACK.length} vehicles.)`);
                throw new error.NoExternalTargetVehiclesError("not-in-slave-mode");
            }
        }
    } catch(err) {
        if(err instanceof error.NoExternalTargetVehiclesError) {
            providedTargetVehicles = null;
        } else {
            throw err;
        }
    }
}

/*
Main entry point for tracking logic. Caller should provide the Puppeteer browser and page instances. Execution will block on this function and will only
return once tracker logic completes or a significant error is encountered.
*/
async function runTracker(browser, page, extraOpts = {}) {
    // Ensure the tracker can run; meaning all required ADSB functionality is currently available.
    await ensureTrackerCanBeRun(page);
    // Set a handler on interrupt signal.
    process.on("SIGINT", function() {
        shouldRunTracker = false;
    });
    // Set this tracker up from relevant data points in extraOpts.
    await setupFromOptions(extraOpts);

    // Setup a response interceptor to catch all binCraft responses.
    page.on("response", binaryUpdateReceived);
    var numSecondsHeartbeatCounter = 0;
    try {
        // Set should run to true.
        shouldRunTracker = true;
        // Now, select all target vehicles.
        await selectTargetVehicles(page);
        // Loop until we shouldRunTracker is set to false.
        while(shouldRunTracker) {
            // Await a timeout to allow event loop to continue.
            await new Promise(resolve => setTimeout(resolve, NUM_SECONDS_LOOP * 1000));
            numSecondsHeartbeatCounter += NUM_SECONDS_LOOP;
            if(numSecondsHeartbeatCounter >= 20) {
                numSecondsHeartbeatCounter = 0;
                // At the end of the timeout, send a heartbeat signal to the master, if required, letting it know we're alive.
                await master.sendHeartbeat();
            }
        }
    } catch(err) {
        if(err instanceof error.PageEvaluationError) {
            // Critical error that refers to a page missing an expected control or tool. This will absolutely happen if client is redirected perhaps by an ad.
            com.logger.error(`Failed to run a scan! Evaluating logic for identifier ${err.functionIdentifier} failed!`);
            com.logger.error(err.message);
            await master.sendErrorReport("page-evaluation-error", `Failed to run a scan! Evaluating logic for identifier ${err.functionIdentifier} failed!`);
        } else if(err instanceof error.WebControlsError) {
            // Critical error that means the target environment is missing crucial tools the tracker uses. This should prompt a mail to sysadmin for repairs.
            /* TODO: report this to server/write to file. */
            await master.sendErrorReport("web-controls-error", err.message, err.stack, err.webControlsStatus);
            throw err;
        } else {
            // Some other unhandled error. Critical because we don't know what's going on.
            await master.sendErrorReport("unknown", err.cause, err.stack);
            throw err;
        }
    }
}


exports.runTracker = runTracker;
