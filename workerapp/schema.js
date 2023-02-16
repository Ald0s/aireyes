/*
A module for maintaining the schema for our local database.
The system runs a local database to stop the duplication of already stored data.
*/
const { Sequelize, Model, DataTypes } = require("sequelize");

class Aircraft extends Model {}
class FlightPoint extends Model {}


async function initSchemas(sequelize) {
    Aircraft.init({
        icao: {
            type: DataTypes.STRING,
            primaryKey: true
        },
        type: {
            type: DataTypes.STRING,
            allowNull: true
        },
        registration: {
            type: DataTypes.STRING,
            allowNull: true
        },
        flightName: {
            type: DataTypes.STRING,
            allowNull: true
        },
        description: {
            type: DataTypes.STRING,
            allowNull: true
        },
        year: {
            type: DataTypes.INTEGER,
            allowNull: true
        },
        ownerOperator: {
            type: DataTypes.STRING,
            allowNull: true
        },
        isSetup: {
            type: DataTypes.BOOLEAN,
            defaultValue: false
        },
        isActive: {
            type: DataTypes.BOOLEAN,
            defaultValue: false
        },
        lastBinaryUpdate: DataTypes.BIGINT,
        comprehensive: DataTypes.BOOLEAN
    }, {
        sequelize,
        timestamps: false
    });

    FlightPoint.init({
        id: {
            type: DataTypes.INTEGER,
            primaryKey: true,
            autoIncrement: true
        },
        flightPointHash: {
            type: DataTypes.STRING,
            allowNull: false,
            unique: true
        },
        /* Timestamp MUST be in seconds. */
        timestamp: DataTypes.BIGINT,
        /* An ISO format date for comparison, this is a string literal now to avoid complications with timezones. */
        date: DataTypes.STRING,
        latitude: {
            type: DataTypes.FLOAT(13, 11),
            allowNull: true
        },
        longitude: {
            type: DataTypes.FLOAT(14, 11),
            allowNull: true
        },
        altitude: {
            type: DataTypes.INTEGER,
            allowNull: true
        },
        groundSpeed: {
            type: DataTypes.INTEGER,
            allowNull: true
        },
        rotation: {
            type: DataTypes.FLOAT(5, 2),
            allowNull: true
        },
        verticalRate: {
            type: DataTypes.FLOAT(5, 2),
            allowNull: true
        },
        dataSource: {
            type: DataTypes.STRING,
            allowNull: true
        },
        synchronised: {
            type: DataTypes.BOOLEAN,
            defaultValue: false
        },
        isOnGround: DataTypes.BOOLEAN,
        isAscending: DataTypes.BOOLEAN,
        isDescending: DataTypes.BOOLEAN
    }, {
        sequelize,
        timestamps: false
    });

    // An Aircraft can have many Flights.
    Aircraft.hasMany(FlightPoint);
    FlightPoint.belongsTo(Aircraft);
}

exports.initSchemas = initSchemas;
