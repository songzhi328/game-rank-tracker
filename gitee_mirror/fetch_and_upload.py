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


def gather_all_regions() -> Dict:
    """Fetch all regions and return the full data structure."""
    regions_data = {}
    for name, code in REGIONS.items():
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

    # Fetch data
    logger.info("=== Fetching Google Play rankings ===")
    data = gather_all_regions()

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
