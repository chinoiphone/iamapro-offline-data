#!/usr/bin/env python3
"""Rehace catalog.json con la última versión publicada de cada estado."""
import datetime
import glob
import json
import os
import subprocess
import tempfile


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main():
    releases = json.loads(gh("release", "list", "--limit", "500", "--json", "tagName"))
    latest = {}
    with tempfile.TemporaryDirectory() as folder:
        for release in releases:
            tag = release["tagName"]
            if not tag.startswith("us-"):
                continue
            target = os.path.join(folder, tag)
            os.makedirs(target)
            try:
                gh("release", "download", tag, "-p", "*.entry.json", "-D", target)
            except subprocess.CalledProcessError:
                continue
            for path in glob.glob(os.path.join(target, "*.entry.json")):
                with open(path, encoding="utf-8") as handle:
                    entry = json.load(handle)
                current = latest.get(entry["id"])
                if current is None or entry["version"] > current["version"]:
                    latest[entry["id"]] = entry

    catalog = {
        "schemaVersion": 1,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "packs": sorted(latest.values(), key=lambda entry: entry["name"]),
    }
    with open("catalog.json", "w", encoding="utf-8") as handle:
        json.dump(catalog, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"{len(catalog['packs'])} paquetes")


if __name__ == "__main__":
    main()
