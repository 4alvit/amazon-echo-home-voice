import assert from "node:assert/strict";
import test from "node:test";
import worker from "./relay.mjs";

const encoder = new TextEncoder();
const signedBody = '{ "version": "1.0", "request": { "type": "LaunchRequest" }, "text": "café" }\n';
const speech = '{"version":"1.0","response":{"outputSpeech":{"type":"PlainText","text":"Battery charge is 65 percent."},"shouldEndSession":true}}';

function request(body = signedBody, extraHeaders = {}, options = {}) {
  const headers = new Headers({
    "content-type": "application/json;charset=UTF-8",
    "signature-256": "unchanged-amazon-signature",
    "signaturecertchainurl": "https://s3.amazonaws.com/echo.api/echo-api-cert.pem",
  });
  for (const [name, value] of Object.entries(extraHeaders)) headers.set(name, value);
  return new Request("https://home-energy.example.workers.dev/alexa", {
    method: "POST",
    headers,
    body,
    ...(body instanceof ReadableStream ? { duplex: "half" } : {}),
    ...options,
  });
}

function backend(handler = () => new Response(speech, { headers: { "Content-Type": "application/json" } })) {
  const calls = [];
  return {
    calls,
    env: { ALEXA_BACKEND: { fetch: async (...args) => { calls.push(args); return handler(...args); } } },
  };
}

function streamChunks(...chunks) {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

async function assertJsonError(response, status, error) {
  assert.equal(response.status, status);
  assert.equal(response.headers.get("Content-Type"), "application/json");
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.deepEqual(await response.json(), { error });
}

test("preserves signed bytes and sends only the three allowed headers to the fixed VPC binding", async () => {
  const upstream = backend();
  const response = await worker.fetch(request(signedBody, {
    Authorization: "Bearer never-forward",
    Cookie: "session=never-forward",
    "X-User-ID": "never-forward",
    "CF-Access-Client-Secret": "never-forward",
    "User-Agent": "Apache-HttpClient/UNAVAILABLE (Java/17)",
    "Content-Length": String(encoder.encode(signedBody).byteLength),
  }), upstream.env);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), speech);
  assert.equal(upstream.calls.length, 1);
  const [url, init] = upstream.calls[0];
  assert.equal(url, "http://alexa-backend/alexa");
  assert.equal(init.method, "POST");
  assert.equal(init.redirect, "manual");
  assert.ok(init.body instanceof ArrayBuffer);
  assert.deepEqual(new Uint8Array(init.body), encoder.encode(signedBody));
  assert.deepEqual(Object.fromEntries(init.headers), {
    "content-type": "application/json;charset=UTF-8",
    "signature-256": "unchanged-amazon-signature",
    signaturecertchainurl: "https://s3.amazonaws.com/echo.api/echo-api-cert.pem",
  });
  assert.equal(init.signal.aborted, false);
});

test("NAS rejection remains a rejection even when all signature headers are present", async () => {
  const rejection = '{"error":"Invalid Alexa request"}';
  const upstream = backend(() => new Response(rejection, {
    status: 400,
    headers: { "Content-Type": "application/json", Server: "private-nas", "Set-Cookie": "private=value" },
  }));
  const response = await worker.fetch(request(), upstream.env);
  assert.equal(upstream.calls.length, 1);
  assert.equal(response.status, 400);
  assert.equal(await response.text(), rejection);
  assert.deepEqual(Object.fromEntries(response.headers), {
    "cache-control": "no-store",
    "content-type": "application/json",
  });
});

for (const [method, path] of [
  ["GET", "/alexa"], ["HEAD", "/alexa"], ["OPTIONS", "/alexa"],
  ["POST", "/health"], ["POST", "/"], ["POST", "/alexa/"],
  ["POST", "/alexa?url=https://other.example"], ["POST", "/%61lexa"],
]) {
  test(`rejects ${method} ${path} without contacting the backend`, async () => {
    const upstream = backend();
    const input = new Request(`https://home-energy.example.workers.dev${path}`, { method });
    await assertJsonError(await worker.fetch(input, upstream.env), 404, "Not found");
    assert.equal(upstream.calls.length, 0);
  });
}

for (const headers of [
  { "Signature-256": "" },
  { SignatureCertChainUrl: "" },
  { "Signature-256": "x".repeat(8193) },
  { SignatureCertChainUrl: "x".repeat(8193) },
  { "Content-Type": "text/plain" },
  { "Content-Encoding": "gzip" },
  { "Content-Length": "-1" },
  { "Content-Length": "not-a-number" },
  { "Content-Length": "0" },
  { "Content-Length": "2" },
]) {
  test(`rejects invalid ${Object.keys(headers)[0]} (${Object.values(headers)[0].slice(0, 20)})`, async () => {
    const upstream = backend();
    await assertJsonError(await worker.fetch(request(signedBody, headers), upstream.env), 400, "Invalid Alexa request");
    assert.equal(upstream.calls.length, 0);
  });
}

test("rejects a truly absent signature header", async () => {
  const upstream = backend();
  const input = request();
  input.headers.delete("Signature-256");
  await assertJsonError(await worker.fetch(input, upstream.env), 400, "Invalid Alexa request");
  assert.equal(upstream.calls.length, 0);
});

