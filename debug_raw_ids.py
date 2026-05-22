# debug_raw_ids.py
# Dump raw adamIds from MZStore to verify if app_id is in the list
import sys
sys.path.insert(0, ".")

import requests
import json
import re

from tracker import _extract_server_data

TARGET_IDS = ["1593130084", "6746151928"]  # Torchlight / 心动小镇 US IDs

def extract_raw_ids(region):
    url = "https://itunes.apple.com/WebObjects/MZStore.woa/wa/viewTop"
    params = {"genreId": "6014", "popId": "27", "cc": region}
    headers = {"User-Agent": "iTunes/12.0 (Windows; Microsoft Windows 10)"}
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    sd = _extract_server_data(resp.text)
    if not sd:
        return {}
    charts_raw = (
        sd.get("pageData", {})
        .get("topChartsPageData", {})
        .get("topCharts", [])
    )
    result = {}
    for c in charts_raw:
        title = c.get("shortTitle", "")
        ids = c.get("adamIds", [])
        result[title] = ids
    return result

for region in ["us", "gb", "jp", "kr", "sg", "de", "fr"]:
    print(f"=== Region: {region} ===")
    charts = extract_raw_ids(region)
    for title, ids in charts.items():
        for tid in TARGET_IDS:
            if tid in ids:
                rank = ids.index(tid) + 1
                print(f"  FOUND: {tid} in chart '{title}' at rank {rank}!")
    # If not found, print all IDs for manual inspection
    for title, ids in charts.items():
        found_any = any(tid in ids for tid in TARGET_IDS)
        if not found_any:
            print(f"  Chart '{title}': {len(ids)} apps, sample IDs: {ids[:5]}")
    print()
