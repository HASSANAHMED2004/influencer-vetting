"""YouTube Data API v3 client — the free part of the pipeline.

Checks whether a channel has at least one video above the view threshold.

Quota note: this uses the cheap endpoints (channels.list -> uploads playlist ->
playlistItems.list -> videos.list), roughly a few units per channel, rather than
search.list (100 units each). It scans the most recent ``MAX_VIDEOS_TO_SCAN``
uploads; a viral video older than that window would be missed — raise the cap if
that matters to you (at higher quota cost).
"""

from __future__ import annotations

import logging

import requests

from .config import YOUTUBE_MIN_VIDEO_VIEWS
from .models import Handle, HandleStatus, PlatformResult, Verdict
from .quota import http_status

logger = logging.getLogger(__name__)

_API_ROOT = "https://www.googleapis.com/youtube/v3"
MAX_VIDEOS_TO_SCAN = 200  # most-recent uploads to inspect per channel
_PAGE_SIZE = 50  # API maximum


class YouTubeClient:
    """Thin wrapper over the YouTube Data API. One instance per run."""

    def __init__(self, api_key: str, session: requests.Session | None = None,
                 timeout: float = 20.0) -> None:
        if not api_key:
            raise ValueError("YouTube API key is required")
        self._key = api_key
        self._session = session or requests.Session()
        self._timeout = timeout

    def _get(self, endpoint: str, params: dict) -> dict:
        params = {**params, "key": self._key}
        resp = self._session.get(f"{_API_ROOT}/{endpoint}", params=params,
                                 timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def resolve_channel_id(self, ref: str) -> str | None:
        """Resolve a normalized YouTube ref (``id:``/``handle:``/``user:``) to a channel id."""
        kind, _, value = ref.partition(":")
        if kind == "id":
            return value
        params = {"part": "id"}
        if kind == "handle":
            params["forHandle"] = value
        elif kind == "user":
            params["forUsername"] = value
        else:
            return None
        items = self._get("channels", params).get("items", [])
        return items[0]["id"] if items else None

    def _uploads_playlist_id(self, channel_id: str) -> str | None:
        params = {"part": "contentDetails", "id": channel_id}
        items = self._get("channels", params).get("items", [])
        if not items:
            return None
        return items[0]["contentDetails"]["relatedPlaylists"].get("uploads")

    def _recent_video_ids(self, playlist_id: str) -> list[str]:
        video_ids: list[str] = []
        page_token: str | None = None
        while len(video_ids) < MAX_VIDEOS_TO_SCAN:
            params = {
                "part": "contentDetails",
                "playlistId": playlist_id,
                "maxResults": _PAGE_SIZE,
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._get("playlistItems", params)
            for item in data.get("items", []):
                video_ids.append(item["contentDetails"]["videoId"])
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return video_ids[:MAX_VIDEOS_TO_SCAN]

    def _max_view_count(self, video_ids: list[str]) -> int:
        highest = 0
        for start in range(0, len(video_ids), _PAGE_SIZE):
            batch = video_ids[start : start + _PAGE_SIZE]
            data = self._get("videos", {"part": "statistics", "id": ",".join(batch)})
            for item in data.get("items", []):
                views = int(item.get("statistics", {}).get("viewCount", 0))
                highest = max(highest, views)
        return highest

    def check(self, handle: Handle) -> PlatformResult:
        """Return a PlatformResult for the YouTube criterion (any video >= threshold)."""
        if handle.status is HandleStatus.MISSING:
            return PlatformResult(verdict=Verdict.SKIPPED, note="no youtube")
        if handle.status is HandleStatus.UNRESOLVABLE or not handle.value:
            return PlatformResult(verdict=Verdict.REVIEW, note="unresolvable youtube link")

        try:
            channel_id = self.resolve_channel_id(handle.value)
            if channel_id is None:
                return PlatformResult(handle=handle.value, verdict=Verdict.REVIEW,
                                      note="channel not found")
            playlist_id = self._uploads_playlist_id(channel_id)
            if playlist_id is None:
                return PlatformResult(handle=handle.value, verdict=Verdict.REVIEW,
                                      note="no uploads playlist")
            max_views = self._max_view_count(self._recent_video_ids(playlist_id))
        except requests.RequestException as exc:  # network/quota errors are recoverable
            logger.warning("YouTube lookup failed for %s: %s", handle.value, exc)
            return PlatformResult(handle=handle.value, verdict=Verdict.REVIEW,
                                  note=f"youtube api error: {exc}",
                                  error_status=http_status(exc))

        verdict = Verdict.PASS if max_views >= YOUTUBE_MIN_VIDEO_VIEWS else Verdict.FAIL
        return PlatformResult(handle=handle.value, avg_views=float(max_views), verdict=verdict)
