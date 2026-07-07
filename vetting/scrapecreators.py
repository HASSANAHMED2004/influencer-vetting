"""ScrapeCreators client — the paid part (Instagram + TikTok + LinkedIn).

Only runs when SCRAPECREATORS_API_KEY is set; otherwise the pipeline skips these
steps and marks them "not-checked".

Endpoint paths and response schemas verified live against the ScrapeCreators API
(2026-07-05):
  - Instagram  /v1/instagram/profile      -> followers + embedded recent media (1 call)
  - TikTok     /v1/tiktok/profile         -> followers
               /v3/tiktok/profile/videos  -> recent videos (play counts)
  - LinkedIn   /v1/linkedin/profile       -> followers + recentPosts (links only)
               /v1/linkedin/post          -> likeCount per post (only when followers pass)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import requests

from .config import LINKEDIN_MIN_FOLLOWERS, LINKEDIN_RECENT_POSTS_COUNT
from .metrics import (
    average_recent_likes,
    evaluate_instagram,
    evaluate_linkedin,
    evaluate_tiktok,
    representative_views,
)
from .models import Handle, HandleStatus, PlatformResult, Verdict, VideoStat

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.scrapecreators.com"
_IG_PROFILE = "/v1/instagram/profile"
_TT_PROFILE = "/v1/tiktok/profile"
_TT_VIDEOS = "/v3/tiktok/profile/videos"
_LI_PROFILE = "/v1/linkedin/profile"
_LI_POST = "/v1/linkedin/post"

# Parse failures we convert into a REVIEW verdict rather than crashing the run.
_PARSE_ERRORS = (KeyError, TypeError, ValueError, AttributeError)


class ScrapeCreatorsClient:
    """Fetches follower counts and recent-content metrics for IG, TikTok, LinkedIn."""

    def __init__(self, api_key: str, session: requests.Session | None = None,
                 timeout: float = 30.0) -> None:
        if not api_key:
            raise ValueError("ScrapeCreators API key is required")
        self._key = api_key
        self._session = session or requests.Session()
        self._timeout = timeout

    def _get(self, path: str, params: dict) -> dict:
        resp = self._session.get(
            f"{_BASE_URL}{path}", params=params,
            headers={"x-api-key": self._key}, timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # --- Instagram: one profile call yields followers + recent media views ---

    def check_instagram(self, handle: Handle, *, now: datetime | None = None) -> PlatformResult:
        guard = _guard(handle)
        if guard is not None:
            return guard
        now = now or datetime.now(UTC)
        try:
            resp = self._get(_IG_PROFILE, {"handle": handle.value})
            user = (resp.get("data") or {}).get("user")
            if not user:  # deleted/renamed/private account -> data is null
                return _not_found(handle, resp)
            followers = _to_int((user.get("edge_followed_by") or {}).get("count"))
            videos = _ig_videos(user)
        except requests.RequestException as exc:
            return _api_error(handle, exc)
        except _PARSE_ERRORS as exc:
            return _parse_error(handle, exc)
        avg = representative_views(videos, now=now)
        return PlatformResult(handle=handle.value, followers=followers,
                              avg_views=avg, verdict=evaluate_instagram(followers, avg))

    # --- TikTok: profile for followers, separate endpoint for video views ---

    def check_tiktok(self, handle: Handle, *, now: datetime | None = None) -> PlatformResult:
        guard = _guard(handle)
        if guard is not None:
            return guard
        now = now or datetime.now(UTC)
        try:
            profile = self._get(_TT_PROFILE, {"handle": handle.value})
            stats = profile.get("stats")
            if profile.get("account_deactivated") or not stats:
                return _not_found(handle, profile)
            followers = _to_int(stats.get("followerCount"))
            videos = _tt_videos(self._get(_TT_VIDEOS, {"handle": handle.value}))
        except requests.RequestException as exc:
            return _api_error(handle, exc)
        except _PARSE_ERRORS as exc:
            return _parse_error(handle, exc)
        avg = representative_views(videos, now=now)
        return PlatformResult(handle=handle.value, followers=followers,
                              avg_views=avg, verdict=evaluate_tiktok(followers, avg))

    # --- LinkedIn: profile for followers; post calls only when followers pass ---

    def check_linkedin(self, handle: Handle, *, now: datetime | None = None) -> PlatformResult:
        guard = _guard(handle)
        if guard is not None:
            return guard
        try:
            profile = self._get(_LI_PROFILE, {"url": handle.url})
            followers = _to_int(profile.get("followers"))
            if followers is None:
                return PlatformResult(
                    handle=handle.value, verdict=Verdict.REVIEW, note="follower count unavailable",
                )
            if followers < LINKEDIN_MIN_FOLLOWERS:
                return PlatformResult(
                    handle=handle.value, followers=followers, verdict=Verdict.FAIL,
                    note=f"below {LINKEDIN_MIN_FOLLOWERS} followers",
                )
            likes = self._linkedin_like_stats(profile.get("recentPosts") or [])
        except requests.RequestException as exc:
            return _api_error(handle, exc)
        except _PARSE_ERRORS as exc:
            return _parse_error(handle, exc)
        avg = average_recent_likes(likes)
        return PlatformResult(handle=handle.value, followers=followers,
                              avg_views=avg, verdict=evaluate_linkedin(followers, avg))

    def _linkedin_like_stats(self, posts: list[dict]) -> list[VideoStat]:
        """Fetch likeCount for up to N authored posts (one request each)."""
        stats: list[VideoStat] = []
        for post in posts:
            if len(stats) >= LINKEDIN_RECENT_POSTS_COUNT:
                break
            if not _is_authored_linkedin_post(post):
                continue
            link = post.get("link")
            if not link:
                continue
            detail = self._get(_LI_POST, {"url": link})
            likes = _to_int(detail.get("likeCount"))
            if likes is None:
                continue
            stats.append(VideoStat(
                play_count=likes,
                created_at=_parse_iso(post.get("datePublished")),
                is_pinned=False,
            ))
        return stats


# --- Guards and error results ---


def _guard(handle: Handle) -> PlatformResult | None:
    if handle.status is HandleStatus.MISSING:
        return PlatformResult(verdict=Verdict.SKIPPED, note=f"no {handle.platform}")
    if handle.status is HandleStatus.UNRESOLVABLE or not handle.url:
        return PlatformResult(verdict=Verdict.REVIEW,
                              note=f"unresolvable {handle.platform} link")
    return None


def _is_authored_linkedin_post(post: dict) -> bool:
    """Skip reshares/reactions — only count posts authored by the profile owner."""
    activity = (post.get("activityType") or "").casefold()
    return not activity.startswith("shared by")


def _not_found(handle: Handle, payload: dict) -> PlatformResult:
    """A 200 response signalling the account doesn't exist / is deactivated."""
    message = payload.get("message") or payload.get("error") or "account not found"
    return PlatformResult(handle=handle.value, verdict=Verdict.REVIEW,
                          note=f"not found: {message}")


