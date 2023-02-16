const axios = require("axios");
const com = require("./com.js");
const path = require("path");
const { Manager } = require("socket.io-client");

const LOG_DIRECTORY = path.join("exports", "payloads");

// Axios instance.
var socketManager = null;
var socket = null;
var socketConnected = false;
var axiosInstance = null;
var authenticated = false;
/* Instance identifier, this is for the naming of saved files. */
var identifier = `AirEyesSlave`;
/* Should all outgoing JSON be saved to disk? */
var shouldSavePayloads = false;

async function setupFromOptions(options = {}) {
    identifier = options.identifier || "AirEyesSlave";
    shouldSavePayloads = options.shouldSavePayloads || false;
}

async function saveOutgoingPayload(payloadJson) {
    if(shouldSavePayloads) {
        // Gotta save payloads.
        let timeNow = Date.now();
        // Make filename.
        let fileName = `${identifier}-${timeNow.toString()}.json`;
        // If we are required to print all outgoing, do so now.
        await com.writeFileAsUtf8(LOG_DIRECTORY, fileName, payloadJson);
    }
}

async function connectSocket(baseUrl, workerUniqueId, cookie) {
    // Setup socket manager.
    socketManager = new Manager(baseUrl, {
        autoConnect: true,
        withCredentials: true,
        extraHeaders: {
            "User-Agent": "aireyes/slave",
            "Cookie": cookie,
            "WorkerUniqueId": workerUniqueId
        }
    });
    // Now, setup a socket towards the /worker namespace.
    socket = socketManager.socket("/worker");
    socket.on("connect", () => {
        com.logger.debug(`Successfully opened worker socket!`);
        socketConnected = true;
    });
    socket.on("connect_error", (error) => {
        com.logger.error(`Failed to open socket to /worker namespace!`);
        socketConnected = false;
        throw error;
    });
    socket.on("disconnect", (reason) => {
        com.logger.debug(`Socket connection to master closed: ${reason}`);
        socketConnected = false;
    });
    return socket;
}

const createSession = async (workerName, workerUniqueId) => {
    const authParams = {
        workerName: workerName,
        workerUniqueId: workerUniqueId
    };

    const resp = await axiosInstance.post("/api/worker/authenticate", authParams);
    const [cookie] = resp.headers["set-cookie"];
    axiosInstance.defaults.headers.Cookie = cookie;
    axiosInstance.defaults.headers.WorkerUniqueId = workerUniqueId;
    return cookie;
};

/*
*/
async function authenticateSlave(workerName, workerUniqueId, baseUrl) {
    // Create a new axios instance from this URL and headers.
    axiosInstance = axios.create({
        baseURL: baseUrl,
        headers: {
            "User-Agent": "aireyes/slave"
        }
    });
    // Create a new session, and above that, create a new SocketManager and Socket.
    return await createSession(workerName, workerUniqueId)
        .then((cookie) => {
            // All good.
            com.logger.debug(`Successfully authenticated worker!`);
            authenticated = true;
            return cookie;
        })
        .then((cookie) => connectSocket(baseUrl, workerUniqueId, cookie))
        .then((socket) => {
            // Returns both the Axios instance and the connected socket.
            return [axiosInstance, socket];
        })
        .catch((err) => {
            com.logger.error(`Failed to authenticate slave worker. ${err}`);
            throw err;
        });
}

/*
Send a new signal update for this radar worker to the master server. This function will return the response data if the
request resulted in an HTTP 200, otherwise, an error will be thrown.

Arguments
---------
:state: The state of this worker.
:extraSignalData: A dictionary in which extra data can be supplied. This will be JSON'ified.

Returns
-------
The response data.
*/
async function sendSignal(state, extraSignalData = {}) {
    if(!authenticated) {
        // If not authenticated, no need to send a hearbeat anyway.
        return;
    }
    // Construct our signal. As a base, this just contains the state.
    let workerSignal = {
        state: state
    };
    // Assign all items in our extra signal data object to this worker signal object.
    Object.assign(workerSignal, extraSignalData || {});
    // JSON'ify the worker signal.
    let workerSignalJson = JSON.stringify(workerSignal);
    // Attempt to, if required, save this to disk.
    await saveOutgoingPayload(workerSignalJson);
    // Perform a POST request to /api/worker/update.
    const resp = await axiosInstance.post(`/api/worker/update/${state}`, workerSignalJson, {
        headers: {"Content-Type": "application/json"}
    });
    // Ensure this is successful, with an HTTP 200, or throw an error.
    if(resp.status !== 200) {
        com.logger.error(`Sending signal to master server has failed with HTTP ${resp.status}, text; ${resp.statusText}`);
        throw new Error("failed-send-signal");
    }
    // Otherwise, return the data.
    return resp.data;
}

