import type { RunEvent } from "../types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export const ACCESS_TOKEN_KEY = "hydrobridge_token";
export const REFRESH_TOKEN_KEY = "hydrobridge_refresh_token";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

interface TokenPair {
  access_token: string;
  refresh_token: string;
}

let refreshInFlight: Promise<string | null> | null = null;

function storedAccessToken(fallback?: string): string | undefined {
  if (typeof window === "undefined") return fallback;
  return window.localStorage.getItem(ACCESS_TOKEN_KEY) || fallback;
}

async function rotateAccessToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    const refreshToken = window.localStorage.getItem(REFRESH_TOKEN_KEY);
    if (!refreshToken) return null;
    const response = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    });
    if (!response.ok) {
      window.localStorage.removeItem(ACCESS_TOKEN_KEY);
      window.localStorage.removeItem(REFRESH_TOKEN_KEY);
      return null;
    }
    const tokens = (await response.json()) as TokenPair;
    window.localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
    window.localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
    window.dispatchEvent(
      new CustomEvent("hydrobridge-token-refreshed", {
        detail: tokens.access_token,
      }),
    );
    return tokens.access_token;
  })().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

async function fetchWithAuthentication(
  url: string,
  options: RequestInit,
  fallbackToken?: string,
  retryRefresh = true,
): Promise<Response> {
  const accessToken = storedAccessToken(fallbackToken);
  const headers = new Headers(options.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  let response = await fetch(url, { ...options, headers, cache: "no-store" });
  if (response.status === 401 && retryRefresh) {
    const rotated = await rotateAccessToken();
    if (rotated) {
      headers.set("Authorization", `Bearer ${rotated}`);
      response = await fetch(url, { ...options, headers, cache: "no-store" });
    }
  }
  return response;
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
  token?: string,
): Promise<T> {
  const headers = new Headers(options.headers);
  if (!headers.has("Content-Type") && options.body) {
    headers.set("Content-Type", "application/json");
  }
  const canRefresh = !["/auth/login", "/auth/refresh", "/auth/logout"].includes(path);
  const response = await fetchWithAuthentication(
    `${API_BASE}${path}`,
    { ...options, headers },
    token,
    canRefresh,
  );
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const payload = await response.json();
      message = payload.detail ?? payload.message ?? message;
    } catch {
      // Keep the HTTP fallback when the server did not return JSON.
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function streamRunEvents(
  runId: string,
  token: string,
  onEvent: (event: RunEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetchWithAuthentication(
    `${API_BASE}/runs/${runId}/events`,
    { signal },
    token,
  );
  if (!response.ok || !response.body) {
    throw new ApiError("无法连接任务事件流", response.status);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const dataLine = chunk
        .split("\n")
        .find((line) => line.startsWith("data: "));
      if (!dataLine) continue;
      try {
        onEvent(JSON.parse(dataLine.slice(6)) as RunEvent);
      } catch {
        // An incomplete or non-JSON keep-alive event is safe to ignore.
      }
    }
  }
}

export async function downloadArtifact(
  artifactId: string,
  token: string,
): Promise<void> {
  const response = await fetchWithAuthentication(
    `${API_BASE}/artifacts/${artifactId}/download`,
    {},
    token,
  );
  if (!response.ok) {
    let message = `下载失败（${response.status}）`;
    try {
      const payload = await response.json();
      message = payload.detail ?? payload.message ?? message;
    } catch {
      // Keep the HTTP fallback when the server did not return JSON.
    }
    throw new ApiError(message, response.status);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = `hydrobridge-report-${artifactId}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}
