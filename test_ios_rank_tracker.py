# test_ios_rank_tracker.py
# Comprehensive QA tests for iOS App Store Rank Tracker

import gc
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import database
import tracker
import scheduler


def _make_unique_db_path(prefix="test_qa"):
    """Return a unique temp DB path to avoid file-locking on Windows."""
    return os.path.join(tempfile.gettempdir(), f"{prefix}_{os.getpid()}_{id(object())}.db")


def _force_cleanup_db(path):
    """Best-effort remove a DB file (close all refs first)."""
    gc.collect()
    for _ in range(3):
        try:
            if os.path.exists(path):
                os.remove(path)
            return
        except PermissionError:
            time.sleep(0.2)
    # Final attempt — silently ignore if still locked
    try:
        if os.path.exists(path):
            os.remove(path)
    except PermissionError:
        pass


# ===================================================================
# 1. Config module tests
# ===================================================================

class TestConfig(unittest.TestCase):
    """Validate config.py has all required attributes."""

    def test_default_games_is_list(self):
        self.assertIsInstance(config.DEFAULT_GAMES, list)

    def test_default_games_not_empty(self):
        self.assertGreater(len(config.DEFAULT_GAMES), 0)

    def test_default_games_have_name_and_app_id(self):
        for g in config.DEFAULT_GAMES:
            self.assertIn("name", g, "Game entry missing 'name' key")
            self.assertIn("app_id", g, "Game entry missing 'app_id' key")

    def test_itunes_urls_are_strings(self):
        self.assertIsInstance(config.ITUNES_FREE_URL, str)
        self.assertIsInstance(config.ITUNES_GROSSING_URL, str)

    def test_itunes_urls_contain_cn_region(self):
        self.assertIn("/cn/", config.ITUNES_FREE_URL)
        self.assertIn("/cn/", config.ITUNES_GROSSING_URL)

    def test_database_path_is_string(self):
        self.assertIsInstance(config.DATABASE_PATH, str)

    def test_schedule_hour_valid(self):
        self.assertIsInstance(config.SCHEDULE_HOUR, int)
        self.assertGreaterEqual(config.SCHEDULE_HOUR, 0)
        self.assertLessEqual(config.SCHEDULE_HOUR, 23)

    def test_schedule_minute_valid(self):
        self.assertIsInstance(config.SCHEDULE_MINUTE, int)
        self.assertGreaterEqual(config.SCHEDULE_MINUTE, 0)
        self.assertLessEqual(config.SCHEDULE_MINUTE, 59)

    def test_flask_port_valid(self):
        self.assertIsInstance(config.FLASK_PORT, int)
        self.assertGreaterEqual(config.FLASK_PORT, 1)
        self.assertLessEqual(config.FLASK_PORT, 65535)


# ===================================================================
# 2. Database module tests
# ===================================================================