/*
*/
async function sendHeartbeat() {
    let result;
    // Ensure we're authenticated. Return if not.
    if(!authenticated) {
        return;
    }
    // Now, check; do we have an active socket io instance? If so, send the heartbeat via that, and acknowledge the receipt.
    if(socketConnected) {
        let heartbeatSignal = {state: "heartbeat"};
        result = await new Promise((resolve, reject) => {
            result = socket.emit("heartbeat", heartbeatSignal, (response) => {
                resolve(response);
            });
        });
    } else {
        // Otherwise, if no socket, we are at least authenticated. So, call out to the local sendSignal function to send via HTTP.
        result = await sendSignal("heartbeat");
    }
    return result;
}

/*
Send one or multiple aircraft/flight point combinations to the server for creation/updating. The expected input is an array of Aircraft instances,
the result of querying an aircraft along with associated flight point records. If the input is a single Aircraft instance, it will be placed within
an array for the purposes of this function. This function will perform a JSON request, and will return an AircraftSubmissionReceipts object, which
maps an aircraft ICAO to a list of flight point receipts.

Arguments
---------
:aircraftArray: Either an instance of, or an array of instances of Aircraft, optionally each with associated flight points.

Returns
-------
Returns an AircraftSubmissionReceipts object. Which maps an aircraft's ICAO to an array of flight point receipt objects, containing the flight point's hash, aircraft's
icao and a boolean; whether the point is now synchronised.
*/
async function sendAircraft(aircraftArray) {
    // Ensure our input is an array.
    if(!Array.isArray(aircraftArray)) {
        // Not one yet, update to be within one.
        aircraftArray = [aircraftArray];
    }
    // Stringify the array straight away.
    let aircraftArrayJson = JSON.stringify(aircraftArray);
    // Attempt to, if required, save this to disk.
    await saveOutgoingPayload(aircraftArrayJson);
    // Now, use the axios instance to perform a POST request toward the aircraft API.
    let aircraftSubmissionReceipts = await axiosInstance.post("/api/worker/aircraft", aircraftArrayJson, {
        headers: {"Content-Type": "application/json"}
    })
    .then(response => response.data)
    .catch((err) => {
        com.logger.error(`Failed to send aircraft to server, error: ${err}`);
        throw err;
    });
    // Return the receipts object.
    return aircraftSubmissionReceipts;
}

/*
*/
async function reportAircraftTimeout(aircraft, timeoutProperties) {
    // We will use the timeoutProperties object as the timeout report.
    let timeoutReport = {
        aircraftIcao: aircraft.icao,
        timeOfReport: timeoutProperties.timeOfReport,
        lastBinaryUpdate: timeoutProperties.lastBinaryUpdate,
        currentConfigAircraftTimeout: timeoutProperties.currentConfigAircraftTimeout,
    };
    let reportJson = JSON.stringify(timeoutReport);
    // Attempt to, if required, save this to disk.
    await saveOutgoingPayload(reportJson);
    return await axiosInstance.post(`/api/worker/aircraft/${aircraft.icao}/timeout`, reportJson, {
        headers: {"Content-Type": "application/json"}
    })
    .then(response => response.data)
    .catch((err) => {
        com.logger.error(`Failed to report aircraft timeout.`);
        throw err;
    })
}

/*
Send a request to the server for the latest list of target vehicles that should be tracked.

Returns
-------
This function will return an array, containing each target vehicle object, just as they appear locally.
*/
async function requestTrackerTargets() {
    return await axiosInstance.get("/api/worker/targets")
    .then((response) => response.data)
    .catch((err) => {
        com.logger.error(`Failed to request tracker targets.`);
        throw err;
    })
}

/*
Send a partially nulled history trace object to the server, signalling that we are
ready to accept work.

Returns
-------
A AircraftDayTraceResponseSchema equivalent object.
*/
async function requestTraceHistoryWork() {
    return await axiosInstance.post("/api/worker/trace", {}, {
        headers: {"Content-Type": "application/json"}
    })
    .then(response => response.data)
    .catch((err) => {
        com.logger.debug(`Failed to request trace history work, error: ${err}`);
        throw err;
    });
}

