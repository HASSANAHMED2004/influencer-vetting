"""Pure metric logic: turning a list of recent videos into a representative
'average views' number, and applying the numeric thresholds.

Kept free of I/O so the vetting rules can be unit-tested with tiny inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import median

from .config import (
    INSTAGRAM_MIN_AVG_VIEWS,
    INSTAGRAM_MIN_FOLLOWERS,
    LINKEDIN_MIN_AVG_COMMENTS,
    LINKEDIN_MIN_FOLLOWERS,
    MIN_VIDEO_AGE_HOURS,
    RECENT_VIDEOS_COUNT,
    TIKTOK_MIN_AVG_VIEWS,
    TIKTOK_MIN_FOLLOWERS,
)
from .models import Verdict, VideoStat


def representative_views(
    videos: list[VideoStat],
    *,
    now: datetime,
    recent_count: int = RECENT_VIDEOS_COUNT,
    min_age_hours: int = MIN_VIDEO_AGE_HOURS,
) -> float | None:
    """Median play count of the most recent eligible videos.

    Median (not mean) so one viral hit or one dud doesn't skew the result, and
    'recent only' so early low-view videos don't unfairly drag an improving
    creator down. Pinned videos and anything younger than ``min_age_hours``
    (not enough time to accumulate views) are excluded.

    Returns ``None`` when there are no eligible videos to judge (e.g. a
    photo-only Instagram account) — the caller should treat that as "review",
    not an automatic fail.
    """
    eligible = [
        v
        for v in videos
        if not v.is_pinned and _age_hours(v.created_at, now) >= min_age_hours
    ]
    if not eligible:
        return None
    # Most recent first; undated videos sort last so dated ones are preferred.
    eligible.sort(key=lambda v: v.created_at or datetime.min.replace(tzinfo=UTC),
                  reverse=True)
    recent = eligible[:recent_count]
    return float(median(v.play_count for v in recent))


def _age_hours(created_at: datetime | None, now: datetime) -> float:
    """Hours since a video was posted. Undated videos are treated as old enough."""
    if created_at is None:
        return float("inf")
    delta = now - _as_aware(created_at)
    return delta.total_seconds() / 3600.0


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def evaluate_followers_and_views(
    followers: int | None,
    avg_views: float | None,
    *,
    min_followers: int,
    min_avg_views: int,
) -> Verdict:
    """Apply the two-part follower + average-views rule for IG/TikTok.

    - Missing follower count -> REVIEW (couldn't fetch; don't guess).
    - Followers below bar -> FAIL immediately.
    - Followers OK but no view data (e.g. no reels) -> REVIEW.
    - Both present -> PASS/FAIL on the average-views bar.
    """
    if followers is None:
        return Verdict.REVIEW
    if followers < min_followers:
        return Verdict.FAIL
    if avg_views is None:
        return Verdict.REVIEW
    return Verdict.PASS if avg_views >= min_avg_views else Verdict.FAIL


def evaluate_instagram(followers: int | None, avg_views: float | None) -> Verdict:
    return evaluate_followers_and_views(
        followers, avg_views,
        min_followers=INSTAGRAM_MIN_FOLLOWERS, min_avg_views=INSTAGRAM_MIN_AVG_VIEWS,
    )


def evaluate_tiktok(followers: int | None, avg_views: float | None) -> Verdict:
    return evaluate_followers_and_views(
        followers, avg_views,
        min_followers=TIKTOK_MIN_FOLLOWERS, min_avg_views=TIKTOK_MIN_AVG_VIEWS,
    )


def evaluate_linkedin(followers: int | None, avg_comments: float | None) -> Verdict:
    """LinkedIn rule: followers >= 500 AND median recent-post comments >= 100."""
    return evaluate_followers_and_views(
        followers, avg_comments,
        min_followers=LINKEDIN_MIN_FOLLOWERS, min_avg_views=LINKEDIN_MIN_AVG_COMMENTS,
    )
