/*
The module used by slaves of type history-trawler.
THIS IS INCOMPLETE AND CAN'T YET BE USED.
*/
const conf = require("./conf");
const com = require("./com");
const data = require("./data");
const error = require("./error");
const master = require("./master");
const database = require("./database");

/* Are we in slave mode? If so, we should not run autonomously, consulting the master where applicable. */
var isSlaveMode = false;

async function getSelectedPlane(page) {
    return await page.evaluate(() => {
        let plane = SelectedPlane;
        return {
            icao: plane.icao,
            name: plane.name,
            altitude: plane.altitude,
            position: plane.position,
            speed: plane.speed,
            traceSize: plane.trace.length,
            airportCode: plane.icao.substring(plane.icao.length-2)
        };
    });
}

/*
Commit the given TargetedVehicleTrace object to the database. This will merge all discovered traces, create the aircraft, should it not exist,
then all discovered flight points will be created and associated with the aircraft. Finally, all unsynchronised points found for this aircraft
on this day will be queried.

Arguments
---------
:targetedVehicleTrace: A TargetedVehicleTrace object.
:dayIsoString: A date, in ISO format string.

Returns
-------
An array of two items;
    The Aircraft model, alongside all flight points discovered on the day
    The ISO string given.
*/
async function commitVehicleTraceResult(targetedVehicleTrace, dayIsoString) {
    // Use the vehicle trace data result object to get an aircraft state & merged traces.
    let [aircraftState, mergedTrace] = await data.aircraftWithPointsFromTraces(targetedVehicleTrace);
    // Find or create the aircraft referenced by this state object. This is an existing database model.
    let [aircraftModel, wasCreated] = await database.createAircraft(aircraftState);
    if(wasCreated === true) {
        com.logger.debug(`Created a new aircraft ${aircraftModel.flightName} (${aircraftModel.icao}), committing flight points to database...`);
    } else {
        com.logger.debug(`Committing newly discovered flight points for existing aircraft; ${aircraftModel.flightName} (${aircraftModel.icao}) to database.`);
    }
    // Now, we have a merged trace. Convert that to a list of FlightPoint via the data module.
    let flightPoints = await data.flightPointsFromTrace(mergedTrace);
    // Get a BuiltFlightPoints object from building these flight points.
    let builtFlightPoints = database.buildFlightPointsModels(flightPoints);
    // Now, save the flight points to this aircraft, then get all unsynchronised points from this day and return that along with the aircraft model.
    return await builtFlightPoints.saveToAircraft(aircraftModel)
        .then((aircraft) => database.getUnsynchronisedPointsFromDayFor(aircraft.icao, dayIsoString))
        .then((aircraftModelWithPoints) => [aircraftModelWithPoints, dayIsoString]);
}

/*
Select an aircraft on the given Page by its icao. In order for this function to be successful, the Page must already be open to ADSBExchange.
Optionally, calling code can request manual selection; which only uses web controls.

Arguments
---------
:page: An instance of Page.
:aircraftIcao: The ICAO of a targeted aircraft.
:useAdsbInterface: True if the selection should be performed by evaluating ADSB-native code.

Returns
-------
Void.
*/
async function selectAircraftByIcao(page, aircraftIcao, useAdsbInterface = true) {
    if(useAdsbInterface) {
        // Use ADSB interface, which means we'll evaluate a call to selectPlaneByHex. At the moment, this function will return a boolean
        // to indicate success. If the result is true, we can return from this function. Otherwise, if the return value is false, we'll
        // throw an exception.
        com.logger.debug(`Selecting plane ${aircraftIcao} by ADSB interface...`);
        let planeSelected = await page.evaluate((icao) => {
            return selectPlaneByHex(icao);
        }, aircraftIcao);

        if(planeSelected === true) {
            com.logger.debug(`Successfully selected plane by hex!`);
        } else {
            com.logger.error(`Failed to select plane ${aircraftIcao} by hex!`);
            throw new error.WebControlError("selectPlaneByHex", "Failed to call this function.");
        }
    } else {
        // We have decided to select the aircraft icao without the use of ADSB interface. This means we'll be manually typing in and
        // selecting components, then waiting for changes on the Page as response.
        com.logger.warn(`WARNING! Attempting to select aircraft by ICAO without ADSB interface! This may be buggy, and not even work.`);
        // First, chain typing target ICAO into search input.
        await page.type("#search_input", targetIcao, { delay: 100 })
            .then(() => page.waitForTimeout(1000));
        // Next, chain locating & clicking the search button, then we'll wait for the sidebar's selector to appear.
        await page.$("#search_form")
            .then((form) => form.$("button[type='submit']"))
            .then((submit) => submit.click({
                delay: 500
            }));
    }
}

