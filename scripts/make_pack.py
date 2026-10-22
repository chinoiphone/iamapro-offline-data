#!/usr/bin/env python3
"""Arma el paquete us-<estado>.iamapack.zip (con la misma forma que el de
Cuba: manifest.json, routing/, search/) y su ficha para el catálogo.

Uso: make_pack.py <estado> <carpeta de trabajo> <carpeta de salida>
"""
import datetime
import hashlib
import json
import os
import shutil
import sys
import zipfile

REPO = os.environ.get("GITHUB_REPOSITORY", "chinoiphone/iamapro-offline-data")
NAMES = {"district-of-columbia": "District of Columbia", "us-virgin-islands": "U.S. Virgin Islands"}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    state, work, out = sys.argv[1:4]
    region = f"us-{state}"
    name = NAMES.get(state, state.replace("-", " ").title())
    now = datetime.datetime.now(datetime.timezone.utc)
    version = now.strftime("%Y.%m.%d")
    tag = f"{region}-{version}"

    pack = os.path.join(work, region)
    shutil.rmtree(pack, ignore_errors=True)
    os.makedirs(os.path.join(pack, "routing"))
    os.makedirs(os.path.join(pack, "search"))
    shutil.move(os.path.join(work, "valhalla_tiles.tar"), os.path.join(pack, "routing", "valhalla_tiles.tar"))
    shutil.move(os.path.join(work, "search.sqlite"), os.path.join(pack, "search", "search.sqlite"))

    # La misma configuración que el de Cuba: la app cambia las rutas /data/…
    # por las del teléfono al instalarlo.
    with open(os.path.join(work, "valhalla.json"), encoding="utf-8") as handle:
        config = json.load(handle)
    config["mjolnir"]["tile_extract"] = f"/data/{region}/routing/valhalla_tiles.tar"
    with open(os.path.join(pack, "routing", "valhalla.json"), "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)

    with open(os.path.join(pack, "NOTICE.txt"), "w", encoding="utf-8") as handle:
        handle.write("Datos © colaboradores de OpenStreetMap, licencia ODbL: "
                     "https://www.openstreetmap.org/copyright\n")

    with open(os.path.join(work, "stats.json"), encoding="utf-8") as handle:
        stats = json.load(handle)

    files = []
    # Los mismos tipos de archivo que conoce la app (OfflinePackManifest);
    # NOTICE.txt va en el paquete pero no en la lista.
    for relative, component in [
        ("search/search.sqlite", "search"),
        ("routing/valhalla_tiles.tar", "routing"),
        ("routing/valhalla.json", "routing"),
    ]:
        path = os.path.join(pack, relative)
        files.append({
            "component": component,
            "relativePath": relative,
            "byteCount": os.path.getsize(path),
            "sha256": sha256(path),
            "required": True,
            "contentVersion": version,
        })

    manifest = {
        "schemaVersion": 2,
        "regionID": region,
        "regionName": name,
        "packVersion": now.strftime("%Y.%m.%d.%H%M"),
        "dataRevision": files[1]["sha256"][:16],
        "createdAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "minimumAppBuild": 1,
        "bounds": stats["bounds"],
        "capabilities": {
            "mapDisplay": False, "addressSearch": True, "pointOfInterestSearch": True,
            "offlineRouting": True, "turnByTurnVoice": True, "motorcycleProfile": True,
            "bicycleProfile": True, "walkingProfile": True, "carProfile": True,
        },
        "counts": stats["counts"],
        "files": files,
    }
    with open(os.path.join(pack, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    os.makedirs(out, exist_ok=True)
    archive = os.path.join(out, f"{region}.iamapack.zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zipped:
        for root, _, names in os.walk(pack):
            for filename in sorted(names):
                full = os.path.join(root, filename)
                zipped.write(full, os.path.relpath(full, work))

    entry = {
        "id": region,
        "name": name,
        "version": version,
        "tag": tag,
        "url": f"https://github.com/{REPO}/releases/download/{tag}/{region}.iamapack.zip",
        "byteCount": os.path.getsize(archive),
        "sha256": sha256(archive),
        "installedByteCount": sum(item["byteCount"] for item in files),
        "bounds": stats["bounds"],
        "counts": stats["counts"],
    }
    with open(os.path.join(out, f"{region}.entry.json"), "w", encoding="utf-8") as handle:
        json.dump(entry, handle, indent=2)
    with open(os.path.join(out, "tag.txt"), "w", encoding="utf-8") as handle:
        handle.write(tag)
    print(json.dumps(entry, indent=2))


if __name__ == "__main__":
    main()
