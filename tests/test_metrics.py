"""Tests for the average-views methodology and threshold logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from vetting.metrics import evaluate_instagram, evaluate_linkedin, representative_views
from vetting.models import Verdict, VideoStat

NOW = datetime(2026, 7, 4, tzinfo=UTC)


def _video(views: int, days_old: int = 10, pinned: bool = False) -> VideoStat:
    return VideoStat(play_count=views, created_at=NOW - timedelta(days=days_old),
                     is_pinned=pinned)


def test_median_ignores_a_single_viral_spike():
    videos = [_video(v) for v in (1000, 1200, 900, 1100, 50000)]  # one viral outlier
    # Median of recent videos, not mean — the spike shouldn't lift the number.
    assert representative_views(videos, now=NOW) == 1100


def test_old_low_view_videos_do_not_drag_down_recent_performance():
    recent = [_video(4000, days_old=d) for d in (2, 3, 4, 5)]
    ancient = [_video(50, days_old=d) for d in (900, 950, 1000)]
    # Only the most recent ~12 count, so the ancient duds are irrelevant here.
    assert representative_views(recent + ancient, now=NOW) == 4000


def test_pinned_and_too_fresh_videos_are_excluded():
    videos = [
        _video(9000, pinned=True),  # pinned -> excluded
        _video(8000, days_old=0),  # <48h old -> excluded
        _video(2000),
        _video(2200),
    ]
    assert representative_views(videos, now=NOW) == 2100


def test_no_eligible_videos_returns_none():
    assert representative_views([], now=NOW) is None
    assert representative_views([_video(5000, days_old=0)], now=NOW) is None


def test_instagram_verdict_two_part_rule():
    assert evaluate_instagram(5000, 4000.0) is Verdict.PASS
    assert evaluate_instagram(500, 4000.0) is Verdict.FAIL  # too few followers
    assert evaluate_instagram(5000, 1000.0) is Verdict.FAIL  # too few views
    assert evaluate_instagram(5000, None) is Verdict.REVIEW  # followers ok, no reels
    assert evaluate_instagram(None, None) is Verdict.REVIEW  # couldn't fetch


def test_linkedin_verdict_500_followers_100_comments():
    assert evaluate_linkedin(600, 120.0) is Verdict.PASS
    assert evaluate_linkedin(400, 120.0) is Verdict.FAIL  # under 500 followers
    assert evaluate_linkedin(600, 50.0) is Verdict.FAIL  # under 100 median comments
    assert evaluate_linkedin(600, None) is Verdict.REVIEW  # followers ok, no posts
