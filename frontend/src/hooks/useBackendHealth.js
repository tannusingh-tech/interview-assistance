import { useEffect, useState } from "react";
import { fetchHealth } from "../api.js";

/** Polls GET /health. Returns "checking" | "online" | "offline". */
export function useBackendHealth(intervalMs = 10000) {
  const [status, setStatus] = useState("checking");

  useEffect(() => {
    let timer;
    let controller;
    let cancelled = false;

    const check = async () => {
      controller = new AbortController();
      try {
        const data = await fetchHealth(controller.signal);
        if (!cancelled) setStatus(data.status === "ok" ? "online" : "offline");
      } catch (err) {
        if (!cancelled && err.name !== "AbortError") setStatus("offline");
      }
      if (!cancelled) timer = setTimeout(check, intervalMs);
    };

    check();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller?.abort();
    };
  }, [intervalMs]);

  return status;
}
