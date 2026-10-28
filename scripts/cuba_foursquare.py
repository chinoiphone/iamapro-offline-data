#!/usr/bin/env python3
"""Los lugares de Foursquare (FSQ Open Source Places) que le faltan al
paquete de Cuba (cuba-core): paladares, restaurantes, hoteles, casas
particulares, bares, cafeterías, playas, museos… Se suman a la búsqueda sin
repetir los que ya vienen de OpenStreetMap (el mismo nombre cerca) y sin la
basura que trae Foursquare en Cuba: check-ins de vuelos y cruceros, nombres
en tailandés puestos en La Habana, calles y barrios (ya están).

Uso: cuba_foursquare.py <cuba-core.iamapack.zip> <nuevo.iamapack.zip>

Con FSQ_HF_TOKEN (un token «Read» de Hugging Face de una cuenta que aceptó las
condiciones en huggingface.co/datasets/foursquare/fsq-os-places) usa la versión
más nueva de Foursquare (o FSQ_RELEASE, p. ej. 2026-10-15). Sin token, la copia
pública de source.coop de 2025-02-06, que no pide cuenta. Licencia Apache 2.0
© Foursquare Labs, Inc. (va en NOTICE.txt).
"""
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
import zipfile

HF_TOKEN = os.environ.get("FSQ_HF_TOKEN", "").strip()
HF_DATASET = "foursquare/fsq-os-places"
CUBA = (19.6, -85.1, 23.4, -74.0)  # sur, oeste, norte, este

# Lo que no es un lugar al que ir, o ya viene de OpenStreetMap.
SKIPPED = (
    "States and Municipalities", "Travel and Transportation > Road", "Cruise", "Boat or Ferry",
    "Residential Building", "Housing Development", "Structure", "Transportation Service",
    "Airport Gate", "Airport Lounge", "Baggage", "Bus Stop", "Platform", "Event",
    # Los vuelos («Vuelo LATAM LA 2411», «Copa Flight CM437 HAV-PTY») y las bases.
    "Plane", "Moving Target", "Military",
)
# Los negocios cambian mucho en Cuba: solo los que Foursquare vio hace poco.
# Lo que dura (un museo, una playa, un hotel) vale aunque sea más viejo.
FRESH_SINCE = "2019"
LASTING = ("Landmarks and Outdoors", "Arts and Entertainment > Museum", "Lodging",
           "Education", "Hospital", "Spiritual Center", "Government Building",
           "Performing Arts Venue", "Transport Hub")


def category(labels):
    """La categoría del paquete de Cuba (`OfflineSearchCategory` en la app)."""
    text = " | ".join(labels or [])
    rules = [
        ("Fuel Station", "gasStation"), ("Gas Station", "gasStation"), ("Parking", "parking"),
        ("ATM", "atm"), ("Bank", "bank"), ("Pharmacy", "pharmacy"), ("Drugstore", "pharmacy"),
        ("Hospital", "hospital"), ("Medical Center", "hospital"), ("Emergency", "hospital"),
        ("Police", "police"), ("University", "university"), ("College", "university"),
        ("School", "school"), ("Government Building", "government"), ("Embassy", "government"),
        ("City Hall", "government"), ("Lodging", "hotel"),
        ("Cafe, Coffee", "cafe"), ("Dessert", "cafe"), ("Bakery", "cafe"), ("Ice Cream", "cafe"),
        ("Juice Bar", "cafe"), ("Night Club", "other"), ("Dining and Drinking > Bar", "other"),
        ("Restaurant", "restaurant"), ("Dining and Drinking", "restaurant"),
        ("Farm", "other"), ("Landmarks and Outdoors", "landmark"), ("Museum", "landmark"),
        ("Performing Arts Venue", "landmark"), ("Art Gallery", "landmark"), ("Theater", "landmark"),
        ("Spiritual Center", "landmark"), ("Airport", "landmark"), ("Bus Station", "landmark"),
        ("Train Station", "landmark"),
    ]
    for key, value in rules:
        if key in text:
            return value
    return "other"


