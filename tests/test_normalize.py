"""Golden tests for handle/country normalization against real-world messy inputs."""

from __future__ import annotations

import pytest

from vetting.models import HandleStatus
from vetting.normalize import (
    is_filtered_country,
    normalize_instagram,
    normalize_tiktok,
    normalize_youtube,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://www.instagram.com/ch.harm/?igsh=abc123", "ch.harm"),
        ("https://instagram.com/harrisun_ai", "harrisun_ai"),
        ("@harrisun_ai", "harrisun_ai"),
        ("HarrisUN_AI", "harrisun_ai"),  # lowercased
        ("  lisarussellfilm  ", "lisarussellfilm"),  # trimmed
    ],
)
def test_instagram_resolves(raw, expected):
    result = normalize_instagram(raw)
    assert result.status is HandleStatus.OK
    assert result.value == expected


@pytest.mark.parametrize("raw", ["", "NA", "na", "yes", "None", None])
def test_instagram_missing(raw):
    assert normalize_instagram(raw).status is HandleStatus.MISSING


@pytest.mark.parametrize(
    "raw",
    [
        "https://linktr.ee/ChristinaZ",  # aggregator
        "https://www.instagram.com/p/CxYz123/",  # a post, not a profile
        "https://mypersonalsite.com/about",  # unrelated site
        "two words",  # not a valid handle
    ],
)
def test_instagram_unresolvable(raw):
    assert normalize_instagram(raw).status is HandleStatus.UNRESOLVABLE


def test_tiktok_from_url():
    result = normalize_tiktok("https://www.tiktok.com/@christina.zahabi")
    assert result.status is HandleStatus.OK
    assert result.value == "christina.zahabi"
    assert result.url == "https://www.tiktok.com/@christina.zahabi"


def test_tiktok_short_link_needs_human():
    assert normalize_tiktok("https://vm.tiktok.com/ZMabc/").status is HandleStatus.UNRESOLVABLE


def test_tiktok_bare_username():
    assert normalize_tiktok("nako_fish").value == "nako_fish"


@pytest.mark.parametrize(
    "raw,expected_ref",
    [
        ("https://youtube.com/@takoshotdat", "handle:takoshotdat"),
        ("takoshotdat", "handle:takoshotdat"),
        ("https://www.youtube.com/channel/UC1234567890123456789012", "id:UC1234567890123456789012"),
        ("https://www.youtube.com/user/MrZ2128", "user:MrZ2128"),
    ],
)
def test_youtube_refs(raw, expected_ref):
    result = normalize_youtube(raw)
    assert result.status is HandleStatus.OK
    assert result.value == expected_ref


@pytest.mark.parametrize(
    "country,filtered",
    [
        ("India", True),
        ("pakistan", True),  # case-insensitive
        ("  Nigeria ", True),  # whitespace
        ("Iran", True),
        ("United States", False),
        ("Cyprus", False),
        (None, False),
        ("", False),
    ],
)
def test_geographic_screen(country, filtered):
    assert is_filtered_country(country) is filtered
