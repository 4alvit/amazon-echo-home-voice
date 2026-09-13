// A bounded browser/OAuth relay for one fixed private VPC Service.
// The separate Alexa webhook continues to use its signed-request relay.
const DEADLINE_MS = 15_000;
const MAX_URL_BYTES = 16_384;
const MAX_DYNAMIC_RESPONSE_BYTES = 256 * 1024;
const MAX_STATIC_RESPONSE_BYTES = 2 * 1024 * 1024;
const REALM = "/realms/home-energy";
const OIDC = `${REALM}/protocol/openid-connect`;
const LABEL = "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?";
const WORKER_HOST = new RegExp(`^${LABEL}\\.${LABEL}\\.workers\\.dev$`);
const RESPONSE_HEADERS = [
  "Content-Type", "Content-Language", "Location", "Vary", "Retry-After",
  "WWW-Authenticate", "Content-Security-Policy", "Content-Security-Policy-Report-Only",
  "X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy",
  "Strict-Transport-Security", "Permissions-Policy", "Cross-Origin-Opener-Policy",
  "Cross-Origin-Resource-Policy", "Cross-Origin-Embedder-Policy",
];

class RequestError extends Error {
  constructor(status) { super("Invalid request"); this.status = status; }
}

function errorResponse(status) {
  return new Response(status === 404 ? "Not found" : "Request unavailable", {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" },
  });
}

function route(request, env) {
  if (!WORKER_HOST.test(env.PUBLIC_HOST ?? "") || !["auth", "portal"].includes(env.ROLE)) {
    throw new Error("Invalid deployment configuration");
  }
  if (request.url.length > MAX_URL_BYTES || /[\\\u0000-\u0020\u007f]/.test(request.url)) {
    throw new RequestError(400);
  }
  const url = new URL(request.url);
  if (url.protocol !== "https:" || url.host !== env.PUBLIC_HOST || url.username || url.password || url.hash) {
    throw new RequestError(404);
  }
  // Public routes need no percent-encoded path characters. Reject all encoding
  // here to avoid disagreement with an upstream decoder or path normalizer.
  const path = url.pathname;
  if (!/^\/[A-Za-z0-9._/-]*$/.test(path) || path.includes("//")
      || path.split("/").some(part => part === "." || part === "..")) {
    throw new RequestError(404);
  }
  let methods;
  let tokenEndpoint = false;
  let staticAsset = false;
  if (env.ROLE === "portal") {
    if (["/", "/login", "/callback"].includes(path)) methods = ["GET"];
    if (["/connection", "/disconnect", "/logout"].includes(path)) methods = ["POST"];
    if (path !== "/callback" && url.search) throw new RequestError(404);
  } else {
    if (path === `${OIDC}/auth`) methods = ["GET"];
    if ([`${OIDC}/token`, `${OIDC}/token/introspect`, `${OIDC}/revoke`].includes(path)) {
      methods = ["POST"];
      tokenEndpoint = true;
      if (url.search) throw new RequestError(404);
    }
    if (path === `${OIDC}/logout`) methods = ["GET", "POST"];
    if (path === `${OIDC}/certs` || path === `${REALM}/.well-known/openid-configuration`) methods = ["GET", "HEAD"];
    if (path.startsWith(`${REALM}/login-actions/`) && path.length > `${REALM}/login-actions/`.length) methods = ["GET", "POST"];
    if (path.startsWith("/resources/") && path.length > "/resources/".length) {
      methods = ["GET", "HEAD"];
      staticAsset = true;
    }
  }
  if (!methods?.includes(request.method)) throw new RequestError(404);
  return { url, tokenEndpoint, staticAsset, requestLimit: env.ROLE === "portal" ? 16_384 : 32_768 };
}

function visibleHeader(value, maximum) {
  return value.length <= maximum && !/[\u0000-\u001f\u007f]/.test(value);
}

function clientIp(value) {
  if (!value || value.length > 45) return null;
  if (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(value)) {
    return value.split(".").every(part => Number(part) <= 255 && String(Number(part)) === part) ? value : null;
  }
  if (!/^[0-9a-fA-F:]+$/.test(value) || !value.includes(":")) return null;
  try {
    const hostname = new URL(`http://[${value}]/`).hostname;
    return hostname.startsWith("[") ? value : null;
  } catch { return null; }
}

