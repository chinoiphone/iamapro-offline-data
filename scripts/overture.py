"""Los negocios de Overture Maps (el mapa abierto de Meta, Microsoft,
Amazon y otros) que le faltan a OpenStreetMap: se suman a la búsqueda del
estado sin repetir lugares, y a los de OpenStreetMap se les completa el
teléfono, la web y la dirección que no tenían.

La versión (OVERTURE_RELEASE, p. ej. 2026-10-21.0) viene de la acción.
Licencias: CDLA-Permissive-2.0 / ODbL, según la fuente de cada lugar.
"""
import math
import re

POI = 0


def normalized(text):
    text = (text or "").lower()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    skip = {"the", "el", "la", "los", "las", "of", "de", "del", "and", "y", "inc", "llc"}
    return " ".join(word for word in text.split() if word not in skip)


STREET_WORDS = {
    "street": "st", "avenue": "ave", "boulevard": "blvd", "road": "rd", "drive": "dr", "lane": "ln",
    "court": "ct", "place": "pl", "parkway": "pkwy", "highway": "hwy", "circle": "cir", "terrace": "ter",
    "southwest": "sw", "southeast": "se", "northwest": "nw", "northeast": "ne",
    "north": "n", "south": "s", "east": "e", "west": "w",
}


def normalized_street(text):
    """«Southwest 8th Street» y «SW 8th St» → «sw 8th st»."""
    words = normalized(text).split()
    return " ".join(STREET_WORDS.get(word, word) for word in words)


def same_place(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) >= 4 and (a in b or b in a):
        return True
    left, right = set(a.split()), set(b.split())
    return len(left & right) / max(len(left), len(right)) >= 0.75


def category(basic, taxonomy):
    """La categoría al estilo de OpenStreetMap (la usan los botones de la
    barra sin internet: Gasolina, Comida…)."""
    text = f"{taxonomy or ''} {basic or ''}"
    rules = [
        ("fast_food", "amenity=fast_food"), ("ice_cream", "amenity=ice_cream"),
        ("restaurant", "amenity=restaurant"), ("eatery", "amenity=restaurant"),
        ("coffee", "amenity=cafe"), ("cafe", "amenity=cafe"), ("bakery", "shop=bakery"),
        ("nightclub", "amenity=nightclub"), ("pub", "amenity=pub"), ("_bar", "amenity=bar"),
        ("gas_station", "amenity=fuel"), ("fueling", "amenity=fuel"), ("charging", "amenity=charging_station"),
        ("atm", "amenity=atm"), ("bank", "amenity=bank"), ("hospital", "amenity=hospital"),
        ("pharmacy", "amenity=pharmacy"), ("drugstore", "amenity=pharmacy"),
        ("supermarket", "shop=supermarket"), ("grocery", "shop=supermarket"),
        ("convenience", "shop=convenience"), ("hotel", "tourism=hotel"), ("motel", "tourism=motel"),
        ("lodging", "tourism=hotel"), ("parking", "amenity=parking"), ("beach", "natural=beach"),
        ("fitness", "leisure=fitness_centre"), ("gym", "leisure=fitness_centre"),
        ("laundr", "shop=laundry"), ("dry_clean", "shop=dry_cleaning"), ("car_rental", "amenity=car_rental"),
        ("post_office", "amenity=post_office"), ("barber", "shop=hairdresser"), ("hair", "shop=hairdresser"),
        ("nail", "shop=beauty"), ("beauty", "shop=beauty"), ("spa", "leisure=spa"),
        ("automotive_repair", "shop=car_repair"), ("auto_repair", "shop=car_repair"),
        ("tire", "shop=tyres"), ("museum", "tourism=museum"), ("movie_theater", "amenity=cinema"),
        ("cinema", "amenity=cinema"), ("university", "amenity=university"), ("college", "amenity=college"),
        ("school", "amenity=school"), ("library", "amenity=library"), ("police", "amenity=police"),
        ("fire_station", "amenity=fire_station"), ("shopping_mall", "shop=mall"), ("mall", "shop=mall"),
        ("department_store", "shop=department_store"), ("clothing", "shop=clothes"),
        ("veterinar", "amenity=veterinary"), ("pet_", "shop=pet"), ("zoo", "tourism=zoo"),
        ("aquarium", "tourism=aquarium"), ("amusement", "tourism=theme_park"), ("marina", "leisure=marina"),
        ("campground", "tourism=camp_site"), ("airport", "aeroway=aerodrome"), ("brewery", "craft=brewery"),
        ("winery", "craft=winery"), ("stadium", "leisure=stadium"), ("golf", "leisure=golf_course"),
        ("dentist", "amenity=dentist"), ("doctor", "amenity=doctors"), ("clinic", "amenity=clinic"),
        ("park", "leisure=park"),
    ]
    for key, tag in rules:
        if key in text:
            return tag
    return f"overture={taxonomy or basic or 'place'}"


