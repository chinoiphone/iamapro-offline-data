#!/usr/bin/env python3
"""Prueba un paquete publicado igual que lo usa la app: lo descarga del
catálogo, revisa el manifiesto y los archivos, busca (con la misma consulta
y el mismo orden que USOfflineSearch.swift) y, con VALHALLA_CONTAINER, pide
rutas a Valhalla 3.8.3 solo con valhalla_tiles.tar, como la app.

Uso: validate_pack.py <estado> <carpeta de trabajo> <resultado.json>
"""
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import subprocess
import sys
import unicodedata
import zipfile

CAPITALS = {
    "alabama": "Montgomery", "alaska": "Juneau", "arizona": "Phoenix", "arkansas": "Little Rock",
    "california/norcal": "Sacramento", "california/socal": "Los Angeles", "colorado": "Denver",
    "connecticut": "Hartford", "delaware": "Dover", "district-of-columbia": "Washington",
    "florida": "Tallahassee", "georgia": "Atlanta", "hawaii": "Honolulu", "idaho": "Boise",
    "illinois": "Springfield", "indiana": "Indianapolis", "iowa": "Des Moines", "kansas": "Topeka",
    "kentucky": "Frankfort", "louisiana": "Baton Rouge", "maine": "Augusta", "maryland": "Annapolis",
    "massachusetts": "Boston", "michigan": "Lansing", "minnesota": "Saint Paul",
    "mississippi": "Jackson", "missouri": "Jefferson City", "montana": "Helena",
    "nebraska": "Lincoln", "nevada": "Carson City", "new-hampshire": "Concord",
    "new-jersey": "Trenton", "new-mexico": "Santa Fe", "new-york": "Albany",
    "north-carolina": "Raleigh", "north-dakota": "Bismarck", "ohio": "Columbus",
    "oklahoma": "Oklahoma City", "oregon": "Salem", "pennsylvania": "Harrisburg",
    "puerto-rico": "San Juan", "rhode-island": "Providence", "south-carolina": "Columbia",
    "south-dakota": "Pierre", "tennessee": "Nashville", "texas": "Austin",
    "us-virgin-islands": "Charlotte Amalie", "utah": "Salt Lake City", "vermont": "Montpelier",
    "virginia": "Richmond", "washington": "Olympia", "west-virginia": "Charleston",
    "wisconsin": "Madison", "wyoming": "Cheyenne",
}
# Juneau no tiene carretera a ningún otro sitio: la ruta larga sale de Anchorage.
LONG_ROUTE_FROM = {"alaska": "Anchorage"}
# Islas: puede que no haya ninguna ciudad lejana unida por carretera.
ISLANDS = {"hawaii", "us-virgin-islands", "puerto-rico", "district-of-columbia", "rhode-island", "delaware"}

# Las mismas abreviaturas que USOfflineSearch.abbreviationPairs.
ABBREVIATION_PAIRS = [
    ("st", "street"), ("ave", "avenue"), ("av", "avenue"), ("blvd", "boulevard"),
    ("rd", "road"), ("dr", "drive"), ("ln", "lane"), ("ct", "court"), ("pl", "place"),
    ("pkwy", "parkway"), ("hwy", "highway"), ("cir", "circle"), ("ter", "terrace"),
    ("terr", "terrace"), ("trl", "trail"), ("sq", "square"), ("expy", "expressway"),
    ("fwy", "freeway"), ("sw", "southwest"), ("se", "southeast"), ("nw", "northwest"),
    ("ne", "northeast"), ("n", "north"), ("s", "south"), ("e", "east"), ("w", "west"),
    ("mt", "mount"), ("ft", "fort"), ("intl", "international"), ("apt", "apartment"),
]
ABBREVIATIONS = {}
for _short, _long in ABBREVIATION_PAIRS:
    ABBREVIATIONS.setdefault(_short, []).append(_long)
    ABBREVIATIONS.setdefault(_long, []).append(_short)
SHORT_FORMS = {short for short, _ in ABBREVIATION_PAIRS}
LONG_TO_SHORT = {}
for _short, _long in ABBREVIATION_PAIRS:
    LONG_TO_SHORT.setdefault(_long, _short)
SHORT_TO_LONG = {}
for _short, _long in ABBREVIATION_PAIRS:
    SHORT_TO_LONG.setdefault(_short, _long)

SAMPLES = 30


