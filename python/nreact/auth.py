"""OpenAI browser/device OAuth and an independent nreact credential cache.

Protocol reference: openai/codex ef6c0582028ff8e1228e6031465c1c2d9d5d51b6.
Importing this module does not inspect credentials or initiate authentication.
"""

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass, field
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable

from ._credentials import AuthError, CredentialStore

ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # Public native-client identifier, not a secret.
PROVIDER = "openai-codex"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def default_auth_path() -> Path:
    configured = os.getenv("NREACT_AUTH_FILE")
    return Path(configured).expanduser().absolute() if configured else Path.home() / ".nreact" / "openai-auth.json"


def _post(path: str, data: dict, *, form: bool = False, timeout: float = 30) -> dict:
    # Callers supply fixed paths; neither the issuer nor token destination is configurable.
    body = urllib.parse.urlencode(data).encode("ascii") if form else json.dumps(data).encode("utf-8")
    request = urllib.request.Request(ISSUER + path, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded" if form else "application/json",
        "Accept": "application/json", "User-Agent": "nreact/0.3.0", "originator": "nreact",
    })
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(131_073)
        if len(raw) > 131_072:
            raise AuthError("OpenAI authentication response exceeded 128 KB.")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        return payload
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        # Never echo a response body, URL, authorization code or token.
        raise AuthError(f"OpenAI authentication returned HTTP {status}.", http_status=status) from None
    except (urllib.error.URLError, TimeoutError, OSError, HTTPException):
        raise AuthError("OpenAI authentication connection failed or timed out.") from None
    except (ValueError, TypeError, UnicodeError):
        raise AuthError("OpenAI authentication returned an invalid response.") from None


