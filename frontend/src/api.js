const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
const API_PREFIX = `${API_BASE}/api/v1`;

export class ApiError extends Error {
  constructor(message, { status = 0, retryAfter = 0, code = "API_ERROR" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
    this.code = code;
  }
}

async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs || 25000);
  try {
    const response = await fetch(`${API_PREFIX}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    const retryAfter = Number.parseInt(response.headers.get("Retry-After") || "0", 10) || 0;
    if (!response.ok) {
      let detail = "";
      try {
        const body = await response.json();
        detail = typeof body?.detail === "string" ? body.detail : "";
      } catch {
        detail = "";
      }
      throw new ApiError(detail || `HTTP ${response.status}`, {
        status: response.status,
        retryAfter,
        code: response.status === 429 ? "RATE_LIMITED" : "HTTP_ERROR",
      });
    }
    return response.json();
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (error?.name === "AbortError") {
      throw new ApiError("Сервер не ответил вовремя.", { code: "TIMEOUT" });
    }
    throw new ApiError("Не удалось связаться с сервером.", { code: "NETWORK" });
  } finally {
    window.clearTimeout(timeout);
  }
}

export function fetchReady() {
  return request("/ready", { timeoutMs: 8000 });
}

export function fetchMeta() {
  return request("/meta", { timeoutMs: 8000 });
}

export function fetchExamples() {
  return request("/examples", { timeoutMs: 8000 });
}

export function askQuestion(question, requestId) {
  return request("/chat", {
    method: "POST",
    body: JSON.stringify({
      question,
      session_id: null,
      request_id: requestId || null,
    }),
  });
}
