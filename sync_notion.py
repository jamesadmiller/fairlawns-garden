#!/usr/bin/env python3
"""
sync_notion.py - Fetch garden data from Notion and regenerate HTML pages.

Requires: NOTION_TOKEN environment variable set to a Notion integration token
with read access to the Fairlawns databases.

Databases:
  Beds:   660ba75d-a1d9-4b98-9006-c832a320df94
  Plants: 35f8fbd3-1096-4b50-a47b-f73e2e34e7c4
  Tasks:  9b857676-d399-4710-9350-bb661826d709
"""

import json
import os
import re
import sys
from datetime import date, timezone, datetime

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"
BEDS_DB   = "660ba75d-a1d9-4b98-9006-c832a320df94"
PLANTS_DB = "35f8fbd3-1096-4b50-a47b-f73e2e34e7c4"
TASKS_DB  = "9b857676-d399-4710-9350-bb661826d709"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PAGES = ["index.html", "plants.html", "tasks.html", "beds.html"]

def notion_headers():
    if not NOTION_TOKEN:
        raise RuntimeError("NOTION_TOKEN environment variable is not set.")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

def query_database(db_id, filter_body=None):
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    headers = notion_headers()
    results = []
    body = {}
    if filter_body:
        body.update(filter_body)
    while True:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        body["start_cursor"] = data["next_cursor"]
    return results

def prop(page, name, fallback=None):
    return page.get("properties", {}).get(name, None) or {}

def text_prop(page, name):
    p = prop(page, name)
    t = p.get("type", "")
    if t in ("title", "rich_text"):
        parts = p.get(t, [])
        return "".join(r.get("plain_text", "") for r in parts).strip()
    if t == "select":
        sel = p.get("select")
        return sel.get("name", "") if sel else ""
    return fallback or ""

def select_prop(page, name):
    p = prop(page, name)
    sel = p.get("select")
    return sel.get("name", "") if sel else ""

def checkbox_prop(page, name):
    p = prop(page, name)
    return bool(p.get("checkbox", False))

def number_prop(page, name):
    p = prop(page, name)
    return p.get("number")

def url_prop(page, name):
    p = prop(page, name)
    return p.get("url", "")

def relation_urls(page, name):
    p = prop(page, name)
    ids = [r.get("id", "") for r in p.get("relation", [])]
    return [f"https://app.notion.com/p/{i.replace('-', '')}" for i in ids if i]

def first_relation_url(page, name):
    urls = relation_urls(page, name)
    return urls[0] if urls else ""

def page_url(page):
    pid = page.get("id", "").replace("-", "")
    return f"https://app.notion.com/p/{pid}" if pid else ""

def fetch_beds():
    print("Fetching beds...")
    pages = query_database(BEDS_DB)
    beds = []
    for p in pages:
        beds.append({
            "name":       text_prop(p, "Name") or text_prop(p, "Bed Name"),
            "garden":     select_prop(p, "Garden"),
            "sun":        select_prop(p, "Sunlight") or select_prop(p, "Sun"),
            "aspect":     text_prop(p, "Aspect"),
            "zone":       text_prop(p, "Zone"),
            "dimensions": text_prop(p, "Dimensions"),
            "area":       number_prop(p, "Area (m2)") or number_prop(p, "Area"),
            "notes":      text_prop(p, "Notes"),
            "url":        page_url(p),
        })
    beds.sort(key=lambda b: b.get("name", ""))
    print(f"  -> {len(beds)} beds")
    return beds

def fetch_plants(bed_url_map):
    print("Fetching plants...")
    pages = query_database(PLANTS_DB)
    plants = []
    for p in pages:
        bed_url = (first_relation_url(p, "Bed") or
                   first_relation_url(p, "Garden Bed") or
                   first_relation_url(p, "Beds"))
        bed_name = bed_url_map.get(bed_url, "")
        notes = text_prop(p, "Notes")[:200]
        plants.append({
            "name":     text_prop(p, "Plant Name") or text_prop(p, "Name"),
            "latin":    text_prop(p, "Latin Name"),
            "type":     select_prop(p, "Type"),
            "sun":      select_prop(p, "Sunlight"),
            "watering": select_prop(p, "Watering"),
            "flowering":text_prop(p, "Flowering Period"),
            "colour":   text_prop(p, "Flower Colour"),
            "size":     text_prop(p, "Mature Size"),
            "difficulty": select_prop(p, "Difficulty"),
            "pruning":  text_prop(p, "Pruning Month"),
            "frost":    checkbox_prop(p, "Frost Protection"),
            "bed":      bed_name,
            "bed_url":  bed_url,
            "notes":    notes,
            "url":      page_url(p),
        })
    plants.sort(key=lambda pl: pl.get("name", ""))
    print(f"  -> {len(plants)} plants")
    return plants

def fetch_tasks(plant_url_map):
    print("Fetching tasks...")
    pages = query_database(TASKS_DB)
    tasks = []
    for p in pages:
        plant_url = first_relation_url(p, "Plant") or first_relation_url(p, "Plants")
        instructions = text_prop(p, "Instructions")[:300]
        tasks.append({
            "name":         text_prop(p, "Task Name") or text_prop(p, "Name"),
            "type":         select_prop(p, "Task Type"),
            "month":        select_prop(p, "Month"),
            "priority":     select_prop(p, "Priority"),
            "done":         checkbox_prop(p, "Done"),
            "instructions": instructions,
            "plant_url":    plant_url,
            "url":          page_url(p),
        })
    tasks.sort(key=lambda t: (t.get("month", ""), t.get("priority", "")))
    print(f"  -> {len(tasks)} tasks")
    return tasks

DATA_RE = re.compile(r'<!-- DATA:START -->.*?<!-- DATA:END -->', re.DOTALL)

def inject_data(html_path, garden_data):
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    data_json = json.dumps(garden_data, separators=(",", ":"), ensure_ascii=False)
    replacement = (
        "<!-- DATA:START -->\n"
        f'<script id="garden-data" type="application/json">{data_json}</script>\n'
        "<!-- DATA:END -->"
    )
    if DATA_RE.search(content):
        new_content = DATA_RE.sub(replacement, content)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"  Updated {os.path.basename(html_path)}")
    else:
        print(f"  WARNING: No DATA markers found in {html_path}")

def main():
    print("=" * 60)
    print("Fairlawns Garden - Notion Sync")
    print(f"Date: {date.today()}")
    print("=" * 60)

    beds = fetch_beds()
    bed_url_map = {b["url"]: b["name"] for b in beds}
    plants = fetch_plants(bed_url_map)
    plant_url_map = {pl["url"]: pl["name"] for pl in plants}
    tasks = fetch_tasks(plant_url_map)

    garden_data = {
        "updated": str(date.today()),
        "beds":    beds,
        "plants":  plants,
        "tasks":   tasks,
    }

    data_path = os.path.join(SCRIPT_DIR, "garden-data.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(garden_data, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {data_path}")

    print("\nUpdating HTML pages...")
    for page in HTML_PAGES:
        path = os.path.join(SCRIPT_DIR, page)
        if os.path.exists(path):
            inject_data(path, garden_data)
        else:
            print(f"  MISSING: {path}")

    print("\nSync complete.")
    print(f"  Beds: {len(beds)} - Plants: {len(plants)} - Tasks: {len(tasks)}")

if __name__ == "__main__":
    main()
