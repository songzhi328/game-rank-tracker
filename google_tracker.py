# google_tracker.py
# Google Play Store rank tracking
#
# Data sources (tried in order):
#   1. Gitee mirror — GitHub Actions (overseas runner) fetches Google Play
#      directly and uploads JSON to Gitee. Set GITEE_RAW_URL to enable.
#   2. codetabs.com — free proxy relay (circumvents GFW, limited to ~50 apps)
#   3. Playwright fallback — Vercel proxy or qimai.cn SPA (legacy)

import json
import logging
import os
import re
import time
import urllib.request
import urllib.parse
from typing import Dict, List, Optional
from urllib.parse import urlencode

import config
import database

logger = logging.getLogger(__name__)

COGETABS_PROXY = "https://api.codetabs.com/v1/proxy?quest="

# Gitee mirror — GitHub Actions fetches Google Play (overseas) → uploads JSON to Gitee
# Supports two access methods:
#   1. Raw URL (public repo): GITEE_RAW_URL
#      e.g. GITEE_RAW_URL=https://gitee.com/user/repo/raw/branch/path/file.json
#   2. API + Token (private/public): GITEE_TOKEN + GITEE_OWNER + GITEE_REPO
#      Optional: GITEE_PATH (default: google_play_rankings.json), GITEE_BRANCH (default: master)
GITEE_RAW_URL = os.environ.get("GITEE_RAW_URL", "").strip()
GITEE_TOKEN = os.environ.get("GITEE_TOKEN", "").strip()
GITEE_OWNER = os.environ.get("GITEE_OWNER", "").strip()
GITEE_REPO = os.environ.get("GITEE_REPO", "").strip()

# Google Play Store collection types
_COLLECTION_FREE = "topselling_free"
_COLLECTION_PAID = "topselling_paid"

# Google Play collection URL — the /category/GAME page has server-rendered data
# (collection pages like /store/apps/collection/topselling_free load via JS)
_PLAY_URL = "https://play.google.com/store/apps/category/GAME?gl={country}&hl=en"


# ── Strategy 1: codetabs.com Proxy (primary) ───────────────────────────


def _parse_af_initdata(html: str) -> List[Dict]:
    """
    Parse Google Play GAME category page to extract app ranking data from
    AF_initDataCallback blocks (server-rendered JSON in ds:3).

    The GAME category page has ~6 sections; the first section (index 0)
    contains the top-selling free games ranked by popularity.

    Returns list of dicts with package_name in page order.
    """
    # Parse the ds:3 data block from the GAME category page
    match = re.search(
        r"AF_initDataCallback\(\{key:\s*'ds:3'.*?data:(.*?), sideChannel:",
        html, re.DOTALL,
    )
    if not match:
        logger.warning("ds:3 data block not found in Google Play page")
        return []

    data_str = match.group(1)
    if len(data_str) < 1000:
        logger.warning("ds:3 data block too small (%d bytes), page likely loaded data via JS", len(data_str))
        return []

    try:
        data = json.loads(data_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse ds:3 JSON data")
        return []

    # Navigate: data = [[null, [cat1, cat2, ...]]]
    cats = data[0][1] if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list) and len(data[0]) > 1 else []
    if not isinstance(cats, list):
        return []

    seen_pkgs: set = set()
    all_apps: List[Dict] = []

    for cat in cats:
        if not isinstance(cat, list):
            continue

        # Find the app list - typically the last element with multiple sub-items
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

        # Parse each app entry from this category
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
                if d == 2 and '.' in s and len(s) > 15 and not s.startswith('http'):
                    # Validate it looks like a real package name (reverse-domain notation)
                    if (
                        s.count('.') >= 1
                        and ' ' not in s
                        and s.split('.')[-1].isalpha()
                        and not any(c.isupper() for c in s[1:])  # no mixed case
                        and s[0].islower()
                    ):
                        pkg = s
                        break

            if not pkg or pkg in seen_pkgs:
                continue
            seen_pkgs.add(pkg)

            all_apps.append({"package_name": pkg})

    return all_apps


