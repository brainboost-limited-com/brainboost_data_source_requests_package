from __future__ import annotations

import json
import time
from pathlib import Path

import requests

try:
    from configuration import storage_proxy_pool_database_path
except Exception:
    storage_proxy_pool_database_path = ""


class ProxyPool:
    """Manages a local proxy database and optional refresh from remote sources."""

    def __init__(
        self,
        proxy_source_url: str | list[str] | None = None,
        proxy_db: str | None = None,
        *,
        auto_refresh: bool = False,
    ):
        self.proxy_source_urls = self._normalize_proxy_sources(proxy_source_url)
        self.proxy_db_path = self._resolve_proxy_db_path(proxy_db)
        self.proxy_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.current_proxy = None

        if auto_refresh:
            self.download_and_update_proxies()

    @staticmethod
    def _default_proxy_db_path() -> Path:
        return Path(__file__).resolve().parent / "resources" / "proxies_db.json"

    @staticmethod
    def _normalize_proxy_sources(proxy_source_url: str | list[str] | None) -> list[str]:
        if proxy_source_url is None:
            return ["https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.json"]
        if isinstance(proxy_source_url, str):
            return [proxy_source_url]
        return [str(url).strip() for url in proxy_source_url if str(url).strip()]

    def _resolve_proxy_db_path(self, proxy_db: str | None) -> Path:
        candidates = []
        if proxy_db:
            candidates.append(Path(proxy_db))
        if storage_proxy_pool_database_path:
            candidates.append(Path(storage_proxy_pool_database_path))
        candidates.append(self._default_proxy_db_path())

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[-1]

    def _load_proxies(self) -> list[dict]:
        try:
            with open(self.proxy_db_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return []
        except Exception:
            return []

        if not isinstance(data, list):
            return []
        return [proxy for proxy in data if isinstance(proxy, dict)]

    def _save_proxies(self, proxies: list[dict]) -> None:
        with open(self.proxy_db_path, "w", encoding="utf-8") as handle:
            json.dump(proxies, handle, indent=2, ensure_ascii=False)

    @staticmethod
    def _proxy_identity(proxy: dict) -> tuple[str, int]:
        return (str(proxy.get("ip", "")).strip(), int(proxy.get("port", 0) or 0))

    @staticmethod
    def proxy_url(proxy: dict) -> str | None:
        raw_proxy = str(proxy.get("proxy") or "").strip()
        if raw_proxy:
            return raw_proxy

        protocol = str(proxy.get("protocol") or "").strip()
        ip = str(proxy.get("ip") or "").strip()
        port = str(proxy.get("port") or "").strip()
        if protocol and ip and port:
            return f"{protocol}://{ip}:{port}"
        return None

    @staticmethod
    def requests_proxy_mapping(proxy: dict) -> dict[str, str] | None:
        proxy_url = ProxyPool.proxy_url(proxy)
        if not proxy_url:
            return None

        if proxy_url.startswith("socks5://"):
            proxy_url = proxy_url.replace("socks5://", "socks5h://", 1)
        elif proxy_url.startswith("socks4://"):
            proxy_url = proxy_url.replace("socks4://", "socks4a://", 1)

        return {"http": proxy_url, "https": proxy_url}

    def download_and_update_proxies(self, more_proxies_json: str | None = None) -> int:
        source_urls = list(self.proxy_source_urls)
        if more_proxies_json:
            source_urls.append(more_proxies_json)

        inserted_or_updated = 0
        for source_url in source_urls:
            try:
                response = requests.get(source_url, timeout=20)
                response.raise_for_status()
                proxies_data = response.json()
                if not isinstance(proxies_data, list):
                    continue

                for proxy in proxies_data:
                    if not isinstance(proxy, dict):
                        continue
                    self.insert_proxy_if_not_exists(proxy)
                    inserted_or_updated += 1
            except requests.RequestException:
                continue
            except ValueError:
                continue
        return inserted_or_updated

    def insert_proxy_if_not_exists(self, proxy: dict) -> None:
        identity = self._proxy_identity(proxy)
        if not all(identity):
            return

        payload = dict(proxy)
        if "request_time" not in payload:
            payload["request_time"] = payload.get("time_request", -1)

        proxies = self._load_proxies()
        updated = False
        for index, existing_proxy in enumerate(proxies):
            if self._proxy_identity(existing_proxy) == identity:
                proxies[index] = {**existing_proxy, **payload}
                updated = True
                break

        if not updated:
            proxies.append(payload)

        self._save_proxies(proxies)

    def list_proxies(self) -> list[dict]:
        return self._load_proxies()

    def get_random_proxy(self) -> dict | None:
        proxies = self.list_proxies()
        if not proxies:
            return None

        import random

        proxy = random.choice(proxies)
        self.current_proxy = self.proxy_url(proxy)
        return proxy

    def get_best_proxy(self) -> dict | None:
        proxies = self.get_best_proxies(limit=1)
        return proxies[0] if proxies else None

    def get_best_proxies(self, *, limit: int = 3, protocols: tuple[str, ...] | None = None) -> list[dict]:
        candidates = []
        for proxy in self.list_proxies():
            protocol = str(proxy.get("protocol") or "").strip().lower()
            request_time = float(proxy.get("request_time", proxy.get("time_request", -1)) or -1)
            if request_time < 0:
                continue
            if protocols and protocol not in protocols:
                continue
            if not self.requests_proxy_mapping(proxy):
                continue
            candidates.append(proxy)

        candidates.sort(key=lambda item: float(item.get("request_time", item.get("time_request", float("inf"))) or float("inf")))
        return candidates[: max(limit, 0)]

    def test_proxy(self, proxy: dict, *, test_url: str = "https://httpbin.org/ip", timeout: int = 10) -> float:
        proxy_mapping = self.requests_proxy_mapping(proxy)
        if not proxy_mapping:
            return -1

        try:
            start_time = time.time()
            response = requests.get(test_url, proxies=proxy_mapping, timeout=timeout)
            response.raise_for_status()
            end_time = time.time()
            return (end_time - start_time) * 1000
        except requests.RequestException:
            return -1
