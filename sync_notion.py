#!/usr/bin/env python3
"""
sync_notion.py — Fetch garden data from Notion and regenerate HTML pages.

Requires: NOTION_TOKEN environment variable set to a Notion integration token
with read access to the Fairlawns databases.

Databases:
  Beds:   4091d4c9-6ab4-4b03-a851-0445e0d1b618
  Plants: d94180e9-c262-4e40-8814-8fed0bf25f11
  Tasks:  4c8fd919-4605-48cc-aa56-3c15502ed925
"""

import json
import mimetypes
import os
import re
import sys
from datetime import date, timezone, datetime
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"
BEDS_DB   = "4091d4c9-6ab4-4b03-a851-0445e0d1b618"
PLANTS_DB = "d94180e9-c262-4e40-8814-8fed0bf25f11"
TASKS_DB  = "4c8fd919-4605-48cc-aa56-3c15502ed925"

# Pages to update (relative to this script's directory)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PAGES = ["index.html", "plants.html", "tasks.html", "beds.html", "plant.html"]
IMAGES_DIR = os.path.join(SCRIPT_DIR, "images", "plants")

# ── Notion API ────────────────────────────────────────────────────────────────

def notion_headers():
    if not NOTION_TOKEN:
        raise RuntimeError("NOTION_TOKEN environment variable is not set.")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def query_database(db_id, filter_body=None):
    """Fetch all pages from a Notion database (handles pagination)."""
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


# ── Property extractors ───────────────────────────────────────────────────────

def prop(page, name):
    """Get a property dict from a Notion page."""
    return page.get("properties", {}).get(name, None) or {}


def text_prop(page, name):
    """Extract plain text from title or rich_text property."""
    p = prop(page, name)
    t = p.get("type", "")
    if t in ("title", "rich_text"):
        parts = p.get(t, [])
        return "".join(r.get("plain_text", "") for r in parts).strip()
    if t == "select":
        sel = p.get("select")
        return sel.get("name", "") if sel else ""
    return ""


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


def files_prop_urls(page, name):
    """Extract download URLs from a Notion 'files' property."""
    p = prop(page, name)
    if p.get("type") != "files":
        return []
    urls = []
    for f in p.get("files", []):
        if f.get("type") == "file":
            urls.append(f.get("file", {}).get("url", ""))
        elif f.get("type") == "external":
            urls.append(f.get("external", {}).get("url", ""))
    return [u for u in urls if u]


def relation_urls(page, name):
    """Extract page URLs from a relation property by looking at related page ids."""
    p = prop(page, name)
    ids = [r.get("id", "") for r in p.get("relation", [])]
    return [f"https://app.notion.com/p/{i.replace('-', '')}" for i in ids if i]


def first_relation_url(page, name):
    urls = relation_urls(page, name)
    return urls[0] if urls else ""


def page_url(page):
    pid = page.get("id", "").replace("-", "")
    return f"https://app.notion.com/p/{pid}" if pid else ""


# ── Plant slugs & images ─────────────────────────────────────────────────────

def slugify(name):
    s = re.sub(r'[^a-z0-9]+', '-', (name or "").lower()).strip('-')
    return s or "plant"


def make_slug(name, page_id):
    short_id = page_id.replace('-', '')[:8]
    return f"{slugify(name)}-{short_id}"


def guess_extension(url, content_type):
    ext = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
    if not ext or ext == ".jpe":
        url_ext = os.path.splitext(urlparse(url).path)[1]
        if url_ext:
            ext = url_ext
    return ext or ".jpg"


IMAGE_FETCH_HEADERS = {
    # Some hosts (e.g. Wikimedia) reject requests without a UA identifying
    # the client, per https://meta.wikimedia.org/wiki/User-Agent_policy
    "User-Agent": "Mozilla/5.0 (compatible; FairlawnsGardenSync/1.0; "
                  "+https://github.com/jamesadmiller/fairlawns-garden)",
}


def download_plant_image(url, slug):
    """Download a plant image (Notion's internal file URLs expire, so we
    fetch and commit it to the repo). Returns the relative path on success,
    or None on failure/no image."""
    try:
        resp = requests.get(url, timeout=30, headers=IMAGE_FETCH_HEADERS)
        resp.raise_for_status()
    except Exception as e:
        print(f"  WARNING: failed to download image for {slug}: {e}", file=sys.stderr)
        return None

    os.makedirs(IMAGES_DIR, exist_ok=True)
    ext = guess_extension(url, resp.headers.get("Content-Type", ""))
    filename = f"{slug}{ext}"
    dest_path = os.path.join(IMAGES_DIR, filename)

    if os.path.exists(dest_path):
        with open(dest_path, "rb") as f:
            if f.read() == resp.content:
                return f"images/plants/{filename}"

    with open(dest_path, "wb") as f:
        f.write(resp.content)
    return f"images/plants/{filename}"


