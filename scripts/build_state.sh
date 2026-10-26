#!/usr/bin/env bash
# Prepara el paquete sin conexión de un estado de EE. UU. para IAmapro:
# rutas (Valhalla 3.8.3, la versión que lee la app) y búsqueda (SQLite).
# Uso: build_state.sh <estado-de-geofabrik>   (p. ej. florida, new-york)
set -euo pipefail

STATE="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${WORK:-$PWD/work/${STATE//\//-}}"
OUT="${OUT:-$PWD/out}"
VALHALLA_IMAGE="ghcr.io/valhalla/valhalla:3.8.3"
mkdir -p "$WORK" "$OUT"

echo "::group::Descargar $STATE de Geofabrik"
curl -fL --retry 5 --retry-delay 20 -o "$WORK/state.osm.pbf" \
  "https://download.geofabrik.de/north-america/us/${STATE}-latest.osm.pbf"
ls -la "$WORK/state.osm.pbf"
echo "::endgroup::"

echo "::group::Rutas (Valhalla)"
mkdir -p "$WORK/valhalla_tiles"
docker run --rm -v "$WORK":/data -w /data "$VALHALLA_IMAGE" bash -c '
  set -e
  valhalla_build_config \
    --mjolnir-tile-dir /data/valhalla_tiles \
    --mjolnir-tile-extract /data/valhalla_tiles.tar \
    --mjolnir-timezone /data/valhalla_tiles/timezones.sqlite \
    --mjolnir-admin /data/valhalla_tiles/admins.sqlite \
    --mjolnir-concurrency "$(nproc)" > /data/valhalla.json
  # Husos horarios y países: para los horarios y las reglas de cada lugar.
  valhalla_build_timezones > /data/valhalla_tiles/timezones.sqlite || echo "sin husos horarios"
  valhalla_build_admins -c /data/valhalla.json /data/state.osm.pbf || echo "sin admins"
  valhalla_build_tiles -c /data/valhalla.json /data/state.osm.pbf
  valhalla_build_extract -c /data/valhalla.json -v
'
sudo rm -rf "$WORK/valhalla_tiles"
ls -la "$WORK/valhalla_tiles.tar"
echo "::endgroup::"

echo "::group::Búsqueda (lugares, calles y direcciones)"
osmium tags-filter "$WORK/state.osm.pbf" \
  n/addr:housenumber w/addr:housenumber nwr/name \
  -o "$WORK/named.osm.pbf" --overwrite
rm -f "$WORK/state.osm.pbf"
osmium export "$WORK/named.osm.pbf" -f geojsonseq -a type,id \
  -x print_record_separator=false \
  -o "$WORK/named.geojsonseq" --overwrite
rm -f "$WORK/named.osm.pbf"
# Con OVERTURE_RELEASE, también los negocios de Overture Maps (ver overture.py).
python3 "$HERE/build_search.py" "$WORK/named.geojsonseq" "$WORK/search.sqlite" "$WORK/stats.json"
rm -f "$WORK/named.geojsonseq"
ls -la "$WORK/search.sqlite"
echo "::endgroup::"

echo "::group::Paquete"
python3 "$HERE/make_pack.py" "$STATE" "$WORK" "$OUT"
ls -la "$OUT"
echo "::endgroup::"
