"""Bright Data client for LinkedIn profile followers.

ScrapeCreators is ~89% blind on LinkedIn (private/unavailable profiles come back
404). Bright Data's "LinkedIn people profiles - collect by URL" scraper recovers
most of them. We use the synchronous (real-time) endpoint so a lookup is a single
blocking call.

Verified live (2026-07-07):
  POST /datasets/v3/scrape?dataset_id=<id>&notify=false&include_errors=true
  headers: Authorization: Bearer <token>, Content-Type: application/json
  body:    {"input": [{"url": "<profile url>"}]}
  -> 200 application/jsonl (one record per line); follower count in `followers`,
     `input_url` echoes the requested URL. ~3s per profile.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import requests

from .metrics import evaluate_linkedin
from .models import Handle, HandleStatus, PlatformResult, Verdict
from .quota import http_status

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.brightdata.com"
_SCRAPE = "/datasets/v3/scrape"
# LinkedIn people profiles - collect by URL (verified live 2026-07-07).
DEFAULT_DATASET_ID = "gd_l1viktl72bvl7bjuj0"

_PARSE_ERRORS = (KeyError, TypeError, ValueError, AttributeError)


class BrightDataLinkedInClient:
    """Fetches LinkedIn follower counts via Bright Data's real-time scraper."""

    def __init__(self, api_token: str, dataset_id: str | None = None,
                 session: requests.Session | None = None, timeout: float = 180.0) -> None:
        if not api_token:
            raise ValueError("Bright Data API token is required")
        self._token = api_token
        self._dataset_id = dataset_id or DEFAULT_DATASET_ID
        self._session = session or requests.Session()
        self._timeout = timeout

    def check_linkedin(self, handle: Handle, *, now: datetime | None = None) -> PlatformResult:
        guard = _guard(handle)
        if guard is not None:
            return guard
        try:
            record = self._scrape(handle.url)
        except requests.RequestException as exc:
            return _api_error(handle, exc)
        except _PARSE_ERRORS as exc:
            return _parse_error(handle, exc)
        if record is None:
            return PlatformResult(handle=handle.value, verdict=Verdict.REVIEW,
                                  note="linkedin unavailable (bright data)")
        followers = _to_int(record.get("followers"))
        if followers is None:
            warn = record.get("warning") or record.get("error") or "no followers returned"
            return PlatformResult(handle=handle.value, verdict=Verdict.REVIEW,
                                  note=f"bright data: {str(warn)[:60]}")
        return PlatformResult(handle=handle.value, followers=followers,
                              verdict=evaluate_linkedin(followers, None),
                              note="via bright data")

    def _scrape(self, url: str) -> dict | None:
        """POST one profile URL to the sync endpoint; return the matching record."""
        resp = self._session.post(
            f"{_BASE_URL}{_SCRAPE}",
            params={"dataset_id": self._dataset_id, "notify": "false",
                    "include_errors": "true"},
            headers={"Authorization": f"Bearer {self._token}",
                     "Content-Type": "application/json"},
            data=json.dumps({"input": [{"url": url}]}),
            timeout=self._timeout,
        )
        resp.raise_for_status()
        records = _parse_jsonl(resp.text)
        for rec in records:
            if isinstance(rec, dict) and rec.get("input_url") == url:
                return rec
        return records[0] if records else None


def _parse_jsonl(text: str) -> list[dict]:
    """Parse Bright Data's JSONL body; tolerate a single JSON object too."""
    text = text.strip()
    if not text:
        return []
    try:  # sometimes a single JSON value (e.g. an error object or an array)
        value = json.loads(text)
        return value if isinstance(value, list) else [value]
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def _guard(handle: Handle) -> PlatformResult | None:
    if handle.status is HandleStatus.MISSING:
        return PlatformResult(verdict=Verdict.SKIPPED, note="no linkedin")
    if handle.status is HandleStatus.UNRESOLVABLE or not handle.url:
        return PlatformResult(verdict=Verdict.REVIEW, note="unresolvable linkedin link")
    return None


def _api_error(handle: Handle, exc: Exception) -> PlatformResult:
    logger.warning("bright data linkedin lookup failed for %s: %s", handle.value, exc)
    return PlatformResult(handle=handle.value, verdict=Verdict.REVIEW,
                          note=f"bright data error: {exc}",
                          error_status=http_status(exc))


def _parse_error(handle: Handle, exc: Exception) -> PlatformResult:
    logger.warning("bright data linkedin parse error for %s: %s", handle.value, exc)
    return PlatformResult(handle=handle.value, verdict=Verdict.REVIEW,
                          note="bright data: unexpected response shape")


def _to_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
