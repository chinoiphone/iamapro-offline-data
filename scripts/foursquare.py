#!/usr/bin/env python3
"""Los lugares de Foursquare (FSQ Open Source Places) para los paquetes de
EE. UU.: se suman a la búsqueda del estado sin repetir los de OpenStreetMap ni
los de Overture, y a los que ya estaban se les completa el teléfono, la web y
la dirección que no tenían (la misma lógica que Overture, ver overture.py).

Hugging Face no filtra bien por zona (para un estado pequeño bajaba casi todo
el archivo, 11 GB), así que se hace en dos pasos:

  foursquare.py extract <fsq-us.parquet>
      Una sola vez: lee la versión más nueva (o FSQ_RELEASE) con FSQ_HF_TOKEN
      y guarda los lugares abiertos de EE. UU., Puerto Rico e Islas Vírgenes,
      sin los que la gente marcó como cerrados o repetidos, ordenados por
      latitud para que leer un estado sea rápido.
  build_search.py (con FSQ_US_PARQUET=<fsq-us.parquet>)
      Por cada estado, `merge` suma los de su zona.

Lugares © Foursquare Labs, Inc., licencia Apache 2.0.
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import overture  # noqa: E402

HF_DATASET = "foursquare/fsq-os-places"
COUNTRIES = ("US", "PR", "VI")
# Los negocios de EE. UU. cambian: solo los que Foursquare vio hace poco.
FRESH_SINCE = os.environ.get("FSQ_FRESH_SINCE", "2023-01-01")
# Lo que no es un lugar al que ir, o ya viene de OpenStreetMap.
SKIPPED = (
    "States and Municipalities", "Travel and Transportation > Road", "Residential Building",
    "Housing Development", "Moving Target", "Plane", "Event", "Bus Line", "Platform", "Baggage",
    "Airport Gate", "Airport Lounge", "Intersection", "Structure", "Home", "Neighborhood",
)


def hugging_face(path, token):
    request = urllib.request.Request(f"https://huggingface.co/api/datasets/{HF_DATASET}/tree/main/{path}",
                                     headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return [item["path"] for item in json.load(response)]


def extract(target):
    import duckdb

    token = os.environ["FSQ_HF_TOKEN"].strip()
    release = os.environ.get("FSQ_RELEASE") or max(
        path.split("dt=")[1] for path in hugging_face("release", token) if "dt=" in path)
    files = [f"hf://datasets/{HF_DATASET}/{path}"
             for path in hugging_face(f"release/dt={release}/places/parquet", token) if path.endswith(".parquet")]
    print(f"Foursquare {release}: {len(files)} archivos")
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"CREATE SECRET hf (TYPE huggingface, TOKEN '{token}')")
    countries = ", ".join(f"'{country}'" for country in COUNTRIES)
    con.execute(f"""
        COPY (
            SELECT fsq_place_id, name, latitude, longitude, address, locality, postcode, tel, website,
                   fsq_category_labels, date_refreshed
            FROM read_parquet({files})
            WHERE country IN ({countries}) AND date_closed IS NULL AND name IS NOT NULL
              AND coalesce(len(unresolved_flags), 0) = 0 AND date_refreshed >= '{FRESH_SINCE}'
            ORDER BY latitude
        ) TO '{target}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 100000)
    """)
    count = con.execute(f"SELECT count(*) FROM read_parquet('{target}')").fetchone()[0]
    with open(target + ".release", "w", encoding="utf-8") as handle:
        handle.write(release)
    print(f"{count} lugares → {target} ({os.path.getsize(target) // 2**20} MB)")


def taxonomy(labels):
    """Las etiquetas de Foursquare al estilo que lee `overture.category`
    («Dining and Drinking > Bar» → «dining_and_drinking_bar»)."""
    text = " ".join(labels or []).lower()
    return text.replace(" > ", "_").replace(", ", "_").replace(" ", "_")


def merge(db, bounds, parquet, log=print):
    """Suma a `places` los lugares de `parquet` dentro de `bounds` [sur,
    oeste, norte, este]. Devuelve los conteos."""
    import duckdb

    south, west, north, east = bounds
    con = duckdb.connect()
    found = con.execute(f"""
        SELECT name, fsq_category_labels, website, tel, address, locality, postcode, CAST(latitude AS DOUBLE), CAST(longitude AS DOUBLE)
        FROM read_parquet('{parquet}')
        WHERE latitude BETWEEN {south} AND {north} AND longitude BETWEEN {west} AND {east}
    """).fetchall()
    rows = []
    for name, labels, website, phone, address, locality, postcode, lat, lon in found:
        text = " | ".join(labels or [])
        if not labels or any(word in text for word in SKIPPED):
            continue
        rows.append((name.strip(), None, taxonomy(labels), website, phone, address, locality, postcode, lat, lon))
    log(f"Foursquare: {len(found)} lugares en el área, {len(rows)} útiles")
    added, enriched = overture.merge_rows(db, rows, "Foursquare", log)
    return {"foursquare_added": added, "foursquare_enriched": enriched}


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "extract":
        extract(sys.argv[2])
    else:
        sys.exit(__doc__)
