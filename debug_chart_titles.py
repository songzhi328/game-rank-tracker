# debug_chart_titles.py
# Debug what chart titles MZStore returns for DE and FR regions
import requests
import json

headers = {"User-Agent": "iTunes/12.0 (Windows; Microsoft Windows 10)"}

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

for region in ["de", "fr"]:
    url = "https://itunes.apple.com/WebObjects/MZStore.woa/wa/viewTop"
    params = {"genreId": "6014", "popId": "27", "cc": region}
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    sd = extract_server_data(resp.text)
    page_data = sd.get("pageData") or {}
    top_charts_page = page_data.get("topChartsPageData") or {}
    charts = top_charts_page.get("topCharts") or []
    print(f"=== Region: {region} ===")
    for c in charts:
        print(f"  shortTitle: {c.get('shortTitle')!r}  title: {c.get('title')!r}")
    if not charts:
        print("  (no charts found in serverData)")
    print()
