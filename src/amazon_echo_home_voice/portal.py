"""A separate WSGI portal for authenticated household setup, never an Alexa relay route."""

import html
import hmac
from http.cookies import SimpleCookie, CookieError
import os
import secrets
from urllib.parse import parse_qs, urlsplit

from .gateway import GatewayConfig, GatewayError, fetch_energy
from .oauth import OAuthClient, OAuthConfig, OAuthError, InvalidToken
from .tenant_store import TenantStore, StoreError, CapacityError


SESSION_COOKIE = "__Host-energy-session"
LOGIN_COOKIE = "__Host-energy-login"
MAX_FORM_BYTES = 16384


class BadRequest(Exception):
    pass


def _response(start, status, content="", *, location=None, cookies=()):
    body = content.encode("utf-8")
    headers = [
        ("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"), ("Pragma", "no-cache"),
        ("Referrer-Policy", "no-referrer"), ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"), ("Strict-Transport-Security", "max-age=31536000"),
        ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
    ]
    if location:
        headers.append(("Location", location))
    headers.extend(("Set-Cookie", cookie) for cookie in cookies)
    start(status, headers)
    return [body]


def _cookie(name, value, age):
    return f"{name}={value}; Path=/; Max-Age={age}; Secure; HttpOnly; SameSite=Lax"


def _cookies(environ):
    value = environ.get("HTTP_COOKIE", "")
    if len(value) > 4096:
        raise BadRequest()
    cookie = SimpleCookie()
    try:
        cookie.load(value)
    except CookieError:
        raise BadRequest() from None
    return {name: morsel.value for name, morsel in cookie.items()}


def _params(value):
    try:
        if len(value) > MAX_FORM_BYTES:
            raise ValueError()
        pairs = parse_qs(value, keep_blank_values=True, strict_parsing=True, max_num_fields=12,
                         encoding="utf-8", errors="strict")
        if any(len(values) != 1 for values in pairs.values()):
            raise ValueError()
        return {key: values[0] for key, values in pairs.items()}
    except (ValueError, UnicodeError):
        raise BadRequest() from None


def _form(environ, origin):
    # Neither forwarded headers nor a user-supplied Host can authorize mutations.
    if environ.get("HTTP_ORIGIN") != origin:
        raise BadRequest()
    if environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        raise BadRequest()
    try:
        size = int(environ.get("CONTENT_LENGTH", ""))
        if not 0 < size <= MAX_FORM_BYTES:
            raise ValueError()
        raw = environ["wsgi.input"].read(size)
        if len(raw) != size:
            raise ValueError()
        return _params(raw.decode("utf-8"))
    except (KeyError, ValueError, UnicodeError):
        raise BadRequest() from None


