#!/usr/bin/env python3
"""
Build dashboard HTML from JSON data files.
Run after update_data.py to regenerate the dashboard with fresh data.

Usage:
    python build_dashboard.py

Input:  pipeline/dashboard_data/*.json
Output: index.html (root of repo)
"""

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "dashboard_data")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "..", "index.html")


def load(name):
    with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    print("Loading data...")
    summary = load("summary.json")
    shared = load("shared_stops.json")
    metro_only = load("metro_only.json")
    top_losing = load("top_losing.json")
    top_opp = load("top_opportunities.json")
    city_stats = load("city_stats.json")
    red_line = load("red_line.json")

    print(f"  {len(shared)} shared stops, {len(metro_only)} metro-only stops")
    print(f"  Updated: {summary['updated']}")

    # Read the existing index.html as template
    template_path = os.path.join(SCRIPT_DIR, "..", "index.html")
    if not os.path.exists(template_path):
        print("ERROR: index.html not found. Please upload index.html first.")
        return

    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Replace embedded JSON data placeholders
    replacements = {
        "SUMMARYJSON": json.dumps(summary, ensure_ascii=False),
        "SHAREDJSON": json.dumps(shared, ensure_ascii=False),
        "METROONLYJSON": json.dumps(metro_only[:800], ensure_ascii=False),
        "TOPLOSINGJSON": json.dumps(top_losing, ensure_ascii=False),
        "TOPOPPJSON": json.dumps(top_opp, ensure_ascii=False),
        "CITYSTATSJSON": json.dumps(city_stats, ensure_ascii=False),
        "REDLINEJSON": json.dumps(red_line, ensure_ascii=False),
    }

    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    size_mb = os.path.getsize(OUTPUT_PATH) / 1e6
    print(f"Dashboard saved: {OUTPUT_PATH} ({size_mb:.1f} MB)")
    print("Done! Commit and push index.html to update GitHub Pages.")


if __name__ == "__main__":
    main()
