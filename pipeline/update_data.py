#!/usr/bin/env python3
"""
Auto-update pipeline: Downloads fresh GTFS from MOT, processes competition data,
and outputs JSON files for the dashboard.
Run weekly (every Monday) to keep data current.
"""

import csv
import json
import os
import re
import sys
import zipfile
import urllib.request
from collections import defaultdict
from datetime import datetime

GTFS_URL = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "gtfs_cache")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "dashboard_data")

METRO_AGENCY = "15"
DAN_AGENCY = "5"
EGGED_AGENCY = "3"
DANKAL_AGENCY = "22"  # Red Line (תבל)

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def _extract_city(desc):
    """Extract city from stop_desc format: 'רחוב: X עיר: Y רציף: Z קומה: W'"""
    if not desc:
        return ""
    m = re.search(r"עיר:\s*(.+?)\s*רציף:", desc)
    if m:
        return m.group(1).strip()
    m = re.search(r"עיר:\s*(.+?)$", desc)
    if m:
        return m.group(1).strip()
    return ""


def download_gtfs():
    zip_path = os.path.join(DATA_DIR, "gtfs.zip")
    log("Downloading GTFS from MOT...")
    urllib.request.urlretrieve(GTFS_URL, zip_path)
    log(f"Downloaded {os.path.getsize(zip_path) / 1e6:.1f} MB")
    log("Extracting...")
    with zipfile.ZipFile(zip_path, "r") as z:
        for name in ["agency.txt", "routes.txt", "trips.txt", "stops.txt", "stop_times.txt", "shapes.txt"]:
            z.extract(name, DATA_DIR)
    log("Extraction complete.")


def read_csv_file(filename):
    path = os.path.join(DATA_DIR, filename)
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def process_data():
    log("Loading agencies...")
    agencies = read_csv_file("agency.txt")
    agency_map = {a["agency_id"]: a["agency_name"] for a in agencies}
    log(f"  {len(agencies)} agencies")

    log("Loading routes...")
    routes = read_csv_file("routes.txt")
    route_agency = {r["route_id"]: r["agency_id"] for r in routes}
    route_name = {r["route_id"]: r.get("route_short_name", "") for r in routes}
    log(f"  {len(routes)} routes")

    log("Loading trips...")
    trips = read_csv_file("trips.txt")
    trip_route = {t["trip_id"]: t["route_id"] for t in trips}
    trip_shape = {t["trip_id"]: t.get("shape_id", "") for t in trips}
    log(f"  {len(trips)} trips")

    log("Loading stops...")
    stops = read_csv_file("stops.txt")
    stop_info = {}
    for s in stops:
        try:
            stop_info[s["stop_id"]] = {
                "name": s["stop_name"],
                "lat": float(s["stop_lat"]),
                "lon": float(s["stop_lon"]),
                "city": _extract_city(s.get("stop_desc", "")),
            }
        except (ValueError, KeyError):
            pass
    log(f"  {len(stop_info)} stops")

    log("Processing stop_times (this takes a while)...")
    stop_departures = defaultdict(lambda: defaultdict(int))
    stop_routes = defaultdict(lambda: defaultdict(set))

    with open(os.path.join(DATA_DIR, "stop_times.txt"), "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            count += 1
            tid = row["trip_id"]
            sid = row["stop_id"]
            rid = trip_route.get(tid)
            if not rid:
                continue
            aid = route_agency.get(rid)
            if not aid:
                continue
            stop_departures[sid][aid] += 1
            rn = route_name.get(rid, rid)
            stop_routes[sid][aid].add(rn)
            if count % 2_000_000 == 0:
                log(f"  Processed {count / 1e6:.0f}M stop_times...")

    log(f"  Total: {count:,} stop_times processed")

    log("Building competition analysis...")
    shared_stops = []
    metro_only_stops = []
    competitor_agencies = {DAN_AGENCY, EGGED_AGENCY}

    for sid, agencies_deps in stop_departures.items():
        if sid not in stop_info:
            continue
        info = stop_info[sid]
        metro_deps = agencies_deps.get(METRO_AGENCY, 0)
        comp_deps = sum(agencies_deps.get(a, 0) for a in competitor_agencies)
        metro_routes = stop_routes[sid].get(METRO_AGENCY, set())
        comp_names = []
        for a in competitor_agencies:
            if a in agencies_deps:
                comp_names.append(agency_map.get(a, a))

        if metro_deps > 0 and comp_deps > 0:
            ratio = comp_deps / metro_deps if metro_deps > 0 else 999
            shared_stops.append({
                "id": sid,
                "name": info["name"],
                "lat": info["lat"],
                "lon": info["lon"],
                "city": info["city"],
                "metro_deps": metro_deps,
                "comp_deps": comp_deps,
                "ratio": round(ratio, 2),
                "metro_routes": list(metro_routes)[:10],
                "competitors": ", ".join(comp_names),
                "metro_pax": metro_deps * 5,
                "comp_pax": comp_deps * 5,
            })
        elif metro_deps > 0:
            metro_only_stops.append({
                "id": sid,
                "name": info["name"],
                "lat": info["lat"],
                "lon": info["lon"],
                "city": info["city"],
                "deps": metro_deps,
                "routes": list(metro_routes)[:10],
                "pax": metro_deps * 5,
            })

    log(f"  Shared stops: {len(shared_stops)}, Metro-only: {len(metro_only_stops)}")

    log("Extracting Red Line shapes...")
    red_shapes = extract_red_line_shapes(trip_shape, route_agency)

    total_metro_pax = sum(s["metro_pax"] for s in shared_stops) + sum(s["pax"] for s in metro_only_stops)
    total_comp_pax = sum(s["comp_pax"] for s in shared_stops)
    metro_dom = sum(1 for s in shared_stops if s["ratio"] < 0.5)
    balanced = sum(1 for s in shared_stops if 0.5 <= s["ratio"] <= 1.5)
    comp_dom = sum(1 for s in shared_stops if s["ratio"] > 1.5)
    almost_there = sum(1 for s in shared_stops if 1.0 <= s["ratio"] <= 2.0 and s["metro_deps"] + s["comp_deps"] >= 100)
    strongholds = sum(1 for s in shared_stops if s["ratio"] < 0.5 and s["metro_deps"] + s["comp_deps"] >= 100)

    top_losing = sorted([s for s in shared_stops if s["ratio"] > 1.5], key=lambda x: x["comp_pax"] - x["metro_pax"], reverse=True)[:50]
    top_opportunities = sorted([s for s in shared_stops if 1.0 <= s["ratio"] <= 2.0], key=lambda x: x["comp_deps"] + x["metro_deps"], reverse=True)[:50]

    city_stats = defaultdict(lambda: {"metro_pax": 0, "comp_pax": 0, "stops": 0, "shared": 0})
    for s in shared_stops:
        c = s["city"] or "לא ידוע"
        city_stats[c]["metro_pax"] += s["metro_pax"]
        city_stats[c]["comp_pax"] += s["comp_pax"]
        city_stats[c]["shared"] += 1
        city_stats[c]["stops"] += 1
    for s in metro_only_stops:
        c = s["city"] or "לא ידוע"
        city_stats[c]["metro_pax"] += s["pax"]
        city_stats[c]["stops"] += 1

    city_list = [{"city": c, **v, "ratio": round(v["comp_pax"] / max(v["metro_pax"], 1), 2)} for c, v in city_stats.items()]
    city_list.sort(key=lambda x: x["comp_pax"], reverse=True)

    summary = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_stops": len(shared_stops) + len(metro_only_stops),
        "shared_stops": len(shared_stops),
        "metro_only_stops": len(metro_only_stops),
        "total_metro_pax_weekly": total_metro_pax,
        "total_comp_pax_weekly": total_comp_pax,
        "estimated_annual_loss": total_comp_pax * 52,
        "metro_dominant": metro_dom,
        "balanced": balanced,
        "comp_dominant": comp_dom,
        "almost_there": almost_there,
        "strongholds": strongholds,
    }

    log("Saving JSON outputs...")
    outputs = {
        "summary.json": summary,
        "shared_stops.json": shared_stops,
        "metro_only.json": metro_only_stops,
        "top_losing.json": top_losing,
        "top_opportunities.json": top_opportunities,
        "city_stats.json": city_list[:30],
        "red_line.json": red_shapes,
    }

    for fname, data in outputs.items():
        path = os.path.join(OUTPUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=None if len(json.dumps(data)) > 100000 else 2)
        size = os.path.getsize(path)
        log(f"  {fname}: {size / 1024:.0f} KB")

    log("Pipeline complete!")
    return summary