/*
*/
async function ensureSidebarOpen(page, aircraftIcao) {
    // Check if sidebar is open. If so, just return. Otherwise, select the first target icao by hex.
    let isSidebarOpen = await page.$("#selected_infoblock")
        .then((infoBlock) => infoBlock.evaluate((block) => {
            return window.getComputedStyle(block).display !== "none";
        }, infoBlock));
    if(!isSidebarOpen) {
        console.log(`Sidebar not open. Opening it now with first target icao; ${aircraftIcao}`);
        let planeSelected = await page.waitForTimeout(1200)
            .then(() => selectAircraftByIcao(page, aircraftIcao));
        if(!planeSelected) {
            throw new Exception(`Failed to open sidebar!`);
        }
    } else {
        console.log(`Sidebar already open!`);
    }
}

/*
*/
async function ensureHistoryTraceOpen(page) {
    // This will ensure sidebar is also open.
    console.log(`Ensuring trace history is open in sidebar...`);
    // First, check if history is even open.
    let isHistoryOpen = await page.$("#history_collapse")
        .then((historyCollapse) => historyCollapse.evaluate((collapse) => {
            return window.getComputedStyle(collapse).display !== "none";
        }, historyCollapse));
    // If history not open, open it. Otherwise just return.
    if(!isHistoryOpen) {
        console.log(`History is not open. Opening it now...`);
        // Chain the waiting for, clicking of and opening of the history viewer.
        await page.waitForSelector("#show_trace")
            .then(() => page.evaluate(() => {
                toggleShowTrace();
            })
        )
        .then(() => page.waitForSelector(".greyButton.active"))
        .then(() => page.waitForTimeout(1900));
    } else {
        console.log(`No need to open history - it is already open!`);
    }
    // Await for and return the history date picker.
    console.log(`Trace history open.`);
}

/*
Changes the currently selected date, in the context of trace history for a specific aircraft to the given day. Prior to calling
this function, an aircraft MUST be selected, and the trace history collapse MUST be open. This function will return an anonymous
async function that, when awaited, will actuate the change.

Arguments
---------
:page: An instance of Page.
:targetDay: An ISO string for the target day.
:useAdsbInterface: If true, ADSB code evaluation will be used for the actuation process, otherwise this will be done manually via web controls.

Returns
-------
An anonymous async function that, when awaited, will actuate the change.
*/
async function selectTargetedDate(page, targetDay, useAdsbInterface = true) {
    if(useAdsbInterface) {
        com.logger.debug(`Preparing date selection via ADSB interface...`);
        // Return an async function that when awaited, will execute the targeting of the given target day.
        return async (page, targetDay) => {
            com.logger.debug(`Performing ADSB interface trace date selection targeting: ${targetDay}`);
            // This will evaluate the setTraceDate + shiftTrace functions on the page, returning the date that has been selected.
            return await page.evaluate((dayIsoString) => {
                let setDate = setTraceDate({ string: dayIsoString });
                shiftTrace();
                return setDate;
            }, targetDay);
        };
    } else {
        com.logger.warn(`WARNING! Attempting to configure date picker without ADSB interface! This may be buggy, and not even work.`);
        com.logger.debug(`Configuring date picker contents for target day; ${targetDay}`);
        // Locate the history date picker, then manipulate its contents to read target day
        await historyDatePicker.click({ delay: 1100 })
            .then(_ => page.keyboard.down("ControlLeft"))
            .then(_ => page.keyboard.press("KeyA"))
            .then(_ => page.keyboard.up("ControlLeft"))
            .then(_ => page.keyboard.press("Backspace"))
            .then(_ => historyDatePicker.type(targetDay, { delay: 300 }))
            .catch((err) => {
                com.logger.error(`Failed to configure date picker!\nError: ${err}`);
                throw new error.WebControlError("configureDatePickerFor", err.message);
            });
        com.logger.debug(`Successfully configured date picker. Returning async callable to execute the search!`);
        // Finally, return a lazily-evaluated async function that, when called, will press enter, and click out of the date picker.
        return async (page) => {
            com.logger.debug(`Executing manual search via trace input!`);
            await page.keyboard.press("Enter")
                .then(_ => page.waitForTimeout(1800));
        };
    }
}

