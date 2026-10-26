import { useEffect, useState } from "react";

/** useState that mirrors to localStorage; tolerates storage being unavailable. */
export function usePersistentState(key, initial) {
  const [value, setValue] = useState(() => {
    try {
      const saved = window.localStorage.getItem(key);
      return saved ?? initial;
    } catch {
      return initial;
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      /* ignore: persistence is a convenience only */
    }
  }, [key, value]);

  return [value, setValue];
}