def extract_red_line_shapes(trip_shape, route_agency):
    known_shape_ids = {"136679", "136680", "136681", "136682", "136683", "163398", "163399", "163400", "163401"}
    shapes = defaultdict(list)

    shapes_path = os.path.join(DATA_DIR, "shapes.txt")
    if not os.path.exists(shapes_path):
        return {"main": [], "branch": [], "stations": []}

    with open(shapes_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["shape_id"] in known_shape_ids:
                shapes[row["shape_id"]].append(
                    (float(row["shape_pt_lat"]), float(row["shape_pt_lon"]), int(row["shape_pt_sequence"]))
                )

    main_pts = sorted(shapes.get("136681", []), key=lambda x: x[2])
    branch_pts = sorted(shapes.get("136679", []), key=lambda x: x[2])

    main_simplified = [[p[0], p[1]] for p in main_pts[::3]] + ([[main_pts[-1][0], main_pts[-1][1]]] if main_pts else [])
    branch_simplified = [[p[0], p[1]] for p in branch_pts[::3]] + ([[branch_pts[-1][0], branch_pts[-1][1]]] if branch_pts else [])

    stations = [
        {"name": "הקוממיות", "lat": 32.002287, "lon": 34.746818, "city": "בת ים"},
        {"name": "ארלוזורוב", "lat": 32.081554, "lon": 34.796524, "city": "תל אביב"},
        {"name": "אבא הלל", "lat": 32.082911, "lon": 34.802729, "city": "רמת גן"},
        {"name": "בן גוריון", "lat": 32.091009, "lon": 34.823592, "city": "רמת גן"},
        {"name": "תחנה מרכזית פ\"ת", "lat": 32.094693, "lon": 34.886538, "city": "פתח תקווה"},
        {"name": "קריית אריה", "lat": 32.105931, "lon": 34.861896, "city": "פתח תקווה"},
    ]

    return {"main": main_simplified, "branch": branch_simplified, "stations": stations}


if __name__ == "__main__":
    download_gtfs()
    summary = process_data()
    print("\n=== Summary ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
