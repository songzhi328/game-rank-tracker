# app.py
# Flask main application with API routes for Game Rank Tracker (multi-store, multi-region)

import logging
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request

import config
import database
import scheduler
import tracker

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)


# ---------------------------------------------------------------------------
# Startup / Teardown
# ---------------------------------------------------------------------------

@app.before_request
def _ensure_init() -> None:
    pass


@app.teardown_appcontext
def _shutdown_scheduler(exception: Any = None) -> None:
    pass


# ---------------------------------------------------------------------------
# Web routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Render the main dashboard page."""
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/stores", methods=["GET"])
def api_get_stores():
    """
    GET /api/stores
    Returns the list of supported stores.
    """
    stores = [{"code": code, "name": name} for code, name in config.STORES.items()]
    return jsonify({"success": True, "stores": stores})


@app.route("/api/regions", methods=["GET"])
def api_get_regions():
    """
    GET /api/regions?store=ios
    Returns the list of supported regions for the given store.
    """
    store = request.args.get("store", "ios")
    if store == "google":
        regions = [{"code": code, "name": config.REGIONS.get(code, code)}
                    for code in config.GOOGLE_PLAY_REGIONS]
    else:
        regions = [{"code": code, "name": name} for code, name in config.REGIONS.items()]
    return jsonify({"success": True, "regions": regions})


@app.route("/api/games", methods=["GET"])
def api_get_games():
    """
    GET /api/games?region=cn&store=ios
    Returns all tracked games with their latest rankings for the specified region and store.
    """
    region = request.args.get("region", "cn")
    store = request.args.get("store", "ios")
    games = database.get_all_games()
    latest = database.get_latest_rankings()

    for game in games:
        game_id = game["id"]
        region_ranks = latest.get(game_id, {}).get(region, {}).get(store, {})
        game["latest_free"] = region_ranks.get("free", None)
        game["latest_grossing"] = region_ranks.get("grossing", None)
        game["latest_paid"] = region_ranks.get("paid", None)

    return jsonify({"success": True, "games": games, "store": store})


@app.route("/api/games", methods=["POST"])
def api_add_game():
    """
    POST /api/games
    Body JSON: {"name": "...", "app_id": "...", "google_app_id": "..."}
    Adds a new game to track.
    """
    data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
    name = str(data.get("name", "")).strip()
    app_id = str(data.get("app_id", "")).strip()
    google_app_id = str(data.get("google_app_id", "")).strip()

    if not name or not app_id:
        return jsonify({"success": False, "error": "name 和 app_id 不能为空"}), 400

    try:
        game = database.add_game(name, app_id, google_app_id)
        return jsonify({"success": True, "game": game}), 201
    except Exception as exc:
        logger.error("Failed to add game: %s", exc)
        return jsonify({"success": False, "error": "添加失败，App ID 可能已存在"}), 409


@app.route("/api/games/<int:game_id>", methods=["DELETE"])
def api_delete_game(game_id: int):
    """DELETE /api/games/<game_id> - Removes a game and its ranking history."""
    deleted = database.delete_game(game_id)
    if deleted:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "游戏不存在"}), 404


@app.route("/api/games/<int:game_id>/region-ids", methods=["GET"])
def api_get_region_ids(game_id: int):
    """GET /api/games/<game_id>/region-ids - Region-specific app IDs for a game."""
    region_ids = database.get_region_app_ids(game_id)
    return jsonify({"success": True, "game_id": game_id, "region_ids": region_ids})


@app.route("/api/games/<int:game_id>/region-ids", methods=["PUT"])
def api_set_region_ids(game_id: int):
    """PUT /api/games/<game_id>/region-ids - Sets region-specific app IDs."""
    data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
    region_ids = data.get("region_ids", {})
    if not isinstance(region_ids, dict):
        return jsonify({"success": False, "error": "region_ids 必须是对象"}), 400

    try:
        database.set_region_app_ids(game_id, region_ids)
        updated = database.get_region_app_ids(game_id)
        return jsonify({"success": True, "game_id": game_id, "region_ids": updated})
    except Exception as exc:
        logger.error("Failed to set region IDs: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/games/<int:game_id>/google-app-id", methods=["PUT"])
def api_set_google_app_id(game_id: int):
    """
    PUT /api/games/<game_id>/google-app-id
    Body JSON: {"google_app_id": "com.example.app"}
    Sets the Google Play app ID (package name) for a game.
    """
    data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
    google_app_id = str(data.get("google_app_id", "")).strip()
    if not google_app_id:
        return jsonify({"success": False, "error": "google_app_id 不能为空"}), 400
    try:
        database.update_google_app_id(game_id, google_app_id)
        return jsonify({"success": True, "game_id": game_id, "google_app_id": google_app_id})
    except Exception as exc:
        logger.error("Failed to set google_app_id: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/rankings/<int:game_id>", methods=["GET"])
def api_get_rankings(game_id: int):
    """
    GET /api/rankings/<game_id>?region=cn&store=ios&limit=30
    Returns historical rankings for a game in a specific region and store.
    """
    region = request.args.get("region", "cn")
    store = request.args.get("store", "ios")
    limit = request.args.get("limit", 30, type=int)
    limit = max(1, min(limit, 200))
    data = database.get_rankings_for_game(game_id, region=region, store=store, limit=limit)
    return jsonify({"success": True, "rankings": data, "region": region, "store": store})


@app.route("/api/fetch-now", methods=["POST"])
def api_fetch_now():
    """
    POST /api/fetch-now
    Manually triggers an immediate ranking fetch for all stores.
    """
    logger.info("Manual fetch triggered via API.")
    try:
        results = tracker.fetch_and_save_all()
        last_checked = database.get_last_checked_at()
        return jsonify({"success": True, "results": results, "last_checked": last_checked})
    except Exception as exc:
        logger.error("Manual fetch failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/status", methods=["GET"])
def api_status():
    """
    GET /api/status?store=ios
    Returns the last checked timestamps per region for the given store.
    """
    store = request.args.get("store", "ios")
    region_status = {}
    if store == "google":
        regions = config.GOOGLE_PLAY_REGIONS
    else:
        regions = config.REGIONS
    for code in regions:
        region_status[code] = {
            "last_checked": database.get_last_checked_at(region=code, store=store),
        }
    return jsonify(
        {
            "success": True,
            "last_checked": database.get_last_checked_at(store=store),
            "checked_today": database.has_checked_today(),
            "regions": region_status,
            "store": store,
        }
    )


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    """Initialize DB, seed defaults, start scheduler, and return the Flask app."""
    database.init_db()
    database.seed_default_games()
    scheduler.start_scheduler()
    return app


if __name__ == "__main__":
    create_app()
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
        use_reloader=False,
    )