test("rejects an empty body", async () => {
  const upstream = backend();
  await assertJsonError(await worker.fetch(request(""), upstream.env), 400, "Invalid Alexa request");
  assert.equal(upstream.calls.length, 0);
});

test("accepts exactly 32 KiB from a chunked client and forwards a fixed-length buffer", async () => {
  const upstream = backend();
  const bytes = new Uint8Array(32768).fill(32);
  const body = streamChunks(bytes.slice(0, 1000), bytes.slice(1000));
  const response = await worker.fetch(request(body), upstream.env);
  assert.equal(response.status, 200);
  assert.deepEqual(new Uint8Array(upstream.calls[0][1].body), bytes);
});

test("rejects a declared oversized body before reading it", async () => {
  const upstream = backend();
  const body = new ReadableStream();
  await assertJsonError(await worker.fetch(request(body, { "Content-Length": "32769" }), upstream.env), 413, "Invalid Alexa request");
  assert.equal(upstream.calls.length, 0);
});

test("enforces the streamed body limit even with a small advertised length", async () => {
  const upstream = backend();
  const body = streamChunks(new Uint8Array(32768), new Uint8Array(1));
  await assertJsonError(await worker.fetch(request(body, { "Content-Length": "10" }), upstream.env), 413, "Invalid Alexa request");
  assert.equal(upstream.calls.length, 0);
});

test("rejects an oversized stream without Content-Length and cancels its producer", async () => {
  const upstream = backend();
  let cancelled = false;
  const body = new ReadableStream({
    start(controller) { controller.enqueue(new Uint8Array(32769)); },
    cancel() { cancelled = true; },
  });
  await assertJsonError(await worker.fetch(request(body), upstream.env), 413, "Invalid Alexa request");
  assert.equal(cancelled, true);
  assert.equal(upstream.calls.length, 0);
});

test("never follows or returns an upstream redirect", async () => {
  const upstream = backend(() => new Response("", {
    status: 307, headers: { Location: "https://other.example/capture" },
  }));
  await assertJsonError(await worker.fetch(request(), upstream.env), 502, "Alexa service unavailable");
  assert.equal(upstream.calls.length, 1);
  assert.equal(upstream.calls[0][1].redirect, "manual");
});

for (const [label, result] of [
  ["HTML", () => new Response("<html>Challenge</html>", { headers: { "Content-Type": "text/html" } })],
  ["malformed JSON", () => new Response("{", { headers: { "Content-Type": "application/json" } })],
  ["JSON scalar", () => new Response("null", { headers: { "Content-Type": "application/json" } })],
  ["invalid UTF-8", () => new Response(new Uint8Array([0xff]), { headers: { "Content-Type": "application/json" } })],
  ["empty success", () => new Response(null, { status: 204 })],
  ["non-response", () => ({ status: 200 })],
  ["exception", () => { throw new Error("Private backend address and credentials"); }],
  ["oversized response", () => new Response(JSON.stringify({ text: "x".repeat(16384) }), { headers: { "Content-Type": "application/json" } })],
]) {
  test(`returns a generic error for ${label}`, async () => {
    const upstream = backend(result);
    await assertJsonError(await worker.fetch(request(), upstream.env), 502, "Alexa service unavailable");
  });
}

test("preserves an upstream JSON service failure", async () => {
  const upstream = backend(() => new Response('{"error":"Verifier unavailable"}', {
    status: 503, headers: { "Content-Type": "application/json" },
  }));
  const response = await worker.fetch(request(), upstream.env);
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "Verifier unavailable" });
});

test("a missing VPC binding fails closed without falling back to public fetch", async () => {
  await assertJsonError(await worker.fetch(request(), {}), 502, "Alexa service unavailable");
});

test("the six-second deadline includes a stalled client body", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let cancelled = false;
  const upstream = backend();
  const input = request(new ReadableStream({ cancel() { cancelled = true; } }));
  const pending = worker.fetch(input, upstream.env);
  t.mock.timers.tick(6000);
  await assertJsonError(await pending, 502, "Alexa service unavailable");
  assert.equal(cancelled, true);
  assert.equal(upstream.calls.length, 0);
});

test("the six-second deadline aborts a stalled VPC request", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let called;
  const started = new Promise((resolve) => { called = resolve; });
  const upstream = backend(() => { called(); return new Promise(() => {}); });
  const pending = worker.fetch(request(), upstream.env);
  await started;
  t.mock.timers.tick(6000);
  await assertJsonError(await pending, 502, "Alexa service unavailable");
  assert.equal(upstream.calls[0][1].signal.aborted, true);
});

test("the same deadline includes a stalled upstream response body", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let reading;
  const started = new Promise((resolve) => { reading = resolve; });
  let cancelled = false;
  const upstream = backend(() => new Response(new ReadableStream({
    pull() { reading(); },
    cancel() { cancelled = true; },
  }), { headers: { "Content-Type": "application/json" } }));
  const pending = worker.fetch(request(), upstream.env);
  await started;
  t.mock.timers.tick(6000);
  await assertJsonError(await pending, 502, "Alexa service unavailable");
  assert.equal(upstream.calls[0][1].signal.aborted, true);
  assert.equal(cancelled, true);
});
