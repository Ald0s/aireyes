/*
ADSBExchange offers a helpful global variable called TIMEZONE that will evaluate to the timezone (for the current view?)
Not sure about that last part but lets say we're hanging over melbourne, TIMEZONE evaluates to 'AEST'
*/
const puppeteer = require("puppeteer-extra");
const StealthPlugin = require("puppeteer-extra-plugin-stealth");
puppeteer.use(StealthPlugin());

const conf = require("./conf");
const com = require("./com");
const master = require("./master");
const error = require("./error");
const database = require("./database");

// Two worker type modules.
const tracker = require("./tracker");
const trawler = require("./trawler");

/* Slave override. So we can debug slave mode with default values. */
const SHOULD_BE_SLAVE = true;
/* Default map center location. Can't be null if no override given. By default to the center of Melbourne. */
const DEFAULT_MAP_CENTER = [ -37.813856, 144.964126 ];
/* Default map zoom. Can't be null if no override given. */
const DEFAULT_ZOOM = 10;
/* Submission log creation override. If true, every single JSON message sent to the server is also written to a file. */
const SHOULD_SAVE_PAYLOADS = false;

/* An object that will hold the parsed command from the server. */
var slaveCommand = {};
/* Was this worker initialised from a master server? If this evaluates true, this will enable attempted reverse communication. */
var isSlave = false;
/* If true, this will save all payloads being sent to the server, to a file. */
var shouldSavePayloads = false;
/* This will override SHOULD override DEFAULT_MAP_CENTER only if set to something other than null. Set to null to disable.. By default, somewhere in the ocean south of Australia. */
var restrictViewOverride = [ -39.500563, 141.626005 ];
/* Override zoom on the map. Overrides default zoom, if override view location given. */
var restrictViewZoom = 15;
/* State variable for containing the Sequelize instance. */
var sequelize = null;

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
    // Now, read some values from the slave command.
    shouldSavePayloads = (slaveCommand.should_save_payloads || false) || SHOULD_SAVE_PAYLOADS;
} else {
    com.logger.debug("Incorrect number of arguments to test!\nCorrect args: # node <filename> [worker-name]\nOR\n# node <filename> slave [base64 command]");
    return;
}

if(isSlave === true) {
    com.logger.debug(`Starting radar worker as a slave. We will attempt reverse communication to master server...`);
} else {
    com.logger.debug(`Manually starting radar worker, we will disable reverse communication...`);
}

/*
Slave/master configurable options.
These can be changed by programmer, but will be overriden, if applicable, by configuration set by the master.
*/
var WORKER_NAME = slaveCommand.name || "tungstented";
var WORKER_UNIQUE_ID = slaveCommand.unique_id || "9771dd61b1ac906d1181c6054125c558";
var WORKER_TYPE = slaveCommand.worker_type || "aircraft-tracker";
var CONNECTION_MODE = slaveCommand.connection_mode || "restful";
var PHONE_HOME_URL = slaveCommand.phone_home_url || "http://127.0.0.1:5000";
var RUN_HEADLESS = slaveCommand.run_headless || false;
var START_FULLSCREEN = true && (!(slaveCommand.run_headless || false));
var PROXIES = (slaveCommand.proxy_url_list || []).length > 0 ? JSON.parse(slaveCommand.proxy_url_list) : [];
var USE_PROXY = PROXIES.length > 0 && (slaveCommand.use_proxy || false);

/*
Attempt to locate private module - this contains proxies and ungoogled Chromium.
Configuration mutual with user/master config will be overriden if, and only if, MASTER config is sent.

If you wish to set this up, create a directory 'private' within this directory. Then, create a file 'conf.js'. Within this file, you may declare the following
constant values and export them from the module:

Constant Name               Description                                                                     Example
-------------               -----------                                                                     -------
USE_UNGOOGLED_CHROMIUM      The absolute file path to the ungoogled chromium executable to use.             "/home/<user>/ungoogled-chromium/ungoogled-chromium_<version>_linux/chrome"
PROXIES                     An array of proxy addresses to alternate between.                               [ "http://USERNAME:PASSWORD@DOMAIN:PORT", "http://USERNAME:PASSWORD@DOMAIN:PORT" ]
*/
var privateConf = null;
var useUngoogledChromium = false;
var ungoogledChromiumExecPath = null;

try {
    require.resolve("./private/conf");
    privateConf = require("./private/conf");
    // If we're a slave, do not use private configuration proxies.
    if(!isSlave) {
        USE_PROXY = true && USE_PROXY;
        PROXIES = privateConf.PROXIES;
    }
    useUngoogledChromium = true;
    ungoogledChromiumExecPath = privateConf.UNGOOGLED_CHROMIUM_PATH;
} catch(e) {
    // If we're a slave, do not react to failing to find private configuration.
    if(!isSlave) {
        USE_PROXY = false;
        privateConf = null;
        useUngoogledChromium = false;
    }
}

/*
Initiate the shutdown process for this slave instance. This will close the connection to the master server (if established,)
close the puppeteer browser (if instantiated) and finally, will close the database (if open.)

Arguments
---------
:browser: An instance of Browser.
:reason: The reason for shutdown.
:shouldSignal: Whether or not to send a shutdown signal, and the reason, to the server.
:extraShutdownData: A dictionary that will be JSON'ified and sent alongside out reason (if allowed.)
*/
async function shutdownSlave(browser, reason, shouldSignal = true, extraShutdownData = {}) {
    com.logger.warn(`AirEyes tracker shutdown initiated, reason: ${reason}`);
    // Close down our master communication.
    await master.disconnectSlave(reason, shouldSignal, extraShutdownData);
    // Close the browser.
    if(browser !== undefined) {
        await browser.close();
    }
    // Finally, close the database.
    if(database !== undefined) {
        await database.closeDatabase();
    }
}