def _page(content):
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Home Energy</title>
<style>body{font:17px/1.5 system-ui,sans-serif;max-width:42rem;margin:3rem auto;padding:0 1.2rem;color:#18352d;background:#f5f8f6}h1{font-size:2rem}label{display:block;margin-top:1rem}input{box-sizing:border-box;width:100%;font:inherit;padding:.6rem;border:1px solid #637d72;border-radius:.3rem}button,.button{display:inline-block;font:inherit;background:#176648;color:white;border:0;border-radius:.3rem;padding:.7rem 1rem;cursor:pointer;margin-top:1rem}section{padding:1.2rem;background:white;border-radius:.5rem;margin:1rem 0}.hint{color:#40584d;font-size:.9rem}a{color:#145d43}hr{border:0;border-top:1px solid #ccd8d0;margin:1.5rem 0}</style>
</head><body><main><h1>Home Energy</h1>""" + content + "</main></body></html>"


def _home(session, connected):
    csrf = html.escape(session["csrf"], quote=True)
    status = ("Your gateway is connected. Link this same Home Energy account in the Alexa app, "
              "then say: <strong>Alexa, ask home energy for battery status.</strong>"
              if connected else "Connect your gateway below, then link this same Home Energy account in the Alexa app.")
    disconnect = f'''<form method="post" action="/disconnect"><input type="hidden" name="csrf" value="{csrf}">
<button type="submit">Disconnect this home</button></form>
<p class="hint">Disconnecting deletes the saved gateway credentials and stops new energy requests for this home.</p>''' if connected else ""
    return _page(f'''<p>{status}</p><section><h2>{"Replace" if connected else "Connect"} your gateway</h2>
<p>Use a dedicated read-only IGW token. Your gateway needs a public HTTPS address.</p>
<form method="post" action="/connection" autocomplete="off">
<input type="hidden" name="csrf" value="{csrf}">
<label for="url">IGW energy URL</label><input id="url" name="url" type="url" placeholder="https://energy.example.com/v1/energy" maxlength="2048" required>
<label for="read-token">IGW read token</label><input id="read-token" name="read_token" type="password" maxlength="4096" autocomplete="new-password" required>
<p class="hint">If your gateway uses Cloudflare Access, enter both service credentials. Otherwise leave both fields empty.</p>
<label for="cf-id">Cloudflare Access client ID (optional)</label><input id="cf-id" name="cf_client_id" type="password" maxlength="4096" autocomplete="new-password">
<label for="cf-secret">Cloudflare Access client secret (optional)</label><input id="cf-secret" name="cf_client_secret" type="password" maxlength="4096" autocomplete="new-password">
<button type="submit">Verify and save connection</button></form>
<p class="hint">Saving checks the gateway once. Saved credentials are encrypted and never shown in this page or sent to Alexa. To replace a connection, enter all its credentials again.</p></section>
{disconnect}<hr><form method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf}"><button type="submit">Sign out of this portal</button></form>
<p class="hint">Portal sign-out does not unlink Alexa or end your identity-provider session. On a shared device, also sign out of the identity provider.</p>''')


def _authenticated(store, client, token):
    session = store.session(token)
    if session is None:
        return None
    try:
        identity = client.introspect_portal(session["access_token"])
        if identity.issuer != session["issuer"] or identity.subject != session["subject"]:
            raise InvalidToken("Invalid account session")
    except InvalidToken:
        store.delete_session(token)
        return None
    return session


def application(environ, start_response):
    """Only the portal host serves this application; signed Alexa requests use webhook.py."""
    if os.environ.get("ENERGY_VOICE_MODE") != "multi_household":
        return _response(start_response, "404 Not Found")
    try:
        config = OAuthConfig.from_env()
        callback = urlsplit(config.portal_redirect_uri)
        origin = f"{callback.scheme}://{callback.netloc}"
        if callback.path != "/callback" or environ.get("HTTP_HOST", "").lower() != callback.netloc.lower():
            return _response(start_response, "400 Bad Request")
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "")
        if path not in {"/", "/login", "/callback", "/connection", "/disconnect", "/logout"}:
            return _response(start_response, "404 Not Found")
        expected_method = "POST" if path in {"/connection", "/disconnect", "/logout"} else "GET"
        if method != expected_method:
            return _response(start_response, "405 Method Not Allowed")
        query = _params(environ.get("QUERY_STRING", ""))
        if path != "/callback" and query:
            raise BadRequest()
        store = TenantStore.from_env()
        client = OAuthClient(config)
        cookies = _cookies(environ)
        if path == "/login":
            browser = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(48)
            state = store.create_flow(verifier, browser)
            return _response(start_response, "303 See Other", location=client.authorization_url(state, verifier),
                             cookies=[_cookie(LOGIN_COOKIE, browser, 300)])
        if path == "/callback":
            if set(query) - {"state", "code", "session_state", "iss", "error", "error_description"}:
                raise BadRequest()
            verifier = store.consume_flow(query.get("state"), cookies.get(LOGIN_COOKIE))
            if not verifier or not query.get("code") or "error" in query or query.get("iss", config.issuer) != config.issuer:
                raise BadRequest()
            login = client.exchange_portal_code(query["code"], verifier)
            if cookies.get(SESSION_COOKIE):
                store.delete_session(cookies[SESSION_COOKIE])
            session_token = store.create_session(login.identity.issuer, login.identity.subject,
                                                 login.access_token, login.expires_at)
            return _response(start_response, "303 See Other", location="/", cookies=[
                _cookie(SESSION_COOKIE, session_token, 600), _cookie(LOGIN_COOKIE, "", 0)])
        token = cookies.get(SESSION_COOKIE, "")
        session = _authenticated(store, client, token)
        if session is None:
            return _response(start_response, "200 OK" if path == "/" else "401 Unauthorized",
                             _page('<p>Connect your home to Alexa.</p><p>Sign in to manage your home’s energy gateway.</p><a class="button" href="/login">Sign in</a>'),
                             cookies=[_cookie(SESSION_COOKIE, "", 0)])
        if path == "/":
            connected = store.connection(session["issuer"], session["subject"]) is not None
            return _response(start_response, "200 OK", _home(session, connected))
        form = _form(environ, origin)
        supplied_csrf = form.get("csrf", "")
        if not supplied_csrf.isascii() or not hmac.compare_digest(supplied_csrf, session["csrf"]):
            raise BadRequest()
        if path == "/connection":
            if set(form) - {"csrf", "url", "read_token", "cf_client_id", "cf_client_secret"}:
                raise BadRequest()
            gateway = GatewayConfig(url=form.get("url", ""), read_token=form.get("read_token", ""),
                                    cf_client_id=form.get("cf_client_id", ""), cf_client_secret=form.get("cf_client_secret", ""),
                                    timeout_seconds=3.0, public_only=True)
            fetch_energy(gateway)
            store.save_connection_for_session(token, gateway)
        else:
            if set(form) != {"csrf"}:
                raise BadRequest()
            if path == "/disconnect":
                store.disconnect(session["issuer"], session["subject"])
            elif path == "/logout":
                store.delete_session(token)
                return _response(start_response, "303 See Other", location="/", cookies=[_cookie(SESSION_COOKIE, "", 0)])
        return _response(start_response, "303 See Other", location="/")
    except BadRequest:
        return _response(start_response, "400 Bad Request", _page('<p>This request could not be verified. <a href="/">Return to Home Energy</a> and try again.</p>'))
    except GatewayError:
        return _response(start_response, "400 Bad Request", _page('<p>The gateway could not be verified. Check its public HTTPS URL, read token, and any Cloudflare credentials. Your saved connection has not changed.</p><a href="/">Try again</a>'))
    except CapacityError:
        return _response(start_response, "429 Too Many Requests", _page('<p>Please try signing in again later.</p>'))
    except (OAuthError, StoreError):
        return _response(start_response, "503 Service Unavailable", _page('<p>Account services are temporarily unavailable. Please try again later.</p>'))
