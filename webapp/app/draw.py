import geopandas
import geojson
import random


def points_to_geojson(points, texts):
    # Now, we will construct a Features list.
    features = []
    # Iterate a zip for the points.
    for point, text in zip(points, texts):
        # Create a point feature.
        geojson_point = geojson.Point((point[0], point[1],))
        polygon_feature = geojson.Feature(
            properties = {
                "name": text
            },
            geometry = geojson_point)
        features.append(polygon_feature)
    # Now a feature collection.
    feature_collection = geojson.FeatureCollection(features)
    # And dump return.
    return geojson.dumps(feature_collection, indent = 4)


def lines_to_geojson(lines):
    # Now, we will construct a Features list.
    features = []
    # Iterate a zip for the polys.
    for line in lines:
        # Create a line feature.
        geojson_line = geojson.LineString(list(line.coords))
        polygon_feature = geojson.Feature(
            properties = {
                "stroke": "#%06x" % random.randint(0, 0xFFFFFF)
            },
            geometry = geojson_line)
        features.append(polygon_feature)
    # Now a feature collection.
    feature_collection = geojson.FeatureCollection(features)
    # And dump return.
    return geojson.dumps(feature_collection, indent = 4)


def polygons_to_geojson_1(polygons, names, crs):
    zones_polygons = geopandas.GeoSeries(polygons, crs = crs)
    if crs != 4326:
        transformed_zones_polygons = zones_polygons.to_crs(4326)
    else:
        transformed_zones_polygons = zones_polygons
    # Now, we will construct a Features list.
    features = []
    # Iterate a zip for the polys.
    for polygon, name in zip(transformed_zones_polygons.geometry, names):
        # Create a polygon feature.
        geojson_polygon = geojson.Polygon([list(polygon.exterior.coords)])
        polygon_feature = geojson.Feature(
            properties = {
                "name": name
            },
            geometry = geojson_polygon
        )
        features.append(polygon_feature)
    # Now a feature collection.
    feature_collection = geojson.FeatureCollection(features)
    # And dump return.
    return geojson.dumps(feature_collection, indent = 4)


def polygons_to_geojson(polygons, crs, **kwargs):
    names = kwargs.get("names", None)

    zones_polygons = geopandas.GeoSeries(polygons, crs = crs)
    if crs != 4326:
        transformed_zones_polygons = zones_polygons.to_crs(4326)
    else:
        transformed_zones_polygons = zones_polygons
    # Now, we will construct a Features list.
    features = []
    # Iterate a zip for the polys.
    for polygon in transformed_zones_polygons.geometry:
        # Create a polygon feature.
        geojson_polygon = geojson.Polygon([list(polygon.exterior.coords)])
        polygon_feature = geojson.Feature(
            properties = {
                "stroke": "#ff0000",
                "stroke-width": 2,
                "stroke-opacity": 1,
                "fill": "#ff0000",
                "fill-opacity": 0.5
            },
            geometry = geojson_polygon)
        features.append(polygon_feature)
    # Now a feature collection.
    feature_collection = geojson.FeatureCollection(features)
    # And dump return.
    return geojson.dumps(feature_collection, indent = 4)


def multi_polygons_to_geojson(multi_polygons, crs, **kwargs):
    names = kwargs.get("names", None)

    # Now, we will construct a Features list.
    features = []
    # Iterate a zip for the polys.
    for multi_polygon in multi_polygons:
        for polygon in multi_polygon.geoms:
            # Create a polygon feature.
            geojson_polygon = geojson.Polygon([list(polygon.exterior.coords)])
            polygon_feature = geojson.Feature(
                properties = {
                    "stroke": "#ff0000",
                    "stroke-width": 2,
                    "stroke-opacity": 1,
                    "fill": "#ff0000",
                    "fill-opacity": 0.5
                },
                geometry = geojson_polygon)
            features.append(polygon_feature)
    # Now a feature collection.
    feature_collection = geojson.FeatureCollection(features)
    # And dump return.
    return geojson.dumps(feature_collection, indent = 4)
