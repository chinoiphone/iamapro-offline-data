#!/usr/bin/env bash
# Publica el paquete de un estado como Release (o lo reemplaza si ya existe).
set -euo pipefail
STATE="$1"
OUT="${OUT:-$PWD/out}"
REGION="us-$STATE"
TAG="$(cat "$OUT/tag.txt")"
NAME="$(python3 -c "import json; print(json.load(open('$OUT/$REGION.entry.json'))['name'])")"
if gh release view "$TAG" >/dev/null 2>&1; then
  gh release upload "$TAG" "$OUT/$REGION.iamapack.zip" "$OUT/$REGION.entry.json" --clobber
else
  gh release create "$TAG" "$OUT/$REGION.iamapack.zip" "$OUT/$REGION.entry.json" \
    --title "$NAME · $TAG" \
    --notes "Rutas y búsqueda sin conexión de $NAME para IAmapro. Datos © colaboradores de OpenStreetMap (ODbL)."
fi
