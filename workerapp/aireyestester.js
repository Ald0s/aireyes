const puppeteer = require("puppeteer-extra");
const StealthPlugin = require("puppeteer-extra-plugin-stealth");
puppeteer.use(StealthPlugin());

const conf = require("./conf");
const com = require("./com");
const master = require("./master");
const error = require("./error");
const database = require("./database");

/* Should boot Chromium? */
const SHOULD_BOOT_CHROMIUM = false;
/* Slave override. So we can debug slave mode with default values. */
const SHOULD_BE_SLAVE = true;
/* Default map center location. Set to null to disable. */
const DEFAULT_MAP_CENTER = [];
/* View restriction override. This will ensure the viewport is always zoomed in on a small area - to minimise bandwidth use. */
const SHOULD_RESTRICT_VIEW = true;
/* This will override DEFAULT_MAP_CENTER only if SHOULD_RESTRICT_VIEW is set to true. */
const RESTRICT_VIEW_OVERRIDE = [];
/* Submission log creation override. If true, every single JSON message sent to the server is also written to a file. */
const SHOULD_SAVE_PAYLOADS = false;

/* An object that will hold the parsed command from the server. */
var slaveCommand = {};
/* Was this worker initialised from a master server? If this evaluates true, this will enable attempted reverse communication. */
var isSlave = false;
/* If true, this will save all payloads being sent to the server, to a file. */
var shouldSavePayloads = false;

/*
Parse run args.
Start with TWO extra arg; the worker name, worker type to execute manually.
Start with ONE extra arg; a base64 encoded command string to run as a slave worker.
*/
const runArgs = process.argv.slice(2);
if(runArgs.length === 2) {
    isSlave = false || SHOULD_BE_SLAVE;
    shouldSavePayloads = false || SHOULD_SAVE_PAYLOADS;

    slaveCommand.name = runArgs[0];
    slaveCommand.worker_type = runArgs[1];
} else if(runArgs.length === 1) {
    isSlave = true || SHOULD_BE_SLAVE;
    // Decode run args 0 from base64, then parse from JSON and set result to slaveCommand.
    let decodedJSON = atob(runArgs[0]);
    slaveCommand = JSON.parse(decodedJSON);
    console.log(slaveCommand);
    // Now, read some values from the slave command.
    shouldSavePayloads = (slaveCommand.should_save_payloads || false) || SHOULD_SAVE_PAYLOADS;
} else {
    console.log("Incorrect number of arguments to test!\nCorrect args: # node <filename> [worker-name]\nOR\n# node <filename> slave [base64 command]");
    return;
}

if(isSlave === true) {
    console.log(`Starting radar worker as a slave. We will attempt reverse communication to master server...`);
} else {
    console.log(`Manually starting radar worker, we will disable reverse communication...`);
}

var WORKER_NAME = slaveCommand.name || "tungstented";
var WORKER_UNIQUE_ID = slaveCommand.unique_id || "9771dd61b1ac906d1181c6054125c558";
var WORKER_TYPE = slaveCommand.worker_type || "aircraft-tracker";
var CONNECTION_MODE = slaveCommand.connection_mode || "restful";
var PHONE_HOME_URL = slaveCommand.phone_home_url || "http://127.0.0.1:5000";
var RUN_HEADLESS = slaveCommand.run_headless || false;
var START_FULLSCREEN = true && (!(slaveCommand.run_headless || false));
var PROXIES = (slaveCommand.proxy_url_list || []).length > 0 ? JSON.parse(slaveCommand.proxy_url_list) : [];
var USE_PROXY = PROXIES.length > 0 && (slaveCommand.use_proxy || false);


async function shutdownSlave(browser, reason, shouldSignal = true, extraShutdownData = {}) {
    if(SHOULD_BOOT_CHROMIUM && browser !== undefined) {
        await browser.close()
    }

    console.warn(`AirEyes tracker shutdown initiated, reason: ${reason}`);
    await master.disconnectSlave(reason, shouldSignal, extraShutdownData);
}

(async () => {
    let browser;
    let page;
    try {
        // Before we even consider starting Puppeteer, we must establish connection with our master; only if its required however.
        if(isSlave === true) {
            console.log(`We're running in slave mode. We will need to connect to our master to continue, phone home URL: ${PHONE_HOME_URL}`);
            let [axiosInstance, socket] = await master.authenticateSlave(WORKER_NAME, WORKER_UNIQUE_ID, PHONE_HOME_URL)
                .catch((err) => {
                    console.error(`Failed to connect to master server. Killing process!`);
                    throw new error.ShutdownRequestError("couldnt-authenticate-save");
                });
            // Attach some listeners to the socket, so we can react accordingly.
            socket.on("disconnect", (reason) => {
                console.log(`Socket connection to master closed: ${reason}`);
                if(reason === "transport close") {
                    // This means the server has gone away by itself. We'll simply kill this script.
                    new Promise((resolve, reject) => {
                        return shutdownSlave(browser, "Server has closed the connection. (It has probably been shutdown.)", false)
                            .then(() => {
                                process.kill(process.pid, "SIGINT");
                                resolve();
                            });
                    });
                }
            });
        }

        // Setup the master module.
        await master.setupFromOptions({
            identifier: WORKER_NAME,
            shouldSavePayloads: shouldSavePayloads
        });

        let puppArgs = {};
        puppArgs.headless = RUN_HEADLESS;

        if(SHOULD_BOOT_CHROMIUM) {
            browser = await puppeteer.launch(puppArgs);
            page = await browser.newPage();
        }

        if(isSlave) {
            // Signal server that we've started up. Failing that, we should kill the client.
            await master.sendSignal("initialised")
                .catch((err) => {
                    throw new error.ShutdownRequestError("couldnt-signal-server");
                });
        }

        var shouldRunTest = true;

        process.on('SIGINT', function() {
            shouldRunTest = false;
            process.exit();
        });

        var numSecondsHeartbeatCounter = 0;
        while(shouldRunTest) {
            // Await a timeout to allow event loop to continue.
            await new Promise(resolve => setTimeout(resolve, 2 * 1000));
            numSecondsHeartbeatCounter += 2;
            if(numSecondsHeartbeatCounter >= 20) {
                numSecondsHeartbeatCounter = 0;
                // At the end of the timeout, send a heartbeat signal to the master, if required, letting it know we're alive.
                await master.sendHeartbeat();
            }
        }
        console.log(`Program complete. Shutting down.`);
        await shutdownSlave(browser, "complete");
    } catch(err) {
        if(err instanceof error.GeneralError) {
            /* TODO: send general error. */
            console.error("General error encountered.");
        } else if(err instanceof error.ShutdownRequestError) {
            // This is a shutdown request error. We'll simply the shutdown routine.
            await shutdownSlave(browser, err.reason);
        } else {
            /* TODO: send general error about unknown. */
            console.error("Unknown error encountered.");
            console.error(err);
        }
    }
})();
