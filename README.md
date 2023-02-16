## AirEyes - Realtime Aircraft Tracker

A small(ish?) proof of concept application designed to trace, in realtime, multiple aircraft of the programmers' choosing and publicise the data acquired in a meaningful way. The project is not totally complete as it is; various features, touch ups, fixes and most critically; a real purpose are required to face a production environment. The project, as it stands, is not a user friendly experience and my publication of the codebase is therefore not to release a marketable product, but instead to showcase the work already complete. You're therefore welcome to use the codebase however you please, assuming you adhere to dependency licensing requirements as well.

![Home](/meta/home.png)

![Flights](/meta/flights.png)

### Data Acquisition Features
* Home page, lists realtime enabled displays for each aircraft being tracked; this includes dynamic base statistics. Supports changing active/inactive state in response to aircraft updates,
* Flights page, paginates all flights from all tracked aircraft thus far. Planned to have this page present as dynamic too, but not implemented,
* View flights per aircraft,
* View aggregate information for each aircraft; not implemented but all data required is available,
* Statistics & Analysis. All potential data required is available, but nothing substantial is actually shown right now.
* Heatmap; a deprecated feature designed for a separate purpose, but I have left it in. Aircraft frequency is shown by a breakdown of Suburb. The more an aircraft frequents a suburb, the darker shade the suburb is. You may find the Suburb data for all of Australia in webapp/imports under suburbs.tgz.

### System Overview
The system is split between two primary components; a server, written with Flask Microframework and one or more worker applications, written with NodeJS. A breakdown of the responsibilities for each component is just below.

#### Server Component
* Base configuration identifying target aircraft and other parameters related to their tracking,
* The receipt and acknowledgement of raw flight data; in the form of EPSG:4326 coordinates along with other point-by-point data such as speed, altitude and rotation,
* The correction and transformation of raw locational data into meaningful information that can identify and describe individual flight instances,
* The association, where possible, of flights with airports best meeting takeoff/landing determination criteria,
* Interpolate, where applicable, missing data to ensure flights are as accurate or inaccurate as the programmer wishes based on configurable criteria,
* The coordination of worker applications to ensure realtime data feed is continually active, and missing data is automatically followed up,
* Support realtime communication with public users by means of socket.io; thus ensuring updates by aircraft are as accurately timed as possible,
* Generate and display webpages showing the required data such as realtime activity feeds, processed flight histories etc.

#### Worker Application \#1: Tracker
* Enumerating and constantly observing the required list of aircraft; and efficiently handling appearances/updates by multiple aircraft simultaneously,
* Maintaining a local database of already acknowledged data to minimise unnecessary bandwidth use,
* Identifying and reporting critical changes of state to observed aircraft such as disappearance, potential landing etc.

#### Worker Application \#2: Trawler
* Upon request by server, looking up and submitting data from a particular time.


### Technologies
The project uses a wide range of technologies and third party packages to meet its objectives. Below is a list (incomplete) of technologies used by both the server and worker applications.

#### Server technologies
* Flask Microframework
* SQLAlchemy (+ asyncio extension)
* SQLite 3 (+ SpatiaLite)
* PostgreSQL (+ PostGIS enabled)
* SocketIO (Flask-SocketIO)
* Geoalchemy2
* Geopandas
* Geo/TopoJSON

#### Client technologies
* Puppeteer
* Sequelize
* Jasmine

### Algorithms / How does it work?
There are many systems/algorithms in use throughout the project. I hope to also include these at some stage.

#### Flight determination
My primary research objective was to explore ways of correctly and efficiently construct and report reliable flight information given only loosely coupled inputs. To this end, a highly summarised explanation of the algorithm I have come up with is below.

![Flight determination algorithm](/meta/flight_determination.png)

### Setup / Usage
As previously mentioned, the system is not yet ready to be used and should only be used as a reference, inspiration or showcase project. That said, I will provide some basic steps that are critical to your ability to at least boot the project, but some mind-reading required.

1. Update secret/individual server configuration keys in app/config.py
    ```python
    # Set SECRET_KEY to something random.
    SECRET_KEY = "SOMETHING RANDOM"

    ...

    # Set NODE_EXECUTABLE_PATH to an absolute path to your Node executable.
    NODE_EXECUTABLE_PATH = "/absolute/path/to/node/executable"

    ...

    # Set (where applicable, if using actual database) database URI variables.
    SQLALCHEMY_DATABASE_URI = "postgresql+psycopg2://USERNAME:PASSWORD@localhost:5432/aireyes_test"
    AIOSQLALCHEMY_DATABASE_URL = "postgresql+asyncpg://USERNAME:PASSWORD@localhost:5432/aireyes_test"
    ```
2. Configure Aircraft to track/fuel figures for each aircraft type.

    In directory imports/ locate file named 'aircraft_states.json'. Modify/add aircraft as desired.
    Once done, if you'd like fuel figures tracked for that aircraft, you'll need to modify 'aircraft_fuel.json' as well, and adjust those settings accordingly. The aircraftType and aircraftYear are the associating attributes between the aircraft and fuel figures. Check out the 'Aircraft' model in webapp/app/models.py for information on what unit/type each variable should be in these JSON files.
3. Create a file '.env' in workerapp/ directory to house the following environment variables (make sure to adjust them appropriately!)...
    ```
    NODE_ENV=development
    DATABASE_URI=sqlite:exports/aireyes.db
    LOG_DIRECTORY=/absolute/path/to/logging/dir
    ```
6. Optionally setup a private configuration for WORKER APPLICATION.
    1. Head to directory workerapp/
    2. Create a new directory private/
    3. Inside private, create a new file 'conf.js'
    4. Definition should be in the form of...
    ```
    const PROXIES = [
        "http://USERNAME:PASSWORD@DOMAIN:PORT",
        ...
    ];

    const UNGOOGLED_CHROMIUM_PATH = "/absolute/path/to/ungoogled/chromium/executable";

    exports.PROXIES = PROXIES;
    exports.UNGOOGLED_CHROMIUM_PATH = UNGOOGLED_CHROMIUM_PATH;
    ```
    Warning! It is difficult to find an ungoogled chromium that actually works with Puppeteer. There's a lot of version matching involved.
7. Setup webapp.
    ```
    # Go to webapp.
    $ cd webapp/

    # Install packages via pipenv.
    $ pipenv install --python 3.8

    # Run all tests.
    $ ./test.sh

    # Startup options.
    # Run non-production, live development will use a local PostgreSQL db, development will use an SQLite file.
    $ ./run.sh [LiveDevelopment/Development]
    # Run production (obviously, not advised). An example gunicorn config using eventlet is provided, which will be used by boot.sh
    $ ./boot.sh
    ```
8. Setup worker application.
    ```
    # Go to worker app.
    $ cd ../workerapp

    # Install packages.
    $ npm install

    # Run tests.
    $ npm tests
    ```
9. Running Webapp manage.py
    ```
    # Go to webapp.
    $ cd webapp/

    # Exec commands in this form.
    pipenv run flask <cmd and args>
    ```

### Third party data source disclaimer
The system utilises the incredible ADSBExchange system as its primary data source. This is a research project with a focus on aircraft flight data and thus, not in either commercial or consistent private use. If ADSB decides to break/change/secure the mechanisms by which data is acquired, I will not publish components for overcoming this. Ideally, for ongoing use of the ADSB system (particularly in an automated manner) you should follow their policies closely https://www.adsbexchange.com/data/.
