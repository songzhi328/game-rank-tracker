#!/usr/bin/env python3
"""
GitHub Actions: Fetch Google Play rankings and upload to Gitee.

Runs in GitHub Actions (overseas runner, no GFW).
Fetches Google Play GAME category pages directly for all regions,
parses AF_initDataCallback JSON, and uploads the result to Gitee.

Required environment variables (GitHub Actions secrets):
  GITEE_TOKEN   — Gitee Personal Access Token (https://gitee.com/profile/personal_access_tokens)
  GITEE_OWNER   — Gitee username or org name
  GITEE_REPO    — Gitee repository name

Optional:
  GITEE_PATH    — File path in repo (default: google_play_rankings.json)

Usage:
  export GITEE_TOKEN=xxx GITEE_OWNER=xxx GITEE_REPO=xxx
  python3 fetch_and_upload.py

  # Without GITEE_* vars: prints JSON to stdout (for local testing/debugging)
"""

import base64
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("gitee_mirror")

# ── Google Play ──────────────────────────────────────────────────────────

PLAY_URL = "https://play.google.com/store/apps/category/GAME?gl={country}&hl=en"

REGIONS = {
    "us": "US", "gb": "GB", "jp": "JP", "kr": "KR",
    "sg": "SG", "th": "TH", "de": "DE", "fr": "FR",
    "hk": "HK", "tw": "TW",
    "mo": "MO", "my": "MY", "vn": "VN",
}

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_TIMEOUT = 30
MAX_APPS = 200  # Target apps per region

# Google Play collection page (JS-rendered, needs Playwright)
# Shows top-200+ free apps; the /category/GAME page only has ~50 server-rendered
COLLECTION_URL = "https://play.google.com/store/apps/collection/topselling_free?gl={country}&hl=en"


