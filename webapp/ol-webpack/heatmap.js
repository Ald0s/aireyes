import { Map, View, proj } from 'ol';
import * as olColor from 'ol/color';
import { Style, Fill, Stroke } from 'ol/style';
import XYZ from 'ol/source/XYZ';
import { Tile as TileLayer, Vector as VectorLayer, Heatmap as HeatmapLayer} from 'ol/layer';
import VectorSource from 'ol/source/Vector';
import * as olProj from 'ol/proj';
import {GeoJSON, KML} from 'ol/format';
import {bbox} from 'ol/loadingstrategy';

var map = null;
var showOnlyAircraft = null;
var isRequestOngoing = false;
var highestNumFlightPoints = 0;

const defaultSuburbStyle = new Style({
    fill: new Fill({
        color: "#ff000050"
    }),
    stroke: new Stroke({
        color: "#ff0000ff",
        width: 4
    })
});

const suburbVectorSource = new VectorSource({
    format: new GeoJSON(),
    loader: function(extent, resolution, projection, success, failure) {
        // If currently ongoing, return.
        if(isRequestOngoing === true) {
            return;
        }
        // Set as request is ongoing. This will stop doubling up of requests.
        isRequestOngoing = true;
        // Get the SRS code for the current projection.
        const proj = projection.getCode();
        // Get the zoom level for the map.
        const zoom = map.getView().getZoom();
        // Determine which aircraft to request a map for. If the list is null we pass all. If the list is empty, we pass none, otherwise, comma separated values from the list.
        let aircraft;
        if(showOnlyAircraft === null) {
            aircraft = "all";
        } else if(showOnlyAircraft === []) {
            aircraft = "none";
        } else {
            aircraft = showOnlyAircraft.join(",");
        }
        // Construct a URL from the projection, zoom and bounding box extent.
        const url = `${window.location.origin}/api/suburbs?srsname=${proj}&bbox=${extent.join(",")},${proj}&zoom=${zoom}&aircraft=${aircraft}`;
        const xhr = new XMLHttpRequest();
        xhr.open("GET", url);
        const onError = function() {
            suburbVectorSource.removeLoadedExtent(extent);
            failure();
            isRequestOngoing = false;
        }
        xhr.onerror = onError;
        xhr.onload = function() {
            if (xhr.status == 200) {
                // Convert response text to JSON.
                let featureCollection = JSON.parse(xhr.responseText);
                // Read from feature collection.
                const features = suburbVectorSource.getFormat().readFeatures(featureCollection);
                // Determine the highest number of points out of all features.
                let sortedFeatures = features.sort((f1, f2) => f2.getProperties().num_points-f1.getProperties().num_points);
                if(sortedFeatures.length !== 0) {
                    // Use the first feature's num_points.
                    highestNumFlightPoints = sortedFeatures[1].getProperties().num_points;
                    console.log(`Read ${features.length} features`);
                    // Now add the new features.
                    suburbVectorSource.addFeatures(features);
                }
                success(features);
                isRequestOngoing = false;
            } else {
                onError();
            }
        }
        xhr.send();
    },
    strategy: bbox
 });

const suburbVectorLayer = new VectorLayer({
    source: suburbVectorSource,
    style: function(feature) {
        // Style the feature according to its properties.
        let properties = feature.getProperties();
        let numFlightPoints = parseInt(properties.num_points);
        let fillColour;
        let strokeColour;
        try {
            let asPercentage = Math.min(numFlightPoints / highestNumFlightPoints, 1);
            if(isNaN(asPercentage) || asPercentage < 0.1) {
                throw new Error("No flight points");
            }
            let rgbAlpha = (Math.round(asPercentage * 255) & 0xFF).toString(16)
            fillColour = `#e93e3a${rgbAlpha}`;
            strokeColour = `#e93e3aff`;
        } catch(e) {
            fillColour = "#ffffff60";
            strokeColour = "#ffffffff";
        }
        return new Style({
            fill: new Fill({
                color: fillColour,
            }),
            stroke: new Stroke({
                color: strokeColour,
                width: 2
            })
        });
    }
});

map = new Map({
    target: "map",
    layers: [
        new TileLayer({
            source: new XYZ({
                url: "https://{a-c}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            })
        }),
        suburbVectorLayer
    ],
    view: new View({
        /* Center the view on Melbourne city. */
        center: [16137422, -4553885],
        zoom: 10
    })
});

function centerMap(long, lat) {
    let transformedCenter = olProj.transform([long, lat], 'EPSG:4326', 'EPSG:3857');
    map.getView().setCenter(transformedCenter);
    map.getView().setZoom(12);
}

$(function() {
    var allAircraftChoices = $("[name='checkShouldShow']");

    function checkShowAircraftChanged() {
        /* TODO 0x09 */
        // Enumerate all those checked. If ALL are checked, set showOnlyAircraft to null. Otherwise, set it to a list of just those that are checked.
        let flightNamesChecked = [];
        $("input[name='checkShouldShow']:checked").each(function(index, checkShowAircraft) {
            let control = $(checkShowAircraft)[0];
            if(control.checked) {
                flightNamesChecked.push(control.value);
            }
        });
        if(flightNamesChecked.length === 0) {
            showOnlyAircraft = [];
        } else if(flightNamesChecked.length === allAircraftChoices.length) {
            showOnlyAircraft = null;
        } else {
            showOnlyAircraft = flightNamesChecked;
        }
        // Trigger a refresh on the suburb vector source.
        suburbVectorSource.refresh();
    }
    // For each, add an onchange listener.
    allAircraftChoices.each(function(index, checkShowAircraft) {
        $(checkShowAircraft).change(function() {
            checkShowAircraftChanged();
        });
    });
    checkShowAircraftChanged();
});
