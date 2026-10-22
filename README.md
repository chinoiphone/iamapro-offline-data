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
