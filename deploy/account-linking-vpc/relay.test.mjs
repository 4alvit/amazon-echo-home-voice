import test from "node:test";
import assert from "node:assert/strict";
import relay from "./relay.mjs";

const AUTH = "energy-auth.example.workers.dev";
const PORTAL = "energy-connect.example.workers.dev";
const REALM = "/realms/home-energy";
const OIDC = `${REALM}/protocol/openid-connect`;
const FORM = "application/x-www-form-urlencoded";

function environment(role = "portal", callback = () => new Response("Synthetic page", {
  headers: { "Content-Type": "text/html; charset=utf-8" },
})) {
  const calls = [];
  return { ROLE: role, PUBLIC_HOST: role === "auth" ? AUTH : PORTAL, calls,
    BACKEND: { fetch: async (...args) => { calls.push(args); return callback(...args); } } };
}

function request(path = "/", { role = "portal", method = "GET", headers = {}, body } = {}) {
  return new Request(`https://${role === "auth" ? AUTH : PORTAL}${path}`, {
    method, headers, body, ...(body instanceof ReadableStream ? { duplex: "half" } : {}),
  });
}

async function send(path = "/", options = {}, callback) {
  const env = environment(options.role, callback);
  const response = await relay.fetch(request(path, options), env);
  return { env, response };
}

test("portal forwards exactly its fixed VPC target and no unrelated headers", async () => {
  const { env, response } = await send("/", { headers: {
    Cookie: "__Host-energy-session=synthetic", Origin: `https://${PORTAL}`,
    Accept: "text/html", "Accept-Language": "en-US", "CF-Connecting-IP": "203.0.113.9",
    Forwarded: "host=attacker.example;proto=http", "X-Forwarded-Host": "attacker.example",
    "X-Forwarded-Proto": "http", "X-Forwarded-Port": "81", "X-Forwarded-For": "127.0.0.1",
    "X-Real-IP": "127.0.0.1", "X-Original-URL": "/admin", "CF-Access-Client-Secret": "secret",
    "CF-Access-Jwt-Assertion": "assertion", Authorization: "Bearer unrelated", traceparent: "private-trace",
  } });
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "Synthetic page");
  assert.equal(env.calls.length, 1);
  const [url, options] = env.calls[0];
  assert.equal(url, `http://${PORTAL}/`);
  assert.equal(options.redirect, "manual");
  assert.equal(options.headers.get("X-Forwarded-Host"), PORTAL);
  assert.equal(options.headers.get("X-Forwarded-Proto"), "https");
  assert.equal(options.headers.get("X-Forwarded-Port"), "443");
  assert.equal(options.headers.get("X-Forwarded-For"), "203.0.113.9");
  assert.equal(options.headers.get("Cookie"), "__Host-energy-session=synthetic");
  assert.equal(options.headers.get("Origin"), `https://${PORTAL}`);
  assert.equal(options.headers.get("Accept-Language"), "en-US");
  for (const name of ["Forwarded", "X-Real-IP", "X-Original-URL", "CF-Access-Client-Secret", "CF-Access-Jwt-Assertion", "Authorization", "traceparent"])
    assert.equal(options.headers.has(name), false, name);
});

test("token and introspection preserve Basic client authentication and opaque form bytes", async () => {
  for (const path of [`${OIDC}/token`, `${OIDC}/token/introspect`, `${OIDC}/revoke`]) {
    const body = "token=opaque%2Btoken&client_id=synthetic&scope=energy%3Aread";
    const { env, response } = await send(path, { role: "auth", method: "POST", body,
      headers: { "Content-Type": FORM, Authorization: "Basic c3ludGhldGljOnNlY3JldA==" } });
    await response.text();
    assert.equal(response.status, 200);
    assert.equal(env.calls[0][1].headers.get("Authorization"), "Basic c3ludGhldGljOnNlY3JldA==");
    assert.equal(new TextDecoder().decode(env.calls[0][1].body), body);
    assert.ok(env.calls[0][1].body instanceof ArrayBuffer);
  }
});

test("only Basic authorization is accepted at the confidential OAuth endpoints", async () => {
  const { env, response } = await send(`${OIDC}/token`, { role: "auth", method: "POST", body: "code=synthetic",
    headers: { "Content-Type": FORM, Authorization: "Bearer private-token" } });
  assert.equal(response.status, 400);
  assert.equal(env.calls.length, 0);
});