/*
Handle the current assignment. This requires a target aircraft ICAO and target day. The function will type the target aircraft's ICAO out into the search input.
Then, the search form will be located, and from that, the search button; which will be clicked. This, after a very short delay will open the left sidebar showing
the most recent known location for the target aircraft. The show trace button will be searched for and clicked. This will open the history dialog in which the
history date picker can be found.

The date picker will be configured to hold the target date. At which point, an array of promises will be awaited resolving to the full trace and recent trace.
The function will then construct a TargetedVehicleTrace object, and use this to populate the local database. Once completed, the database will be queried for
all points saved on the required day.

Arguments
---------
:page: An instance of Page.
:currentAssignment: A RequestedTraceHistory object.

Returns
-------
An array, the first index is the Aircraft model, with all points associated with the required day, and the second is the ISO format string date.
*/
async function handleTraceAssignment(page, currentAssignment) {
    // Get basic information about the trace assignment.
    let targetIcao = currentAssignment.targetAircraftIcao;
    let targetAirportCode = targetIcao.substring(targetIcao.length-2);
    let targetDay = currentAssignment.targetDay;
    com.logger.debug(`Trawler assignment beginning execution:\n\tTarget ICAO:\t${targetIcao}\n\tTarget day:\t${targetDay}`);
    // Interpolate the two possible types of trace data for the aircraft, even though this is history, so its very unlikely we'll ever have to handle recent trace.
    const recentTraceDocument = `trace_recent_${targetIcao}.json`;
    const fullTraceDocument = `trace_full_${targetIcao}.json`;

    // Ensure sidebar & history is open.
    await ensureSidebarOpen(page, targetIcao)
        .then(() => ensureHistoryTraceOpen(page));
    // Now, commence saving logic.
    console.log(`Saving trace for aircraft ${targetIcao} on ${targetDay}`);
    // Await a promise array for all settled, this array will first issue a chain for selecting the aircraft, and selecting the target day, and finally, for saving the trace(s).
    let [recentTraceResult, fullTraceResult] = await Promise.allSettled([
        page.waitForResponse((response) => response.url() === `https://globe.adsbexchange.com/globe_history/${targetDay}/traces/${targetAirportCode}/${recentTraceDocument}` && response.status() === 200, { timeout: 6000 }),
        page.waitForResponse((response) => response.url() === `https://globe.adsbexchange.com/globe_history/${targetDay}/traces/${targetAirportCode}/${fullTraceDocument}` && response.status() === 200, { timeout: 6000 }),
        selectAircraftByIcao(page, targetIcao)
            .then(() => selectTargetedDate(page, targetDay))
    ])
    .then((promiseResults) => {
        // Get recent & full trace promise results.
        let recentTraceResult = promiseResults[0];
        let fullTraceResult = promiseResults[1];
        // If both traces failed, we will throw an exception.
        if(recentTraceResult.status === "rejected" && fullTraceResult.status === "rejected") {
            // If both reasons are instances of TimeoutError, don't worry; just throw exception no-traces, no need to print any logs.
            if(recentTraceResult.reason instanceof TimeoutError && fullTraceResult.reason instanceof TimeoutError) {
                throw new Exception("no-traces");
            } else {
                console.error(`Failed to get trace history for aircraft ${targetIcao} on day ${targetDay}, both traces were rejected!`);
                console.warn(recentTraceResult.reason);
                console.warn(fullTraceResult.reason);
                // Timeout actually doesn't mean anything's wrong. For now, we'll simply throw an exception declaring no-traces.
                throw new Error("unk");
            }
        }
        return [recentTraceResult, fullTraceResult];
    })
    .catch((err) => {
        if(err === "no-traces") {
            console.warn(`No trace data for ${targetIcao} on day ${targetDay}.`);
            return [null, null];
        } else {
            com.logger.error(`Failed to get history trace data.\nError: ${err}`);
            throw new error.GeneralError(err);
        }
    });
    // Attempt to extract recent & full JSON. For now, we will only utilise FULL json.
    let recentJson = (recentTraceResult !== null && recentTraceResult.status === "fulfilled") ? await recentTraceResult.value.json() : null;
    let fullJson = (fullTraceResult !== null && fullTraceResult.status === "fulfilled") ? await fullTraceResult.value.json() : null;
    // Create a TargetedVehicleTrace object.
    let targetedVehicleTrace = {
        targetedVehicle: aircraftState,
        fullTrace: fullJson,
        recentTrace: recentJson,
        gotFullTrace: fullJson !== null,
        gotRecentTrace: recentJson !== null
    };
    console.log(`Got trace for ${targetIcao} on day ${targetDay}! Full trace? ${fullJson !== null}, Recent trace? ${recentJson !== null}`);
    // Commit the relevant parts to the database and return the aircraft model alongside relevant flight points.
    return await commitVehicleTraceResult(targetedVehicleTrace, targetDay);
}