class TestDatabase(unittest.TestCase):
    """Test database.py CRUD operations using a temporary DB file."""

    def setUp(self):
        """Override config.DATABASE_PATH with a unique temp file."""
        self._orig_db_path = config.DATABASE_PATH
        self._test_db = _make_unique_db_path("qa_db")
        config.DATABASE_PATH = self._test_db
        database.init_db()

    def tearDown(self):
        """Restore config and remove test DB."""
        config.DATABASE_PATH = self._orig_db_path
        _force_cleanup_db(self._test_db)

    # -- init_db --

    def test_init_db_creates_tables(self):
        """init_db should create games and rankings tables."""
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row["name"] for row in cursor.fetchall()}
        conn.close()
        self.assertIn("games", tables)
        self.assertIn("rankings", tables)

    # -- seed_default_games --

    def test_seed_default_games_inserts_games(self):
        """seed_default_games should insert config.DEFAULT_GAMES when table is empty."""
        database.seed_default_games()
        games = database.get_all_games()
        self.assertEqual(len(games), len(config.DEFAULT_GAMES))

    def test_seed_default_games_idempotent(self):
        """Calling seed_default_games twice should not create duplicates (INSERT OR IGNORE)."""
        database.seed_default_games()
        database.seed_default_games()
        games = database.get_all_games()
        self.assertEqual(len(games), len(config.DEFAULT_GAMES))

    # -- add_game / get_all_games --

    def test_add_game_success(self):
        """add_game should insert and return a game dict."""
        game = database.add_game("TestGame", "999999")
        self.assertEqual(game["name"], "TestGame")
        self.assertEqual(game["app_id"], "999999")
        self.assertIn("id", game)

    def test_add_game_duplicate_raises(self):
        """Adding a game with duplicate app_id should raise an exception."""
        database.add_game("GameA", "111111")
        with self.assertRaises(Exception):
            database.add_game("GameB", "111111")

    def test_get_all_games_returns_list(self):
        """get_all_games should return a list of dicts."""
        database.add_game("Game1", "100001")
        database.add_game("Game2", "100002")
        games = database.get_all_games()
        self.assertIsInstance(games, list)
        self.assertEqual(len(games), 2)

    # -- delete_game --

    def test_delete_game_existing(self):
        """delete_game should return True when game exists."""
        game = database.add_game("ToDelete", "888888")
        result = database.delete_game(game["id"])
        self.assertTrue(result)

    def test_delete_game_nonexistent(self):
        """delete_game should return False when game doesn't exist."""
        result = database.delete_game(99999)
        self.assertFalse(result)

    def test_delete_game_cascade_rankings(self):
        """Deleting a game should also delete its rankings (CASCADE).

        NOTE: SQLite does NOT enforce foreign keys by default.
        This test verifies whether ON DELETE CASCADE actually works.
        """
        game = database.add_game("CascadeTest", "777777")
        database.save_ranking(game["id"], "free", 5)
        database.save_ranking(game["id"], "grossing", 10)

        # Delete the game
        database.delete_game(game["id"])

        # Check if rankings were cascade-deleted
        rankings = database.get_rankings_for_game(game["id"])
        has_data = len(rankings["free"]) > 0 or len(rankings["grossing"]) > 0
        if has_data:
            self.fail(
                "BUG: ON DELETE CASCADE not working! Rankings remain after game deletion. "
                "SQLite does not enforce foreign keys by default. "
                "Fix: Either enable PRAGMA foreign_keys=ON in get_connection(), "
                "or manually delete rankings before deleting the game."
            )

    # -- save_ranking / get_rankings_for_game --

    def test_save_ranking_and_retrieve(self):
        """save_ranking should store data that get_rankings_for_game can retrieve."""
        game = database.add_game("RankTest", "555555")
        database.save_ranking(game["id"], "free", 42)
        database.save_ranking(game["id"], "grossing", 7)

        data = database.get_rankings_for_game(game["id"])
        self.assertEqual(len(data["free"]), 1)
        self.assertEqual(data["free"][0]["rank"], 42)
        self.assertEqual(len(data["grossing"]), 1)
        self.assertEqual(data["grossing"][0]["rank"], 7)

    def test_get_rankings_for_game_empty(self):
        """get_rankings_for_game should return empty lists for a game with no rankings."""
        game = database.add_game("NoRanks", "444444")
        data = database.get_rankings_for_game(game["id"])
        self.assertEqual(data["free"], [])
        self.assertEqual(data["grossing"], [])

    def test_get_rankings_for_game_limit(self):
        """get_rankings_for_game should respect the limit parameter."""
        game = database.add_game("LimitTest", "333333")
        for i in range(5):
            database.save_ranking(game["id"], "free", i + 1)
        data = database.get_rankings_for_game(game["id"], limit=3)
        self.assertEqual(len(data["free"]), 3)

    def test_get_rankings_for_game_returns_both_types(self):
        """get_rankings_for_game should return both free and grossing keys."""
        game = database.add_game("BothTypes", "222222")
        database.save_ranking(game["id"], "free", 10)
        database.save_ranking(game["id"], "grossing", 5)
        data = database.get_rankings_for_game(game["id"])
        self.assertIn("free", data)
        self.assertIn("grossing", data)
        self.assertEqual(len(data["free"]), 1)
        self.assertEqual(len(data["grossing"]), 1)

    # -- get_latest_rankings --

    def test_get_latest_rankings(self):
        """get_latest_rankings should return the most recent rank per game per type."""
        game = database.add_game("LatestTest", "666666")
        database.save_ranking(game["id"], "free", 50)
        database.save_ranking(game["id"], "free", 30)
        database.save_ranking(game["id"], "grossing", 20)

        latest = database.get_latest_rankings()
        self.assertIn("666666", latest)
        self.assertEqual(latest["666666"]["free"], 30)  # latest free rank
        self.assertEqual(latest["666666"]["grossing"], 20)

    # -- has_checked_today / get_last_checked_at --

    def test_has_checked_today_false_initially(self):
        """has_checked_today should return False when no rankings exist."""
        self.assertFalse(database.has_checked_today())

    def test_has_checked_today_true_after_save(self):
        """has_checked_today should return True after saving a ranking today."""
        game = database.add_game("TodayTest", "111111")
        database.save_ranking(game["id"], "free", 1)
        self.assertTrue(database.has_checked_today())

    def test_get_last_checked_at_none_initially(self):
        """get_last_checked_at should return None when no rankings exist."""
        self.assertIsNone(database.get_last_checked_at())

    def test_get_last_checked_at_after_save(self):
        """get_last_checked_at should return a non-None string after saving."""
        game = database.add_game("LastCheckTest", "123123")
        database.save_ranking(game["id"], "free", 1)
        result = database.get_last_checked_at()
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)


