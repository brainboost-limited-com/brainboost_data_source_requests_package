from __future__ import annotations

from dataclasses import dataclass, field
import socket
from typing import Callable

import requests

from .ProxyPool import ProxyPool
from .UserAgentPool import UserAgentPool


DEFAULT_RETRY_STATUS_CODES = frozenset({401, 403, 408, 429, 500, 502, 503, 504})


@dataclass(slots=True)
class RequestAttempt:
    strategy: str
    status_code: int | None = None
    error: str = ""
    validator_message: str = ""


@dataclass(slots=True)
class FallbackRequestResult:
    response: requests.Response | None
    strategy: str
    attempts: list[RequestAttempt] = field(default_factory=list)
    error: Exception | None = None

    @property
    def last_attempt(self) -> RequestAttempt | None:
        if not self.attempts:
            return None
        return self.attempts[-1]

    def ensure_response(self) -> requests.Response:
        if self.response is None:
            if self.error is not None:
                raise self.error
            raise requests.RequestException("No HTTP response returned")

        if self.last_attempt and self.last_attempt.validator_message:
            raise RuntimeError(self.last_attempt.validator_message)
        return self.response

    def raise_for_status(self) -> requests.Response:
        response = self.ensure_response()
        response.raise_for_status()
        return response


ResponseValidator = Callable[[requests.Response], str | None]


class FallbackSession:
    """Direct-first requests transport with optional Tor/proxy fallbacks."""

    def __init__(
        self,
        *,
        timeout: int = 15,
        default_headers: dict[str, str] | None = None,
        retry_status_codes: set[int] | frozenset[int] | None = None,
        allow_tor_fallback: bool = False,
        allow_proxy_fallback: bool = False,
        max_proxy_attempts: int = 2,
        proxy_db_path: str | None = None,
        user_agents_list_path: str | None = None,
        tor_host: str = "127.0.0.1",
        tor_port: int = 9050,
    ) -> None:
        self.timeout = timeout
        self.default_headers = {
            "Accept-Language": "en-GB,en;q=0.5",
            **(default_headers or {}),
        }
        self.retry_status_codes = frozenset(retry_status_codes or DEFAULT_RETRY_STATUS_CODES)
        self.allow_tor_fallback = allow_tor_fallback
        self.allow_proxy_fallback = allow_proxy_fallback
        self.max_proxy_attempts = max(max_proxy_attempts, 0)
        self.tor_host = tor_host
        self.tor_port = tor_port
        self.user_agent_pool = UserAgentPool(user_agents_list_path=user_agents_list_path)
        self.proxy_pool = ProxyPool(proxy_db=proxy_db_path, auto_refresh=False)
        self._direct_session: requests.Session | None = None
        self._tor_session: requests.Session | None = None

    def close(self) -> None:
        for session in (self._direct_session, self._tor_session):
            if session is not None:
                session.close()
        self._direct_session = None
        self._tor_session = None

    def _new_session(self, *, proxy: dict | None = None) -> requests.Session:
        session = requests.Session()
        session.headers.update(self.default_headers)
        user_agent = self.user_agent_pool.get_random_user_agent()
        if user_agent:
            session.headers["User-Agent"] = user_agent

        if proxy is not None:
            proxy_mapping = ProxyPool.requests_proxy_mapping(proxy)
            if proxy_mapping:
                session.proxies.update(proxy_mapping)
        return session

    def _get_direct_session(self) -> requests.Session:
        if self._direct_session is None:
            self._direct_session = self._new_session()
        return self._direct_session

    def _get_rotated_direct_session(self) -> requests.Session:
        return self._new_session()

    def _tor_is_available(self) -> bool:
        if not self.allow_tor_fallback:
            return False
        try:
            with socket.create_connection((self.tor_host, self.tor_port), timeout=0.75):
                return True
        except OSError:
            return False

    def _get_tor_session(self) -> requests.Session | None:
        if not self._tor_is_available():
            return None

        if self._tor_session is None:
            self._tor_session = self._new_session(
                proxy={"protocol": "socks5", "ip": self.tor_host, "port": self.tor_port}
            )
        return self._tor_session

    def _iter_strategy_sessions(self):
        yield "direct", self._get_direct_session(), False
        yield "direct_rotated", self._get_rotated_direct_session(), True

        tor_session = self._get_tor_session()
        if tor_session is not None:
            yield "tor", tor_session, False

        if self.allow_proxy_fallback and self.max_proxy_attempts > 0:
            proxy_candidates = self.proxy_pool.get_best_proxies(limit=self.max_proxy_attempts)
            for index, proxy in enumerate(proxy_candidates, start=1):
                proxy_url = ProxyPool.proxy_url(proxy) or "unknown_proxy"
                yield f"proxy_{index}:{proxy_url}", self._new_session(proxy=proxy), True

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        retry_status_codes: set[int] | frozenset[int] | None = None,
        response_validator: ResponseValidator | None = None,
        **kwargs,
    ) -> FallbackRequestResult:
        timeout = kwargs.pop("timeout", self.timeout)
        retry_codes = frozenset(retry_status_codes or self.retry_status_codes)
        attempts: list[RequestAttempt] = []
        last_response: requests.Response | None = None
        last_error: Exception | None = None
        last_strategy = ""

        for strategy, session, close_after_attempt in self._iter_strategy_sessions():
            last_strategy = strategy
            try:
                response = session.request(method=method, url=url, headers=headers, timeout=timeout, **kwargs)
            except requests.RequestException as exc:
                attempts.append(RequestAttempt(strategy=strategy, error=str(exc)))
                last_error = exc
                if close_after_attempt:
                    session.close()
                continue

            validator_message = ""
            if response_validator is not None:
                validator_message = str(response_validator(response) or "").strip()

            attempts.append(
                RequestAttempt(
                    strategy=strategy,
                    status_code=response.status_code,
                    validator_message=validator_message,
                )
            )
            last_response = response

            if response.status_code in retry_codes or validator_message:
                if close_after_attempt:
                    session.close()
                continue

            if close_after_attempt:
                session.close()

            return FallbackRequestResult(
                response=response,
                strategy=strategy,
                attempts=attempts,
                error=last_error,
            )

        return FallbackRequestResult(
            response=last_response,
            strategy=last_strategy,
            attempts=attempts,
            error=last_error,
        )

    def get(self, url: str, **kwargs) -> FallbackRequestResult:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> FallbackRequestResult:
        return self.request("POST", url, **kwargs)