/*
Execute trawler logic. This will signal the server for some starting commands to save the trace for. Then, will loop
on this same logic until such time the received command in response is 'shutdown.'
*/
async function runHistoryTrawler(browser, page, extraOpts = {}) {
    /*
    NOT IMPLEMENTED.
    This module is not yet complete and can't be used.
    */
    throw new Exception("AirEyes trawler is NOT COMPLETE!")
    // Setup our extra opts.
    isSlaveMode = extraOpts.isSlave || false;
    if(isSlaveMode) {
        com.logger.debug(`Trawler is running in slave mode - we will attempt constant contact with the master.`);
    } else {
        com.logger.debug(`Trawler not running in slave mode - no need for master contact.`);
        /* TODO: incomplete. Trawler currently can't run without master presence. */
        throw new Exception("TRAWLER RUNNING WITHOUT MASTER - NOT IMPLEMENTED YET");
    }
    com.logger.debug(`Initiated history trawler logic...`);
    // Holds the current assignment from the master.
    var currentAssignment = null;
    // A boolean flag, when true, looping trawler logic will stop.
    var shouldShutdown = false;

    do {
        let nextCommand;
        // If current assignment is null, perform an empty request for more work.
        if(currentAssignment === null) {
            // Signal master server requesting work.
            com.logger.debug(`No assignment. Requesting first work from server!`);
            nextCommand = await master.requestTraceHistoryWork();
        } else {
            // We have a current assignment.
            com.logger.debug(`Trace history trawler handling assignment; collect all flight data for aircraft ${currentAssignment.targetAircraftIcao} on ${currentAssignment.targetDay}`);
            // Await a promise chain that will handle the assignment, get back the aircraft model, complete with just the points discovered on the required day,
            // then send the trace data to the server, receiving back the next command.
            nextCommand = await handleTraceAssignment(page, currentAssignment)
                .then(([aircraftModelWithPoints, dayIsoString]) => master.sendTraceHistory(aircraftModelWithPoints, dayIsoString))
                .catch((err) => {
                    com.logger.error(`Failed to handle trace assignment & send results for next command.\nError: ${err}`);
                    throw new error.GeneralError(err);
                });
        }
        // If nextCommand is null, throw an exception.
        if(nextCommand === null) {
            com.logger.error(`Failed to runHistoryTrawler, nextCommand is null!`);
            throw new Error("Next command is NULL");
        }
        // First, check if this command has a 'receipts' attribute. This is the reply RE the previously sent history. Attempt to get the attribute,
        // if we fail, simply get an empty array. If we succeed, acknowledge receipt of these flight points in local database.
        let flightPointReceiptsArray = nextCommand.receipts || [];
        if(flightPointReceiptsArray.length > 0) {
            // If we have more than one flight point receipt, synchronise these with the database.
            com.logger.debug(`Received ${flightPointReceiptsArray.length} flight point receipts from last aircraft trace history.`);
            await database.updateFlightPointReceipts(flightPointReceiptsArray);
        }
        // Switch the command.
        switch(nextCommand.command) {
            case "trawl":
                // If the next command is trawl, we will ensure we've been given a requestedTraceHistory object. In this case, that becomes our current assignment.
                // If requestedTraceHistory is null or undefined, throw an exception.
                currentAssignment = nextCommand.requestedTraceHistory || null;
                if(currentAssignment === null) {
                    throw new Error(`Failed to continue executing runHistoryTrawler, we were given a command to trawl, but requestedTraceHistory is null.`);
                }
                // Break & loop.
                break;

            case "shutdown":
                // If we've been signalled with shutdown.
                com.logger.debug(`We've been requested to shutdown`);
                throw new error.ShutdownRequestError("shutdown-requested");

            default:
                throw new Error(`Failed to runHistoryTrawler, command is unrecognised; ${nextCommand.command}`);
        }
    } while(!shouldShutdown);
}


exports.runHistoryTrawler = runHistoryTrawler;
