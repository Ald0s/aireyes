import { io } from "./socketio/socket.io.esm.min.js";

let aircraftSocket;

let monitoredAircraft = {
    // Internal dictionary for augmenting aircraft HTML elements.
    aircraft: {},

    /*
    Add an aircraft, given an ICAO and an HTML element that represents the aircraft's card to the monitored aircraft system.
    If the aircraft's ICAO already exists, no action is taken.
    */
    monitorAircraft: function(icao, aircraftCard) {
        let aircraftCardParent = aircraftCard.parent();
        let gridParent = aircraftCardParent.parent();

        if(icao in this.aircraft) {
            console.warn(`Did not monitor aircraft ${icao}, it is already being monitored!`);
            throw new Error("aircraft-already-monitored");
        }
        // Add to this list.
        console.log(`Monitoring aircraft with icao ${icao}!`);
        this.aircraft[icao] = {
            aircraftIcao: icao,
            gridParent: gridParent,
            aircraftCard: aircraftCard,
            aircraftCardParent: aircraftCardParent,
            loadingPanel: aircraftCard.find(".loading"),
            statisticsPanel: aircraftCard.find(".statistics"),

            aircraftImage: aircraftCard.find(".aircraft-img"),

            flightTimeHoursDisplay: aircraftCard.find(".flight-time-total-hours-all"),
            flightTimeHours: aircraftCard.find(".flight-time-total-hours"),
            flightTimeMinutes: aircraftCard.find(".flight-time-total-minutes"),
            distanceTravelled: aircraftCard.find(".distance-travelled"),
            fuelUsed: aircraftCard.find(".fuel-used"),

            latestSummary: null,
            isUnlocked: false,

            isActive: false,
            timeoutId: null,

            /* Should be gallons. */
            updateFuelUsed: function(fuelUsed) {
                let roundedFuelUsed = Math.round(fuelUsed);
                // Set fuel used.
                $(this.fuelUsed).text(roundedFuelUsed.toLocaleString("en-US"));
            },

            /* This is given in meters. So remember to div by 1000. */
            updateDistanceTravelled: function(totalDistanceTravelled) {
                // If totalDistanceTravelled is 0 or null, just set total rounded to 0.
                let roundedTotalDistanceTravelled = (totalDistanceTravelled === null || totalDistanceTravelled === 0) ? 0 :
                    Math.round(totalDistanceTravelled/1000);
                // Set distance travelled.
                $(this.distanceTravelled).text(roundedTotalDistanceTravelled.toLocaleString("en-US"));
            },

            /* */
            updateFlightTime: function(flightTimeTotalMinutes) {
                let roundedTotalFlightTime = Math.round(flightTimeTotalMinutes);
                // Get number of hours, floor total minutes div 60.
                let totalHours = Math.floor(roundedTotalFlightTime/60);
                // Get number of minutes, floor total minutes modulo 60.
                let totalMinutes = Math.floor(roundedTotalFlightTime%60);
                // If we have 0 total hours, set flight time hours display to gone.
                if(totalHours === 0) {
                    $(this.flightTimeHoursDisplay).addClass("d-none");
                } else {
                    $(this.flightTimeHoursDisplay).removeClass("d-none");
                    // Set flight time hours.
                    $(this.flightTimeHours).text(totalHours.toLocaleString("en-US"));
                }
                // Set total minutes irrespective.
                $(this.flightTimeMinutes).text(totalMinutes.toLocaleString("en-US"));
            },

            /* Pass true to set this card as active; no fade on image and active badge is present. Otherwise, image is faded and active badge is gone. */
            setActive: function(newIsActive) {
                if(newIsActive && !this.isActive) {
                    // Move it up!
                    this.gridParent.prepend(this.aircraftCardParent);
                    // Set active.
                    this.aircraftCard.addClass("active");
                } else if(!newIsActive && this.isActive) {
                    // From active to inactive.
                    if(this.aircraftCard.hasClass("active")) {
                        // Get number of active (minus this aircraft, since we are confirmed active). If 0, do nothing.
                        let activeAircraftCards = this.gridParent.find(".aircraft-item.active");
                        let numActive = activeAircraftCards.length;
                        if(numActive-1 === 0) {
                            console.log(`No need to move newly inactive aircraft ${this.aircraftIcao}, no other aircraft are active.`);
                        } else {
                            // Otherwise, move this aircraft card parent to after the last active aircraft.
                            let lastActiveCard = $(activeAircraftCards[numActive-1]);
                            console.log(`Moving aircraft ${this.aircraftIcao}, it is now inactive.`);
                            this.aircraftCardParent.insertAfter(lastActiveCard.parent());
                        }
                    }
                    this.aircraftCard.removeClass("active");
                }
                this.isActive = newIsActive;
            },

            /* Pass true to show the loading panel and hide the statistics panel. False for the inverse. */
            setLoading: function(isLoading) {
                if(isLoading) {
                    this.loadingPanel.removeClass("d-none");
                    this.statisticsPanel.addClass("d-none");
                } else {
                    this.loadingPanel.addClass("d-none");
                    this.statisticsPanel.removeClass("d-none");
                }
            },

            updateAircraft: function(aircraftSummary) {
                // Clear the current timeout Id, if its set.
                if(this.timeoutId !== null) {
                    clearTimeout(this.timeoutId);
                }
                // Update each aspect of the summary.
                this.updateFuelUsed(aircraftSummary.totalFuelUsed);
                this.updateDistanceTravelled(aircraftSummary.distanceTravelled);
                this.updateFlightTime(aircraftSummary.flightTimeTotal);
                // If the aircraft is active as per latest summary, set it active, otherwise, inactive.
                this.setActive(aircraftSummary.isActiveNow);
                // Remove the loading panel and instead, set the stats panel to visible.
                this.setLoading(false);
                // Now, set latest summary to aircraft summary.
                this.latestSummary = aircraftSummary;
                let thisAircraft = this;
                // Finally, reactivate the timeout, for 60 seconds; call the other member function aircraftTimedOut as callback.
                this.timeoutId = setTimeout(() => {
                    thisAircraft.aircraftTimedOut();
                }, 60 * 1000);
            },

            aircraftTimedOut: function() {
                console.warn(`Aircraft ${this.aircraftIcao} has timed out. Setting it to inactive.`);
                this.setActive(false);
            }
        };
    },

    /*
    Given an AircraftViewModelSchema object, attempt to unlock the requested aircraft. This will simply set isUnlocked to true, thus
    allowing that aircraft to be updated realtime. The function will then call updateAircraft with the summary object. This function
    will fail if the aircraft is not monitored. If the aircraft is already unlocked, logic will simply skip forward to updating.
    */
    unlockAircraft: function(initialAircraftSummary) {
        let existingAircraft = this.aircraft[initialAircraftSummary.aircraftIcao] || null;
        // If not in aircraft, do not continue with update.
        if(!existingAircraft) {
            console.warn(`Not unlocking aircraft with icao ${initialAircraftSummary.aircraftIcao}, it is not monitored.`);
            throw new Error("aircraft-not-monitored");
        } else if(!existingAircraft.isUnlocked) {
            // If aircraft exists but is not unlocked, we will now unlock it.
            console.log(`Unlocking aircraft; ${initialAircraftSummary.aircraftName} (${initialAircraftSummary.aircraftIcao}), it can now accept realtime updates.`);
            existingAircraft.isUnlocked = true;
        }
        // Now, irrespective, update the aircraft.
        this.updateAircraft(initialAircraftSummary);
    },

    /*
    Given an AircraftViewModelSchema object, update both the corresponding aircraft in this object and the displayed statistics on
    the card. This function will fail if the requested aircraft is not yet unlocked, or is not tracked.
    */
    updateAircraft: function(aircraftSummary) {
        let existingAircraft = this.aircraft[aircraftSummary.aircraftIcao] || null;
        // If not in aircraft, do not continue with update.
        if(!existingAircraft) {
            console.warn(`Not updating aircraft with icao ${aircraftSummary.aircraftIcao}, it is not monitored.`);
            throw new Error("aircraft-not-monitored");
        } else if(!existingAircraft.isUnlocked) {
            // If aircraft exists but is not yet unlocked, do not continue.
            console.warn(`Not updating aircraft ${aircraftSummary.aircraftName} (${aircraftSummary.AircraftIcao}) it is not yet unlocked - still waiting on initial aircraft summary.`);
            throw new Error("aircraft-not-unlocked");
        }
        // Update the aircraft internally.
        existingAircraft.updateAircraft(aircraftSummary);
    }
};

