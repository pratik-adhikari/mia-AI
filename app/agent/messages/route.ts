import { auth } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import { Agent, fetch as undiciFetch } from "undici";
import { start } from "workflow/api";

import { AUTHENTICATION_ENABLED } from "@/lib/server-config";
import { runDeepResearch } from "@/workflows/deep-research";

const backendTimeoutMs = Number(
  process.env.MIA_BACKEND_RESPONSE_TIMEOUT_MS ?? 900_000,
);
const backendDispatcher = new Agent({
  headersTimeout: backendTimeoutMs,
  bodyTimeout: backendTimeoutMs,
});

function backendOrigin(request: Request): URL {
  const configured =
    process.env.MIA_BACKEND_INTERNAL_URL ??
    process.env.MIA_BACKEND_URL ??
    process.env.NEXT_PUBLIC_MIA_API_URL;
  return configured ? new URL(configured) : new URL(new URL(request.url).origin);
}

/**
 * Authenticated BFF for agent turns.
 *
 * The Python backend persists the seed crawl and creates a durable research job.
 * This server route starts the Vercel Workflow before the response reaches the
 * browser, so deep research no longer depends on client-side dispatch.
 */
export async function POST(request: Request) {
  const session = AUTHENTICATION_ENABLED ? await auth() : null;
  if (AUTHENTICATION_ENABLED && !session?.userId) {
    return NextResponse.json({ detail: "Authentication required" }, { status: 401 });
  }

  // The browser already attached its Clerk session token through
  // useAuthenticatedFetch(). Forward that exact token to Python so the backend
  // validates the same session/azp that authenticated the browser request.
  // Fall back to Clerk's server token only for non-browser callers.
  const incomingAuthorization = request.headers.get("authorization");
  const token = incomingAuthorization || !session ? null : await session.getToken();
  const authorization =
    incomingAuthorization ?? (token ? `Bearer ${token}` : null);
  const backend = backendOrigin(request);
  const response = await undiciFetch(new URL("/api/agent/messages", backend), {
    method: "POST",
    headers: {
      "Content-Type": request.headers.get("content-type") ?? "application/json",
      ...(authorization ? { Authorization: authorization } : {}),
    },
    body: await request.text(),
    cache: "no-store",
    dispatcher: backendDispatcher,
  });

  const raw = await response.text();
  if (!response.ok) {
    return new Response(raw, {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/json",
      },
    });
  }

  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return NextResponse.json(
      { detail: "Python backend returned an invalid agent response" },
      { status: 502 },
    );
  }

  let researchDispatchError: string | null = null;
  const jobId =
    typeof payload.backgroundJobId === "string" ? payload.backgroundJobId : null;
  if (jobId && process.env.MIA_RESEARCH_DISPATCH !== "local") {
    try {
      await start(runDeepResearch, [jobId, backend.toString()]);
    } catch (error) {
      researchDispatchError =
        error instanceof Error
          ? error.message
          : "Unable to start durable deep research";
    }
  }

  return NextResponse.json({ ...payload, researchDispatchError });
}
