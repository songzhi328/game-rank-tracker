# tracker.py
# iOS App Store rank tracking using MZStore endpoint + iTunes Lookup API
#
# Architecture:
#   1. MZStore endpoint → 200-210 app IDs per chart + partial names
#   2. iTunes Lookup API → batch-resolve missing app names
#   3. Find tracked games' ranks in the full list (using region-specific app_ids)
#
# MZStore returns HTML with embedded JSON (its.serverData) containing:
#   - storePlatformData.lockup-room.results → app details (~77 entries with names)
#   - pageData.topChartsPageData.topCharts → chart arrays with adamIds

import logging
from typing import Dict, List, Tuple

import requests

import config
import database

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MZStore HTML → JSON parsing
# ---------------------------------------------------------------------------

def _extract_server_data(html: str) -> dict:
    """
    Extract and parse the its.serverData JSON object from MZStore HTML.

    The JSON is assigned via: its.serverData={...};
    We find the start, then brace-count to find the end.
    """
    marker = "its.serverData="
    start = html.find(marker)
    if start == -1:
        return {}
    start += len(marker)

    depth = 0
    i = start
    while i < len(html):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1

    if i >= len(html):
        return {}

    import json
    try:
        return json.loads(html[start : i + 1])
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse serverData JSON: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Chart data extraction from serverData
# ---------------------------------------------------------------------------

def _extract_charts(server_data: dict) -> Dict[str, List[str]]:
    """
    Extract chart ID lists from MZStore serverData.

    Returns:
        {"top-free": ["id1", "id2", ...], "top-grossing": ["id1", ...], ...}
    """
    charts_raw = (
        server_data.get("pageData", {})
        .get("topChartsPageData", {})
        .get("topCharts", [])
    )

    result: Dict[str, List[str]] = {}
    for chart in charts_raw:
        short_title = chart.get("shortTitle", "")
        chart_type = config.CHART_TITLE_MAP.get(short_title)
        if chart_type is None:
            logger.debug("Skipping unknown chart title: %s", short_title)
            continue
        adam_ids = chart.get("adamIds", [])
        result[chart_type] = adam_ids
        logger.info("Extracted chart '%s' (%s): %d apps", short_title, chart_type, len(adam_ids))

    return result


def _extract_lockup_names(server_data: dict) -> Dict[str, str]:
    """
    Extract app name mapping from MZStore lockup-room.

    Returns:
        {"app_id": "App Name", ...}
    """
    lockup = (
        server_data.get("storePlatformData", {})
        .get("lockup-room", {})
        .get("results", {})
    )
    names: Dict[str, str] = {}
    for app_id, details in lockup.items():
        name = details.get("name", "")
        if name:
            names[str(app_id)] = name
    logger.info("Extracted %d app names from lockup-room", len(names))
    return names


# ---------------------------------------------------------------------------
# iTunes Lookup API (batch name resolution)
# ---------------------------------------------------------------------------

