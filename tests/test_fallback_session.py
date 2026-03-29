from pathlib import Path
import sys

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from brainboost_data_source_requests_package.FallbackSession import FallbackSession


def _response(url: str, status_code: int, body: str = "") -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = body.encode("utf-8")
    response.url = url
    response.request = requests.Request("GET", url).prepare()
    return response


def test_direct_request_succeeds_without_fallback(monkeypatch):
    calls = []

    def fake_request(self, method, url, headers=None, timeout=None, **kwargs):
        calls.append({"proxies": dict(self.proxies), "headers": dict(self.headers)})
        return _response(url, 200, "ok")

    monkeypatch.setattr(requests.Session, "request", fake_request)

    session = FallbackSession(allow_tor_fallback=False, allow_proxy_fallback=False)
    result = session.get("https://example.com")

    assert result.strategy == "direct"
    assert len(result.attempts) == 1
    assert result.response is not None
    assert result.response.status_code == 200
    assert calls[0]["proxies"] == {}


def test_rotated_direct_fallback_runs_after_retryable_status(monkeypatch):
    responses = iter([
        _response("https://example.com", 403, "blocked"),
        _response("https://example.com", 200, "ok"),
    ])

    def fake_request(self, method, url, headers=None, timeout=None, **kwargs):
        return next(responses)

    monkeypatch.setattr(requests.Session, "request", fake_request)

    session = FallbackSession(allow_tor_fallback=False, allow_proxy_fallback=False)
    result = session.get("https://example.com")

    assert [attempt.strategy for attempt in result.attempts] == ["direct", "direct_rotated"]
    assert result.response is not None
    assert result.response.status_code == 200
    assert result.strategy == "direct_rotated"


def test_proxy_fallback_runs_after_direct_attempts(monkeypatch):
    calls = []
    network_error = requests.RequestException("temporary failure")
    responses = iter([
        _response("https://example.com", 403, "blocked"),
        network_error,
        _response("https://example.com", 200, "proxy-ok"),
    ])

    def fake_request(self, method, url, headers=None, timeout=None, **kwargs):
        calls.append(dict(self.proxies))
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(requests.Session, "request", fake_request)

    session = FallbackSession(allow_tor_fallback=False, allow_proxy_fallback=True, max_proxy_attempts=1)
    monkeypatch.setattr(session.proxy_pool, "get_best_proxies", lambda limit=1: [
        {"protocol": "http", "ip": "127.0.0.1", "port": 8080}
    ])

    result = session.get("https://example.com")

    assert [attempt.strategy for attempt in result.attempts] == [
        "direct",
        "direct_rotated",
        "proxy_1:http://127.0.0.1:8080",
    ]
    assert result.response is not None
    assert result.response.status_code == 200
    assert calls[-1] == {
        "http": "http://127.0.0.1:8080",
        "https": "http://127.0.0.1:8080",
    }


def test_validator_failure_triggers_fallback(monkeypatch):
    responses = iter([
        _response("https://example.com", 200, "challenge"),
        _response("https://example.com", 200, "ok"),
    ])

    def fake_request(self, method, url, headers=None, timeout=None, **kwargs):
        return next(responses)

    monkeypatch.setattr(requests.Session, "request", fake_request)

    session = FallbackSession(allow_tor_fallback=False, allow_proxy_fallback=False)
    result = session.get(
        "https://example.com",
        response_validator=lambda response: "challenge page" if response.text == "challenge" else None,
    )

    assert [attempt.strategy for attempt in result.attempts] == ["direct", "direct_rotated"]
    assert result.response is not None
    assert result.response.text == "ok"