def words(text):
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return "".join(c if c.isalnum() else " " for c in folded).split()


def fts_query(text):
    groups = []
    for word in words(text)[:8]:
        variants = [word] + ABBREVIATIONS.get(word, [])
        terms = [f'"{v}"' if v in SHORT_FORMS or len(v) == 1 else f'"{v}"*' for v in variants]
        groups.append(terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")")
    return " AND ".join(groups) or None


def expanded(text):
    return " ".join(SHORT_TO_LONG.get(w, w) for w in words(text))


def abbreviated(text):
    return " ".join(LONG_TO_SHORT.get(w, w) for w in words(text))


def meters(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def key(row):
    return f"{row['name'].lower()}|{int(row['lat'] * 2000)}|{int(row['lon'] * 2000)}"


# USOfflineSearch.placeProminence
PLACE_PROMINENCE = {
    "place=city": 45, "place=town": 35, "place=village": 22, "place=suburb": 15,
    "place=hamlet": 10, "place=neighbourhood": 10, "place=quarter": 10, "place=island": 10,
}
COLUMNS = "p.id, p.name, p.kind, p.category, p.lat, p.lon"


def name_score(name, wanted):
    """USOfflineSearch.nameScore: 0 igual, 10 empieza igual, 18 todas las
    palabras, 30 solo en la dirección o la ciudad."""
    name = expanded(name)
    if name == wanted:
        return 0
    if name.startswith(wanted):
        return 10
    name_words = name.split()
    if all(any(n.startswith(w) for n in name_words) for w in wanted.split()):
        return 18
    return 30


def search(db, text, lat, lon, limit=25):
    """USOfflineSearch.search: lo más cercano, lo más parecido del estado y
    las ciudades con ese nombre; el parecido del nombre más la distancia."""
    query = fts_query(text)
    if not query:
        return []
    wanted = expanded(text)
    match = f"- {{city}} : ({query})" if len(words(text)) == 1 else query
    cosine = max(math.cos(math.radians(lat)), 0.2)
    lat_delta, lon_delta = 0.5, 0.5 / cosine
    towns = ", ".join(f"'{t}'" for t in sorted(PLACE_PROMINENCE))
    rows = db.execute(
        f"""SELECT {COLUMNS} FROM places_fts f JOIN places p ON p.id = f.rowid
        WHERE places_fts MATCH ? AND p.lat BETWEEN ? AND ? AND p.lon BETWEEN ? AND ?
        ORDER BY (p.lat - ?) * (p.lat - ?) + (p.lon - ?) * (p.lon - ?) * ? LIMIT 200""",
        (match, lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta,
         lat, lat, lon, lon, cosine * cosine),
    ).fetchall()
    rows += db.execute(
        f"""SELECT {COLUMNS} FROM places_fts f JOIN places p ON p.id = f.rowid
        WHERE places_fts MATCH ? ORDER BY bm25(places_fts, 10.0, 4.0, 1.0) LIMIT 100""",
        (match,),
    ).fetchall()
    rows += db.execute(
        f"""SELECT {COLUMNS} FROM places_fts f JOIN places p ON p.id = f.rowid
        WHERE places_fts MATCH ? AND p.kind = 0 AND p.category IN ({towns})
        ORDER BY lower(p.name) = ? DESC, bm25(places_fts, 10.0, 4.0, 1.0) LIMIT 40""",
        (f"name : ({query})", text.strip().lower()),
    ).fetchall()
    found = []
    for rid, name, kind, category, plat, plon in rows:
        row = {"id": rid, "name": name, "kind": kind, "category": category, "lat": plat, "lon": plon}
        text_score = name_score(name, wanted)
        prominence = PLACE_PROMINENCE.get(category, 0) if text_score <= 10 else 0
        distance = meters(lat, lon, plat, plon)
        found.append((text_score + math.log10(max(distance, 50) / 50) * 12 - prominence, row))
    found.sort(key=lambda item: item[0])
    seen, results = set(), []
    for _, row in found:
        if key(row) in seen:
            continue
        seen.add(key(row))
        results.append(row)
        if len(results) == limit:
            break
    return results


def offset(lat, lon, distance, rng):
    angle = rng.uniform(0, 2 * math.pi)
    dlat = distance * math.cos(angle) / 111_000
    dlon = distance * math.sin(angle) / (111_000 * max(math.cos(math.radians(lat)), 0.2))
    return lat + dlat, lon + dlon


def found_in(results, target, user_lat, user_lon, top=10):
    """Está entre los primeros, o hay otro con el mismo nombre más cerca de
    quien busca (otra sucursal de la misma cadena: eso también vale)."""
    target_key = key(target)
    target_distance = meters(user_lat, user_lon, target["lat"], target["lon"])
    wanted = expanded(target["name"])
    for row in results[:top]:
        if key(row) == target_key:
            return True
        if expanded(row["name"]) == wanted and meters(user_lat, user_lon, row["lat"], row["lon"]) <= target_distance:
            return True
    return False


def sample_rows(db, rng, where, count):
    top = db.execute("SELECT max(id) FROM places").fetchone()[0] or 0
    rows, attempts = [], 0
    while len(rows) < count and attempts < count * 200:
        attempts += 1
        row = db.execute(
            f"SELECT id, name, kind, category, lat, lon FROM places WHERE id = ? AND {where}",
            (rng.randint(1, top),),
        ).fetchone()
        if row:
            rows.append({"id": row[0], "name": row[1], "kind": row[2], "category": row[3], "lat": row[4], "lon": row[5]})
    return rows


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Report:
    def __init__(self, state):
        self.state = state
        self.checks = []
        self.info = {}

    def check(self, name, ok, detail=""):
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("OK   " if ok else "FAIL ") + name + (f": {detail}" if detail else ""), flush=True)
        return ok

    @property
    def ok(self):
        return all(item["ok"] for item in self.checks)


