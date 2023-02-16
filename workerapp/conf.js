require("dotenv").config();
const path = require("path");

/* User adjustable configuration */
const SCAN_PASS_NUM_TIMES = 5
const SCAN_PASS_TIME_BETWEEN_MINUTES = 0.5
const NEW_AIRCRAFT_SCAN_SECONDS = 10
const AIRCRAFT_TIMEOUT = 60; // Seconds.
const CURRENT_TIMEZONE = "Australia/Melbourne"
const MAX_ACCEPTED_NULL_ASSIGNMENT_COUNT = 20;

/* Inner system configuration */
const NODE_ENV = process.env.NODE_END || "development";
const LOG_DIRECTORY = process.env.LOG_DIRECTORY;
const DATABASE_URI = process.env.DATABASE_URI;

const BINARY_UPDATE_BINCRAFT_REGEX = /\/data\/globe_\d+\.binCraft$/;
const BINARY_UPDATE_BINCRAFT_TYPE_REGEX = /\s*application\/zstd\s*/;
const BINARY_UPDATE_ZSTD_BINCRAFT_REGEX = /\/re-api\/\?/;

const BLOCKED_RESOURCE = [
    'quantserve',
    'adzerk',
    'doubleclick',
    'adition',
    'exelator',
    'sharethrough',
    'twitter',
    'google-analytics',
    'fontawesome',
    'facebook',
    'analytics',
    'optimizely',
    'clicktale',
    'mixpanel',
    'zedo',
    'clicksor',
    'tiqcdn',
    'googlesyndication',
    'pub.network',
    'indexww',
    'amazon-adsystem',
    '3lift',
    'openx',
    'bidswitch',
    'ssp.yahoo',
    'criteo',
    'lijit',
    'casalemedia',
    'yieldmo',
    'pubmatic',
    'sharethrough',
    'rubiconproject',
    'adlightning',
    '4dex.io',
];

exports.CURRENT_TIMEZONE = CURRENT_TIMEZONE;
exports.NODE_ENV = NODE_ENV;
exports.DATABASE_URI = DATABASE_URI;
exports.LOG_DIRECTORY = LOG_DIRECTORY;
exports.BLOCKED_RESOURCE = BLOCKED_RESOURCE;

exports.MAX_ACCEPTED_NULL_ASSIGNMENT_COUNT = MAX_ACCEPTED_NULL_ASSIGNMENT_COUNT;
exports.SCAN_PASS_NUM_TIMES = SCAN_PASS_NUM_TIMES;
exports.SCAN_PASS_TIME_BETWEEN_MINUTES = SCAN_PASS_TIME_BETWEEN_MINUTES;
exports.NEW_AIRCRAFT_SCAN_SECONDS = NEW_AIRCRAFT_SCAN_SECONDS;
exports.AIRCRAFT_TIMEOUT = AIRCRAFT_TIMEOUT;

exports.BINARY_UPDATE_BINCRAFT_REGEX = BINARY_UPDATE_BINCRAFT_REGEX;
exports.BINARY_UPDATE_BINCRAFT_TYPE_REGEX = BINARY_UPDATE_BINCRAFT_TYPE_REGEX;
exports.BINARY_UPDATE_ZSTD_BINCRAFT_REGEX = BINARY_UPDATE_ZSTD_BINCRAFT_REGEX;

/*
OLD CONFIGS, WE MAY OR MAY NOT EVER USE THESE AGAIN.
This is essentially just to give us a base for when we write the flight parser on Python side.
*/
/* TODO: Set this to true if we should not consider start->end points that look as though they're in steep descent/could be a take off and landing. */
const OBSCURE_LANDING_SENSITIVITY = false;
const IGNORE_INCOMPLETE_FLIGHTS = true;
/* Number of seconds between flight points to classify that flight as timing out.
In the abscence of any 'ground' updates after an aircraft has landed/taken off, this timeout will be used as a tie breaker. */
const NUM_SECONDS_FLIGHT_TIMEOUT = 10 * 60;
/* The number of hours between segments to consider maximum for same flight. Anything over will imply, in the abscence of ground contact AND vertical rate/altitude checks, a new flight. */
const NUM_HOURS_BETWEEN_SEGMENTS_CONSIDER_NEW_FLIGHT = 4;
/* The minimum number of hours in duration for an obscure layover for OBSCURE LAYOVER LOGIC / OBSCURE LANDING SENSITIVITY LOGIC TO BE APPLIED */
const NUM_HOURS_OBSCURE_LAYOVER = 6;
