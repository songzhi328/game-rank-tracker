# fetch_now.py
# Independent script to trigger rankings fetch for all games across all stores and regions.
# Can be called directly or by automation (e.g., cron / WorkBuddy scheduler).
#
# Usage:
#   python fetch_now.py           # run once, print results (iOS + Google Play)
#   python fetch_now.py --web     # trigger via HTTP API (requires server running)
#   python fetch_now.py --ios     # iOS only
#   python fetch_now.py --google  # Google Play only

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def fetch_direct(target: str = "all") -> None:
    """Call tracker and database directly (no HTTP server needed)."""
    import database
    import tracker
    from google_tracker import fetch_and_save_google_rankings

    database.init_db()
    database.seed_default_games()

    if target in ("all", "ios"):
        logger.info("===== 开始查询 iOS App Store 排名 =====")
        ios_results = tracker.fetch_and_save_rankings()
    else:
        ios_results = {}

    if target in ("all", "google"):
        logger.info("===== 开始查询 Google Play Store 排名 =====")
        google_results = fetch_and_save_google_rankings()
    else:
        google_results = {}

    # Print summary
    games = database.get_all_games()
    print("\n====== 排名查询结果 ======")

    if ios_results:
        print("\n--- iOS App Store ---")
        for game in games:
            gid = game["id"]
            gname = game["name"]
            region_data = ios_results.get(gid, {})
            if not region_data:
                continue
            print(f"\n【{gname}】(iOS)")
            for region in sorted(region_data.keys()):
                r = region_data[region]
                free = r.get("free", -1)
                grossing = r.get("grossing", -1)
                fs = str(free) if free != -1 else "—"
                gs = str(grossing) if grossing != -1 else "—"
                print(f"  {region:3s} | 免费: {fs:>4s} | 畅销: {gs:>4s}")

    if google_results:
        print("\n--- Google Play Store ---")
        for game in games:
            gid = game["id"]
            gname = game["name"]
            region_data = google_results.get(gid, {})
            if not region_data:
                continue
            print(f"\n【{gname}】(Google Play)")
            for region in sorted(region_data.keys()):
                r = region_data[region]
                free = r.get("free", -1)
                paid = r.get("paid", -1)
                fs = str(free) if free != -1 else "—"
                ps = str(paid) if paid != -1 else "—"
                print(f"  {region:3s} | 免费: {fs:>4s} | 付费: {ps:>4s}")

    last_checked = database.get_last_checked_at()
    print(f"\n查询时间: {last_checked}")


def fetch_via_web() -> None:
    """Trigger fetch via HTTP API (requires Flask server on port 5000)."""
    import json

    import requests

    try:
        resp = requests.post("http://localhost:5000/api/fetch-now", timeout=600)
        data = resp.json()
        if data.get("success"):
            logger.info("查询完成: %s", data.get("last_checked"))
            results = data.get("results", {})

            ios_data = results.get("ios", {})
            for gid, regions in ios_data.items():
                for region, ranks in regions.items():
                    f = ranks.get("free", -1)
                    g = ranks.get("grossing", -1)
                    fs = str(f) if f != -1 else "—"
                    gs = str(g) if g != -1 else "—"
                    print(f"  iOS Game {gid} | {region:3s} | free={fs:>4s} | grossing={gs:>4s}")

            google_data = results.get("google", {})
            for gid, regions in google_data.items():
                for region, ranks in regions.items():
                    f = ranks.get("free", -1)
                    p = ranks.get("paid", -1)
                    fs = str(f) if f != -1 else "—"
                    ps = str(p) if p != -1 else "—"
                    print(f"  Google Game {gid} | {region:3s} | free={fs:>4s} | paid={ps:>4s}")
        else:
            logger.error("查询失败: %s", data.get("error"))
            sys.exit(1)
    except requests.exceptions.ConnectionError:
        logger.error("无法连接服务器，请确认 python app.py 已启动")
        sys.exit(1)


if __name__ == "__main__":
    target = "all"
    if "--web" in sys.argv:
        fetch_via_web()
    else:
        if "--ios" in sys.argv:
            target = "ios"
        elif "--google" in sys.argv:
            target = "google"
        fetch_direct(target)