test("OAuth query encoding and browser redirect are preserved without following", async () => {
  const query = "?state=opaque%2Bstate&redirect_uri=https%3A%2F%2Fexample.amazon.com%2Fcallback&scope=openid+energy%3Aread";
  const destination = "https://example.amazon.com/callback?code=synthetic%2Bcode&state=opaque%2Bstate";
  const { env, response } = await send(`${OIDC}/auth${query}`, { role: "auth" }, () => new Response(null, {
    status: 302, headers: { Location: destination },
  }));
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("Location"), destination);
  assert.equal(env.calls[0][0], `http://${AUTH}${OIDC}/auth${query}`);
  assert.equal(env.calls.length, 1);
  await response.text();
});

test("separate cookies including Expires commas and security headers survive", async () => {
  const headers = new Headers({ "Content-Type": "text/html", Location: "/", "Content-Length": "0",
    "Content-Security-Policy": "default-src 'none'", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
    "Cache-Control": "public, max-age=1000", "Server": "private-server", "X-Private-Trace": "hidden" });
  const first = "__Host-energy-session=synthetic; Path=/; Secure; HttpOnly; SameSite=Lax";
  const second = "__Host-energy-login=; Expires=Wed, 21 Oct 2015 07:28:00 GMT; Path=/; Secure; HttpOnly";
  headers.append("Set-Cookie", first);
  headers.append("Set-Cookie", second);
  const { response } = await send("/callback?state=synthetic&code=synthetic", {}, () => new Response(null, { status: 303, headers }));
  assert.equal(response.status, 303);
  assert.deepEqual(response.headers.getSetCookie(), [first, second]);
  assert.equal(response.headers.get("Location"), "/");
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.equal(response.headers.get("Content-Security-Policy"), "default-src 'none'");
  assert.equal(response.headers.get("X-Frame-Options"), "DENY");
  for (const name of ["Content-Length", "Server", "X-Private-Trace"]) assert.equal(response.headers.has(name), false);
});

for (const path of ["/", "/admin", "/admin/", "/realms/master/", "/realms/other/protocol/openid-connect/auth",
  `${REALM}/account`, `${REALM}/clients-registrations/default`, "/health", "/metrics", "/lb-check", "/alexa",
  "/realms/home-energy%2f..%2fmaster/", "/realms/home-energy/%252e%252e/master/",
  "/resources//private", "/resources/%2e%2e/admin", "/resources/style.css;ignored", "/resources/é.css",
  `${REALM}/login-actions/`, `${OIDC}/auth/extra`]) {
  test(`auth rejects unconfigured or ambiguous path ${path}`, async () => {
    const { env, response } = await send(path, { role: "auth" });
    assert.equal(response.status, 404);
    assert.equal(env.calls.length, 0);
  });
}

for (const path of ["/admin", "/health", "/alexa", "/login/extra", "/?unwanted=true", "/login?redirect=elsewhere", "/callback%2fextra"]) {
  test(`portal rejects unsupported path ${path}`, async () => {
    const { env, response } = await send(path);
    assert.equal(response.status, 404);
    assert.equal(env.calls.length, 0);
  });
}

test("portal route methods and query restrictions are exact", async () => {
  for (const [path, method] of [["/", "POST"], ["/login", "HEAD"], ["/connection", "GET"],
    ["/disconnect", "GET"], ["/logout", "GET"], ["/callback", "DELETE"]]) {
    const { response, env } = await send(path, { method });
    assert.equal(response.status, 404);
    assert.equal(env.calls.length, 0);
  }
  for (const path of ["/connection", "/disconnect", "/logout"]) {
    const { response } = await send(path, { method: "POST", body: "csrf=synthetic", headers: { "Content-Type": FORM } });
    assert.equal(response.status, 200);
    await response.text();
  }
});

test("Keycloak required action, login assets, discovery and logout have supported methods", async () => {
  for (const path of [`${REALM}/login-actions/required-action?execution=UPDATE_PASSWORD`,
    `${REALM}/login-actions/authenticate?session_code=synthetic`, "/resources/synthetic/login/keycloak.v2/css/styles.css",
    `${REALM}/.well-known/openid-configuration`, `${OIDC}/certs`, `${OIDC}/logout`]) {
    const { response } = await send(path, { role: "auth" });
    assert.equal(response.status, 200, path);
    await response.text();
  }
});

