import { auth } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import { start } from "workflow/api";

import { runDeepResearch } from "@/workflows/deep-research";

function backendOrigin(request: Request): URL {
  const configured =
    process.env.MIA_BACKEND_URL ?? process.env.NEXT_PUBLIC_MIA_API_URL;
  return configured ? new URL(configured) : new URL(request.url).origin;
}

/**
 * Authenticated BFF for agent turns.
 *
 * The Python backend persists the seed crawl and creates a durable research job.
 * This server route starts the Vercel Workflow before the response reaches the
 * browser, so deep research no longer depends on client-side dispatch.
 */
export async function POST(request: Request) {
  const session = await auth();
  if (!session.userId) {
    return NextResponse.json({ detail: "Authentication required" }, { status: 401 });
  }

  const token = await session.getToken();
  const backend = backendOrigin(request);
  const response = await fetch(new URL("/api/agent/messages", backend), {
    method: "POST",
    headers: {
      "Content-Type": request.headers.get("content-type") ?? "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: await request.text(),
    cache: "no-store",
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
  if (jobId) {
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
