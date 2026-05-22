# config.py
# Configuration for Game Rank Tracker (iOS App Store + Google Play Store)
# Uses MZStore endpoint (200+ results) + iTunes Lookup API for iOS
# Uses Google Play Store batchexecute API for Android

# ---- Stores ----
STORES = {
    "ios": "iOS App Store",
    "google": "Google Play Store",
}

# ---- Default games to track ----
# Each game has a default app_id and region_ids for per-region overrides
# For Google Play: use the package name (e.g., "com.supercell.clashofclans")
DEFAULT_GAMES = [
    {
        "name": "火炬之光：无限",
        "app_id": "1528917194",  # CN default
        "google_app_id": "com.xd.torchlight.cn",  # CN Google Play ID
        "region_ids": {
            "cn": "1528917194",
            "us": "1593130084",
            "gb": "1593130084",
            "jp": "1593130084",
            "kr": "1593130084",
            "sg": "1593130084",
            "th": "1593130084",
            "de": "1593130084",
            "fr": "1593130084",
            "hk": "1593130084",
            "mo": "1593130084",
            "tw": "1593130084",
        },
    },
    {
        "name": "心动小镇",
        "app_id": "1561903786",  # CN default
        "google_app_id": "com.xd.xdtown.googlepay",  # CN Google Play ID
        "region_ids": {
            "cn": "1561903786",
            "us": "6746151928",
            "gb": "6746151928",
            "jp": "6746151928",
            "kr": "6746151928",
            "sg": "6746151928",
            "th": "6746151928",
            "de": "6746151928",
            "fr": "6746151928",
            "hk": "6746151928",
            "mo": "6746151928",
            "tw": "6746151928",
        },
    },
]

# ---- iOS App Store: Regions ----
REGIONS = {
    "cn": "中国",
    "us": "美国",
    "gb": "英国",
    "jp": "日本",
    "kr": "韩国",
    "sg": "新加坡",
    "th": "泰国",
    "de": "德国",
    "fr": "法国",
    "hk": "香港",
    "mo": "澳门",
    "tw": "台湾",
    "my": "马来西亚",
    "vn": "越南",
}

# ---- iOS App Store: MZStore Endpoint ----
MZSTORE_BASE_URL = "https://itunes.apple.com/WebObjects/MZStore.woa/wa/viewTop"
MZSTORE_HEADERS = {
    "User-Agent": "iTunes/12.0 (Windows; Microsoft Windows 10)",
}
MZSTORE_GENRE_ID = "6014"  # Games genre
MZSTORE_POP_ID = "27"      # Default tab (all charts returned regardless)

# Chart shortTitle -> internal chart type mapping
CHART_TITLE_MAP = {
    "免费": "top-free",
    "畅销排行": "top-grossing",
    "付费": "top-paid",
    "Free": "top-free",
    "Top Grossing": "top-grossing",
    "Paid": "top-paid",
    "無料": "top-free",
    "トップセールス": "top-grossing",
    "有料": "top-paid",
    "무료": "top-free",
    "최고 매출": "top-grossing",
    "유료": "top-paid",
    "Gratis": "top-free",
    "Umsatz": "top-grossing",
    "Gekauft": "top-paid",
    "Gratuites": "top-free",
    "Rentables": "top-grossing",
    "Payantes": "top-paid",
    "免費": "top-free",
    "最高收入": "top-grossing",
    "暢銷排行": "top-grossing",
    "付費": "top-paid",
}

# iOS chart types we track
TRACKED_CHARTS = ["top-free", "top-grossing"]

# Target rank limit
CHART_LIMIT = 500

# iTunes Lookup API
LOOKUP_URL_TEMPLATE = "https://itunes.apple.com/{region}/lookup"
LOOKUP_BATCH_SIZE = 100

# HTTP request timeout
REQUEST_TIMEOUT = 15

# Database file path
DATABASE_PATH = "rank_tracker.db"

# Scheduled job time (24-hour format)
SCHEDULE_HOUR = 21
SCHEDULE_MINUTE = 0

# Flask settings
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000
FLASK_DEBUG = False

# ---- Google Play Store ----
GOOGLE_PLAY_COLLECTIONS = {
    "top-free": "topselling_free",
    "top-paid": "topselling_paid",
}

GOOGLE_PLAY_CATEGORY = "GAME"

# Google Play Store supported countries (subset of iOS regions with Google Play presence)
GOOGLE_PLAY_REGIONS = {
    "us": "US",
    "gb": "GB",
    "jp": "JP",
    "kr": "KR",
    "sg": "SG",
    "th": "TH",
    "de": "DE",
    "fr": "FR",
    "hk": "HK",
    "tw": "TW",
    "mo": "MO",
    "my": "MY",
    "vn": "VN",
}

GOOGLE_PLAY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
}

GOOGLE_PLAY_CHART_TYPES = ["top-free", "top-paid"]
