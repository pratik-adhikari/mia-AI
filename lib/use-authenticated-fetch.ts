"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback } from "react";

/** Attach Clerk's short-lived session JWT when the Python API is on another origin. */
export function useAuthenticatedFetch() {
  const { getToken } = useAuth();
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
