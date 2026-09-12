const BACKEND_URL = "http://alexa-backend/alexa";
const MAX_REQUEST_BYTES = 32 * 1024;
const MAX_RESPONSE_BYTES = 16 * 1024;
const MAX_SIGNATURE_LENGTH = 8192;
const DEADLINE_MS = 6000;
const SIGNATURE_HEADERS = ["Signature-256", "SignatureCertChainUrl"];

class RequestError extends Error {
  constructor(status) {
    super("Invalid Alexa request");
    this.status = status;
  }
}

function jsonError(status, error) {
  return new Response(JSON.stringify({ error }), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
}

function isJson(contentType) {
  return contentType?.split(";", 1)[0].trim().toLowerCase() === "application/json";
}

async function readLimited(stream, limit, signal) {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader();
  const chunks = [];
  let size = 0;
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", cancel, { once: true });
  try {
    while (true) {
      signal.throwIfAborted();
      const { value, done } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      if (value.byteLength === 0) continue;
      size += value.byteLength;
      if (size > limit) {
        cancel();
        throw new RequestError(413);
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return bytes;
  } finally {
    signal.removeEventListener("abort", cancel);
    reader.releaseLock();
  }
}

async function relay(request, env, signal) {
  const contentType = request.headers.get("Content-Type");
  const encoding = request.headers.get("Content-Encoding");
  if (!isJson(contentType) || (encoding && encoding.toLowerCase() !== "identity")) {
    throw new RequestError(400);
  }

  const headers = new Headers({ "Content-Type": contentType });
  for (const name of SIGNATURE_HEADERS) {
    const value = request.headers.get(name);
    if (!value?.trim() || value.length > MAX_SIGNATURE_LENGTH) {
      throw new RequestError(400);
    }
    headers.set(name, value);
  }

  const advertisedSize = request.headers.get("Content-Length");
  if (advertisedSize !== null) {
    if (!/^[0-9]+$/.test(advertisedSize) || Number(advertisedSize) < 1) {
      throw new RequestError(400);
    }
    if (Number(advertisedSize) > MAX_REQUEST_BYTES) throw new RequestError(413);
  }
  const body = await readLimited(request.body, MAX_REQUEST_BYTES, signal);
  if (body.byteLength === 0 || (advertisedSize !== null && Number(advertisedSize) !== body.byteLength)) {
    throw new RequestError(400);
  }
  signal.throwIfAborted();

  // An ArrayBuffer has a known length. Workers supplies Content-Length to the
  // WSGI backend; forwarding the incoming stream would use chunked encoding.
  const response = await env.ALEXA_BACKEND.fetch(BACKEND_URL, {
    method: "POST",
    headers,
    body: body.buffer,
    redirect: "manual",
    signal,
  });
  signal.throwIfAborted();
  if (!(response instanceof Response)
      || !(response.status === 200 || (response.status >= 400 && response.status <= 599))
      || !isJson(response.headers.get("Content-Type"))) {
    if (response instanceof Response) void response.body?.cancel().catch(() => {});
    throw new Error("Invalid backend response");
  }

  let responseBody;
  try {
    responseBody = await readLimited(response.body, MAX_RESPONSE_BYTES, signal);
    const decoded = new TextDecoder("utf-8", { fatal: true }).decode(responseBody);
    const payload = JSON.parse(decoded);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("Invalid backend response");
    }
  } catch {
    // Response errors, including size limits, are upstream failures.
    throw new Error("Invalid backend response");
  }
  return new Response(responseBody, {
    status: response.status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/alexa" || url.search) {
      return jsonError(404, "Not found");
    }

    const controller = new AbortController();
    let timer;
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new Error("Relay deadline exceeded"));
      }, DEADLINE_MS);
    });
    try {
      // The race bounds body reads too, even if a binding ignores cancellation.
      return await Promise.race([relay(request, env, controller.signal), deadline]);
    } catch (error) {
      if (error instanceof RequestError && !controller.signal.aborted) {
        return jsonError(error.status, "Invalid Alexa request");
      }
      return jsonError(502, "Alexa service unavailable");
    } finally {
      clearTimeout(timer);
    }
  },
};
