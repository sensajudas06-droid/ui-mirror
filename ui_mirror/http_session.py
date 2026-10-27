"""HTTP sessions that keep user credentials at the explicitly selected origin."""

from __future__ import annotations

from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit

import requests
from requests.auth import AuthBase
from requests.structures import CaseInsensitiveDict
from requests.utils import check_header_validity, get_auth_from_url


def _origin(url: str) -> tuple[str, str, int] | None:
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.encode("idna").decode("ascii").lower()
    port = parts.port
    return parts.scheme.lower(), host, port if port is not None else (443 if parts.scheme.lower() == "https" else 80)


def _parse_cookies(value: str | None) -> dict[str, str]:
    if not value or not value.strip():
        return {}
    check_header_validity(("Cookie", value))
    parsed: SimpleCookie = SimpleCookie()
    try:
        parsed.load(value)
    except CookieError as exc:
        raise ValueError("Geçersiz oturum çerezi") from exc
    if not parsed:
        raise ValueError("Geçersiz oturum çerezi")
    return {name: morsel.value for name, morsel in parsed.items()}


class _NoAmbientAuth(AuthBase):
    def __call__(self, request: requests.PreparedRequest) -> requests.PreparedRequest:
        return request


_NO_AMBIENT_AUTH = _NoAmbientAuth()


class OriginScopedSession(requests.Session):
    """Send supplied headers/cookies only to the start URL's exact origin.

    The check runs before every send, including automatic redirects. Normal
    same-origin server cookies remain available. Implicit .netrc authentication
    is disabled; environment proxy and CA configuration still work.
    """

    def __init__(
        self,
        origin_url: str | None,
        *,
        extra_headers: dict[str, str] | None = None,
        cookies: str | None = None,
    ) -> None:
        super().__init__()
        self._credential_origin = _origin(origin_url) if origin_url else None
        self._origin_headers = CaseInsensitiveDict(extra_headers or {})
        for name, value in self._origin_headers.items():
            check_header_validity((name, value))
        self._origin_cookies = _parse_cookies(cookies)
        if (self._origin_headers or self._origin_cookies) and self._credential_origin is None:
            raise ValueError("Başlık ve çerez kullanımı için geçerli hedef URL gerekli")

    def prepare_request(self, request: requests.Request) -> requests.PreparedRequest:
        # A truthy no-op auth prevents Requests from reading .netrc. Preserve
        # credentials explicitly present in a URL or supplied as request auth.
        original_auth = request.auth
        if request.auth is None and self.auth is None:
            url_auth = get_auth_from_url(request.url or "")
            request.auth = url_auth if any(url_auth) else _NO_AMBIENT_AUTH
        try:
            prepared = super().prepare_request(request)
        finally:
            request.auth = original_auth
        self._scope_credentials(prepared)
        return prepared

    def rebuild_auth(self, prepared_request: requests.PreparedRequest, response: requests.Response) -> None:
        # Requests' default implementation reapplies .netrc after redirects.
        if "Authorization" in prepared_request.headers and self.should_strip_auth(
            response.request.url, prepared_request.url
        ):
            prepared_request.headers.pop("Authorization", None)

    def send(self, request: requests.PreparedRequest, **kwargs) -> requests.Response:
        self._scope_credentials(request)
        return super().send(request, **kwargs)

    def _scope_credentials(self, request: requests.PreparedRequest) -> None:
        trusted = self._credential_origin is not None and _origin(request.url or "") == self._credential_origin
        if not trusted:
            for name in self._origin_headers:
                if name in self.headers:
                    request.headers[name] = self.headers[name]
                else:
                    request.headers.pop(name, None)
            request.headers.pop("Authorization", None)
            request.headers.pop("Cookie", None)
            return

        request.headers.update(self._origin_headers)
        if self._origin_cookies and "Cookie" not in self._origin_headers:
            merged: SimpleCookie = SimpleCookie()
            try:
                merged.load(request.headers.get("Cookie", ""))
                for name, value in self._origin_cookies.items():
                    merged[name] = value
            except CookieError as exc:
                raise ValueError("Geçersiz oturum çerezi") from exc
            request.headers["Cookie"] = "; ".join(morsel.OutputString() for morsel in merged.values())
