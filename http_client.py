from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HttpClientError(RuntimeError):
    pass


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int | float = 30,
) -> dict[str, Any]:
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    request = Request(url, data=body, headers=request_headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HttpClientError(f"HTTP {exc.code} for {url}: {detail[:500]}") from exc
    except URLError as exc:
        raise HttpClientError(f"request failed for {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise HttpClientError(f"request timed out for {url}") from exc

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        raise HttpClientError(f"invalid JSON response from {url}") from exc
    if not isinstance(parsed, dict):
        raise HttpClientError(f"JSON response from {url} was not an object")
    return parsed