def parse_af_initdata(html: str) -> List[Dict]:
    """Parse Google Play GAME category page AF_initDataCallback (ds:3) JSON."""
    match = re.search(
        r"AF_initDataCallback\(\{key:\s*'ds:3'.*?data:(.*?), sideChannel:",
        html, re.DOTALL,
    )
    if not match:
        logger.warning("ds:3 data block not found")
        return []

    data_str = match.group(1)
    if len(data_str) < 1000:
        logger.warning("ds:3 data block too small (%d bytes)", len(data_str))
        return []

    try:
        data = json.loads(data_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse ds:3 JSON")
        return []

    cats = (
        data[0][1]
        if isinstance(data, list) and len(data) > 0
        and isinstance(data[0], list) and len(data[0]) > 1
        else []
    )
    if not isinstance(cats, list):
        return []

    seen_pkgs: set = set()
    all_apps: List[Dict] = []

    for cat in cats:
        if not isinstance(cat, list):
            continue

        app_list = None
        for idx in range(len(cat) - 1, -1, -1):
            elem = cat[idx]
            if not isinstance(elem, list) or len(elem) < 2:
                continue
            first_child = elem[0]
            if not isinstance(first_child, list):
                continue

            def _find_pkg(obj, depth=0):
                if depth > 6:
                    return None
                if isinstance(obj, list) and len(obj) >= 1:
                    if isinstance(obj[0], str) and '.' in obj[0] and len(obj[0]) > 15:
                        return obj[0]
                    for item in obj:
                        p = _find_pkg(item, depth + 1)
                        if p:
                            return p
                return None

            pkg = _find_pkg(elem)
            if pkg and '.' in pkg and not pkg.startswith('http') and not pkg.startswith('/'):
                app_list = first_child
                break

        if not app_list:
            continue

        for entry in app_list:
            if not isinstance(entry, list) or len(entry) < 1:
                continue
            left = entry[0] if isinstance(entry[0], list) else entry

            def _extract(obj, depth=0):
                out = []
                if depth > 10:
                    return out
                if isinstance(obj, str):
                    out.append((depth, obj))
                elif isinstance(obj, list):
                    for item in obj:
                        out.extend(_extract(item, depth + 1))
                return out

            strs = _extract(left)
            pkg = None
            for d, s in strs:
                if (
                    d == 2 and '.' in s and len(s) > 15
                    and not s.startswith('http')
                    and ' ' not in s
                    and s.split('.')[-1].isalpha()
                    and not any(c.isupper() for c in s[1:])
                    and s[0].islower()
                ):
                    pkg = s
                    break

            if not pkg or pkg in seen_pkgs:
                continue
            seen_pkgs.add(pkg)
            all_apps.append({"package_name": pkg})

    return all_apps


def fetch_region(region_code: str) -> Optional[Dict[str, Dict[str, int]]]:
    """Fetch and parse one region's GAME category page."""
    url = PLAY_URL.format(country=region_code)
    logger.info("Fetching %s (%s)…", region_code, url)

    try:
        req = urllib.request.Request(url, headers=FETCH_HEADERS)
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Fetch failed %s: %s", region_code, exc)
        return None

    if not html or len(html) < 50000:
        logger.warning("Response too small %s: %d bytes", region_code, len(html or ""))
        return None

    apps = parse_af_initdata(html)
    logger.info("  → %d apps", len(apps))

    result = {}
    for rank, app in enumerate(apps, start=1):
        pkg = app.get("package_name", "")
        if pkg:
            result[pkg] = {"free": rank, "paid": rank}

    return result


def _fetch_region_with_playwright(region_code: str) -> Optional[Dict[str, Dict[str, int]]]:
    """
    Use Playwright to load Google Play category page with full JS rendering.
    Starts with server-rendered AF_initDataCallback data (~50 apps), then scrolls
    to trigger lazy loading and get up to MAX_APPS (200) apps per region.

    Returns:
        {package_name: {"free": rank, "paid": rank}} or None on failure.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.info("playwright not installed, skipping Playwright fetch for %s", region_code)
        return None

    # Use the category page (server-rendered) - more reliable than collection page
    url = PLAY_URL.format(country=region_code)
    logger.info("Playwright: fetching %s (%s)…", region_code, url)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
                locale="en-US",
            )
            page = context.new_page()

            # Block resource-heavy third-party requests
            page.route("**/*", lambda route: (
                route.abort()
                if any(d in route.request.url for d in [
                    "google-analytics", "doubleclick", "googlesyndication",
                    "googleadservices", "youtube.com", "facebook.net",
                ])
                else route.continue_()
            ))

            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)  # extra wait for JS rendering

            # ---- Strategy 1: Extract from server-rendered AF_initDataCallback ----
            html_content = page.content()
            server_apps = parse_af_initdata(html_content)
            logger.info("  %s: %d apps from AF_initDataCallback", region_code, len(server_apps))

            seen: set = set()
            all_pkgs: List[str] = []

            # Add server-rendered apps first (deduplicated)
            for app in server_apps:
                pkg = app.get("package_name", "")
                if pkg and pkg not in seen:
                    seen.add(pkg)
                    all_pkgs.append(pkg)

            # ---- Strategy 2: Scroll and extract from DOM ----
            # Multiple selector patterns used by Google Play
            selectors = [
                'a[href*="?id="]',
                'a[href*="/store/apps/details?"]',
                'a[href*="details?id="]',
            ]

            for scroll_round in range(20):  # up to 20 scrolls
                prev_count = len(all_pkgs)

                # Try all selectors to extract package names
                for selector in selectors:
                    try:
                        found = page.evaluate(f"""() => {{
                            const links = document.querySelectorAll('{selector}');
                            const results = [];
                            const vs = new Set();
                            for (const link of links) {{
                                const m = link.href.match(/[?&]id=([^&]+)/);
                                if (m && !vs.has(m[1]) && m[1].includes('.')) {{
                                    vs.add(m[1]);
                                    results.push(m[1]);
                                }}
                            }}
                            return results;
                        }}""")
                        for pkg in found:
                            if pkg not in seen:
                                seen.add(pkg)
                                all_pkgs.append(pkg)
                    except Exception:
                        pass

                new_count = len(all_pkgs) - prev_count
                if new_count > 0 or scroll_round == 0:
                    logger.info("  %s scroll %d: %d apps (+%d)",
                                region_code, scroll_round + 1, len(all_pkgs), new_count)

                if len(all_pkgs) >= MAX_APPS:
                    logger.info("  %s: reached %d apps, stopping", region_code, MAX_APPS)
                    break

                if scroll_round > 0 and new_count == 0:
                    logger.info("  %s: no new apps after scroll, reached end", region_code)
                    break

                # Scroll down to trigger lazy loading
                page.evaluate("window.scrollBy(0, 4000)")
                page.wait_for_timeout(3000)

            browser.close()

            if not all_pkgs:
                logger.warning("Playwright %s: no apps found", region_code)
                return None

            result = {}
            for rank, pkg in enumerate(all_pkgs[:MAX_APPS], start=1):
                result[pkg] = {"free": rank, "paid": rank}

            logger.info("  %s final: %d apps via Playwright", region_code, len(result))
            return result

    except Exception as exc:
        logger.warning("Playwright %s failed: %s", region_code, exc)
        return None


def gather_all_regions(use_playwright: bool = False) -> Dict:
    """Fetch all regions and return the full data structure.

    Args:
        use_playwright: If True, use Playwright for JS-rendered pages
                        to get up to MAX_APPS per region.
    """
    regions_data = {}
    for name, code in REGIONS.items():
        if use_playwright:
            data = _fetch_region_with_playwright(code)
        else:
            data = fetch_region(code)
        if data:
            regions_data[code] = data
        else:
            logger.warning("Skipping %s due to fetch failure", code)

    return {
        "fetched_at": __import__("datetime").datetime.utcnow().strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "regions": regions_data,
    }


# ── Gitee upload ─────────────────────────────────────────────────────────

GITEE_API_BASE = "https://gitee.com/api/v5"


def gitee_get_file(token: str, owner: str, repo: str, path: str) -> Optional[str]:
    """Get SHA of existing file on Gitee. Returns None if file doesn't exist."""
    url = f"{GITEE_API_BASE}/repos/{owner}/{repo}/contents/{path}?access_token={token}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("sha")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        logger.warning("Gitee GET error: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Gitee GET error: %s", exc)
        return None


def gitee_upload(
    token: str, owner: str, repo: str, path: str,
    content_str: str, commit_msg: str,
) -> bool:
    """Create or update a file on Gitee. Returns True on success."""
    existing_sha = gitee_get_file(token, owner, repo, path)
    content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")

    body = {
        "access_token": token,
        "content": content_b64,
        "message": commit_msg,
    }
    if existing_sha:
        body["sha"] = existing_sha

    url = f"{GITEE_API_BASE}/repos/{owner}/{repo}/contents/{path}"
    data_json = json.dumps(body).encode("utf-8")

    try:
        req = urllib.request.Request(
            url, data=data_json,
            headers={"Content-Type": "application/json;charset=UTF-8"},
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            logger.info("Uploaded to Gitee: %s", resp_data.get("content", {}).get("path", path))
            return True
    except urllib.error.HTTPError as exc:
        logger.error("Gitee upload failed (HTTP %s): %s", exc.code, exc.read().decode())
        return False
    except Exception as exc:
        logger.error("Gitee upload failed: %s", exc)
        return False


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    token = os.environ.get("GITEE_TOKEN", "")
    owner = os.environ.get("GITEE_OWNER", "")
    repo = os.environ.get("GITEE_REPO", "")
    file_path = os.environ.get("GITEE_PATH", "google_play_rankings.json")
    use_playwright = "--playwright" in sys.argv or os.environ.get("PLAYWRIGHT", "").strip() == "1"

    # Fetch data
    logger.info("=== Fetching Google Play rankings (%s) ===",
                "Playwright" if use_playwright else "direct HTTP")
    data = gather_all_regions(use_playwright=use_playwright)

    total_apps = sum(len(pkgs) for pkgs in data["regions"].values())
    total_regions = len(data["regions"])
    logger.info("Fetched %d regions, %d total app entries", total_regions, total_apps)

    json_str = json.dumps(data, ensure_ascii=False, indent=2)

    # Upload to Gitee
    if token and owner and repo:
        commit_msg = f"Update Google Play rankings [{data['fetched_at'][:10]}]"
        logger.info("=== Uploading to Gitee: %s/%s/%s ===", owner, repo, file_path)
        ok = gitee_upload(token, owner, repo, file_path, json_str, commit_msg)
        if ok:
            logger.info("Upload successful!")
        else:
            logger.error("Upload failed!")
            sys.exit(1)
    else:
        # No Gitee credentials: print to stdout (for local testing)
        logger.info("GITEE_TOKEN/OWNER/REPO not set — printing JSON to stdout")
        print(json_str)


if __name__ == "__main__":
    main()
