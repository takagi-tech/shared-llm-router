import { useCallback, useEffect, useRef, useState } from "react";

let csrf = "";
let intervalMs = 5000;
export function setCSRF(value: string) {
  csrf = value;
}
export function setRefresh(seconds: number) {
  intervalMs = Math.max(1, seconds) * 1000;
}

export class APIError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
      ...init.headers,
    },
  });
  const data = await res.json();
  if (!res.ok) {
    if (res.status === 401 && path !== "/api/auth/login")
      window.dispatchEvent(new Event("session-expired"));
    const detail = data.error?.details
      ?.map(
        (d: { field: string; message: string }) => `${d.field}: ${d.message}`,
      )
      .join(" / ");
    throw new APIError(
      res.status,
      detail || data.error?.message || "通信に失敗しました",
    );
  }
  return data;
}

export function mutate<T>(path: string, method: string, data?: unknown) {
  return api<T>(path, {
    method,
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  });
}

export function useData<T>(path: string | null, poll = true) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [updated, setUpdated] = useState<number | null>(null);
  const currentPath = useRef(path);
  const serial = useRef(0);
  currentPath.current = path;
  const reload = useCallback(async () => {
    if (!path) return;
    const sequence = ++serial.current;
    try {
      const next = await api<T>(path);
      if (currentPath.current === path && sequence === serial.current) {
        setData(next);
        setError("");
        setUpdated(Date.now());
      }
    } catch (e) {
      if (currentPath.current === path && sequence === serial.current)
        setError((e as Error).message);
    }
  }, [path]);
  useEffect(() => {
    setData(null);
    setError("");
    setUpdated(null);
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const run = async () => {
      await reload();
      if (active && poll) timer = setTimeout(run, intervalMs);
    };
    void run();
    return () => {
      active = false;
      clearTimeout(timer);
      serial.current++;
    };
  }, [reload, poll]);
  return { data, error, updated, reload };
}
