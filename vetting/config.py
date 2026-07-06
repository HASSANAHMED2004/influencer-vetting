"""Central configuration: column layout, thresholds, and screening rules.

Everything the pipeline treats as a "magic value" lives here so that tuning the
rules never means hunting through logic code.
"""

from __future__ import annotations

from .models import ExclusionRule, MatchMode

# --- Input sheet layout (1-based column indices, matching the VETO.xlsx file) ---
SHEET_NAME = "Sheet4"
HEADER_ROW = 1
FIRST_DATA_ROW = 2

COL_CHECKBOX = 1  # A
COL_EMAIL = 2  # B
COL_FIRST_NAME = 3  # C
COL_LAST_NAME = 4  # D
COL_YOUTUBE = 9  # I
COL_INSTAGRAM = 12  # L
COL_TIKTOK = 13  # M
COL_COUNTRY = 15  # O

# --- Output columns (appended to the right of the existing data) ---
COL_OUT_COUNTRY_STATUS = 17  # Q
COL_OUT_YT_MAX_VIEWS = 18  # R
COL_OUT_YT_VERDICT = 19  # S
COL_OUT_IG_HANDLE = 20  # T
COL_OUT_IG_FOLLOWERS = 21  # U
COL_OUT_IG_AVG_VIEWS = 22  # V
COL_OUT_IG_VERDICT = 23  # W
COL_OUT_TT_HANDLE = 24  # X
COL_OUT_TT_FOLLOWERS = 25  # Y
COL_OUT_TT_AVG_VIEWS = 26  # Z
COL_OUT_TT_VERDICT = 27  # AA
COL_OUT_OVERALL = 28  # AB
COL_OUT_NOTES = 29  # AC

OUTPUT_HEADERS = {
    COL_OUT_COUNTRY_STATUS: "vet_country_status",
    COL_OUT_YT_MAX_VIEWS: "yt_max_views",
    COL_OUT_YT_VERDICT: "yt_verdict",
    COL_OUT_IG_HANDLE: "ig_handle",
    COL_OUT_IG_FOLLOWERS: "ig_followers",
    COL_OUT_IG_AVG_VIEWS: "ig_avg_views",
    COL_OUT_IG_VERDICT: "ig_verdict",
    COL_OUT_TT_HANDLE: "tt_handle",
    COL_OUT_TT_FOLLOWERS: "tt_followers",
    COL_OUT_TT_AVG_VIEWS: "tt_avg_views",
    COL_OUT_TT_VERDICT: "tt_verdict",
    COL_OUT_OVERALL: "overall_verdict",
    COL_OUT_NOTES: "notes",
}

# --- Numeric thresholds (the vetting criteria) ---
YOUTUBE_MIN_VIDEO_VIEWS = 100_000
INSTAGRAM_MIN_FOLLOWERS = 1_000
INSTAGRAM_MIN_AVG_VIEWS = 3_000
TIKTOK_MIN_FOLLOWERS = 1_000
TIKTOK_MIN_AVG_VIEWS = 3_000

# --- Average-metric methodology (median recent views for IG/TikTok) ---
# Judge reach on recent content only, robust to viral spikes and duds:
# median of the most recent N eligible posts, excluding pinned posts and
# anything too fresh to have accumulated engagement.
RECENT_VIDEOS_COUNT = 12
MIN_VIDEO_AGE_HOURS = 48

# --- Geographic screen (filter on self-reported country of residence) ---
# Rows whose country of residence is one of these are excluded up front.
FILTERED_COUNTRIES = frozenset(
    {
        "pakistan", "india", "nepal", "bangladesh", "iran", "iraq", "nigeria",
        "ethiopia", "jordan", "egypt", "albania", "kenya", "algeria",
    }
)

# --- Exclusion rules: disregard a row by its country of residence ---
# Exclusion is hardcoded to the country column. Rules only list the values to
# match; add more here (or pass --exclude-country / --exclude-country-contains
# on the CLI). Example substring rule:
#   ExclusionRule(values=frozenset({"korea"}), mode=MatchMode.CONTAINS),
EXCLUSION_RULES: list[ExclusionRule] = [
    ExclusionRule(
        values=FILTERED_COUNTRIES,
        mode=MatchMode.EXACT,
        label="country of residence",
    ),
]

# Tokens that appear in handle/country cells but carry no real value.
JUNK_TOKENS = frozenset({"", "none", "na", "n/a", "nil", "-", "yes", "no", "null"})

# An influencer is a candidate for manual content review if their numbers clear
# the bar on AT LEAST ONE platform they are present on. Set to False to instead
# require passing on every platform where they have an account.
QUALIFY_ON_ANY_PLATFORM = True

# Within the PAID platforms, stop once one passes (OR logic), to save credits.
# The paid platforms are tried in priority order — Instagram, then TikTok — and
# the rest are skipped after the first paid pass. YouTube (free) is always checked
# independently and does NOT short-circuit the paid chain.
# Only applies when QUALIFY_ON_ANY_PLATFORM.
SHORT_CIRCUIT_ON_PASS = True