test("only the exact HTTPS deployment host can invoke a binding", async () => {
  for (const url of [`http://${PORTAL}/`, "https://other.example.workers.dev/", `https://${PORTAL}:8443/`, `https://${PORTAL}/#fragment`]) {
    const env = environment();
    const response = await relay.fetch(new Request(url), env);
    assert.equal(response.status, 404);
    assert.equal(env.calls.length, 0);
  }
});

test("bad deployment configuration fails closed", async () => {
  for (const config of [{ PUBLIC_HOST: "localhost" }, { PUBLIC_HOST: "portal.example.com" }, { ROLE: "other" }]) {
    const env = Object.assign(environment(), config);
    assert.equal((await relay.fetch(request(), env)).status, 502);
    assert.equal(env.calls.length, 0);
  }
});

test("only a valid edge address is forwarded, including IPv6", async () => {
  for (const [ip, expected] of [["2001:db8::1", "2001:db8::1"], ["192.0.2.6", "192.0.2.6"],
    ["127.0.0.1, 192.0.2.1", null], ["999.1.1.1", null], ["01.2.3.4", null], ["bad:ip", null], [":::1", null]]) {
    const { env, response } = await send("/", { headers: { "CF-Connecting-IP": ip, "X-Forwarded-For": "127.0.0.1" } });
    await response.text();
    assert.equal(env.calls[0][1].headers.get("X-Forwarded-For"), expected);
  }
});

test("large and forbidden request headers do not reach the backend", async () => {
  for (const headers of [{ Cookie: "a".repeat(4097) }, { Origin: "a".repeat(513) }, { Accept: "a".repeat(1025) }]) {
    const { env, response } = await send("/", { headers });
    assert.equal(response.status, 400);
    assert.equal(env.calls.length, 0);
  }
});

test("request limits reject advertised, streamed and malformed lengths before forwarding", async () => {
  for (const [body, length, status] of [["a=1", "16385", 413], ["a".repeat(16385), null, 413],
    ["a=1", "99", 400], ["a=1", "-1", 400], ["a=1", "03", 400], ["a=1", "1.0", 400], ["", "0", 400]]) {
    const headers = { "Content-Type": FORM, ...(length === null ? {} : { "Content-Length": length }) };
    const { env, response } = await send("/connection", { method: "POST", headers, body });
    assert.equal(response.status, status);
    assert.equal(env.calls.length, 0);
  }
  const { response, env } = await send(`${OIDC}/token`, { role: "auth", method: "POST", body: "a".repeat(32769), headers: { "Content-Type": FORM } });
  assert.equal(response.status, 413);
  assert.equal(env.calls.length, 0);
});

test("request content types, compression and GET bodies are rejected", async () => {
  for (const headers of [{ "Content-Type": "application/json" }, { "Content-Type": FORM, "Content-Encoding": "gzip" }]) {
    const { env, response } = await send("/connection", { method: "POST", body: "csrf=synthetic", headers });
    assert.equal(response.status, 400);
    assert.equal(env.calls.length, 0);
  }
  const { env, response } = await send("/", { headers: { "Content-Length": "5" } });
  assert.equal(response.status, 400);
  assert.equal(env.calls.length, 0);
});

test("oversized advertised responses and encoded responses fail before exposing their bodies", async () => {
  for (const headers of [{ "Content-Length": "262145" }, { "Content-Length": "-1" }, { "Content-Encoding": "gzip" }]) {
    const { response } = await send("/", {}, () => new Response("private upstream diagnostic", { headers }));
    assert.equal(response.status, 502);
    assert.doesNotMatch(await response.text(), /private upstream/);
  }
});

test("streamed dynamic and asset responses stop at their respective limits", async () => {
  for (const [path, options, limit] of [["/", {}, 262144], ["/resources/synthetic/font.woff2", { role: "auth" }, 2097152]]) {
    let canceled = false;
    const { response } = await send(path, options, () => new Response(new ReadableStream({
      pull(controller) { controller.enqueue(new Uint8Array(limit + 1)); },
      cancel() { canceled = true; },
    })));
    await assert.rejects(response.arrayBuffer(), /Service response unavailable/);
    assert.equal(canceled, true);
  }
});

