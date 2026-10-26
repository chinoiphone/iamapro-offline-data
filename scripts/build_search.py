#!/usr/bin/env python3
"""La búsqueda sin conexión de un estado: lugares, calles y direcciones de
OpenStreetMap en SQLite con FTS5 (sin acentos), para IAmapro.

Uso: build_search.py <named.geojsonseq> <search.sqlite> <stats.json>

Entra la salida de `osmium export` de lo que tiene nombre o número de casa.
Cada fila: tipo (0 lugar, 1 calle, 2 dirección), nombre, categoría
(«amenity=restaurant»), número, calle, ciudad, código postal, coordenada y,
de los lugares, teléfono, web y horario.
"""
import json
import math
import os
import sqlite3
import sys

POI_KEYS = [
    "amenity", "shop", "tourism", "leisure", "office", "craft", "healthcare",
    "historic", "aeroway", "railway", "public_transport", "natural", "sport",
    "club", "emergency", "man_made", "place", "building",
]
SKIP_RAILWAY = {"rail", "abandoned", "disused", "razed", "light_rail", "subway", "tram", "narrow_gauge"}
SKIP_NATURAL = {"wood", "scrub", "wetland", "grassland", "tree_row", "water", "coastline"}
CITY_PLACES = {"city", "town", "village", "hamlet"}
STREET_SKIP = {"bus_stop", "platform", "elevator", "street_lamp", "crossing", "traffic_signals"}

POI, STREET, ADDRESS = 0, 1, 2


