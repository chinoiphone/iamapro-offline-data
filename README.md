# IAmapro · datos sin conexión (EE. UU.)

Paquetes por estado para usar IAmapro sin internet fuera de Cuba: rutas
(Valhalla 3.8.3, la versión que lee la app) y búsqueda de lugares, calles y
direcciones (SQLite con FTS5). El mapa en sí se descarga aparte, por zonas,
desde OpenFreeMap.

- `catalog.json`: los paquetes publicados (lo lee la app).
- Cada paquete es un Release `us-<estado>-<versión>` con
  `us-<estado>.iamapack.zip` y su ficha `us-<estado>.entry.json`.
- Se preparan con la acción **Paquetes sin conexión (EE. UU.)** (Actions →
  Run workflow → estados separados por coma, con el nombre de Geofabrik:
  `florida,new-york,texas`).

Datos © colaboradores de OpenStreetMap, bajo licencia ODbL
(https://www.openstreetmap.org/copyright). Extractos de Geofabrik.

## Cuba: lugares de Foursquare

`scripts/cuba_foursquare.py` suma al paquete de Cuba (`cuba-core`, el que va
dentro de la app) los lugares de FSQ Open Source Places que le faltan
(paladares, hoteles, casas particulares, bares, playas…), sin repetir los de
OpenStreetMap y sin la basura (vuelos, cruceros, calles):

    pip install duckdb
    FSQ_HF_TOKEN=hf_… python3 scripts/cuba_foursquare.py cuba-core.iamapack.zip cuba-core-nuevo.iamapack.zip

Con `FSQ_HF_TOKEN` (token «Read» de Hugging Face de una cuenta que aceptó las
condiciones de foursquare/fsq-os-places) usa la versión más nueva de Foursquare;
sin token, la copia pública de source.coop de 2025-02-06, que no pide cuenta.
Los dos bajan solo la parte de Cuba (unos segundos). El paquete sale con otra revisión de datos: la app avisa
«Actualización de mapa» a quien ya lo tiene. Lugares © Foursquare Labs, Inc.,
licencia Apache 2.0 (va en el NOTICE.txt del paquete).