def split_address(freeform):
    """«3555 SW 8th St» → («3555», «SW 8th St»)."""
    if not freeform:
        return None, None
    match = re.match(r"^\s*(\d+[A-Za-z]?(?:-\d+)?)\s+(.+?)\s*$", freeform)
    if match:
        return match.group(1), match.group(2)
    return None, freeform.strip()


def merge(db, bounds, release, log=print):
    """Suma (o completa) los lugares de Overture dentro de `bounds`
    [sur, oeste, norte, este] en la tabla `places`. Devuelve los conteos."""
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    con.execute("INSTALL spatial; LOAD spatial;")
    source = f"s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
    south, west, north, east = bounds
    rows = con.execute(f"""
        SELECT names.primary, basic_category, taxonomy.primary, websites[1], phones[1],
               addresses[1].freeform, addresses[1].locality, addresses[1].postcode,
               ST_Y(geometry), ST_X(geometry)
        FROM read_parquet('{source}', hive_partitioning = 1)
        WHERE bbox.xmin BETWEEN {west} AND {east}
          AND bbox.ymin BETWEEN {south} AND {north}
          AND confidence >= 0.7
          AND coalesce(operating_status, 'open') = 'open'
          AND names.primary IS NOT NULL
    """).fetchall()
    log(f"Overture: {len(rows)} lugares en el área")

    # Los lugares ya puestos (los de OpenStreetMap y los de Overture que se
    # van sumando, que también trae repetidos de distintas fuentes), en
    # celdas de unos 250 m. rowid None = uno de Overture recién sumado.
    cells = {}

    def remember(rowid, name, lat, lon, phone, website, housenumber, street):
        key = (int(lat * 400), int(lon * 400))
        cells.setdefault(key, []).append(
            (rowid, normalized(name), lat, lon, phone, website, housenumber, normalized_street(street))
        )

    for rowid, name, lat, lon, phone, website, housenumber, street in db.execute(
        "SELECT id, name, lat, lon, phone, website, housenumber, street FROM places WHERE kind = ?", (POI,)
    ):
        remember(rowid, name, lat, lon, phone, website, housenumber, street)

    added = enriched = 0
    updates = []
    inserts = []
    for name, basic, taxonomy, website, phone, freeform, locality, postcode, lat, lon in rows:
        if lat is None or lon is None:
            continue
        wanted = normalized(name)
        housenumber, street = split_address(freeform)
        wanted_street = normalized_street(street)
        match = None
        base = (int(lat * 400), int(lon * 400))
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                for candidate in cells.get((base[0] + dlat, base[1] + dlon), []):
                    dx = (candidate[3] - lon) * 111_320 * math.cos(math.radians(lat))
                    dy = (candidate[2] - lat) * 110_574
                    meters = math.hypot(dx, dy)
                    same_name = same_place(candidate[1], wanted)
                    # La misma dirección y algo del nombre en común: el mismo
                    # lugar aunque el punto esté algo corrido.
                    same_address = (
                        housenumber and candidate[6] == housenumber and wanted_street and
                        candidate[7] == wanted_street and
                        bool(set(candidate[1].split()) & set(wanted.split()))
                    )
                    if (same_name and meters <= 200) or (same_address and meters <= 400):
                        match = candidate
                        break
                if match:
                    break
            if match:
                break

        if match:
            rowid, _, _, _, old_phone, old_website, _, old_street = match
            if rowid is not None and ((not old_phone and phone) or (not old_website and website) or (not old_street and street)):
                updates.append((phone, website, housenumber, street, postcode, rowid))
                enriched += 1
            continue

        inserts.append((POI, name, category(basic, taxonomy), housenumber, street,
                        locality, postcode, lat, lon, phone, website, None))
        remember(None, name, lat, lon, phone, website, housenumber, street)
        added += 1

    db.executemany(
        "UPDATE places SET phone = coalesce(phone, ?), website = coalesce(website, ?), "
        "housenumber = coalesce(housenumber, ?), street = coalesce(street, ?), "
        "postcode = coalesce(postcode, ?) WHERE id = ?",
        updates,
    )
    db.executemany(
        "INSERT INTO places (kind, name, category, housenumber, street, city, postcode, lat, lon, phone, website, opening_hours) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        inserts,
    )
    log(f"Overture: {added} nuevos, {enriched} de OpenStreetMap completados")
    return {"overture_added": added, "overture_enriched": enriched}