(async () => {
    let browser;
    let page;
    try {
        // Before we even consider starting Puppeteer, we must establish connection with our master; only if its required however.
        if(isSlave === true) {
            com.logger.debug(`We're running in slave mode. We will need to connect to our master to continue, phone home URL: ${PHONE_HOME_URL}`);
            let [axiosInstance, socket] = await master.authenticateSlave(WORKER_NAME, WORKER_UNIQUE_ID, PHONE_HOME_URL)
                .catch((err) => {
                    com.logger.error(`Failed to connect to master server. Killing process!`);
                    throw new error.ShutdownRequestError("couldnt-authenticate-slave");
                });
            // Attach some listeners to the socket, so we can react accordingly.
            socket.on("disconnect", (reason) => {
                if(reason === "transport close") {
                    // This means the server has gone away by itself. We'll simply kill this script.
                    return new Promise((resolve, reject) => {
                        return shutdownSlave(browser, "Server has closed the connection. (It has probably been shutdown.)", false)
                            .then(() => {
                                process.kill(process.pid, "SIGINT");
                                resolve();
                            });
                    });
                }
            });
        }

        let puppArgs = [];
        // If we want to use a proxy, grab a random proxy URL from the array and create an anonymised proxy.
        if(USE_PROXY && PROXIES.length > 0) {
            newProxyUrl = await proxyChain.anonymizeProxy(PROXIES[ Math.floor(Math.random() * PROXIES.length) ]);
            // Give to args.
            puppArgs.push(`--proxy-server=${newProxyUrl}`);
        }
        // If we wish to start full-screen, push the command line arg for it.
        if(START_FULLSCREEN) {
            puppArgs.push("--start-fullscreen");
        }

        let puppLaunchArgs = {
            headless: RUN_HEADLESS,
            args: puppArgs
        };

        // Do we want to use Ungoogled Chromium? If so, push our executable path.
        if(useUngoogledChromium && ungoogledChromiumExecPath !== null) {
            puppLaunchArgs.executablePath = ungoogledChromiumExecPath;
            puppLaunchArgs.ignoreHTTPSErrors = true;
            puppLaunchArgs.ignoreDefaultArgs = [ "--enable-automation" ];
        }

        // Setup the master module.
        await master.setupFromOptions({
            identifier: WORKER_NAME,
            shouldSavePayloads: shouldSavePayloads
        });
        // Setup our database.
        sequelize = await database.createDatabase();
        // Create new browser.
        browser = await puppeteer.launch(puppLaunchArgs);
        page = await browser.newPage();

        // Enable request interception, then add a request listener. If we locate any service with a URL containing a blocked service, do not allow that request.
        await page.setRequestInterception(true);
        page.on("request", (request) => {
            if (conf.BLOCKED_RESOURCE.some(resource => request.url().indexOf(resource) !== -1)) {
                request.abort();
            } else {
                request.continue();
            }
        });
        await page.goto("https://globe.adsbexchange.com/", {
            waitUntil: "domcontentloaded"
        });
        // Ensure we have a compatible environment.
        if(!(await com.verifyCompatibleEnvironment(page))) {
            throw new error.ShutdownRequestError( "incompatible-environment");
        }
        let mapCenter;
        let targetZoom;
        if(restrictViewOverride == null || restrictViewOverride == undefined) {
            // Now, set our zoom to a reasonable 10 and center our view over the target location, which is right now Melbourne.
            mapCenter = DEFAULT_MAP_CENTER;
            targetZoom = DEFAULT_ZOOM;
        } else {
            mapCenter = restrictViewOverride;
            targetZoom = restrictViewZoom;
        }
        // Now, setup our map parameters.
        if(!await com.setMapParameters(page, mapCenter[0], mapCenter[1], targetZoom)) {
            throw new error.ShutdownRequestError("failed-set-map-parameters");
        }
        // Wait a moment for new planes to download.
        await page.waitForTimeout(2500);
        if(isSlave) {
            // Signal server that we've started up. Failing that, we should kill the client.
            await master.sendSignal("initialised")
                .catch((err) => {
                    throw new error.ShutdownRequestError("couldnt-signal-server");
                });
        }

        // Now, based on our worker type, we'll execute one of our two type modules.
        if(WORKER_TYPE == "aircraft-tracker") {
            com.logger.debug(`Worker type is set as Aircraft tracker - initialising logic.`);
            await tracker.runTracker(browser, page, {
                slaveCommand: slaveCommand,
                isSlave: isSlave
            });
        } else if(WORKER_TYPE == "history-trawler") {
            com.logger.debug(`Worker type is set as History trawler - initialising logic.`);
            await trawler.runHistoryTrawler(browser, page, {
                slaveCommand: slaveCommand,
                isSlave: isSlave
            });
        } else {
            // Unknown type.
            throw new Error(`Unknown slave type: ${WORKER_TYPE}`);
        }
        com.logger.debug(`Program complete. Shutting down.`);
        await shutdownSlave(browser, "complete");
    } catch(err) {
        if(err instanceof error.GeneralError) {
            /* TODO: send general error. */
            com.logger.error("General error encountered.");
            await shutdownSlave(browser, "GENERAL");
        } else if(err instanceof error.ShutdownRequestError) {
            // This is a shutdown request error. We'll simply the shutdown routine.
            await shutdownSlave(browser, err.reason);
        } else {
            /* TODO: send general error about unknown. */
            com.logger.error("Unknown error encountered.");
            com.logger.error(err);
            await shutdownSlave(browser, err.cause);
        }
    }
})();