function requestHeaders(request, env, selected) {
  const headers = new Headers({
    "Accept-Encoding": "identity", "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": env.PUBLIC_HOST, "X-Forwarded-Port": "443",
  });
  for (const [name, maximum] of [["Accept", 1024], ["Accept-Language", 1024],
    ["Content-Type", 256], ["Cookie", env.ROLE === "portal" ? 4096 : 8192], ["Origin", 512]]) {
    const value = request.headers.get(name);
    if (value !== null) {
      if (!visibleHeader(value, maximum)) throw new RequestError(400);
      headers.set(name, value);
    }
  }
  // Cloudflare supplies this edge header. Never trust client Forwarded/XFF.
  const address = clientIp(request.headers.get("CF-Connecting-IP"));
  if (address) headers.set("X-Forwarded-For", address);
  if (selected.tokenEndpoint) {
    const authorization = request.headers.get("Authorization");
    if (authorization !== null) {
      if (!visibleHeader(authorization, 8192) || !/^Basic [A-Za-z0-9+/]+={0,2}$/.test(authorization)) {
        throw new RequestError(400);
      }
      headers.set("Authorization", authorization);
    }
  }
  return headers;
}

function boundedReader(stream, budget, limit) {
  const reader = stream?.getReader();
  let size = 0;
  if (!reader) return null;
  const cancel = () => { void reader.cancel().catch(() => {}); };
  budget.signal.addEventListener("abort", cancel, { once: true });
  return {
    async read() {
      budget.signal.throwIfAborted();
      const chunk = await budget.wait(reader.read());
      budget.signal.throwIfAborted();
      if (!chunk.done) {
        size += chunk.value.byteLength;
        if (size > limit) { cancel(); throw new RequestError(413); }
      }
      return chunk;
    },
    close() { budget.signal.removeEventListener("abort", cancel); reader.releaseLock(); },
    cancel,
  };
}

async function requestBody(request, selected, budget) {
  const encoding = request.headers.get("Content-Encoding");
  if (encoding && encoding.toLowerCase() !== "identity") throw new RequestError(400);
  const advertised = request.headers.get("Content-Length");
  if (advertised !== null && !/^(0|[1-9][0-9]*)$/.test(advertised)) throw new RequestError(400);
  const expected = advertised === null ? null : Number(advertised);
  if (expected !== null && expected > selected.requestLimit) throw new RequestError(413);
  if (request.method !== "POST") {
    if (request.body || (expected !== null && expected !== 0)) throw new RequestError(400);
    return undefined;
  }
  if (request.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase() !== "application/x-www-form-urlencoded") {
    throw new RequestError(400);
  }
  const reader = boundedReader(request.body, budget, selected.requestLimit);
  if (!reader) throw new RequestError(400);
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (!value.byteLength) continue;
      chunks.push(value);
      size += value.byteLength;
    }
  } catch (error) { reader.cancel(); throw error; }
  finally { reader.close(); }
  if (!size || (expected !== null && expected !== size)) throw new RequestError(400);
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
  // Known-length bodies give Gunicorn its required Content-Length header.
  return body.buffer;
}

function responseHeaders(response, env) {
  const headers = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const value = response.headers.get(name);
    if (value !== null) {
      if (!visibleHeader(value, 16_384)) throw new Error("Invalid upstream headers");
      headers.set(name, value);
    }
  }
  const location = headers.get("Location");
  if (location !== null) {
    if (/\\|[\u0000-\u0020\u007f]/.test(location)) throw new Error("Invalid upstream redirect");
    const target = new URL(location, `https://${env.PUBLIC_HOST}`);
    if (target.protocol !== "https:" || target.username || target.password || target.port) {
      throw new Error("Invalid upstream redirect");
    }
    // Keycloak owns the exact registered redirect allowlist. The relay returns
    // its redirect to the browser and never follows it or forwards credentials.
  }
  const cookies = typeof response.headers.getSetCookie === "function"
    ? response.headers.getSetCookie()
    : typeof response.headers.getAll === "function" ? response.headers.getAll("Set-Cookie")
      : response.headers.has("Set-Cookie") ? null : [];
  if (!cookies || cookies.length > 16 || cookies.some(value => !visibleHeader(value, 8192))) {
    throw new Error("Invalid upstream cookies");
  }
  for (const value of cookies) headers.append("Set-Cookie", value);
  headers.set("Cache-Control", "no-store");
  headers.set("Pragma", "no-cache");
  return headers;
}