def _fetch_via_codetabs_proxy() -> Optional[Dict[str, Dict[str, Dict[str, int]]]]:
    """
    Fetch Google Play rankings via api.codetabs.com free proxy.

    Fetches the GAME category page (which has server-rendered JSON data)
    once per region and extracts app rankings from the embedded data.

    Returns:
        {region_code: {package_name: {"free": rank, "paid": rank}}} or None on failure.
    """
    all_results: Dict[str, Dict[str, Dict[str, int]]] = {}

    for region_name, region_code in config.GOOGLE_PLAY_REGIONS.items():
        play_url = _PLAY_URL.format(country=region_code)
        proxy_url = COGETABS_PROXY + urllib.parse.quote(play_url)

        logger.info("Codetabs proxy: %s (%s)", region_name, region_code)

        try:
            req = urllib.request.Request(
                proxy_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Codetabs fetch failed %s: %s", region_code, exc)
            continue

        if not html or len(html) < 50000:
            logger.warning("Codetabs response too small %s: %d bytes", region_code, len(html or ""))
            continue

        apps = _parse_af_initdata(html)
        logger.info("Google Play %s: %d apps found", region_code, len(apps))

        region_data: Dict[str, Dict[str, int]] = {}
        for rank, app in enumerate(apps, start=1):
            pkg = app.get("package_name", "")
            if not pkg:
                continue
            # Use the same rank for both free and paid (GAME page is default top chart)
            region_data[pkg] = {"free": rank, "paid": rank}

        all_results[region_code] = region_data

    return all_results if all_results else None


# ── Strategy 2: Gitee mirror (primary, if configured) ────────────────────
# GitHub Actions → fetches Google Play (overseas runner) → uploads JSON to Gitee
# This server → downloads JSON from Gitee directly (no GFW issues)


def _fetch_from_gitee() -> Optional[Dict[str, Dict[str, Dict[str, int]]]]:
    """
    Fetch Google Play rankings from Gitee mirror.

    Supports two access methods:
      1. Public repo: set GITEE_RAW_URL
      2. Private/public repo: set GITEE_TOKEN + GITEE_OWNER + GITEE_REPO
         (optionally GITEE_BRANCH, default master; GITEE_PATH, default google_play_rankings.json)

    Retries up to GITEE_RETRIES times (default 6) with GITEE_RETRY_DELAY
    seconds (default 60) between attempts. This handles the case where
    GitHub Actions was manually triggered just before — the data may take
    a minute or two to arrive on Gitee.

    Returns:
        {region_code: {package_name: {"free": rank, "paid": rank}}} or None.
    """
    if not GITEE_RAW_URL and not (GITEE_TOKEN and GITEE_OWNER and GITEE_REPO):
        logger.info("Gitee mirror not configured (set GITEE_RAW_URL or GITEE_TOKEN+OWNER+REPO)")
        return None

    max_retries = int(os.environ.get("GITEE_RETRIES", "6"))
    retry_delay = int(os.environ.get("GITEE_RETRY_DELAY", "60"))

    for attempt in range(1, max_retries + 1):
        # Determine URL and auth method
        if GITEE_RAW_URL:
            fetch_url = GITEE_RAW_URL
            headers = {"User-Agent": "Mozilla/5.0"}
        else:
            branch = os.environ.get("GITEE_BRANCH", "master").strip()
            path = os.environ.get("GITEE_PATH", "google_play_rankings.json").strip()
            fetch_url = (
                f"https://gitee.com/api/v5/repos/{GITEE_OWNER}/{GITEE_REPO}"
                f"/contents/{path}?access_token={GITEE_TOKEN}&ref={branch}"
            )
            headers = {"User-Agent": "Mozilla/5.0"}

        logger.info("Gitee fetch attempt %d/%d", attempt, max_retries)
        try:
            req = urllib.request.Request(fetch_url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
        except Exception as exc:
            if attempt < max_retries:
                logger.warning("Gitee fetch failed (attempt %d/%d): %s. Retrying in %ds…",
                               attempt, max_retries, exc, retry_delay)
                time.sleep(retry_delay)
                continue
            logger.warning("Gitee fetch failed after %d attempts: %s", max_retries, exc)
            return None

        # Parse: raw URL returns JSON directly, API returns {content: base64, ...}
        try:
            # Detect API response: has "content" and "sha" keys
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "content" in parsed and "sha" in parsed:
                import base64
                data = json.loads(base64.b64decode(parsed["content"]).decode("utf-8"))
            else:
                data = parsed
        except json.JSONDecodeError:
            logger.warning("Gitee data is not valid JSON")
            return None
        except Exception as exc:
            logger.warning("Gitee data parse failed: %s", exc)
            return None

        regions = data.get("regions")
        if not isinstance(regions, dict) or not regions:
            if attempt < max_retries:
                logger.warning("Gitee data has no regions (attempt %d/%d). Retrying in %ds…",
                               attempt, max_retries, retry_delay)
                time.sleep(retry_delay)
                continue
            logger.warning("Gitee data has no regions after %d attempts", max_retries)
            return None

        fetched_at = data.get("fetched_at", "unknown")
        total = sum(len(v) for v in regions.values() if isinstance(v, dict))
        logger.info("Gitee mirror: %d regions, %d apps (fetched %s)", len(regions), total, fetched_at)
        return regions

    return None


# ── Strategy 3: Vercel Proxy / Playwright (legacy) ───────────────────────


def _fetch_via_vercel_proxy() -> Optional[Dict[str, Dict[str, int]]]:
    """
    Fetch Google Play top charts via a Vercel proxy relay using Playwright.

    Requires the VERCEL_PROXY_URL environment variable to be set.
    The Vercel proxy function (see vercel-proxy/) fetches Google Play Store
    pages and returns the HTML. Playwright loads the proxied HTML and
    extracts ranking data from the DOM.

    Returns:
        {package_name: {"free": rank, "paid": rank}} or None on failure.
    """
    proxy_base = os.environ.get("VERCEL_PROXY_URL", "").strip()
    if not proxy_base:
        logger.info("VERCEL_PROXY_URL not set, skipping Vercel proxy strategy")
        return None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed (pip install playwright)")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()

            # Block Google-hosted resources (unreachable from China)
            # but allow the page itself to load from the Vercel proxy
            page.route("**/*", lambda route: (
                route.abort()
                if any(d in route.request.url for d in [
                    "google.com", "gstatic.com", "googlesyndication.com",
                    "googleadservices.com", "youtube.com",
                ])
                else route.continue_()
            ))

            all_results: Dict[str, Dict[str, int]] = {}

            for chart_type, collection in [
                ("free", _COLLECTION_FREE),
                ("paid", _COLLECTION_PAID),
            ]:
                for region_name, region_code in config.GOOGLE_PLAY_REGIONS.items():
                    play_url = _PLAY_URL.format(
                        collection=collection,
                        country=region_code.upper(),
                    )
                    proxy_url = f"{proxy_base}?{urlencode({'url': play_url})}"

                    logger.info(
                        "Vercel proxy: %s / %s (%s)",
                        chart_type, region_name, region_code,
                    )

                    try:
                        page.goto(proxy_url, timeout=30000, wait_until="domcontentloaded")
                        page.wait_for_timeout(3000)
                    except Exception as exc:
                        logger.warning(
                            "Vercel proxy page load failed %s/%s: %s",
                            chart_type, region_code, exc,
                        )
                        continue

                    # Check for proxy error JSON response
                    body_text = page.eval_on_selector("body", "el => el.innerText")
                    if body_text and body_text.strip().startswith("{"):
                        try:
                            err = json.loads(body_text)
                            if err.get("error"):
                                logger.warning(
                                    "Proxy error for %s/%s: %s",
                                    chart_type, region_code, err["error"],
                                )
                                continue
                        except json.JSONDecodeError:
                            pass

                    # Extract ranking data from the page
                    apps = _parse_google_play_page(page)
                    logger.info(
                        "Google Play %s/%s: %d apps found",
                        chart_type, region_code, len(apps),
                    )

                    for rank, app in enumerate(apps, start=1):
                        pkg = app.get("packageName", "")
                        if not pkg:
                            continue
                        if pkg not in all_results:
                            all_results[pkg] = {}
                        all_results[pkg][chart_type] = rank

            browser.close()
            return all_results if all_results else None

    except Exception as exc:
        logger.error("Vercel proxy strategy failed: %s", exc, exc_info=True)
        return None


def _parse_google_play_page(page) -> List[Dict[str, str]]:
    """
    Extract app listings from a Google Play Store ranking page loaded in
    Playwright. Apps appear in DOM order (rank 1, 2, 3...).

    Uses multiple extraction strategies for resilience against Google's
    frequently-changing CSS class names.

    Returns:
        [{packageName, name}, ...] in DOM/rank order.
    """
    # Strategy A: Find all links containing app detail URLs
    # href="/store/apps/details?id=com.example.app"
    data = page.evaluate("""() => {
        const results = [];
        const seen = new Set();

        // Find all app detail links
        const links = document.querySelectorAll('a[href*="?id="]');
        for (const link of links) {
            const m = link.href.match(/[?&]id=([^&]+)/);
            if (!m || seen.has(m[1])) continue;
            seen.add(m[1]);

            // Try to find app name: check img alt, link text, surrounding text
            let name = '';
            const img = link.querySelector('img');
            if (img && img.alt) name = img.alt;
            if (!name) name = link.innerText.trim();
            if (!name) {
                // Walk parent for a title-like element
                let parent = link.parentElement;
                for (let i = 0; i < 5 && parent; i++) {
                    const titleEl = parent.querySelector('[title]');
                    if (titleEl) { name = titleEl.getAttribute('title'); break; }
                    parent = parent.parentElement;
                }
            }
            if (!name) name = m[1];

            results.push({
                packageName: m[1],
                name: name,
            });
        }

        return results;
    }""")

    # If strategy A returned nothing, try strategy B: search for typical
    # Google Play card selectors
    if not data:
        data = page.evaluate("""() => {
            const results = [];
            const seen = new Set();

            // Strategy B: look for img elements with alt text that may link to apps
            const imgs = document.querySelectorAll('img[alt]');
            for (const img of imgs) {
                if (!img.alt || seen.has(img.alt)) continue;
                const link = img.closest('a');
                if (!link) continue;
                const m = link.href.match(/[?&]id=([^&]+)/);
                if (!m) continue;
                seen.add(img.alt);
                results.push({
                    packageName: m[1],
                    name: img.alt,
                });
            }

            return results;
        }""")

    # Clean up results — filter out non-app entries
    filtered = []
    for item in data:
        pkg = item.get("packageName", "").strip()
        if not pkg:
            continue
        # Skip entries that are clearly not Google Play apps
        if pkg.startswith("http") or pkg.startswith("/"):
            continue
        filtered.append(item)

    return filtered


# ── Strategy 2: qimai.cn Playwright (fallback) ─────────────────────────


def _fetch_via_qimai_playwright() -> Optional[Dict[str, Dict[str, int]]]:
    """
    Fetch Google Play top charts via qimai.cn using Playwright.
    Fallback strategy when Vercel proxy is not available.

    qimai.cn requires an 'analysis' parameter signed by their SPA JavaScript.
    By running fetch() inside the browser context where the SPA is loaded,
    the webpack module interceptors automatically sign the request.

    Returns:
        {package_name: {"free": rank, "paid": rank}} or None on failure.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed (pip install playwright)")
        return None

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""

    try:
        with sync_playwright() as p:
            browser_kwargs = {"headless": True}
            if proxy:
                browser_kwargs["proxy"] = {"server": proxy}
                logger.info("Playwright using proxy: %s", proxy)

            browser = p.chromium.launch(**browser_kwargs)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )
            page = context.new_page()

            # Initialise the SPA so its webpack interceptors are active
            logger.info("Loading qimai.cn SPA…")
            page.goto("https://www.qimai.cn", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)

            all_results = {}

            for chart_type, qimai_type in [("free", "0"), ("paid", "1")]:
                for country in config.GOOGLE_PLAY_REGIONS.values():
                    logger.info("Qimai fetch: %s / %s", chart_type, country)

                    js = """async (args) => {
                        const [tp, co] = args;
                        try {
                            const url = 'https://api.qimai.cn/rank/globalrank'
                                + '?type=' + tp
                                + '&country=' + co.toLowerCase()
                                + '&category=game';
                            const r = await fetch(url, {
                                headers: {
                                    'Accept': 'application/json, text/plain, */*',
                                    'Referer': 'https://www.qimai.cn/',
                                }
                            });
                            const d = await r.json();
                            return JSON.stringify(d);
                        } catch(e) {
                            return JSON.stringify({error: e.message});
                        }
                    }"""
                    raw = page.evaluate(js, [qimai_type, country])
                    data = json.loads(raw)

                    code = data.get("code")
                    if code == 10605:
                        logger.warning(
                            "Qimai IP banned (10605). "
                            "This server IP is flagged by qimai. "
                            "Set HTTPS_PROXY to use a different IP."
                        )
                        browser.close()
                        return None

                    if code != 10000:
                        logger.warning(
                            "Qimai error %s/%s: code=%s %s",
                            chart_type, country, code, data.get("msg", ""),
                        )
                        continue

                    rank_info = data.get("rankInfo") or []
                    logger.info(
                        "Qimai %s/%s: %d apps",
                        chart_type, country, len(rank_info),
                    )

                    for app in rank_info:
                        pkg = app.get("appId", "") or app.get("packageName", "") or ""
                        if not pkg:
                            continue
                        if pkg not in all_results:
                            all_results[pkg] = {}
                        rank = app.get("rank", 0)
                        if isinstance(rank, (int, float)) and rank > 0:
                            all_results[pkg][chart_type] = int(rank)

            browser.close()
            return all_results if all_results else None

    except Exception as exc:
        logger.error("Playwright qimai strategy failed: %s", exc, exc_info=True)
        return None


# ── Result matching & public API ────────────────────────────────────────


def _match_results(
    ranking_data: Dict,
) -> Dict[int, Dict[str, Dict[str, int]]]:
    """
    Match ranking data to tracked games.

    Supports two data formats:
      - Per-region: {region_code: {package_name: {"free": rank, "paid": rank}}}
      - Flat:       {package_name: {"free": rank, "paid": rank}}
                    (same rank applied to all regions)

    ALL tracked games with a google_app_id get entries — unmatched
    games get rank -1 (未上榜) for all regions/chart types.

    Returns:
        {game_id: {region_code: {"free": rank, "paid": rank}}}
    """
    games = database.get_all_games()
    results: Dict[int, Dict[str, Dict[str, int]]] = {}

    # Detect flat vs per-region format: flat has {pkg: {free, paid}}
    # Per-region has {region_upper: {pkg: {free, paid}}}
    is_flat = False
    if ranking_data:
        sample = next(iter(ranking_data.values()))
        is_flat = isinstance(sample, dict) and all(k in ("free", "paid") for k in sample)

    # Mapping from lowercase to uppercase region codes
    region_lower_to_upper = {v.lower(): v for v in config.GOOGLE_PLAY_REGIONS.values()}

    for game in games:
        game_id = game["id"]
        google_app_id = (game.get("google_app_id") or "").strip()
        if not google_app_id:
            continue

        results.setdefault(game_id, {})

        for region_lower, region_upper in config.GOOGLE_PLAY_REGIONS.items():
            if is_flat:
                candidates = ranking_data
            else:
                candidates = ranking_data.get(region_upper, {})

            app_data = None
            if isinstance(candidates, dict):
                app_data = candidates.get(google_app_id)
                if not app_data:
                    for pkg, data in candidates.items():
                        if isinstance(data, dict) and (
                            google_app_id in pkg or pkg in google_app_id
                        ):
                            app_data = data
                            break

            results[game_id].setdefault(region_lower, {})
            if app_data and isinstance(app_data, dict):
                results[game_id][region_lower]["free"] = app_data.get("free", -1)
                results[game_id][region_lower]["paid"] = app_data.get("paid", -1)
            else:
                results[game_id][region_lower]["free"] = -1
                results[game_id][region_lower]["paid"] = -1

    return results


def _save_results(results: Dict[int, Dict[str, Dict[str, int]]]) -> int:
    """Save ranking results to database. Returns count of saved entries."""
    games = database.get_all_games()
    saved = 0
    for game_id, region_data in results.items():
        for region_code, ranks in region_data.items():
            free_r = ranks.get("free", -1)
            paid_r = ranks.get("paid", -1)
            database.save_ranking(game_id, "free", free_r, region=region_code, store="google")
            database.save_ranking(game_id, "paid", paid_r, region=region_code, store="google")
            saved += 1

            name = next((g["name"] for g in games if g["id"] == game_id), str(game_id))
            logger.info(
                "Google Play '%s' [%s] — free: %s, paid: %s",
                name, region_code,
                free_r if free_r != -1 else "未上榜",
                paid_r if paid_r != -1 else "未上榜",
            )
    return saved


def fetch_and_save_google_rankings() -> Dict[int, Dict[str, Dict[str, int]]]:
    """
    Fetch Google Play rankings and persist results.

    Data source priority:
      1. Gitee mirror (if GITEE_RAW_URL env var is set)
      2. codetabs.com proxy (free proxy relay)
      3. Playwright-based strategies (Vercel proxy or qimai.cn SPA)

    All tracked games with a google_app_id get a database entry —
    unmatched games get rank -1 (未上榜).

    Returns:
        {game_id: {region: {"free": rank, "paid": rank}}}
        Rank -1 means the game was not found in the chart.
    """
    results: Dict[int, Dict[str, Dict[str, int]]] = {}

    # Strategy 1: Gitee mirror (if configured)
    ranking_data = _fetch_from_gitee()
    if ranking_data:
        logger.info("Gitee mirror returned data, matching to tracked games…")
        results = _match_results(ranking_data)
    else:
        logger.info("Gitee mirror unavailable, trying codetabs proxy…")

    # Strategy 2: codetabs.com proxy (fallback)
    if not results:
        logger.info("Fetching Google Play rankings via codetabs.com proxy…")
        ranking_data = _fetch_via_codetabs_proxy()
        if ranking_data:
            logger.info("Codetabs proxy returned data, matching to tracked games…")
            results = _match_results(ranking_data)
        else:
            logger.warning("Codetabs proxy returned no data.")

    # Strategy 3: Vercel proxy (last resort)
    if not results:
        proxy_url = os.environ.get("VERCEL_PROXY_URL", "").strip()
        if proxy_url:
            logger.info("Fetching Google Play rankings via Vercel proxy…")
            ranking_data = _fetch_via_vercel_proxy()
            if ranking_data:
                logger.info("Vercel proxy returned data, matching to tracked games…")
                results = _match_results(ranking_data)
            else:
                logger.warning("Vercel proxy returned no data.")

    # Strategy 4: qimai.cn (absolute last resort)
    if not results:
        logger.info("Fetching Google Play rankings via qimai.cn…")
        qimai_data = _fetch_via_qimai_playwright()
        if qimai_data:
            logger.info("qimai.cn returned data, matching to tracked games…")
            results = _match_results(qimai_data)
        else:
            logger.warning(
                "qimai.cn returned no data. "
                "The server IP is likely banned by qimai (code 10605).\n"
                "  Set HTTPS_PROXY=http://your-proxy:port to use a different IP\n"
                "  See vercel-proxy/ directory for proxy deployment instructions."
            )

    if results:
        saved = _save_results(results)
        logger.info("Google Play saved: %d entries", saved)
    else:
        logger.warning("No Google Play ranking data available.")

    return results


def fetch_google_rankings_for_verification() -> Dict[str, Dict[str, Dict[str, int]]]:
    """Fetch Google Play rankings without saving (for verification)."""
    games = database.get_all_games()
    results: Dict[str, Dict[str, Dict[str, int]]] = {}

    proxy_url = os.environ.get("VERCEL_PROXY_URL", "").strip()
    ranking_data = None

    if proxy_url:
        ranking_data = _fetch_via_vercel_proxy()

    if not ranking_data:
        ranking_data = _fetch_via_qimai_playwright()

    if not ranking_data:
        return results

    for game in games:
        google_app_id = (game.get("google_app_id") or "").strip()
        if not google_app_id:
            continue
        app_data = ranking_data.get(google_app_id)
        if not app_data:
            for pkg, data in ranking_data.items():
                if google_app_id in pkg or pkg in google_app_id:
                    app_data = data
                    break
        if app_data:
            r: Dict[str, Dict[str, int]] = {}
            for rc in config.GOOGLE_PLAY_REGIONS:
                r[rc] = {"free": app_data.get("free", -1), "paid": app_data.get("paid", -1)}
            results[game["name"]] = r

    return results
