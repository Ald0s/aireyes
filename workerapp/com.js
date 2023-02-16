const {createLogger, format, transports} = require("winston");
const {combine, timestamp, label, printf, errors, splat, colorize, simple, json} = format;

const dateFormat = require("dateformat");
const fs = require("fs").promises;
const fss = require("fs");
const path = require("path");
const process = require("process");
const conf = require("./conf");

const defaultFormat = printf(({ level, message, label, timestamp }) => {
    return `${timestamp} ${level}: ${message}`;
});

// Create a new Winston logger.
const logger = createLogger({
    level: "info",
    defaultMeta: { service: "aireyes" },
    format: combine(
        timestamp({ format: 'YYYY-MM-DD HH:mm:ss' }),
        errors({ stack: true }),
        json()
    ),
    transports: [
        new transports.File({ filename: path.join(conf.LOG_DIRECTORY, "aireyes-slave-error.log"), level: "error" }),
        new transports.File({ filename: path.join(conf.LOG_DIRECTORY, "aireyes-slave.log") })
    ]
});

// If our environment is not Production, we'll log to console (as well.)
if(process.env.NODE_ENV !== "production") {
    logger.level = "debug";
    logger.add(new transports.Console({
        format: combine(
            timestamp({ format: 'YYYY-MM-DD HH:mm:ss' }),
            colorize(),
            label(),
            defaultFormat
        )
    }));
}

function dateToUtc(date) {
    /*
    Here we've added a change that will first convert the date to a UTC string and return it.
    This is just for the 'date' column on FlightPoint, for comparisons.
    */
    return dateFormat(date, "UTC:yyyy-mm-dd");
}

/*
https://stackoverflow.com/a/35890537
*/
function formatTime(seconds) {
  const dtFormat = new Intl.DateTimeFormat('en-US', {
    dateStyle: 'full',
    timeStyle: 'long',
    hour12: true,
    timeZone: conf.CURRENT_TIMEZONE
  });

  return dtFormat.format(new Date(seconds * 1000));
}

/*
Save the given decoded binary update to disk. This will also create a log indicating when this was saved.
*/
async function saveDecodedBinaryUpdate(decodedBinaryUpdate) {
    let currentTime = Date.now();
    let currentDateTime = new Date(currentTime);
    // Setup the output filename and a directory to the binary updates directory.
    let outputFilename = `bin-update-${currentTime}.json`;
    let outputRelativeDirectory = path.join("exports", "binary_updates");
    logger.debug(`Saving decoded binary update received at ${currentDateTime.toLocaleString()} under filename '${outputFilename}'`);
    // Convert the decoded binary update to JSON.
    let decodedBinaryUpdateJson = JSON.stringify(decodedBinaryUpdate);
    // Now, write out.
    await writeFileAsUtf8(outputRelativeDirectory, outputFilename, decodedBinaryUpdateJson);
}

/*
Cool one liner.
https://stackoverflow.com/a/57708635
*/
const fileExists = async path => !!(await fs.stat(path).catch(e => false));

/*
*/
async function readAllFilesFromDirectory(directory) {
    return await fs.readdir(path.join(process.cwd(), directory));
}

/*
Reads the file from the given directory as binary, and returns a buffer containing the contents.
*/
async function readFileAsUtf8(directory, fileName) {
    let absolutePath = path.join(process.cwd(), directory, fileName);
    if(!(await fileExists(absolutePath))) {
        throw `The requested file; ${fileName} does not exist!`;
    }
    let contentBuffer = await fs.readFile(absolutePath, "utf8");
    return contentBuffer;
}

/*
Reads the file from the given directory as binary, and returns a buffer containing the contents.
*/
async function readFileAsBinary(directory, fileName) {
    let absolutePath = path.join(process.cwd(), directory, fileName);
    if(!(await fileExists(absolutePath))) {
        throw `The requested file; ${fileName} does not exist!`;
    }
    let contentBuffer = await fs.readFile(absolutePath);
    return contentBuffer;
}

/*

*/
async function writeFileAsUtf8(directory, fileName, content) {
    // Ensure all directories are created.
    const absoluteDirectory = path.join(process.cwd(), directory);
    await fs.mkdir(absoluteDirectory, {
        recursive: true
    });
    // Assemble the complete destination file and write content out.
    const destinationFile = path.join(absoluteDirectory, fileName);
    await fs.writeFile(destinationFile, content, "utf8");
    return true;
}

/*

*/
async function writeFileAsBinary(directory, fileName, content) {
    // Ensure all directories are created.
    const absoluteDirectory = path.join(process.cwd(), directory);
    await fs.mkdir(absoluteDirectory, {
        recursive: true
    });
    // Assemble the complete destination file and write content out.
    const destinationFile = path.join(absoluteDirectory, fileName);
    await fs.writeFile(destinationFile, content);
    return true;
}

/*
Ensure the document hasn't changed such that any library or component we require has disappeared.
If this returns false, adjustments may be needed to keep tracking VICPOL.
*/
async function verifyCompatibleEnvironment(page) {
    let verificationResult = await page.evaluate(() => {
        if(typeof OLMap === "undefined" || !OLMap) {
            return false;
        } else if(typeof ol.proj === "undefined" || !ol.proj) {
            return false;
        } else if(typeof selectPlaneByHex === "undefined" || !selectPlaneByHex) {
            return false;
        }
        return true;
    });

    if(verificationResult == true) {
        logger.debug(`Environment has been successfully verified as operable.`);
        return true;
    } else {
        logger.debug(`Could not verify environment for operability!`);
        return false;
    }
}

async function setMapParameters(page, latitude, longitude, zoom) {
    return await page.evaluate((lat, long, zm) => {
        // Transform coordinates.
        let coordTransform = ol.proj.fromLonLat([long, lat]);
        // Set zoom & coordinates.
        OLMap.getView().setZoom(zm);
        OLMap.getView().setCenter(coordTransform);
        return true;
    }, latitude, longitude, zoom);
}

exports.logger = logger;
exports.dateToUtc = dateToUtc;
exports.formatTime = formatTime;

exports.saveDecodedBinaryUpdate = saveDecodedBinaryUpdate;

exports.readAllFilesFromDirectory = readAllFilesFromDirectory;
exports.readFileAsUtf8 = readFileAsUtf8;
exports.readFileAsBinary = readFileAsBinary;
exports.writeFileAsUtf8 = writeFileAsUtf8;
exports.writeFileAsBinary = writeFileAsBinary;

exports.fileExists = fileExists;
exports.verifyCompatibleEnvironment = verifyCompatibleEnvironment;
exports.setMapParameters = setMapParameters;