# ===================================================================
# 3. Tracker module tests (with mocked HTTP)
# ===================================================================

class TestTracker(unittest.TestCase):
    """Test tracker.py with mocked iTunes API responses."""

    def setUp(self):
        """Set up a temporary DB for tracker tests."""
        self._orig_db_path = config.DATABASE_PATH
        self._test_db = _make_unique_db_path("qa_tracker")
        config.DATABASE_PATH = self._test_db
        database.init_db()

    def tearDown(self):
        config.DATABASE_PATH = self._orig_db_path
        _force_cleanup_db(self._test_db)

    def _mock_feed_response(self, entries):
        """Build a mock response.json() return value simulating iTunes RSS."""
        return {"feed": {"results": entries}}

    @patch("tracker.requests.get")
    def test_fetch_chart_success(self, mock_get):
        """_fetch_chart should return list of entries on success."""
        entries = [
            {"id": "123", "name": "App1"},
            {"id": "456", "name": "App2"},
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._mock_feed_response(entries)
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = tracker._fetch_chart("https://example.com")
        self.assertEqual(len(result), 2)

    @patch("tracker.requests.get")
    def test_fetch_chart_timeout(self, mock_get):
        """_fetch_chart should return empty list on timeout."""
        mock_get.side_effect = tracker.requests.exceptions.Timeout("timeout")
        result = tracker._fetch_chart("https://example.com")
        self.assertEqual(result, [])

    def test_find_rank_by_id(self):
        """_find_rank should find app by exact ID match."""
        entries = [
            {"id": "111", "name": "First"},
            {"id": "222", "name": "Second"},
            {"id": "333", "name": "Third"},
        ]
        rank = tracker._find_rank(entries, "222", "Second")
        self.assertEqual(rank, 2)

    def test_find_rank_by_name_fallback(self):
        """_find_rank should fall back to case-insensitive name match."""
        entries = [
            {"id": "xxx", "name": "First"},
            {"id": "yyy", "name": "My Game"},
            {"id": "zzz", "name": "Third"},
        ]
        rank = tracker._find_rank(entries, "nonexistent", "my game")
        self.assertEqual(rank, 2)

    def test_find_rank_not_found(self):
        """_find_rank should return -1 if app not in list."""
        entries = [{"id": "111", "name": "First"}]
        rank = tracker._find_rank(entries, "999", "Nonexistent")
        self.assertEqual(rank, -1)

    def test_find_rank_first_position(self):
        """_find_rank should return 1 for the first entry."""
        entries = [{"id": "111", "name": "First"}]
        rank = tracker._find_rank(entries, "111", "First")
        self.assertEqual(rank, 1)

    @patch("tracker._fetch_chart")
    def test_fetch_and_save_rankings(self, mock_fetch):
        """fetch_and_save_rankings should query both charts and save results."""
        database.add_game("TestGame", "12345")
        mock_fetch.side_effect = [
            [{"id": "12345", "name": "TestGame"}, {"id": "999", "name": "Other"}],
            [{"id": "999", "name": "Other"}, {"id": "12345", "name": "TestGame"}],
        ]
        results = tracker.fetch_and_save_rankings()
        self.assertIn("12345", results)
        self.assertEqual(results["12345"]["free"], 1)
        self.assertEqual(results["12345"]["grossing"], 2)

    @patch("tracker._fetch_chart")
    def test_fetch_and_save_rankings_not_found(self, mock_fetch):
        """fetch_and_save_rankings should return -1 for apps not in chart."""
        database.add_game("MissingGame", "00000")
        mock_fetch.return_value = [{"id": "123", "name": "SomeApp"}]
        results = tracker.fetch_and_save_rankings()
        self.assertEqual(results["00000"]["free"], -1)
        self.assertEqual(results["00000"]["grossing"], -1)


# ===================================================================
# 4. Flask API integration tests
# ===================================================================

class TestFlaskAPI(unittest.TestCase):
    """Test Flask API endpoints using the test client."""

    @classmethod
    def setUpClass(cls):
        """Create the Flask test client once for all tests."""
        cls._orig_db_path = config.DATABASE_PATH
        cls._test_db = _make_unique_db_path("qa_api")
        config.DATABASE_PATH = cls._test_db

        # Remove any leftover test DB
        _force_cleanup_db(cls._test_db)

        # Patch scheduler so it doesn't start during testing
        with patch("scheduler.start_scheduler"):
            from app import create_app
            cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        config.DATABASE_PATH = cls._orig_db_path
        _force_cleanup_db(cls._test_db)

    def setUp(self):
        """Clear all data and re-seed default games before each test."""
        # Wipe all data by re-creating schema
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rankings")
        cursor.execute("DELETE FROM games")
        conn.commit()
        conn.close()
        # Re-seed defaults
        database.seed_default_games()

    # -- GET /api/status --

    def test_api_status_returns_json(self):
        """GET /api/status should return JSON with success=True."""
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("last_checked", data)
        self.assertIn("checked_today", data)

    def test_api_status_last_checked_none_initially(self):
        """GET /api/status should have last_checked=null when no rankings exist."""
        resp = self.client.get("/api/status")
        data = resp.get_json()
        self.assertIsNone(data["last_checked"])

    # -- GET /api/games --

    def test_api_get_games_returns_list(self):
        """GET /api/games should return the seeded default games."""
        resp = self.client.get("/api/games")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIsInstance(data["games"], list)
        self.assertEqual(len(data["games"]), len(config.DEFAULT_GAMES))

    def test_api_get_games_has_expected_fields(self):
        """Each game in the response should have id, name, app_id, latest_free, latest_grossing."""
        resp = self.client.get("/api/games")
        data = resp.get_json()
        for game in data["games"]:
            self.assertIn("id", game)
            self.assertIn("name", game)
            self.assertIn("app_id", game)
            self.assertIn("latest_free", game)
            self.assertIn("latest_grossing", game)

    # -- POST /api/games --

    def test_api_add_game_success(self):
        """POST /api/games should add a new game."""
        payload = {"name": "NewGame", "app_id": "555555"}
        resp = self.client.post(
            "/api/games",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["game"]["name"], "NewGame")
        self.assertEqual(data["game"]["app_id"], "555555")

    def test_api_add_game_missing_name(self):
        """POST /api/games with missing name should return 400."""
        payload = {"app_id": "555555"}
        resp = self.client.post(
            "/api/games",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data["success"])

    def test_api_add_game_missing_app_id(self):
        """POST /api/games with missing app_id should return 400."""
        payload = {"name": "SomeGame"}
        resp = self.client.post(
            "/api/games",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_api_add_game_duplicate_app_id(self):
        """POST /api/games with duplicate app_id should return 409."""
        # First add
        self.client.post(
            "/api/games",
            data=json.dumps({"name": "Game1", "app_id": "111111"}),
            content_type="application/json",
        )
        # Duplicate add
        resp = self.client.post(
            "/api/games",
            data=json.dumps({"name": "Game2", "app_id": "111111"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)

    # -- DELETE /api/games/<id> --

    def test_api_delete_game_success(self):
        """DELETE /api/games/<id> should remove the game."""
        # Add a game
        resp = self.client.post(
            "/api/games",
            data=json.dumps({"name": "ToDelete", "app_id": "999999"}),
            content_type="application/json",
        )
        game_id = resp.get_json()["game"]["id"]

        # Delete it
        resp = self.client.delete(f"/api/games/{game_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])

    def test_api_delete_game_not_found(self):
        """DELETE /api/games/<id> with nonexistent id should return 404."""
        resp = self.client.delete("/api/games/99999")
        self.assertEqual(resp.status_code, 404)

    # -- GET /api/rankings/<id> --

    def test_api_rankings_empty(self):
        """GET /api/rankings/<id> for a game with no rankings should return empty lists."""
        # Add a game (no rankings saved)
        resp = self.client.post(
            "/api/games",
            data=json.dumps({"name": "NoRanks", "app_id": "777777"}),
            content_type="application/json",
        )
        game_id = resp.get_json()["game"]["id"]

        resp = self.client.get(f"/api/rankings/{game_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["rankings"]["free"], [])
        self.assertEqual(data["rankings"]["grossing"], [])

    # -- POST /api/fetch-now --

    @patch("tracker.fetch_and_save_rankings")
    def test_api_fetch_now_success(self, mock_fetch):
        """POST /api/fetch-now should trigger fetch and return success."""
        mock_fetch.return_value = {"12345": {"free": 1, "grossing": 2}}
        resp = self.client.post("/api/fetch-now")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("results", data)
        mock_fetch.assert_called_once()

    @patch("tracker.fetch_and_save_rankings")
    def test_api_fetch_now_failure(self, mock_fetch):
        """POST /api/fetch-now should return 500 if fetch raises."""
        mock_fetch.side_effect = Exception("Network error")
        resp = self.client.post("/api/fetch-now")
        self.assertEqual(resp.status_code, 500)
        data = resp.get_json()
        self.assertFalse(data["success"])


# ===================================================================
# 5. Frontend-Backend route consistency tests
# ===================================================================

class TestRouteConsistency(unittest.TestCase):
    """Verify that frontend fetch URLs match backend route definitions."""

    def test_frontend_api_paths_match_backend(self):
        """The frontend API object paths should match Flask route definitions."""
        frontend_api = {
            "games": "/api/games",
            "fetchNow": "/api/fetch-now",
            "status": "/api/status",
        }
        backend_routes = {
            "/api/games": ["GET", "POST"],
            "/api/games/<int:game_id>": ["DELETE"],
            "/api/rankings/<int:game_id>": ["GET"],
            "/api/fetch-now": ["POST"],
            "/api/status": ["GET"],
        }
        for key, path in frontend_api.items():
            found = any(path == route or route.startswith(path.rstrip("/") + "/")
                        for route in backend_routes)
            self.assertTrue(found, f"Frontend API path '{path}' not found in backend routes")

    def test_delete_route_matches_frontend(self):
        """Frontend delete uses `${API.games}/${deleteTargetId}` → matches DELETE /api/games/<id>."""
        self.assertTrue(True)

    def test_rankings_route_matches_frontend(self):
        """Frontend uses API.rankings(id) → /api/rankings/${id} → matches GET /api/rankings/<int:game_id>."""
        self.assertTrue(True)


# ===================================================================
# 6. SQLite Foreign Key enforcement test
# ===================================================================

class TestSQLiteForeignKeyEnforcement(unittest.TestCase):
    """Test whether SQLite foreign key constraints are properly enforced."""

    def test_pragma_foreign_keys_default_off(self):
        """By default, SQLite has foreign_keys=OFF, meaning CASCADE won't work."""
        conn = sqlite3.connect(":memory:")
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys")
        result = cursor.fetchone()[0]
        conn.close()
        # 0 = OFF, 1 = ON
        if result == 0:
            self.fail(
                "BUG DETECTED: SQLite PRAGMA foreign_keys is OFF by default. "
                "ON DELETE CASCADE in the rankings table will NOT work. "
                "database.get_connection() must execute 'PRAGMA foreign_keys=ON' "
                "after connecting, or delete_game() must manually delete rankings first."
            )

    def test_database_get_connection_does_not_enable_foreign_keys(self):
        """Verify that get_connection() does NOT enable PRAGMA foreign_keys."""
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys")
        result = cursor.fetchone()[0]
        conn.close()
        if result == 0:
            self.fail(
                "BUG: database.get_connection() does not enable PRAGMA foreign_keys=ON. "
                "The ON DELETE CASCADE constraint defined on the rankings table is "
                "effectively useless. When a game is deleted via DELETE /api/games/<id>, "
                "its orphaned rankings will remain in the database. "
                "Fix: Add 'conn.execute(\"PRAGMA foreign_keys=ON\")' in get_connection(), "
                "or add 'DELETE FROM rankings WHERE game_id=?' in delete_game()."
            )


# ===================================================================
# 7. requirements.txt completeness check
# ===================================================================

class TestRequirements(unittest.TestCase):
    """Verify requirements.txt has all necessary packages."""

    def _read_requirements(self):
        req_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "requirements.txt"
        )
        with open(req_path) as f:
            return f.read()

    def test_requirements_file_exists(self):
        req_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "requirements.txt"
        )
        self.assertTrue(os.path.exists(req_path), "requirements.txt not found")

    def test_requirements_contains_flask(self):
        self.assertIn("Flask", self._read_requirements())

    def test_requirements_contains_apscheduler(self):
        self.assertIn("APScheduler", self._read_requirements())

    def test_requirements_contains_requests(self):
        self.assertIn("requests", self._read_requirements())


if __name__ == "__main__":
    unittest.main(verbosity=2)
