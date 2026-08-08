"""Security boundary for the loopback-only web application."""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeGuard
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_REQUEST_BODY = 64 * 1024
CSRF_COOKIE = "openlearn_csrf"
SESSION_COOKIE = "openlearn_session"
CAPABILITY_QUERY = "access_token"
NAMESPACE_PREFIX = "/_openlearn/"
MIN_NAMESPACE_TOKEN_LENGTH = 32
MAX_NAMESPACE_TOKEN_LENGTH = 128
SAFE_METHODS = frozenset({"GET", "HEAD"})
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


def new_csrf_token() -> str:
    """Return an unguessable token scoped to one server launch."""

    return secrets.token_urlsafe(32)


def is_valid_url_namespace(value: object) -> TypeGuard[str]:
    if not isinstance(value, str) or not value.startswith(NAMESPACE_PREFIX):
        return False
    token = value[len(NAMESPACE_PREFIX) :]
    return MIN_NAMESPACE_TOKEN_LENGTH <= len(token) <= MAX_NAMESPACE_TOKEN_LENGTH and all(
        character in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in token
    )


def _split_host(value: str) -> str:
    if value.startswith("["):
        return value.partition("]")[0] + "]"
    return value.partition(":")[0]


@dataclass(frozen=True)
class BrowserSecurity:
    csrf_token: str
    access_token: str
    url_namespace: str
    allowed_hosts: frozenset[str]
    trust_test_client: bool = False

    @classmethod
    def local(
        cls,
        *,
        allow_test_client: bool = False,
        trust_test_client: bool = False,
        access_token: str | None = None,
        url_namespace: str | None = None,
    ) -> BrowserSecurity:
        hosts = {"localhost", "127.0.0.1", "[::1]"}
        if allow_test_client:
            hosts.add("testserver")
        namespace = url_namespace or f"{NAMESPACE_PREFIX}{secrets.token_urlsafe(24)}"
        if not is_valid_url_namespace(namespace):
            raise ValueError("invalid local URL namespace")
        return cls(
            new_csrf_token(),
            access_token or secrets.token_urlsafe(32),
            namespace,
            frozenset(hosts),
            trust_test_client,
        )


class LocalSecurityMiddleware:
    """Reject non-loopback browser access and attach defensive headers."""

    def __init__(self, app: ASGIApp, security: BrowserSecurity) -> None:
        self.app = app
        self.security = security

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authority = headers.get("host", "").lower()
        host = _split_host(authority)
        if host not in self.security.allowed_hosts:
            await self._reject(scope, receive, send, 400, "invalid_host")
            return

        session_token = self._cookie_value(headers.get("cookie", ""), SESSION_COOKIE)
        capability_token = headers.get("x-openlearn-capability")
        request_path = scope.get("path", "/") or "/"
        namespaced = request_path == self.security.url_namespace or request_path.startswith(
            f"{self.security.url_namespace}/"
        )
        trusted_test_request = self.security.trust_test_client and host == "testserver"
        session_authenticated = (
            namespaced
            and session_token is not None
            and secrets.compare_digest(session_token, self.security.access_token)
        )
        capability_authenticated = capability_token is not None and secrets.compare_digest(
            capability_token, self.security.access_token
        )
        authenticated = trusted_test_request or session_authenticated or capability_authenticated
        if not authenticated:
            query = scope.get("query_string", b"").decode("ascii", errors="ignore")
            query_token = self._query_token(query)
            if (
                scope["method"].upper() == "GET"
                and request_path == "/"
                and query_token is not None
                and secrets.compare_digest(query_token, self.security.access_token)
            ):
                response = RedirectResponse(f"{self.security.url_namespace}/", status_code=303)
                response.set_cookie(
                    SESSION_COOKIE,
                    self.security.access_token,
                    httponly=True,
                    samesite="strict",
                    path=self.security.url_namespace,
                )
                response.set_cookie(
                    CSRF_COOKIE,
                    self.security.csrf_token,
                    httponly=True,
                    samesite="strict",
                    path=self.security.url_namespace,
                )
                for name, value in SECURITY_HEADERS.items():
                    response.headers[name] = value
                await response(scope, receive, send)
                return
            await self._reject(scope, receive, send, 401, "local_access_required")
            return

        if not namespaced and not trusted_test_request:
            if not (capability_authenticated and request_path == "/health"):
                await self._reject(scope, receive, send, 401, "local_access_required")
                return
        elif namespaced:
            scope = self._route_scope(scope)

        method = scope["method"].upper()
        if method == "OPTIONS":
            await self._reject(scope, receive, send, 403, "cross_origin_not_allowed")
            return

        content_length = headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BODY:
                    await self._reject(scope, receive, send, 413, "request_too_large")
                    return
            except ValueError:
                await self._reject(scope, receive, send, 400, "invalid_content_length")
                return

        if method not in SAFE_METHODS:
            origin = headers.get("origin")
            if origin and not self._origin_matches(origin, authority, scope.get("scheme", "http")):
                await self._reject(scope, receive, send, 403, "invalid_origin")
                return
            cookie_token = scope.get("state", {}).get("csrf_token")
            if cookie_token is None:
                cookie_token = self._cookie_value(headers.get("cookie", ""), CSRF_COOKIE)
            header_token = headers.get("x-csrf-token")
            supplied = header_token or cookie_token
            if not supplied or not secrets.compare_digest(supplied, self.security.csrf_token):
                await self._reject(scope, receive, send, 403, "invalid_csrf_token")
                return

            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > MAX_REQUEST_BODY:
                    await self._reject(scope, receive, send, 413, "request_too_large")
                    return
                if not message.get("more_body", False):
                    break
            replayed = False

            async def replay_body() -> Message:
                nonlocal replayed
                if replayed:
                    return {"type": "http.request", "body": b"", "more_body": False}
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            receive = replay_body

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    response_headers[name] = value
                response_headers.append(
                    "Set-Cookie",
                    (
                        f"{CSRF_COOKIE}={self.security.csrf_token}; "
                        f"Path={self.security.url_namespace}; HttpOnly; SameSite=Strict"
                    ),
                )
            await send(message)

        await self.app(scope, receive, secure_send)

    def _route_scope(self, scope: Scope) -> Scope:
        routed = dict(scope)
        namespace = self.security.url_namespace
        path = str(scope.get("path", "/"))
        routed["path"] = path[len(namespace) :] or "/"
        raw_path = scope.get("raw_path")
        if isinstance(raw_path, bytes):
            raw_namespace = namespace.encode("ascii")
            routed["raw_path"] = raw_path[len(raw_namespace) :] or b"/"
        existing_root = str(scope.get("root_path", "")).rstrip("/")
        routed["root_path"] = f"{existing_root}{namespace}"
        return routed  # type: ignore[return-value]

    @staticmethod
    def _cookie_value(cookie_header: str, cookie_name: str) -> str | None:
        for item in cookie_header.split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == cookie_name:
                return value
        return None

    @staticmethod
    def _query_token(query: str) -> str | None:
        for item in query.split("&"):
            name, separator, value = item.partition("=")
            if separator and name == CAPABILITY_QUERY:
                return value
        return None

    @staticmethod
    def _origin_matches(origin: str, authority: str, scheme: str) -> bool:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        return parsed.scheme == scheme and parsed.netloc.lower() == authority

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        status: int,
        code: str,
    ) -> None:
        response = JSONResponse({"error": code}, status_code=status, headers=SECURITY_HEADERS)
        await response(scope, receive, send)


def local_hosts(extra: Iterable[str] = ()) -> frozenset[str]:
    return frozenset({"localhost", "127.0.0.1", "[::1]", *extra})
