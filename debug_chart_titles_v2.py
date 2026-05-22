# debug_chart_titles_v2.py
# Double-check exact shortTitle values for FR, HK, MO
import sys
sys.path.insert(0, ".")

import requests
import json

from tracker import _extract_server_data

for region in ["fr", "hk", "mo"]:
    url = "https://itunes.apple.com/WebObjects/MZStore.woa/wa/viewTop"
    params = {"genreId": "6014", "popId": "27", "cc": region}
    headers = {"User-Agent": "iTunes/12.0 (Windows; Microsoft Windows 10)"}
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    sd = _extract_server_data(resp.text)
    charts = (sd.get("pageData") or {}).get("topChartsPageData") or {}).get("topCharts") or []
    print(f"=== Region: {region} ===")
    for c in charts:
        st = c.get("shortTitle", "")
        t = c.get("title", "")
        ids = c.get("adamIds") or []
        print(f"  shortTitle={st!r}")
        print(f"    title={t!r}")
        print(f"    adamIds count={len(ids)}")
    print()