# Hasta dónde un lugar con el mismo nombre es el mismo: los grandes (una
# playa, un parque) tienen el punto de OpenStreetMap lejos del de Foursquare.
def same_spot_radius(labels):
    text = " | ".join(labels or [])
    if any(word in text for word in ("Beach", "Island", "Park", "Nature", "Garden", "Mountain", "Lake")):
        return 2_500
    if any(word in text for word in ("University", "College", "Hospital", "Airport", "Resort", "Plaza",
                                     "Historic", "Harbor", "Marina", "Stadium", "Zoo", "Castle", "Fort",
                                     "Lighthouse")):
        return 1_200
    if any(word in text for word in ("Landmarks and Outdoors", "Lodging", "Museum", "Spiritual Center",
                                     "Performing Arts Venue", "Government Building")):
        return 400
    return 150


def is_latin(name):
    """Letras del español y vecinas (á, ñ, ç, ö…): sin «izm-İT» ni tailandés."""
    return all(not char.isalpha() or char.isascii() or "\u00c0" <= char <= "\u00ff" for char in name)


# «El Guajirito, La Habana, Cuba» → «El Guajirito».
PLACE_SUFFIX = re.compile(r"(?i)\b(cuba|la habana|habana|havana|vedado|municipio|varadero|holgu[ií]n)\b")


def clean_name(name):
    parts = [part.strip() for part in name.strip().rstrip(".").split(",")]
    while len(parts) > 1 and PLACE_SUFFIX.search(parts[-1]):
        parts.pop()
    return ", ".join(parts)


def normalized(text):
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^\w]+", " ", text)
    skip = {"el", "la", "los", "las", "de", "del", "y", "the", "of", "restaurante", "restaurant",
            "hotel", "hostal", "bar", "cafe", "cafeteria", "paladar"}
    return " ".join(word for word in text.split() if word not in skip)


def same_name(a, b):
    if not a or not b:
        return False
    if a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a)):
        return True
    left, right = set(a.split()), set(b.split())
    return len(left & right) / max(len(left), len(right)) >= 0.75


def meters(lat1, lon1, lat2, lon2):
    return math.hypot((lon2 - lon1) * 111_320 * math.cos(math.radians(lat1)), (lat2 - lat1) * 110_574)


