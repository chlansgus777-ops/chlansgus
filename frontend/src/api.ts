export type Json = Record<string, unknown>;

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const base = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) init.body = JSON.stringify(body);
  const r = await fetch(`${base}/api${path}`, init);
  if (!r.ok) throw new ApiError(r.status, `${r.status} ${await r.text()}`);
  return (await r.json()) as T;
}

export const api = {
  get: <T>(p: string) => req<T>("GET", p),
  post: <T>(p: string, b?: unknown) => req<T>("POST", p, b),
  put: <T>(p: string, b?: unknown) => req<T>("PUT", p, b),
  del: <T>(p: string) => req<T>("DELETE", p),
};
