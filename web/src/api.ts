// Every request to the service, with the sign-in token attached. Errors come
// back as ApiError with the service's own words.

import { token } from "./auth";

export class ApiError extends Error {
  constructor(public status: number, message: string, public data: Record<string, unknown> = {}) {
    super(message);
  }
}

async function headers(extra: Record<string, string> = {}): Promise<Record<string, string>> {
  const t = await token();
  return t ? { ...extra, Authorization: `Bearer ${t}` } : extra;
}

async function parse<T>(r: Response): Promise<T> {
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, (data as { error?: string }).error || `Something went wrong (${r.status})`, data);
  return data as T;
}

export async function get<T>(path: string): Promise<T> {
  return parse<T>(await fetch(path, { headers: await headers() }));
}

export async function post<T>(path: string, body: unknown = {}): Promise<T> {
  return parse<T>(await fetch(path, {
    method: "POST", headers: await headers({ "Content-Type": "application/json" }), body: JSON.stringify(body),
  }));
}

export const UPLOAD_TYPES: Record<string, string> = {
  ".pdf": "application/pdf",
  ".txt": "text/plain",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};

export async function upload<T>(path: string, file: File): Promise<T> {
  const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
  const type = UPLOAD_TYPES[ext];
  if (!type) throw new ApiError(415, "Upload a PDF, a Word file (.docx) or a plain text file");
  return parse<T>(await fetch(path, { method: "POST", headers: await headers({ "Content-Type": type }), body: file }));
}

/** A signed-in file (a resume, an export) as a blob - links can't carry the token. */
export async function blob(path: string): Promise<Blob> {
  const r = await fetch(path, { headers: await headers() });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new ApiError(r.status, (data as { error?: string }).error || "Couldn't get that file");
  }
  return r.blob();
}

export async function openFile(path: string): Promise<void> {
  const url = URL.createObjectURL(await blob(path));
  window.open(url, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export async function download(path: string, name: string): Promise<void> {
  const url = URL.createObjectURL(await blob(path));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