test("bounded assets stream successfully without retaining content length", async () => {
  const bytes = new Uint8Array(300000).fill(42);
  const { response } = await send("/resources/synthetic/font.woff2", { role: "auth" }, () => new Response(bytes, {
    headers: { "Content-Type": "font/woff2", "Content-Length": String(bytes.length) },
  }));
  assert.equal(response.status, 200);
  assert.equal(response.headers.has("Content-Length"), false);
  assert.equal((await response.arrayBuffer()).byteLength, bytes.length);
});

test("HEAD does not return an upstream body", async () => {
  const { response } = await send("/resources/synthetic/file.css", { role: "auth", method: "HEAD" });
  assert.equal(await response.text(), "");
});

test("unsafe upstream redirects are rejected without following or exposing details", async () => {
  for (const location of ["http://insecure.example/callback", "https://user:password@example.com/callback", "https://example.com:8443/callback", "javascript:alert(1)"]) {
    const { env, response } = await send("/login", {}, () => new Response(null, { status: 303, headers: { Location: location } }));
    assert.equal(response.status, 502);
    assert.equal(env.calls.length, 1);
    assert.equal(response.headers.has("Location"), false);
  }
});

test("upstream server errors hide diagnostics and cancel their bodies", async () => {
  for (const status of [500, 502, 503, 599]) {
    let canceled = false;
    const { response } = await send("/", {}, () => new Response(new ReadableStream({
      start(controller) { controller.enqueue(new TextEncoder().encode("synthetic-backend-private-detail")); },
      cancel() { canceled = true; },
    }), { status, headers: {
      "Content-Type": "text/plain", "Set-Cookie": "session=synthetic-upstream-value",
      "Retry-After": "60", Location: "https://diagnostic.example/error",
    } }));
    assert.equal(response.status, 502);
    assert.equal(await response.text(), "Request unavailable");
    assert.equal(canceled, true);
    for (const name of ["Set-Cookie", "Retry-After", "Location"]) assert.equal(response.headers.has(name), false);
  }
});

test("OAuth client errors preserve their protocol status and body", async () => {
  for (const status of [400, 401, 403, 429]) {
    const body = JSON.stringify({ error: "invalid_grant", error_description: "Synthetic rejected grant" });
    const { response } = await send(`${OIDC}/token`, {
      role: "auth", method: "POST", body: "code=synthetic", headers: { "Content-Type": FORM },
    }, () => new Response(body, { status, headers: { "Content-Type": "application/json" } }));
    assert.equal(response.status, status);
    assert.equal(await response.text(), body);
    assert.equal(response.headers.get("Content-Type"), "application/json");
  }
});

test("a backend error is generic and never falls back to global fetch", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = () => { throw new Error("Unexpected public fetch"); };
  try {
    const { response } = await send("/", {}, () => { throw new Error("private URL and credentials"); });
    assert.equal(response.status, 502);
    assert.equal(await response.text(), "Request unavailable");
  } finally { globalThis.fetch = original; }
});

test("one total deadline bounds a hanging backend even if it ignores cancellation", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const env = environment("portal", () => new Promise(() => {}));
  const pending = relay.fetch(request(), env);
  await new Promise(setImmediate);
  t.mock.timers.tick(15000);
  const response = await pending;
  assert.equal(response.status, 502);
  assert.equal(env.calls[0][1].signal.aborted, true);
});

test("deadline also bounds a stalled incoming form", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let canceled = false;
  const env = environment();
  const body = new ReadableStream({ pull() { return new Promise(() => {}); }, cancel() { canceled = true; } });
  const pending = relay.fetch(request("/connection", { method: "POST", headers: { "Content-Type": FORM }, body }), env);
  await new Promise(setImmediate);
  t.mock.timers.tick(15000);
  assert.equal((await pending).status, 502);
  assert.equal(env.calls.length, 0);
  assert.equal(canceled, true);
});

test("deadline remains active after headers while a response stream stalls", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let canceled = false;
  const { response } = await send("/", {}, () => new Response(new ReadableStream({
    pull() { return new Promise(() => {}); }, cancel() { canceled = true; },
  })));
  const pending = response.text();
  const rejected = assert.rejects(pending, /Service response unavailable/);
  t.mock.timers.tick(15000);
  await rejected;
  assert.equal(canceled, true);
});

test("client cancellation cancels the upstream stream", async () => {
  let canceled = false;
  const { response } = await send("/", {}, () => new Response(new ReadableStream({
    pull() { return new Promise(() => {}); }, cancel() { canceled = true; },
  })));
  await response.body.cancel();
  assert.equal(canceled, true);
});