def _batch_lookup_names(app_ids: List[str], region: str) -> Dict[str, str]:
    """
    Resolve app names via iTunes Lookup API for IDs missing from lockup-room.

    The Lookup API supports up to 100 IDs per request.
    Returns: {"app_id": "App Name", ...}
    """
    names: Dict[str, str] = {}
    batch_size = config.LOOKUP_BATCH_SIZE

    for offset in range(0, len(app_ids), batch_size):
        batch = app_ids[offset : offset + batch_size]
        url = config.LOOKUP_URL_TEMPLATE.format(region=region)
        ids_param = ",".join(batch)

        try:
            resp = requests.get(
                url,
                params={"id": ids_param},
                timeout=config.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            for app in results:
                aid = str(app.get("trackId", ""))
                name = app.get("trackName", "")
                if aid and name:
                    names[aid] = name
            logger.info(
                "Lookup batch: %d/%d resolved for region %s",
                len([r for r in results if r.get("trackName")]),
                len(batch),
                region,
            )
        except requests.exceptions.RequestException as exc:
            logger.error("Lookup API error for region %s: %s", region, exc)
        except (ValueError, KeyError) as exc:
            logger.error("Lookup API parse error for region %s: %s", region, exc)

    return names


def _lookup_single_name(app_id: str, region: str) -> str:
    """
    Look up the name of a single app via iTunes Lookup API.
    Returns the app name or empty string if not found.
    """
    url = config.LOOKUP_URL_TEMPLATE.format(region=region)
    try:
        resp = requests.get(
            url,
            params={"id": app_id},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if results:
            return results[0].get("trackName", "")
    except Exception as exc:
        logger.warning("Single lookup failed for %s in %s: %s", app_id, region, exc)
    return ""


# ---------------------------------------------------------------------------
# Core: fetch MZStore + resolve names for one region
# ---------------------------------------------------------------------------

def _fetch_region_charts(region: str) -> Dict[str, List[Tuple[int, str, str]]]:
    """
    Fetch chart data for a single region.

    Returns:
        {
            "top-free": [(rank, app_id, app_name), ...],
            "top-grossing": [(rank, app_id, app_name), ...],
        }
    """
    # 1. Fetch MZStore page
    params = {
        "genreId": config.MZSTORE_GENRE_ID,
        "popId": config.MZSTORE_POP_ID,
        "cc": region,
    }
    try:
        resp = requests.get(
            config.MZSTORE_BASE_URL,
            params=params,
            headers=config.MZSTORE_HEADERS,
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.error("MZStore fetch error for region %s: %s", region, exc)
        return {}

    # 2. Parse serverData
    server_data = _extract_server_data(resp.text)
    if not server_data:
        logger.error("No serverData in MZStore response for region %s", region)
        return {}

    # 3. Extract chart IDs and lockup names
    chart_ids = _extract_charts(server_data)
    lockup_names = _extract_lockup_names(server_data)

    # 4. Resolve missing names via Lookup API
    result: Dict[str, List[Tuple[int, str, str]]] = {}
    for chart_type in config.TRACKED_CHARTS:
        ids = chart_ids.get(chart_type, [])
        if not ids:
            logger.warning("No IDs for chart %s in region %s", chart_type, region)
            result[chart_type] = []
            continue

        # Identify IDs missing from lockup
        missing_ids = [aid for aid in ids if aid not in lockup_names]
        lookup_names = {}
        if missing_ids:
            lookup_names = _batch_lookup_names(missing_ids, region)

        # Build ranked list: (rank, app_id, app_name)
        ranked: List[Tuple[int, str, str]] = []
        for rank, aid in enumerate(ids, start=1):
            name = lockup_names.get(aid) or lookup_names.get(aid, "")
            ranked.append((rank, aid, name))

        resolved = sum(1 for _, _, n in ranked if n)
        logger.info(
            "Region %s | %s: %d apps, %d with names",
            region, chart_type, len(ranked), resolved,
        )
        result[chart_type] = ranked

    return result


# ---------------------------------------------------------------------------
# Find rank of a tracked game
# ---------------------------------------------------------------------------

def _find_rank(
    ranked_list: List[Tuple[int, str, str]],
    app_id: str,
    app_name: str,
) -> int:
    """
    Find the rank of an app in a ranked list.

    Matching priority:
      1. Exact app ID match (most reliable)
      2. Case-insensitive name match (fallback)

    Returns:
        1-based rank if found, -1 if not found.
    """
    name_lower = app_name.lower()
    for rank, aid, aname in ranked_list:
        if str(aid) == str(app_id):
            return rank
        if aname and aname.lower() == name_lower:
            return rank
    return -1


# ---------------------------------------------------------------------------
# Public API: fetch and save rankings for all regions
# ---------------------------------------------------------------------------

def fetch_and_save_rankings() -> Dict[int, Dict[str, Dict[str, int]]]:
    """
    Fetch Free and Grossing game charts for ALL configured regions
    using MZStore + Lookup, and persist rankings for tracked games.

    Each game is looked up using its region-specific app_id.
    If no region-specific ID is configured, falls back to the default app_id.

    Returns:
        {game_id: {region: {"free": rank, "grossing": rank}}}
        Rank -1 means the app was not found in the chart.
    """
    games = database.get_all_games()
    results: Dict[int, Dict[str, Dict[str, int]]] = {}

    for region_code in config.REGIONS:
        region_name = config.REGIONS[region_code]
        logger.info("Fetching rankings for region: %s (%s)", region_code, region_name)

        charts = _fetch_region_charts(region_code)
        free_list = charts.get("top-free", [])
        grossing_list = charts.get("top-grossing", [])

        for game in games:
            game_id: int = game["id"]
            default_app_id: str = game["app_id"]
            app_name: str = game["name"]

            # Use region-specific app_id if configured, otherwise default
            region_app_id = database.get_region_app_id(game_id, region_code)

            # Try to look up the app name in this region via the chart data first
            # (avoids extra API call if the app is in the chart)
            region_app_name = ""
            for _, aid, aname in free_list + grossing_list:
                if str(aid) == str(region_app_id) and aname:
                    region_app_name = aname
                    break

            # If not found in chart data, try a single Lookup for the name
            if not region_app_name:
                region_app_name = _lookup_single_name(region_app_id, region_code)

            # Use the region-specific name for matching, fall back to default name
            match_name = region_app_name or app_name

            free_rank = _find_rank(free_list, region_app_id, match_name)
            grossing_rank = _find_rank(grossing_list, region_app_id, match_name)

            database.save_ranking(game_id, "free", free_rank, region=region_code)
            database.save_ranking(game_id, "grossing", grossing_rank, region=region_code)

            if game_id not in results:
                results[game_id] = {}
            results[game_id][region_code] = {
                "free": free_rank,
                "grossing": grossing_rank,
            }
            logger.info(
                "Game '%s' (%s→%s) [%s] — free: %s, grossing: %s",
                app_name,
                default_app_id,
                region_app_id,
                region_code,
                free_rank if free_rank != -1 else "未上榜",
                grossing_rank if grossing_rank != -1 else "未上榜",
            )

    return results


# ---------------------------------------------------------------------------
# Public API: fetch rankings WITHOUT saving (for verification)
# ---------------------------------------------------------------------------

def fetch_rankings_for_verification() -> Dict[str, Dict[str, Dict[str, int]]]:
    """
    Fetch Free and Grossing game charts for ALL configured regions,
    but do NOT save to database (for verification purposes).

    Returns:
        {game_name: {region: {"free": rank, "grossing": rank}}}
        Rank -1 means the app was not found in the chart.
    """
    games = database.get_all_games()
    results: Dict[str, Dict[str, Dict[str, int]]] = {}

    for region_code in config.REGIONS:
        region_name = config.REGIONS[region_code]
        logger.info("Fetching rankings for verification: %s (%s)", region_code, region_name)

        charts = _fetch_region_charts(region_code)
        free_list = charts.get("top-free", [])
        grossing_list = charts.get("top-grossing", [])

        for game in games:
            game_id: int = game["id"]
            default_app_id: str = game["app_id"]
            app_name: str = game["name"]

            # Use region-specific app_id if configured
            region_app_id = database.get_region_app_id(game_id, region_code)

            # Try to lookup the app name in this region
            region_app_name = ""
            for _, aid, aname in free_list + grossing_list:
                if str(aid) == str(region_app_id) and aname:
                    region_app_name = aname
                    break

            if not region_app_name:
                region_app_name = _lookup_single_name(region_app_id, region_code)

            match_name = region_app_name or app_name

            free_rank = _find_rank(free_list, region_app_id, match_name)
            grossing_rank = _find_rank(grossing_list, region_app_id, match_name)

            if app_name not in results:
                results[app_name] = {}
            if region_code not in results[app_name]:
                results[app_name][region_code] = {}

            results[app_name][region_code]["free"] = free_rank
            results[app_name][region_code]["grossing"] = grossing_rank

            logger.info(
                "VERIFY: Game '%s' [%s] — free: %s, grossing: %s",
                app_name,
                region_code,
                free_rank if free_rank != -1 else "未上榜",
                grossing_rank if grossing_rank != -1 else "未上榜",
            )

    return results


# ---------------------------------------------------------------------------
# Unified fetch: iOS App Store + Google Play Store
# ---------------------------------------------------------------------------

def fetch_and_save_all() -> dict:
    """
    Fetch rankings for both iOS App Store and Google Play Store.

    Returns:
        {
            "ios": {game_id: {region: {"free": rank, "grossing": rank}}},
            "google": {game_id: {region: {"free": rank, "paid": rank}}},
        }
    """
    from google_tracker import fetch_and_save_google_rankings

    result = {}

    logger.info("===== Starting iOS App Store fetch =====")
    try:
        ios_results = fetch_and_save_rankings()
        result["ios"] = ios_results
        logger.info("iOS fetch completed successfully")
    except Exception as exc:
        logger.error("iOS fetch failed: %s", exc, exc_info=True)
        result["ios"] = {}

    logger.info("===== Starting Google Play Store fetch =====")
    try:
        google_results = fetch_and_save_google_rankings()
        result["google"] = google_results
        logger.info("Google Play fetch completed successfully")
    except Exception as exc:
        logger.error("Google Play fetch failed: %s", exc, exc_info=True)
        result["google"] = {}

    return result