def _secret(value, name: str, *, limit: int = 32_768) -> str:
    if (not isinstance(value, str) or not 0 < len(value) <= limit
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise AuthError(f"OpenAI authentication returned an invalid {name}.")
    return value


def _claims(token: str) -> dict:
    """Decode local routing/expiry metadata; this does not verify a JWT signature.

    Token acceptance is performed by OpenAI over HTTPS. Claims are never used
    to authenticate a caller or grant local permissions.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3 or len(parts[1]) > 65_536:
            return {}
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        return claims if isinstance(claims, dict) else {}
    except (ValueError, TypeError, UnicodeError):
        return {}


@dataclass(frozen=True)
class _Tokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    account_id: str = field(repr=False)
    expires_at: float


def _number(value) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _tokens_from_response(data: dict, previous: _Tokens | None = None) -> _Tokens:
    access = _secret(data.get("access_token"), "access token")
    refresh = _secret(data.get("refresh_token", previous.refresh_token if previous else None), "refresh token")
    token_type = data.get("token_type", "Bearer")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise AuthError("OpenAI authentication returned an unsupported token type.")
    access_claims = _claims(access)
    id_claims = _claims(data["id_token"]) if isinstance(data.get("id_token"), str) else {}
    account = None
    for claims in (id_claims, access_claims):
        identity = claims.get("https://api.openai.com/auth")
        if isinstance(identity, dict):
            if identity.get("chatgpt_account_is_fedramp") is True:
                raise AuthError("This adapter does not support the FedRAMP ChatGPT endpoint.")
            account = account or identity.get("chatgpt_account_id")
    account = account or (previous.account_id if previous else None)
    if not isinstance(account, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", account):
        raise AuthError("OpenAI authentication returned no valid ChatGPT account identifier.")
    if previous and account != previous.account_id:
        raise AuthError("The ChatGPT account changed during token refresh. Sign in again.")
    expirations = []
    if _number(data.get("expires_in")) and 0 < data["expires_in"] <= 31_536_000:
        expirations.append(time.time() + data["expires_in"])
    if _number(access_claims.get("exp")) and 0 < access_claims["exp"] < 253_402_300_800:
        expirations.append(access_claims["exp"])
    if not expirations:
        raise AuthError("OpenAI authentication returned no usable token expiration.")
    return _Tokens(access, refresh, account, min(expirations))


class AuthStore:
    """Manage only nreact credentials. Status reads never refresh or contact OpenAI."""

    def __init__(self, path: str | Path | None = None):
        self._cache = CredentialStore(path if path is not None else default_auth_path())

    @property
    def path(self) -> Path:
        return self._cache.path

    def _load(self) -> _Tokens | None:
        data = self._cache.load()
        if data is None:
            return None
        if data.get("provider") != PROVIDER or data.get("client_id") != CLIENT_ID:
            raise AuthError("The selected file is not an nreact OpenAI credential cache.")
        try:
            tokens = data["tokens"]
            access = _secret(tokens["access_token"], "cached access token")
            refresh = _secret(tokens["refresh_token"], "cached refresh token")
            account, expires = tokens["account_id"], tokens["expires_at"]
            if (not isinstance(account, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", account)
                    or not _number(expires) or not 0 < expires < 253_402_300_800):
                raise ValueError
            return _Tokens(access, refresh, account, expires)
        except (KeyError, TypeError, ValueError):
            raise AuthError("Invalid nreact OpenAI credential cache. Clear it and sign in again.") from None

    def _save(self, tokens: _Tokens) -> None:
        self._cache.save({"provider": PROVIDER, "client_id": CLIENT_ID, "tokens": asdict(tokens)})

    def status(self) -> dict:
        tokens = self._load()
        state = "signed_out" if tokens is None else "refresh_required" if tokens.expires_at <= time.time() + 60 else "cached"
        return {"provider": PROVIDER, "state": state, "path": str(self.path),
                "expires_at": tokens.expires_at if tokens else None, "network_verified": False}

    def save_login(self, response: dict) -> None:
        tokens = _tokens_from_response(response)
        with self._cache.locked():
            # Refuse to overwrite an unrelated file at a custom auth path.
            existing = self._cache.load()
            if existing is not None and (existing.get("provider") != PROVIDER or existing.get("client_id") != CLIENT_ID):
                raise AuthError("The selected file is not an nreact OpenAI credential cache.")
            self._save(tokens)

    def tokens(self, *, rejected_access_token: str | None = None, expected_account_id: str | None = None) -> _Tokens:
        with self._cache.locked():
            current = self._load()
            if current is None:
                raise AuthError("Sign in first with nreact auth login.")
            if expected_account_id is not None and current.account_id != expected_account_id:
                raise AuthError("The ChatGPT account changed during this request. Start a new run.")
            rejected = rejected_access_token is not None and hmac.compare_digest(current.access_token, rejected_access_token)
            if current.expires_at > time.time() + 60 and not rejected:
                return current
            try:
                response = _post("/oauth/token", {"grant_type": "refresh_token", "client_id": CLIENT_ID,
                                                   "refresh_token": current.refresh_token})
            except AuthError as exc:
                if exc.http_status in {400, 401}:
                    raise AuthError("ChatGPT credentials could not be refreshed. Run nreact auth login again.") from None
                raise
            refreshed = _tokens_from_response(response, current)
            self._save(refreshed)
            return refreshed

    def logout(self) -> bool:
        if not self.path.parent.exists():
            return False
        with self._cache.locked():
            existing = self._cache.load()
            if existing is None:
                return False
            if existing.get("provider") != PROVIDER or existing.get("client_id") != CLIENT_ID:
                raise AuthError("The selected file is not an nreact OpenAI credential cache.")
            return self._cache.clear()


def _exchange(code: str, verifier: str, redirect_uri: str) -> dict:
    return _post("/oauth/token", {"grant_type": "authorization_code", "client_id": CLIENT_ID,
                                 "code": code, "code_verifier": verifier, "redirect_uri": redirect_uri}, form=True)


def _browser_login(announce: Callable, *, open_browser: bool, timeout: float) -> dict:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    callback: dict[str, str] = {}

    class CallbackServer(HTTPServer):
        allow_reuse_address = False

        def server_bind(self):
            if os.name == "nt":
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

        def handle_error(self, request, client_address):
            pass  # Incomplete local requests must not print callback details.

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass  # Callback URLs contain one-time authorization codes.

        def _reply(self, status: int, message: str) -> None:
            body = message.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.headers.get("Host") not in {"localhost:1455", "127.0.0.1:1455"}:
                self._reply(400, "Invalid callback host.")
                return
            if len(self.path) > 16_384:
                self._reply(400, "Invalid callback.")
                return
            try:
                url = urllib.parse.urlsplit(self.path)
                if url.path != "/auth/callback" or url.scheme or url.netloc or url.fragment:
                    self._reply(404, "Not found.")
                    return
                query = urllib.parse.parse_qs(url.query, keep_blank_values=True, max_num_fields=16)
                supplied = query.get("state", [])
                if len(supplied) != 1 or not hmac.compare_digest(supplied[0].encode(), state.encode()):
                    self._reply(400, "Invalid login state. Return to the original login page.")
                    return
                if "error" in query:
                    callback["error"] = "OpenAI login was cancelled or denied."
                    self._reply(400, "Authorization was not completed. Return to the terminal.")
                    return
                codes = query.get("code", [])
                if len(codes) != 1:
                    raise ValueError
                callback["code"] = _secret(codes[0], "authorization code", limit=8192)
            except (ValueError, UnicodeError, AuthError):
                self._reply(400, "Invalid callback parameters.")
                return
            self._reply(200, "Authorization received. Return to the nreact terminal to finish signing in.")

    try:
        server = CallbackServer(("127.0.0.1", 1455), Handler)
    except OSError:
        raise AuthError("Could not bind the login callback on port 1455. Close another login or use --device.") from None
    with server:
        server.timeout = 0.25
        redirect = "http://localhost:1455/auth/callback"
        authorize = ISSUER + "/oauth/authorize?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": redirect,
            "scope": "openid profile email offline_access", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": state, "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true", "originator": "nreact",
        })
        deadline = time.monotonic() + timeout
        announce(authorize, None)
        if open_browser:
            try:
                webbrowser.open(authorize)
            except (webbrowser.Error, OSError):
                pass  # The printed URL remains available for manual opening.
        while not callback:
            if time.monotonic() >= deadline:
                raise AuthError("OpenAI login timed out. Start a new login when ready.")
            server.handle_request()
        if "error" in callback:
            raise AuthError(callback["error"])
    return _exchange(callback["code"], verifier, redirect)


def _device_login(announce: Callable, *, timeout: float) -> dict:
    response = _post("/api/accounts/deviceauth/usercode", {"client_id": CLIENT_ID})
    identity = _secret(response.get("device_auth_id"), "device authorization identifier", limit=4096)
    code = _secret(response.get("user_code", response.get("usercode")), "device code", limit=256)
    try:
        interval = float(response.get("interval", 5))
        if not math.isfinite(interval) or not 0 <= interval <= 60:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise AuthError("OpenAI authentication returned an invalid polling interval.") from None
    interval = max(1, interval)
    announce(ISSUER + "/codex/device", code)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            result = _post("/api/accounts/deviceauth/token", {"device_auth_id": identity, "user_code": code},
                           timeout=min(30, remaining))
        except AuthError as exc:
            if exc.http_status in {403, 404}:
                continue
            raise
        authorization = _secret(result.get("authorization_code"), "authorization code", limit=8192)
        verifier = _secret(result.get("code_verifier"), "PKCE verifier", limit=128)
        return _exchange(authorization, verifier, ISSUER + "/deviceauth/callback")
    raise AuthError("OpenAI device login timed out. Request a new device code when ready.")


def login(*, auth_file: str | Path | None = None, device: bool = False, open_browser: bool = True,
          timeout: float = 900, on_authorize: Callable[[str, str | None], None] | None = None) -> dict:
    """Start an explicit login and save the returned tokens in nreact's own cache.

    Device login prints a verification URL/code and never opens a browser.
    Ctrl+C while waiting closes the callback listener. Credentials are replaced
    only after a successful authorization-code exchange.
    """
    if not _number(timeout) or not 30 <= timeout <= 900:
        raise ValueError("Login timeout must be between 30 and 900 seconds.")
    if type(device) is not bool or type(open_browser) is not bool:
        raise ValueError("device and open_browser must be booleans.")

    def announce(url: str, code: str | None) -> None:
        if on_authorize:
            on_authorize(url, code)
        else:
            print(f"Open this URL to sign in:\n{url}", flush=True)
            if code:
                print(f"One-time device code: {code}", flush=True)

    store = AuthStore(auth_file)
    response = _device_login(announce, timeout=timeout) if device else _browser_login(
        announce, open_browser=open_browser, timeout=timeout)
    store.save_login(response)
    return store.status()