def center(geometry):
    kind = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return None
    if kind == "Point":
        return coords[1], coords[0]
    if kind == "LineString":
        lon, lat = coords[len(coords) // 2]
        return lat, lon
    if kind == "Polygon":
        ring = coords[0]
    elif kind == "MultiPolygon":
        ring = max((polygon[0] for polygon in coords), key=len)
    else:
        return None
    lat = sum(point[1] for point in ring) / len(ring)
    lon = sum(point[0] for point in ring) / len(ring)
    return lat, lon


def records(path):
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            try:
                feature = json.loads(line)
            except ValueError:
                continue
            point = center(feature.get("geometry") or {})
            if point is None:
                continue
            yield feature.get("properties") or {}, point


class CityIndex:
    """La ciudad más cercana (de los `place=city/town/village/hamlet`)."""

    def __init__(self):
        self.cells = {}

    def add(self, name, lat, lon, rank):
        key = (int(lat * 5), int(lon * 5))
        self.cells.setdefault(key, []).append((name, lat, lon, rank))

    def nearest(self, lat, lon):
        best = None
        base = (int(lat * 5), int(lon * 5))
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                for name, plat, plon, rank in self.cells.get((base[0] + dlat, base[1] + dlon), []):
                    dx = (plon - lon) * math.cos(math.radians(lat))
                    dy = plat - lat
                    # Las ciudades «pesan» más que los caseríos.
                    score = math.hypot(dx, dy) / rank
                    if best is None or score < best[0]:
                        best = (score, name)
        return best[1] if best and best[0] < 0.25 else None


def poi_category(tags):
    for key in POI_KEYS:
        value = tags.get(key)
        if not value or value == "no":
            continue
        if key == "railway" and value in SKIP_RAILWAY:
            continue
        if key == "natural" and value in SKIP_NATURAL:
            continue
        return f"{key}={value}"
    return None


def trimmed_bounds(latitudes, longitudes):
    """El rectángulo del estado sin los puntos sueltos (boyas, barcos o
    errores en el mar): del 0,02 % al 99,98 % de las coordenadas, con un
    margen de unos 2 km."""
    if not latitudes:
        return [0.0, 0.0, 0.0, 0.0]
    lats = sorted(latitudes)
    lons = sorted(longitudes)
    # Alaska cruza el meridiano 180: sin las Aleutianas del otro lado, el
    # rectángulo daba la vuelta al mundo.
    if lons[-1] - lons[0] > 90:
        lons = [lon for lon in lons if lon < 0] or lons
    cut = max(int(len(lats) * 0.0002), 0)
    margin = 0.02
    return [lats[cut] - margin, lons[cut] - margin,
            lats[-1 - cut] + margin, lons[-1 - cut] + margin]


def main():
    source, target, stats_path = sys.argv[1:4]

    cities = CityIndex()
    rank = {"city": 4.0, "town": 2.5, "village": 1.5, "hamlet": 1.0}
    for tags, (lat, lon) in records(source):
        if tags.get("place") in CITY_PLACES and tags.get("name"):
            cities.add(tags["name"], lat, lon, rank[tags["place"]])

    if os.path.exists(target):
        os.remove(target)
    db = sqlite3.connect(target)
    db.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        CREATE TABLE places (
            id INTEGER PRIMARY KEY,
            kind INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            housenumber TEXT,
            street TEXT,
            -- «3555 Southwest 8th Street»: lo que se busca como dirección
            -- (el número de casa de un lugar también).
            address TEXT,
            city TEXT,
            postcode TEXT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            phone TEXT,
            website TEXT,
            opening_hours TEXT
        );
        CREATE TABLE info (key TEXT PRIMARY KEY, value TEXT);
        """
    )

    seen_streets = set()
    seen_addresses = set()
    counts = {"places": 0, "streets": 0, "addresses": 0}
    latitudes = []
    longitudes = []
    rows = []

    def flush():
        db.executemany(
            "INSERT INTO places (kind, name, category, housenumber, street, city, postcode, lat, lon, phone, website, opening_hours) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        rows.clear()

    for tags, (lat, lon) in records(source):
        name = (tags.get("name") or "").strip()
        housenumber = (tags.get("addr:housenumber") or "").strip()
        street = (tags.get("addr:street") or "").strip()
        city = (tags.get("addr:city") or "").strip() or None
        postcode = (tags.get("addr:postcode") or "").strip() or None
        highway = tags.get("highway")
        is_way = tags.get("@type") == "way"

        latitudes.append(lat)
        longitudes.append(lon)

        if name and highway and is_way and highway not in STREET_SKIP:
            key = (name.lower(), round(lat, 2), round(lon, 2))
            if key in seen_streets:
                continue
            seen_streets.add(key)
            rows.append((STREET, name, f"highway={highway}", None, None,
                         city or cities.nearest(lat, lon), postcode, lat, lon, None, None, None))
            counts["streets"] += 1
        elif name:
            category = poi_category(tags)
            if category is None:
                continue
            if category.startswith("building=") and not housenumber:
                # Un edificio con nombre pero sin nada más: solo los importantes.
                if tags.get("building") in ("yes", "house", "residential", "garage", "shed", "roof"):
                    continue
            rows.append((POI, name, category, housenumber or None, street or None,
                         city or cities.nearest(lat, lon), postcode, lat, lon,
                         tags.get("phone") or tags.get("contact:phone"),
                         tags.get("website") or tags.get("contact:website"),
                         tags.get("opening_hours")))
            counts["places"] += 1
        elif housenumber and street:
            key = (housenumber.lower(), street.lower(), round(lat, 3), round(lon, 3))
            if key in seen_addresses:
                continue
            seen_addresses.add(key)
            rows.append((ADDRESS, f"{housenumber} {street}", None, housenumber, street,
                         city or cities.nearest(lat, lon), postcode, lat, lon, None, None, None))
            counts["addresses"] += 1

        if len(rows) >= 50_000:
            flush()
    flush()

    # Los negocios de Overture Maps que le faltan a OpenStreetMap.
    release = os.environ.get("OVERTURE_RELEASE")
    if release:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import overture
        counts.update(overture.merge(db, trimmed_bounds(latitudes, longitudes), release))
        db.commit()

    db.executescript(
        """
        UPDATE places SET address = NULLIF(trim(coalesce(housenumber || ' ', '') || coalesce(street, '')), '');
        CREATE VIRTUAL TABLE places_fts USING fts5(
            name, address, city,
            content = 'places', content_rowid = 'id',
            tokenize = "unicode61 remove_diacritics 2",
            detail = column
        );
        INSERT INTO places_fts (places_fts) VALUES ('rebuild');
        CREATE INDEX places_lat_lon ON places (lat, lon);
        CREATE INDEX places_kind_category ON places (kind, category);
        """
    )
    bounds = trimmed_bounds(latitudes, longitudes)
    db.executemany("INSERT INTO info (key, value) VALUES (?, ?)", [
        ("schema", "1"),
        ("bounds", json.dumps({"minimumLatitude": bounds[0], "minimumLongitude": bounds[1],
                               "maximumLatitude": bounds[2], "maximumLongitude": bounds[3]})),
        ("counts", json.dumps(counts)),
    ])
    db.commit()
    db.execute("VACUUM")
    db.close()

    with open(stats_path, "w", encoding="utf-8") as handle:
        json.dump({"counts": counts, "bounds": {
            "minimumLatitude": bounds[0], "minimumLongitude": bounds[1],
            "maximumLatitude": bounds[2], "maximumLongitude": bounds[3],
        }}, handle)
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
