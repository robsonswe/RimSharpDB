# -*- coding: utf-8 -*-
"""
versions_refresh.py - refreshes OFFICIAL versions in db/db.json from a
Steam Workshop Database (steamDB.json) snapshot.

The curated dictionary's per-entry "versions" lists must reflect what the
workshop OFFICIALLY declares today (the same kind of data steamDB.json
carries in its "gameVersions"). Old scans and one-off Steam API calls go
stale; this tool replaces each entry's versions with the fresh official
tags whenever steamDB has data for that workshop item.

Matching strategy (exact and unambiguous only; ambiguous entries are
skipped and left untouched):
    1. The entry's steamId is a publishedfileid present in steamDB
       ("database" root) -> use its gameVersions.
    2. Otherwise, if exactly ONE steamDB entry carries the same packageId
       (case-insensitive) -> use its gameVersions.

Rules:
    - Only version-like tags (e.g. "1.4") are kept, sorted ascending.
    - An entry is only touched when steamDB has a NON-EMPTY gameVersions
      list. Entries steamDB knows nothing about are left alone.
    - name/authors/published and everything else are never modified.

Usage:
    python tools/versions_refresh.py --steamdb <path-to-steamDB.json> [--db db/db.json]

Re-run it whenever a newer steamDB.json snapshot is available.
"""

import argparse
import json
import re
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


def vkey(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except (ValueError, AttributeError):
        return (0,)


def clean_versions(raw):
    return sorted({str(v) for v in raw if VERSION_RE.match(str(v))}, key=vkey)


def load_steamdb(path: Path):
    print(f"loading steamDB snapshot: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    database = data.get("database", {})
    if not database:
        # Legacy shape (older dumps keyed entries at the root) - support it
        # so the tool keeps working against either snapshot generation.
        database = {k: v for k, v in data.items() if k != "version" and isinstance(v, dict)}
    print(f"  snapshot stamp: {data.get('version')}, entries: {len(database)}")

    by_pfid = {}
    by_pkg = {}
    for pfid, entry in database.items():
        if not isinstance(entry, dict):
            continue
        versions = clean_versions(entry.get("gameVersions") or [])
        if not versions:
            continue
        by_pfid[pfid] = versions
        pkg = entry.get("packageId") or entry.get("packageid")
        if isinstance(pkg, str) and pkg.strip():
            by_pkg.setdefault(pkg.strip().lower(), []).append((pfid, versions))
    print(f"  with official versions: {len(by_pfid)} workshop items, {len(by_pkg)} packageIds")
    return database.keys(), by_pfid, by_pkg


def refresh(db_path: Path, known_pfids, by_pfid: dict, by_pkg: dict) -> None:
    curated = json.loads(db_path.read_text(encoding="utf-8"))
    mods = curated.get("mods", {})
    print(f"curated dictionary: {len(mods)} packageIds")

    stats = {"entries": 0, "updated": 0, "unchanged": 0,
             "steamdb_known_no_tags": 0, "not_in_steamdb": 0, "ambiguous": 0}
    for pkg, entries in mods.items():
        pkg_l = pkg.lower()
        pkg_candidates = by_pkg.get(pkg_l, [])
        for steam_id, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            stats["entries"] += 1
            old_versions = clean_versions(entry.get("versions") or [])
            new_versions = None
            if steam_id in by_pfid:
                new_versions = by_pfid[steam_id]
            elif len(pkg_candidates) == 1:
                new_versions = pkg_candidates[0][1]
            elif len(pkg_candidates) > 1:
                stats["ambiguous"] += 1
                continue
            else:
                # steamDB knows the item but has no tags, or does not know it
                stats["steamdb_known_no_tags" if steam_id in known_pfids else "not_in_steamdb"] += 1
                continue
            if new_versions and new_versions != old_versions:
                entry["versions"] = new_versions
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1

    print("result: " + json.dumps(stats))
    with db_path.open("w", encoding="utf-8") as fh:
        json.dump(curated, fh, indent=4, ensure_ascii=False)
    print(f"wrote {db_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--steamdb", required=True,
                        help="Path to a steamDB.json snapshot (RimSort/Steam-Workshop-Database)")
    parser.add_argument("--db", default=str(Path(__file__).resolve().parent.parent / "db" / "db.json"))
    args = parser.parse_args()

    known_pfids, by_pfid, by_pkg = load_steamdb(Path(args.steamdb))
    refresh(Path(args.db), known_pfids, by_pfid, by_pkg)


if __name__ == "__main__":
    main()