def valhalla(container, config, costing, origin, destination):
    request = {
        "locations": [{"lat": origin[0], "lon": origin[1]}, {"lat": destination[0], "lon": destination[1]}],
        "costing": costing,
        "units": "kilometers",
        "directions_options": {"language": "es-ES"},
    }
    if costing == "auto":
        request["alternates"] = 2
    run = subprocess.run(
        ["docker", "exec", container, "valhalla_service", config, "route", json.dumps(request)],
        capture_output=True, text=True, timeout=300,
    )
    output = run.stdout.strip()
    start = output.find("{")
    try:
        return json.loads(output[start:]) if start >= 0 else {"error": run.stderr[-300:]}
    except ValueError:
        return {"error": (output or run.stderr)[-300:]}


def route_check(report, container, config, costing, origin, candidates, label, required=True):
    """La primera ruta que sale entre `origin` y los candidatos (en orden)."""
    tried = []
    for name, lat, lon, distance in candidates[:5]:
        answer = valhalla(container, config, costing, origin[1:], (lat, lon))
        trip = answer.get("trip")
        if trip and trip.get("status") == 0 and trip["summary"]["length"] > 0:
            summary = trip["summary"]
            maneuvers = sum(len(leg.get("maneuvers", [])) for leg in trip.get("legs", []))
            alternates = len(answer.get("alternates", []))
            detail = (f"{origin[0]} → {name}: {summary['length']:.1f} km, {summary['time'] / 60:.0f} min, "
                      f"{maneuvers} indicaciones" + (f", {alternates} alternativas" if costing == "auto" else ""))
            return report.check(f"ruta {label}", maneuvers > 0, detail)
        tried.append(f"{name} ({distance / 1000:.0f} km): {answer.get('error') or answer.get('error_code')}")
    if not candidates:
        # Un estado pequeño (Connecticut, Delaware…) no tiene ciudades tan lejos.
        return report.check(f"ruta {label}", True, "sin ciudades a esa distancia en el estado")
    return report.check(f"ruta {label}", not required, "; ".join(tried)[:400])


def places_around(db, lat, lon, low, high, target, where):
    span = high / 111_000
    lon_span = high / (111_000 * max(math.cos(math.radians(lat)), 0.2))
    rows = db.execute(
        f"SELECT name, lat, lon FROM places WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND {where}",
        (lat - span, lat + span, lon - lon_span, lon + lon_span),
    ).fetchall()
    out = []
    for name, plat, plon in rows:
        distance = meters(lat, lon, plat, plon)
        if low <= distance <= high:
            out.append((name, plat, plon, distance))
    out.sort(key=lambda item: abs(item[3] - target))
    return out


def find_city(db, name, lat, lon):
    for row in search(db, name, lat, lon)[:5]:
        if expanded(row["name"]) == expanded(name) and row["category"] in ("place=city", "place=town", "place=village"):
            return row
    return None


