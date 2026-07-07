"""Tests for the Bright Data LinkedIn client (parsing + verdicts, no network)."""

from __future__ import annotations

import json

from vetting.brightdata import BrightDataLinkedInClient
from vetting.models import Handle, HandleStatus, Verdict


class FakeResp:
    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    """Captures the POST and returns a canned JSONL body."""

    def __init__(self, body: str, status: int = 200):
        self._body = body
        self._status = status
        self.last = None

    def post(self, url, **kwargs):
        self.last = (url, kwargs)
        return FakeResp(self._body, self._status)


def _handle(url="https://www.linkedin.com/in/jane-doe"):
    return Handle("linkedin", "jane-doe", "jane-doe", url, HandleStatus.OK)


def _client(body, status=200):
    return BrightDataLinkedInClient("tok", "gd_x", session=FakeSession(body, status))


def test_followers_pass():
    body = json.dumps({"input_url": "https://www.linkedin.com/in/jane-doe",
                       "followers": 1500, "name": "Jane"})
    res = _client(body).check_linkedin(_handle())
    assert res.followers == 1500
    assert res.verdict is Verdict.PASS
    assert res.note == "via bright data"


def test_followers_below_threshold_fails():
    body = json.dumps({"input_url": "https://www.linkedin.com/in/jane-doe", "followers": 499})
    res = _client(body).check_linkedin(_handle())
    assert res.followers == 499
    assert res.verdict is Verdict.FAIL


def test_missing_followers_is_review():
    body = json.dumps({"input_url": "https://www.linkedin.com/in/jane-doe",
                       "warning": "profile unavailable"})
    res = _client(body).check_linkedin(_handle())
    assert res.followers is None
    assert res.verdict is Verdict.REVIEW
    assert "bright data" in res.note


def test_jsonl_multiple_records_matched_by_input_url():
    url = "https://www.linkedin.com/in/jane-doe"
    body = "\n".join([
        json.dumps({"input_url": "https://www.linkedin.com/in/someone-else", "followers": 10}),
        json.dumps({"input_url": url, "followers": 3000}),
    ])
    res = _client(body).check_linkedin(_handle(url))
    assert res.followers == 3000  # picked the record whose input_url matches
    assert res.verdict is Verdict.PASS


def test_missing_handle_skipped_without_call():
    session = FakeSession("")
    client = BrightDataLinkedInClient("tok", "gd_x", session=session)
    res = client.check_linkedin(Handle("linkedin", None, None, None, HandleStatus.MISSING))
    assert res.verdict is Verdict.SKIPPED
    assert session.last is None  # no network call for a missing handle


def test_unresolvable_handle_is_review_without_call():
    session = FakeSession("")
    client = BrightDataLinkedInClient("tok", "gd_x", session=session)
    res = client.check_linkedin(
        Handle("linkedin", "x", None, None, HandleStatus.UNRESOLVABLE))
    assert res.verdict is Verdict.REVIEW
    assert session.last is None


def test_request_payload_shape():
    body = json.dumps({"input_url": "https://www.linkedin.com/in/jane-doe", "followers": 900})
    client = _client(body)
    client.check_linkedin(_handle())
    url, kwargs = client._session.last  # type: ignore[attr-defined]
    assert url.endswith("/datasets/v3/scrape")
    assert kwargs["params"]["dataset_id"] == "gd_x"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert json.loads(kwargs["data"]) == {
        "input": [{"url": "https://www.linkedin.com/in/jane-doe"}]}