def _api_error(handle: Handle, exc: Exception) -> PlatformResult:
    logger.warning("%s lookup failed for %s: %s", handle.platform, handle.value, exc)
    return PlatformResult(handle=handle.value, verdict=Verdict.REVIEW,
                          note=f"api error: {exc}")


def _parse_error(handle: Handle, exc: Exception) -> PlatformResult:
    logger.warning("%s parse failed for %s: %r", handle.platform, handle.value, exc)
    return PlatformResult(handle=handle.value, verdict=Verdict.REVIEW,
                          note=f"unexpected response shape: {exc!r}")


# --- Response parsers: the ONLY place that knows the vendor's JSON shapes. ---


def _to_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _ig_videos(user: dict) -> list[VideoStat]:
    """Video Reels among the ~12 recent posts embedded in an IG profile response."""
    media = user.get("edge_owner_to_timeline_media") or {}
    videos: list[VideoStat] = []
    for edge in media.get("edges", []):
        node = edge.get("node") or {}
        if not node.get("is_video"):
            continue  # photos/carousels have no view metric
        views = _to_int(node.get("video_view_count"))
        if views is None:
            continue
        videos.append(VideoStat(
            play_count=views,
            created_at=_epoch_to_dt(node.get("taken_at_timestamp")),
            is_pinned=bool(node.get("pinned_for_users")),
        ))
    return videos


def _tt_videos(payload: dict) -> list[VideoStat]:
    """Recent TikTok videos from the aweme_list response."""
    videos: list[VideoStat] = []
    for item in payload.get("aweme_list", []) or []:
        plays = _to_int((item.get("statistics") or {}).get("play_count"))
        if plays is None:
            continue
        videos.append(VideoStat(
            play_count=plays,
            created_at=_epoch_to_dt(item.get("create_time")),
            is_pinned=bool(item.get("is_top")),
        ))
    return videos


def _epoch_to_dt(value: object) -> datetime | None:
    seconds = _to_int(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