def hugging_face(path):
    import urllib.request

    request = urllib.request.Request(f"https://huggingface.co/api/datasets/{HF_DATASET}/tree/main/{path}",
                                     headers={"Authorization": f"Bearer {HF_TOKEN}"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return [item["path"] for item in json.load(response)]


def source():
    """(versión, archivos, filtro extra). Los de Hugging Face van ordenados
    por país y los de source.coop partidos por zonas: DuckDB solo baja los
    trozos de Cuba."""
    if HF_TOKEN:
        release = os.environ.get("FSQ_RELEASE") or max(
            path.split("dt=")[1] for path in hugging_face("release") if "dt=" in path)
        files = [f"hf://datasets/{HF_DATASET}/{path}"
                 for path in hugging_face(f"release/dt={release}/places/parquet") if path.endswith(".parquet")]
        # Lo que la gente marcó en Foursquare como cerrado, repetido, privado…
        return f"huggingface {release}", files, "AND coalesce(len(unresolved_flags), 0) = 0"
    release = os.environ.get("FSQ_RELEASE", "2025-02-06")
    files = [f"https://data.source.coop/fused/fsq-os-places/{release}/places/{index}.parquet" for index in range(81)]
    return f"source.coop {release}", files, ""


def foursquare_rows(release, files, extra):
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    if HF_TOKEN:
        con.execute(f"CREATE SECRET hf (TYPE huggingface, TOKEN '{HF_TOKEN}')")
    south, west, north, east = CUBA
    rows = con.execute(f"""
        SELECT fsq_place_id, name, latitude, longitude, address, fsq_category_labels, date_refreshed
        FROM read_parquet({files})
        WHERE country = 'CU' AND latitude BETWEEN {south} AND {north} AND longitude BETWEEN {west} AND {east}
          AND date_closed IS NULL AND name IS NOT NULL {extra}
    """).fetchall()
    print(f"Foursquare ({release}): {len(rows)} lugares abiertos en Cuba")
    return rows


def wanted(rows, log):
    """Los que son lugares de verdad y no muy viejos; entre los repetidos de
    Foursquare (el mismo nombre a menos de 100 m), el revisado más tarde."""
    counts = {"vistos": len(rows), "no_latinos": 0, "no_son_lugares": 0, "viejos": 0}
    kept = []
    for place_id, name, lat, lon, address, labels, refreshed in rows:
        name = clean_name(name)
        text = " | ".join(labels or [])
        if len(name) < 2 or not is_latin(name):
            counts["no_latinos"] += 1
            continue
        if not labels or labels[0] == "Travel and Transportation" or any(word in text for word in SKIPPED):
            counts["no_son_lugares"] += 1
            continue
        if (refreshed or "") < FRESH_SINCE and not any(word in text for word in LASTING):
            counts["viejos"] += 1
            continue
        kept.append((place_id, name, lat, lon, address, labels, refreshed or ""))

    kept.sort(key=lambda row: row[6], reverse=True)
    cells = {}
    unique = []
    for row in kept:
        key = normalized(row[1])
        base = (int(row[2] * 400), int(row[3] * 400))
        near = [other for dlat in (-1, 0, 1) for dlon in (-1, 0, 1)
                for other in cells.get((base[0] + dlat, base[1] + dlon), [])]
        if any(same_name(other[0], key) and meters(row[2], row[3], other[1], other[2]) <= 100 for other in near):
            continue
        cells.setdefault(base, []).append((key, row[2], row[3]))
        unique.append(row)
    counts["repetidos_en_foursquare"] = len(kept) - len(unique)
    log(f"Foursquare en Cuba: {counts}")
    return unique


def merge(db, rows, release, log):
    """Suma a `places` los que no están ya. Devuelve cuántos sumó."""
    # Lo que ya hay (sin calles ni cruces), en celdas de unos 280 m y, para
    # los lugares grandes, de unos 2,8 km. Los de categoría «address» son
    # lugares con número de casa («Sociedad Asturiana Castropol»): cuentan.
    fine, coarse, areas, by_name = {}, {}, {}, {}
    for name, lat, lon, category_name, municipality, province in db.execute(
        "SELECT name, latitude, longitude, category, municipality, province FROM places"
    ):
        if municipality or province:
            areas.setdefault((int(lat * 400), int(lon * 400)), []).append((lat, lon, municipality, province))
        if category_name in ("street", "intersection"):
            continue
        entry = (normalized(name), lat, lon)
        fine.setdefault((int(lat * 400), int(lon * 400)), []).append(entry)
        coarse.setdefault((int(lat * 40), int(lon * 40)), []).append(entry)
        by_name.setdefault(entry[0], []).append((lat, lon))

    def neighbours(grid, scale, lat, lon):
        base = (int(lat * scale), int(lon * scale))
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                yield from grid.get((base[0] + dlat, base[1] + dlon), [])

    def already(name, lat, lon, radius):
        key = normalized(name)
        grid, scale = (fine, 400) if radius <= 250 else (coarse, 40)
        if any(same_name(other[0], key) and meters(lat, lon, other[1], other[2]) <= radius
               for other in neighbours(grid, scale, lat, lon)):
            return True
        # El mismo nombre exacto («Coppelia», «Taberna La Botija»): el mismo
        # lugar aunque el punto esté corrido hasta 300 m.
        return any(meters(lat, lon, other_lat, other_lon) <= 300 for other_lat, other_lon in by_name.get(key, []))

    def area(lat, lon):
        """El municipio y la provincia del lugar más cercano que los tenga."""
        best = None
        for scale_step in (1, 4, 16):
            # Si no hay nada a ~300 m, más lejos (en el campo).
            for other in neighbours(areas, 400, lat, lon) if scale_step == 1 else (
                    item for dlat in range(-scale_step, scale_step + 1) for dlon in range(-scale_step, scale_step + 1)
                    for item in areas.get((int(lat * 400) + dlat, int(lon * 400) + dlon), [])):
                distance = meters(lat, lon, other[0], other[1])
                if best is None or distance < best[0]:
                    best = (distance, other[2], other[3])
            if best:
                break
        return (best[1], best[2]) if best else ("", "")

    inserts = []
    repeated = 0
    for place_id, name, lat, lon, address, labels, _ in rows:
        if already(name, lat, lon, same_spot_radius(labels)):
            repeated += 1
            continue
        municipality, province = area(lat, lon)
        inserts.append((f"fsq-{place_id}", name, (address or "").strip(), municipality or "", province or "",
                        category(labels), lat, lon))
    db.executemany(
        "INSERT INTO places (id, name, address, municipality, province, category, latitude, longitude, importance) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.3)",
        inserts,
    )
    db.execute("INSERT INTO places_fts(places_fts) VALUES('optimize')")
    db.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('foursquare_release', ?)", (release,))
    db.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('foursquare_places', ?)", (str(len(inserts)),))
    log(f"Foursquare: {len(inserts)} nuevos, {repeated} ya estaban")
    return len(inserts)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    source_zip, target_zip = sys.argv[1:3]
    work = tempfile.mkdtemp(prefix="cuba-core-")
    try:
        with zipfile.ZipFile(source_zip) as archive:
            archive.extractall(work)
        pack = os.path.join(work, "cuba-core")
        manifest_path = os.path.join(pack, "manifest.json")
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)

        database = os.path.join(pack, "search", "search.sqlite")
        db = sqlite3.connect(database)
        if db.execute("SELECT count(*) FROM places WHERE id LIKE 'fsq-%'").fetchone()[0]:
            # Otra versión de Foursquare encima de una que ya los tenía: se
            # quitan los de antes (los triggers limpian el índice).
            db.execute("DELETE FROM places WHERE id LIKE 'fsq-%'")
        release, files, extra = source()
        added = merge(db, wanted(foursquare_rows(release, files, extra), print), release, print)
        db.commit()
        db.execute("VACUUM")
        db.close()

        with open(os.path.join(pack, "NOTICE.txt"), encoding="utf-8") as handle:
            notice = handle.read()
        if "Foursquare" not in notice:
            notice = notice.rstrip("\n") + (
                "\nLugares adicionales: FSQ Open Source Places © Foursquare Labs, Inc., "
                "licencia Apache 2.0 (https://www.apache.org/licenses/LICENSE-2.0).\n"
            )
            with open(os.path.join(pack, "NOTICE.txt"), "w", encoding="utf-8") as handle:
                handle.write(notice)

        now = datetime.datetime.now(datetime.timezone.utc)
        version = now.strftime("%Y.%m.%d.%H%M")
        for entry in manifest["files"]:
            path = os.path.join(pack, entry["relativePath"])
            entry["byteCount"] = os.path.getsize(path)
            entry["sha256"] = sha256(path)
            if entry["relativePath"] in ("search/search.sqlite", "NOTICE.txt"):
                entry["contentVersion"] = now.strftime("%Y.%m.%d")
        manifest["packVersion"] = version
        # Otra revisión: la app avisa «Actualización de mapa» a quien ya
        # tiene el paquete (ver CubaCoreInstaller.checkForCoreUpdateAndNotify).
        manifest["dataRevision"] = hashlib.sha256(
            "".join(entry["sha256"] for entry in manifest["files"]).encode()
        ).hexdigest()[:16]
        manifest["createdAt"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

        if os.path.exists(target_zip):
            os.remove(target_zip)
        with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for folder, _, names in sorted(os.walk(pack)):
                relative = os.path.relpath(folder, work)
                archive.write(folder, relative)
                for name in sorted(names):
                    archive.write(os.path.join(folder, name), os.path.join(relative, name))
        print(f"Paquete {version} ({manifest['dataRevision']}): {added} lugares de Foursquare → {target_zip}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