/*
Send trace history we've acquired in response to a request from the master server.
This will simply query the /trace route with all provided data about the aircraft.

Arguments
---------
:aircraftModel: An Aircraft model, with all flight points located on the given day.
:dayIsoString: The day from which to query flight points, in the form of an ISO format string.

Returns
-------
A AircraftDayTraceResponseSchema equivalent object.
*/
async function sendTraceHistory(aircraftModel, dayIsoString) {
    // Assemble an AircraftDayTraceHistorySchema compatible object from the given data.
    // Now, build the object.
    let aircraftDayTraceHistory = {
        day: dayIsoString,
        aircraft: aircraftModel,
        intentionallyEmpty: (aircraftModel.FlightPoints.length == 0) ? true : false
    };
    let aircraftDayTraceJson = JSON.stringify(aircraftDayTraceHistory);
    // Attempt to, if required, save this to disk.
    await saveOutgoingPayload(aircraftDayTraceJson);
    // Now, perform a request to the trace route.
    return await axiosInstance.post("/api/worker/trace", aircraftDayTraceJson, {
        headers: {"Content-Type": "application/json"}
    })
    .then(response => response.data)
    .catch((err) => {
        com.logger.debug(`Failed to send trace history, error: ${err}`);
        throw err;
    });
}

/*
Send an error report from this radar worker to the master server. This will be logged alongside the worker.

Arguments
---------
:errorCode: Required. A string-type error code representing the overall issue.
:description: Optional. A friendly readable description that describes what happened.
:stackTrace: Optional. The stack trace associated with this error. This should just be a string, if given.
:extraInformation: An object, containing extra values. Both keys and values should be of type string.
*/
async function sendErrorReport(errorCode, description = null, stackTrace = null, extraInformation = null) {
    if(!authenticated) {
        // If not authenticated, we are unable to send the report; we should keep track of it some other way.
        // TODO: perhaps print a log to disk?
        com.logger.warn(`Failed to sendErrorReport, this instance is not authenticated.`);
        return;
    }
    // Assemble a report object.
    let errorReport = {
        errorCode: errorCode,
        friendlyDescription: description,
        stackTrace: stackTrace,
        extraInformation: extraInformation
    };
    // Convert to JSON.
    let errorReportJson = JSON.stringify(errorReport);
    // Attempt to, if required, save this to disk.
    await saveOutgoingPayload(errorReportJson);
    // Now perform a request and return the response.
    return await axiosInstance.post("/api/worker/error", errorReportJson, {
        headers: {"Content-Type": "application/json"}
    })
    .then(response => response.data)
    .catch((err) => {
        com.logger.debug(`Failed to send error report! Error: ${err}`);
        throw err;
    });
}

/*
Perform shutdown operations on any open connections to the master server. This involves currently disconnecting the socket,
if it is substantial, and also connected. Also, this function will signal the server IF we are currently authenticated AND
the calling code is allowing us to.

Arguments
---------
:reason: A textual reason for the shutdown.
:shouldSignal: If false, no 'shutdown' signal will be sent to the server prior to disconnection.
:extraShutdownData: A dictionary that will be JSON'ified and sent alongside our shutdown signal.
*/
async function disconnectSlave(reason = "No reason.", shouldSignal = true, extraShutdownData = {}) {
    // First, signal the server, if we are allowed.
    if(authenticated && shouldSignal) {
        com.logger.debug(`Reporting our shutdown to the master server...`);
        // Assemble a shutdown signal object, using 'reason' as a base.
        let shutdownSignalData = {
            reason: reason
        };
        // Copy all objects in extra shutdown data to this object.
        Object.assign(shutdownSignalData, extraShutdownData || {});
        // Send the signal.
        await sendSignal("shutdown", shutdownSignalData);
    }
    // Next, shutdown the socket if need be.
    if(socket && socket.connected) {
        com.logger.debug(`Disconnecting slave SocketIO instance...`);
        socket.disconnect();
    }
    authenticated = false;
    socketConnected = false;
}

exports.setupFromOptions = setupFromOptions;
exports.authenticateSlave = authenticateSlave;
exports.sendSignal = sendSignal;
exports.sendHeartbeat = sendHeartbeat;
exports.sendAircraft = sendAircraft;
exports.requestTraceHistoryWork = requestTraceHistoryWork;
exports.requestTrackerTargets = requestTrackerTargets;
exports.sendTraceHistory = sendTraceHistory;
exports.disconnectSlave = disconnectSlave;
exports.reportAircraftTimeout = reportAircraftTimeout;