function makeBudget() {
  const controller = new AbortController();
  let rejectDeadline;
  const deadline = new Promise((_, reject) => { rejectDeadline = reject; });
  // Every wait observes this promise; suppress an unhandled rejection between
  // response chunks if the browser stops reading without canceling its stream.
  void deadline.catch(() => {});
  const timer = setTimeout(() => {
    controller.abort(new Error("Service deadline exceeded"));
    rejectDeadline(new Error("Service deadline exceeded"));
  }, DEADLINE_MS);
  return { signal: controller.signal, wait: promise => Promise.race([promise, deadline]),
    finish: () => clearTimeout(timer) };
}

function streamedResponse(response, request, env, selected, budget) {
  const limit = selected.staticAsset ? MAX_STATIC_RESPONSE_BYTES : MAX_DYNAMIC_RESPONSE_BYTES;
  const advertised = response.headers.get("Content-Length");
  const encoding = response.headers.get("Content-Encoding");
  if ((advertised !== null && (!/^(0|[1-9][0-9]*)$/.test(advertised) || Number(advertised) > limit))
      || (encoding && encoding.toLowerCase() !== "identity") || response.status < 200 || response.status > 599) {
    throw new Error("Invalid upstream response");
  }
  const headers = responseHeaders(response, env);
  if (request.method === "HEAD" || [204, 205, 304].includes(response.status) || !response.body) {
    void response.body?.cancel().catch(() => {});
    budget.finish();
    return new Response(null, { status: response.status, headers });
  }
  const reader = boundedReader(response.body, budget, limit);
  let closed = false;
  let abort;
  const finish = () => {
    if (closed) return;
    closed = true;
    budget.signal.removeEventListener("abort", abort);
    reader.close();
    budget.finish();
  };
  const body = new ReadableStream({
    start(controller) {
      abort = () => { controller.error(new Error("Service response unavailable")); reader.cancel(); finish(); };
      budget.signal.addEventListener("abort", abort, { once: true });
    },
    async pull(controller) {
      try {
        const { value, done } = await reader.read();
        if (closed) return;
        if (done) { controller.close(); finish(); }
        else controller.enqueue(value);
      } catch {
        if (!closed) { controller.error(new Error("Service response unavailable")); reader.cancel(); finish(); }
      }
    },
    cancel() { reader.cancel(); finish(); },
  });
  // Streaming keeps large static assets out of the Worker's JavaScript heap.
  // Content-Length is intentionally omitted after imposing a streaming cap.
  return new Response(body, { status: response.status, headers });
}

export default {
  async fetch(request, env) {
    let budget;
    let upstream;
    try {
      const selected = route(request, env);
      const headers = requestHeaders(request, env, selected);
      budget = makeBudget();
      const body = await requestBody(request, selected, budget);
      upstream = await budget.wait(env.BACKEND.fetch(`http://${env.PUBLIC_HOST}${selected.url.pathname}${selected.url.search}`, {
        method: request.method, headers, body, redirect: "manual", signal: budget.signal,
      }));
      budget.signal.throwIfAborted();
      if (!(upstream instanceof Response)) throw new Error("Invalid upstream response");
      // Server errors can contain private backend diagnostics. OAuth 4xx
      // responses keep their protocol status and body below.
      if (upstream.status >= 500) throw new Error("Backend unavailable");
      return streamedResponse(upstream, request, env, selected, budget);
    } catch (error) {
      budget?.finish();
      void upstream?.body?.cancel().catch(() => {});
      return errorResponse(error instanceof RequestError ? error.status : 502);
    }
  },
};
