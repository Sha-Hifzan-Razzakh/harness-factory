"""A small client for the API. The UI never imports the factory: whatever it shows or changes goes through here."""

from __future__ import annotations

import os
from typing import Any

import httpx


class ApiError(Exception):
    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


class Api:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout_s: float = 300.0) -> None:
        self.base_url = (base_url or os.environ.get("HF_API_URL") or "http://127.0.0.1:8765").rstrip("/")
        token = token if token is not None else os.environ.get("HF_API_TOKEN")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout_s)

    def send(self, method: str, path: str, json: Any = None, **params: Any) -> tuple[int, Any]:
        """Any status: the caller decides (a change answers 409 or 422 with a body worth showing)."""
        r = self.http.request(method, path, json=json, params={k: v for k, v in params.items() if v is not None})
        try:
            body = r.json()
        except ValueError:
            body = r.text
        return r.status_code, body

    def get(self, path: str, **params: Any) -> Any:
        status, body = self.send("GET", path, **params)
        if status >= 400:
            raise ApiError(status, body.get("detail", body) if isinstance(body, dict) else body)
        return body

    def health(self) -> str | None:
        """None when the API answers; otherwise why not."""
        try:
            status, _ = self.send("GET", "/api/health")
            if status != 200:
                return f"health check answered {status}"
            status, body = self.send("GET", "/api/meta")
            if status == 401:
                return "the API needs a token: set HF_API_TOKEN for the UI"
            return None if status == 200 else f"/api/meta answered {status}: {body}"
        except httpx.HTTPError as e:
            return f"{type(e).__name__}: {e}"