def cleanup_orphaned_images(keep_filenames):
    if not os.path.isdir(IMAGES_DIR):
        return
    for fname in os.listdir(IMAGES_DIR):
        if fname not in keep_filenames:
            os.remove(os.path.join(IMAGES_DIR, fname))


# ── Data fetchers ─────────────────────────────────────────────────────────────

def fetch_beds():
    print("Fetching beds…")
    pages = query_database(BEDS_DB)
    beds = []
    for p in pages:
        beds.append({
            "name":       text_prop(p, "Bed Name") or text_prop(p, "Name"),
            "garden":     select_prop(p, "Garden"),
            "sun":        select_prop(p, "Sun") or select_prop(p, "Sunlight"),
            "aspect":     text_prop(p, "Aspect"),
            "zone":       text_prop(p, "Zone"),
            "dimensions": text_prop(p, "Dimensions"),
            "area":       number_prop(p, "Area (m²)") or number_prop(p, "Area"),
            "notes":      text_prop(p, "Notes").replace('\r', ' ').replace('\n', ' '),
            "url":        page_url(p),
        })
    beds.sort(key=lambda b: b.get("name", ""))
    print(f"  \u2192 {len(beds)} beds")
    return beds


def fetch_plants(bed_url_map):
    print("Fetching plants…")
    pages = query_database(PLANTS_DB)
    plants = []
    keep_images = set()
    for p in pages:
        bed_url = (first_relation_url(p, "Bed") or
                   first_relation_url(p, "Garden Bed") or
                   first_relation_url(p, "Beds") or
                   first_relation_url(p, "Bed Name"))
        bed_name = bed_url_map.get(bed_url, "")
        notes = text_prop(p, "Notes").replace('\r', ' ').replace('\n', ' ')
        name = text_prop(p, "Plant Name") or text_prop(p, "Name")
        slug = make_slug(name, p.get("id", ""))

        image = ""
        image_urls = files_prop_urls(p, "Plant Image")
        if image_urls:
            image = download_plant_image(image_urls[0], slug) or ""
        if image:
            keep_images.add(os.path.basename(image))

        plants.append({
            "name":          name,
            "slug":          slug,
            "latin":         text_prop(p, "Latin Name"),
            "type":          select_prop(p, "Type"),
            "sun":           select_prop(p, "Sunlight"),
            "watering":      select_prop(p, "Watering"),
            "flowering":     text_prop(p, "Flowering Period"),
            "colour":        text_prop(p, "Flower Colour"),
            "size":          text_prop(p, "Mature Size (H x Spr)"),
            "difficulty":    select_prop(p, "Difficulty"),
            "pruning":       text_prop(p, "Pruning Month"),
            "pruning_how":   text_prop(p, "Pruning Instructions").replace('\r', ' ').replace('\n', ' '),
            "soil":          text_prop(p, "Soil Preference"),
            "propagation":   text_prop(p, "Division / Propagation"),
            "wildlife":      text_prop(p, "Wildlife Value"),
            "id_confidence": select_prop(p, "ID Confidence"),
            "rhs_link":      url_prop(p, "RHS Link"),
            "frost":         checkbox_prop(p, "Frost Protection"),
            "bed":           bed_name,
            "bed_url":       bed_url,
            "notes":         notes,
            "image":         image,
            "url":           page_url(p),
        })
    plants.sort(key=lambda pl: pl.get("name", ""))
    cleanup_orphaned_images(keep_images)
    print(f"  \u2192 {len(plants)} plants")
    return plants


def fetch_tasks(plant_url_map):
    print("Fetching tasks…")
    pages = query_database(TASKS_DB)
    tasks = []
    for p in pages:
        plant_url = first_relation_url(p, "Plant") or first_relation_url(p, "Plants")
        instructions = text_prop(p, "Instructions")[:300].replace('\r', ' ').replace('\n', ' ')
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
    print(f"  \u2192 {len(tasks)} tasks")
    return tasks


# ── HTML injection ────────────────────────────────────────────────────────────

DATA_RE = re.compile(
    r'<!-- DATA:START -->.*?<!-- DATA:END -->',
    re.DOTALL
)


def inject_data(html_path, garden_data):
    """Replace the DATA block in an HTML file with fresh JSON."""
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
        print(f"  WARNING: No DATA:START/END markers found in {html_path} — skipping")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Fairlawns Garden — Notion Sync")
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

    print("\nUpdating HTML pages…")
    for page in HTML_PAGES:
        path = os.path.join(SCRIPT_DIR, page)
        if os.path.exists(path):
            inject_data(path, garden_data)
        else:
            print(f"  MISSING: {path}")

    print("\nSync complete.")
    print(f"  Beds: {len(beds)} · Plants: {len(plants)} · Tasks: {len(tasks)}")


if __name__ == "__main__":
    main()
