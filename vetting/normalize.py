"""Pure normalization helpers: messy handle/link cells -> canonical handles,
and country strings -> the geographic screen decision.

No I/O, no network — everything here is deterministic and unit-tested.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .config import FILTERED_COUNTRIES, JUNK_TOKENS
from .models import Handle, HandleStatus

# Handle character set shared by Instagram and TikTok (letters, digits, dot, underscore).
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

# Path segments that are pages, not usernames, on instagram.com.
_IG_RESERVED = frozenset(
    {"p", "reel", "reels", "explore", "stories", "tv", "s", "accounts", "about", ""}
)

# Bio-link aggregators: present, but not a platform handle — a human must resolve them.
_AGGREGATOR_HOSTS = frozenset(
    {
        "linktr.ee",
        "linktree.com",
        "beacons.ai",
        "linkin.bio",
        "lnk.bio",
        "lnk.to",
        "campsite.bio",
        "bio.link",
        "solo.to",
        "linkpop.com",
        "koji.to",
        "carrd.co",
        "milkshake.app",
        "linkbio.co",
        "tap.bio",
    }
)


def _clean(value: object) -> str | None:
    """Strip a raw cell to a trimmed string, or None if it is blank/junk."""
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in JUNK_TOKENS:
        return None
    return text or None


def normalize_country(value: object) -> str | None:
    """Return the trimmed country string, or None if blank/junk."""
    return _clean(value)


def is_filtered_country(value: object) -> bool:
    """True if the self-reported country of residence is on the exclude list."""
    country = normalize_country(value)
    return country is not None and country.strip().lower() in FILTERED_COUNTRIES


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _handle_from_url_path(url: str, reserved: frozenset[str]) -> str | None:
    """Extract the first meaningful path segment from a profile URL, else None."""
    path = urlparse(url).path.strip("/")
    if not path:
        return None
    first = path.split("/")[0].lstrip("@")
    if first.lower() in reserved or not _HANDLE_RE.match(first):
        return None
    return first


def _looks_like_url(text: str) -> bool:
    return "://" in text or text.lower().startswith("www.") or "/" in text


def normalize_instagram(value: object) -> Handle:
    """Normalize an Instagram cell (URL, @handle, bare username, or junk)."""
    return _normalize_social(
        value,
        platform="instagram",
        canonical_hosts={"instagram.com"},
        reserved=_IG_RESERVED,
        url_template="https://www.instagram.com/{handle}",
    )


def normalize_tiktok(value: object) -> Handle:
    """Normalize a TikTok cell. TikTok usernames live after an '@'."""
    text = _clean(value)
    if text is None:
        return Handle("tiktok", None, None, None, HandleStatus.MISSING)

    # Short share links (vm/vt.tiktok.com) redirect to the real profile — a human
    # (or a redirect-follow) is needed; do not guess.
    if _looks_like_url(text):
        host = _host(text)
        if host in _AGGREGATOR_HOSTS or host in {"vm.tiktok.com", "vt.tiktok.com"}:
            return Handle("tiktok", text, None, text, HandleStatus.UNRESOLVABLE)
        if host.endswith("tiktok.com"):
            handle = _handle_from_url_path(text, frozenset({""}))
            if handle:
                handle = handle.lower()
                return Handle(
                    "tiktok", text, handle, f"https://www.tiktok.com/@{handle}",
                    HandleStatus.OK,
                )
        return Handle("tiktok", text, None, text, HandleStatus.UNRESOLVABLE)

    candidate = text.lstrip("@")
    if _HANDLE_RE.match(candidate):
        handle = candidate.lower()
        return Handle(
            "tiktok", text, handle, f"https://www.tiktok.com/@{handle}", HandleStatus.OK
        )
    return Handle("tiktok", text, None, None, HandleStatus.UNRESOLVABLE)


def _normalize_social(
    value: object,
    *,
    platform: str,
    canonical_hosts: set[str],
    reserved: frozenset[str],
    url_template: str,
) -> Handle:
    """Shared logic for platforms whose username is the first URL path segment."""
    text = _clean(value)
    if text is None:
        return Handle(platform, None, None, None, HandleStatus.MISSING)

    if _looks_like_url(text):
        host = _host(text)
        if host in _AGGREGATOR_HOSTS:
            return Handle(platform, text, None, text, HandleStatus.UNRESOLVABLE)
        if host in canonical_hosts:
            handle = _handle_from_url_path(text, reserved)
            if handle:
                handle = handle.lower()
                return Handle(
                    platform, text, handle, url_template.format(handle=handle),
                    HandleStatus.OK,
                )
        # A link to some other site (personal website, etc.) — can't derive a handle.
        return Handle(platform, text, None, text, HandleStatus.UNRESOLVABLE)

    candidate = text.lstrip("@")
    if _HANDLE_RE.match(candidate):
        handle = candidate.lower()
        return Handle(
            platform, text, handle, url_template.format(handle=handle), HandleStatus.OK
        )
    return Handle(platform, text, None, None, HandleStatus.UNRESOLVABLE)


# --- YouTube references are richer: they may be channel IDs, @handles, or /user/ URLs ---

_YT_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def normalize_youtube(value: object) -> Handle:
    """Normalize a YouTube cell into a reference the API client can resolve.

    The resolved ``value`` is prefixed so the client knows how to look it up:
    ``id:UC...`` (channel id), ``handle:name`` (@handle / bare name),
    or ``user:name`` (legacy /user/ URL).
    """
    text = _clean(value)
    if text is None:
        return Handle("youtube", None, None, None, HandleStatus.MISSING)

    if _looks_like_url(text):
        host = _host(text)
        if host in _AGGREGATOR_HOSTS:
            return Handle("youtube", text, None, text, HandleStatus.UNRESOLVABLE)
        if not host.endswith("youtube.com") and host != "youtu.be":
            return Handle("youtube", text, None, text, HandleStatus.UNRESOLVABLE)
        segments = [s for s in urlparse(text).path.strip("/").split("/") if s]
        if not segments:
            return Handle("youtube", text, None, text, HandleStatus.UNRESOLVABLE)
        head = segments[0]
        if head.startswith("@"):
            return Handle("youtube", text, f"handle:{head[1:].lower()}", text, HandleStatus.OK)
        if head == "channel" and len(segments) > 1 and _YT_CHANNEL_ID_RE.match(segments[1]):
            return Handle("youtube", text, f"id:{segments[1]}", text, HandleStatus.OK)
        if head in {"user", "c"} and len(segments) > 1:
            return Handle("youtube", text, f"user:{segments[1]}", text, HandleStatus.OK)
        return Handle("youtube", text, None, text, HandleStatus.UNRESOLVABLE)

    bare = text.lstrip("@")
    if _YT_CHANNEL_ID_RE.match(bare):
        return Handle("youtube", text, f"id:{bare}", None, HandleStatus.OK)
    if _HANDLE_RE.match(bare):
        return Handle("youtube", text, f"handle:{bare.lower()}", None, HandleStatus.OK)
    return Handle("youtube", text, None, None, HandleStatus.UNRESOLVABLE)
