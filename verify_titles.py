# verify_titles.py
# Verify exact shortTitle values for FR, HK, MO
import requests
import json

def extract_server_data(text):
    marker = "its.serverData="
    start = text.find(marker)
    if start == -1:
        return {}
    start += len(marker)
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if i >= len(text):
        return {}
    try:
        return json.loads(text[start:i+1])
    except Exception:
        return {}

headers = {"User-Agent": "iTunes/12.0 (Windows; Microsoft Windows 10)"}

for region in ["fr", "hk", "mo"]:
    resp = requests.get(
        "https://itunes.apple.com/WebObjects/MZStore.woa/wa/viewTop",
        params={"genreId": "6014", "popId": "27", "cc": region},
        headers=headers,
        timeout=15,
    )
    sd = extract_server_data(resp.text)
    page_data = sd.get("pageData")
    if page_data is None:
        print(f"{region}: no pageData")
        continue
    top_page = page_data.get("topChartsPageData")
    if top_page is None:
        print(f"{region}: no topChartsPageData")
        continue
    charts = top_page.get("topCharts") or []
    print(f"=== Region: {region} ===")
    for c in charts:
        st = c.get("shortTitle", "")
        t = c.get("title", "")
        ids = c.get("adamIds") or []
        print(f"  shortTitle={st!r}")
        print(f"    title={t!r}")
        print(f"    adamIds count={len(ids)}")
    if not charts:
        print("  (no charts found)")
    print()