$(() => {
    aircraftSocket = io("/aircraft");

    // Iterate all aircraft item classes, saving each to our monitoring dictionary.
    $(".aircraft-item").each(function(idx) {
        // For each aircraft item, we'll save its document element identified by its hex ID in the dictionary above.
        monitoredAircraft.monitorAircraft(
            $(this).find(".aircraft-icao").val(),
            $(this)
        );
    });

    aircraftSocket.on("connect", () => {
        console.log("Connected to aircraft socket server! We will now receive real-time updates for all aircraft.");
        // On connect, emit aircraft_realtime event, to be added to that room and receive realtime updates.
        console.log(`Requesting real-time aircraft updates`);
        aircraftSocket.emit("aircraft_realtime");
    });

    aircraftSocket.on("aircraft-summary", (aircraftSummaryArray) => {
        // This will be sent by the server on connection to the aircraft namespace. The specific type sent will be an array of serialised AircraftSummary objects.
        // With this, we will unlock an aircraft card for each found and update accordingly. This event is usually triggered by a targeted send, that is, the
        // server will send this directly to a specific client.
        for(let aircraftSummary of aircraftSummaryArray) {
            // Unlock each aircraft and provide an initial update.
            monitoredAircraft.unlockAircraft(aircraftSummary);
        }
    });

    aircraftSocket.on("aircraft-update", (aircraftSummary) => {
        // This will be sent by the server each time an aircraft is updated. The actual type is a single instance of a serialised AircraftSummary object.
        // These events will be sent to the entire realtime aircraft room. We will use this to update the card for the corresponding aircraft.
        monitoredAircraft.updateAircraft(aircraftSummary);
    });

    aircraftSocket.on("aircraft-landed", (aircraftSummary) => {
        // Technically also another aircraft update event, but this is sent only when the server deems an aircraft as having landed.
        monitoredAircraft.updateAircraft(aircraftSummary);
    });

    aircraftSocket.on("connect_error", () => {

    });

    aircraftSocket.on("disconnect", () => {
        console.log("Disconnected from server! Retrying...");
    });
});

window.monitoredAircraft = monitoredAircraft;
