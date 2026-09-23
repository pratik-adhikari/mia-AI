"use client";

import { useCallback } from "react";
import { useSessionToken } from "@/lib/app-providers";

/** Attach Clerk's short-lived session JWT when the Python API is on another origin. */
export function useAuthenticatedFetch() {
  const getToken = useSessionToken();
  return useCallback(
    async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const token = await getToken();
      const headers = new Headers(init.headers);
      if (token) headers.set("Authorization", `Bearer ${token}`);
      return fetch(input, { ...init, headers });
    },
    [getToken]
  );
}