def main():
    state, work, result_path = sys.argv[1:4]
    catalog_path = os.environ.get("CATALOG", "catalog.json")
    container = os.environ.get("VALHALLA_CONTAINER")
    region = "us-" + state.replace("/", "-")
    report = Report(state)
    rng = random.Random(state)
    os.makedirs(work, exist_ok=True)

    try:
        with open(catalog_path, encoding="utf-8") as handle:
            entry = next((p for p in json.load(handle)["packs"] if p["id"] == region), None)
        if not report.check("en el catálogo", entry is not None, region):
            return finish(report, result_path)
        report.info.update({"version": entry["version"], "zip": entry["byteCount"],
                            "installed": entry["installedByteCount"], "counts": entry["counts"]})

        # 1. Descarga: tamaño y huella, como USOfflineMaps antes de instalar.
        archive = os.path.join(work, f"{region}.iamapack.zip")
        subprocess.run(["curl", "-fsSL", "--retry", "5", "-o", archive, entry["url"]], check=True)
        size = os.path.getsize(archive)
        report.check("descarga: tamaño", size == entry["byteCount"], f"{size:,} bytes")
        report.check("descarga: huella sha256", sha256(archive) == entry["sha256"])

        with zipfile.ZipFile(archive) as zipped:
            bad = zipped.testzip()
            report.check("zip sano", bad is None, bad or "")
            zipped.extractall(work)
        os.remove(archive)
        pack = os.path.join(work, region)

        # 2. Manifiesto y archivos.
        with open(os.path.join(pack, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        report.check("manifiesto", manifest.get("schemaVersion") == 2 and manifest.get("regionID") == region
                     and manifest.get("dataRevision") and manifest.get("counts") == entry["counts"],
                     f"v{manifest.get('packVersion')}")
        for item in manifest.get("files", []):
            path = os.path.join(pack, item["relativePath"])
            exists = os.path.exists(path)
            report.check(f"archivo {item['relativePath']}",
                         exists and os.path.getsize(path) == item["byteCount"] and sha256(path) == item["sha256"],
                         f"{item['byteCount']:,} bytes")
        installed = sum(item["byteCount"] for item in manifest.get("files", []))
        report.check("tamaño instalado = catálogo", installed == entry["installedByteCount"])

        # 3. Búsqueda.
        db = sqlite3.connect(os.path.join(pack, "search", "search.sqlite"))
        quick = db.execute("PRAGMA quick_check").fetchone()[0]
        report.check("base de búsqueda sana", quick == "ok", quick)
        counts = dict(db.execute("SELECT kind, count(*) FROM places GROUP BY kind").fetchall())
        expected = entry["counts"]
        report.check("cuentas = catálogo",
                     counts.get(0, 0) == expected["places"] + expected.get("overture_added", 0)
                     and counts.get(1, 0) == expected["streets"] and counts.get(2, 0) == expected["addresses"],
                     f"{counts.get(0, 0):,} lugares ({expected.get('overture_added', 0):,} de Overture), "
                     f"{counts.get(1, 0):,} calles, {counts.get(2, 0):,} direcciones")
        columns = [row[1] for row in db.execute("PRAGMA table_info(places)")]
        report.check("columna de direcciones", "address" in columns)
        with_phone = db.execute("SELECT count(*) FROM places WHERE kind = 0 AND phone IS NOT NULL").fetchone()[0]
        with_web = db.execute("SELECT count(*) FROM places WHERE kind = 0 AND website IS NOT NULL").fetchone()[0]
        report.info["phones"], report.info["websites"] = with_phone, with_web

        bounds = entry["bounds"]
        center = ((bounds["minimumLatitude"] + bounds["maximumLatitude"]) / 2,
                  (bounds["minimumLongitude"] + bounds["maximumLongitude"]) / 2)

        def inside(lat, lon):
            return (bounds["minimumLatitude"] <= lat <= bounds["maximumLatitude"]
                    and bounds["minimumLongitude"] <= lon <= bounds["maximumLongitude"])

        capital_name = CAPITALS[state]
        capital = find_city(db, capital_name, *center)
        if capital:
            capital_detail = f"{capital['category']} {capital['lat']:.4f},{capital['lon']:.4f}"
        else:
            # Para ver por qué: lo que salió y las filas que se llaman así.
            top = [f"{r['name']} ({r['category']})" for r in search(db, capital_name, *center)[:5]]
            same = db.execute(
                "SELECT category, lat, lon FROM places WHERE name = ? AND kind = 0 LIMIT 6", (capital_name,)
            ).fetchall()
            capital_detail = f"salió: {', '.join(top)}; con ese nombre: {same}"
        if report.check(f"buscar «{capital_name}»", capital is not None, capital_detail):
            report.check("la capital dentro del área del paquete", inside(capital["lat"], capital["lon"]))

        def sampled(label, rows, query_of, threshold):
            hits, misses = 0, []
            for row in rows:
                user = offset(row["lat"], row["lon"], 3_000, rng)
                text = query_of(row)
                if found_in(search(db, text, *user), row, *user):
                    hits += 1
                else:
                    misses.append(text)
            share = hits / len(rows) if rows else 0
            report.check(label, rows and share >= threshold,
                         f"{hits}/{len(rows)}" + (f"; no salió: {', '.join(misses[:4])}" if misses else ""))

        places = sample_rows(db, rng, "kind = 0 AND length(name) >= 3", SAMPLES)
        sampled("negocios al azar (a 3 km)", places, lambda row: row["name"], 0.9)
        addresses = sample_rows(db, rng, "kind = 2", SAMPLES)
        sampled("direcciones al azar (a 3 km)", addresses, lambda row: row["name"], 0.9)
        sampled("direcciones abreviadas («N Main St»)", addresses, lambda row: abbreviated(row["name"]), 0.85)
        sample_points = sample_rows(db, rng, "kind = 0", 300)
        share_inside = sum(inside(r["lat"], r["lon"]) for r in sample_points) / max(len(sample_points), 1)
        report.check("los lugares caen dentro del área", share_inside >= 0.97, f"{share_inside:.0%}")

        if capital:
            fuel = places_around(db, capital["lat"], capital["lon"], 0, 15_000, 0, "kind = 0 AND category = 'amenity=fuel'")
            report.check("gasolineras a 15 km de la capital", len(fuel) > 0, f"{len(fuel)}")

        # 4. Rutas, solo con valhalla_tiles.tar (como la app).
        if container and capital:
            tar = os.path.join(pack, "routing", "valhalla_tiles.tar")
            config = os.path.join(work, "valhalla-test.json")
            built = subprocess.run(
                ["docker", "exec", container, "valhalla_build_config",
                 "--mjolnir-tile-extract", tar, "--mjolnir-tile-dir", os.path.join(work, "no-tiles")],
                capture_output=True, text=True, check=True,
            )
            with open(config, "w", encoding="utf-8") as handle:
                handle.write(built.stdout)

            origin = (capital_name, capital["lat"], capital["lon"])
            towns = "kind = 0 AND category IN ('place=city', 'place=town', 'place=village')"
            medium = places_around(db, capital["lat"], capital["lon"], 8_000, 80_000, 30_000, towns)
            route_check(report, container, config, "auto", origin, medium, "en carro (~30 km)")
            route_check(report, container, config, "motorcycle", origin, medium, "en moto (~30 km)")
            short = places_around(db, capital["lat"], capital["lon"], 1_000, 3_000, 2_000, "kind = 0")
            route_check(report, container, config, "pedestrian", origin, short, "a pie (~2 km)")
            route_check(report, container, config, "bicycle", origin, short, "en bicicleta (~2 km)")

            from_name = LONG_ROUTE_FROM.get(state)
            start = find_city(db, from_name, *center) if from_name else capital
            if start:
                long_origin = (from_name or capital_name, start["lat"], start["lon"])
                far = places_around(db, start["lat"], start["lon"], 150_000, 700_000, 300_000,
                                    "kind = 0 AND category IN ('place=city', 'place=town')")
                route_check(report, container, config, "auto", long_origin, far, "larga en carro (~300 km)",
                            required=state not in ISLANDS)
        db.close()
    except Exception as error:  # noqa: BLE001 — cualquier fallo es un resultado
        report.check("sin errores", False, f"{type(error).__name__}: {error}")
    return finish(report, result_path)


def finish(report, result_path):
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump({"state": report.state, "ok": report.ok, "checks": report.checks, "info": report.info},
                  handle, ensure_ascii=False, indent=2)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
