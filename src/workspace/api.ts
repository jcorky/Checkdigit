/*
 * Same-origin client for /v1/workspaces. The admin session cookie rides along;
 * every request names the workspace chosen in the header. Errors carry the
 * service's finding code when it answers with one.
 */

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string, public readonly detail: unknown) {
    super(message);
  }
}

const KEY = "checkdigit.workspace";

export function workspaceId(): string {
  try {
    return localStorage.getItem(KEY) || "default";
  } catch {
    return "default";
  }
}

export function setWorkspaceId(id: string): void {
  try {
    localStorage.setItem(KEY, id.trim() || "default");
  } catch {
    /* storage may be unavailable; the page keeps working with the default */
  }
}

export function base(): string {
  return `/v1/workspaces/${encodeURIComponent(workspaceId())}`;
}

function describe(status: number, body: unknown): { code: string; message: string } {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (d && typeof d === "object" && "code" in d) {
      const dd = d as { code: string; detail?: unknown };
      return { code: dd.code, message: typeof dd.detail === "string" ? dd.detail : JSON.stringify(dd.detail ?? "") };
    }
    if (typeof d === "string") return { code: `HTTP_${status}`, message: d };
    return { code: `HTTP_${status}`, message: JSON.stringify(d) };
  }
  return { code: `HTTP_${status}`, message: `request failed with status ${status}` };
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers ?? {});
  if (init.body && typeof init.body === "string" && !headers.has("content-type")) headers.set("content-type", "application/json");
  const res = await fetch(base() + path, { ...init, headers, credentials: "same-origin" });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const { code, message } = describe(res.status, body);
    throw new ApiError(res.status, code, message, body);
  }
  return body as T;
}

function withBody(method: string, body: unknown): RequestInit {
  return body === undefined ? { method } : { method, body: JSON.stringify(body) };
}

export const get = <T = unknown>(path: string) => api<T>(path);
export const post = <T = unknown>(path: string, body?: unknown) => api<T>(path, withBody("POST", body));
export const put = <T = unknown>(path: string, body?: unknown) => api<T>(path, withBody("PUT", body));
export const patch = <T = unknown>(path: string, body?: unknown) => api<T>(path, withBody("PATCH", body));
export const del = <T = unknown>(path: string) => api<T>(path, { method: "DELETE" });

/** Resumable upload in 8 MiB parts; returns the source file id. */
export async function uploadFile(file: File, onProgress: (sent: number, total: number) => void): Promise<string> {
  const created = await post<{ upload_id: string }>("/uploads", { filename: file.name, size: file.size });
  const part = 8 * 1024 * 1024;
  let offset = 0;
  while (offset < file.size) {
    const chunk = file.slice(offset, Math.min(file.size, offset + part));
    const res = await fetch(`${base()}/uploads/${created.upload_id}?offset=${offset}`, {
      method: "PUT", body: chunk, credentials: "same-origin", headers: { "content-type": "application/octet-stream" },
    });
    if (res.status === 409) {
      const body = (await res.json()) as { detail?: { expected_offset?: number } };
      offset = body.detail?.expected_offset ?? offset;
      continue;
    }
    if (!res.ok) throw new ApiError(res.status, `HTTP_${res.status}`, "upload part failed", await res.text());
    offset += chunk.size;
    onProgress(offset, file.size);
  }
  let sha256: string | undefined;
  if (file.size <= 64 * 1024 * 1024 && globalThis.crypto?.subtle) {
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    sha256 = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  const done = await post<{ source_file_id: string }>(`/uploads/${created.upload_id}/complete`, { sha256, content_type: file.type || "" });
  return done.source_file_id;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}
