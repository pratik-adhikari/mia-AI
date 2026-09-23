import { auth } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import { start } from "workflow/api";

import { AUTHENTICATION_ENABLED } from "@/lib/server-config";
import { runDeepResearch } from "@/workflows/deep-research";

/** Authenticate ownership before handing execution to the durable workflow service. */
export async function POST(request: Request) {
  const session = AUTHENTICATION_ENABLED ? await auth() : null;
  if (AUTHENTICATION_ENABLED && !session?.userId) {
    return NextResponse.json({ detail: "Authentication required" }, { status: 401 });
  }
  const body = (await request.json()) as { jobId?: string };
  if (!body.jobId) {
    return NextResponse.json({ detail: "jobId is required" }, { status: 422 });
  }
  const incomingAuthorization = request.headers.get("authorization");
  const token = incomingAuthorization || !session ? null : await session.getToken();
  const authorization =
    incomingAuthorization ?? (token ? `Bearer ${token}` : null);
  const configured =
    process.env.MIA_BACKEND_INTERNAL_URL ??
    process.env.MIA_BACKEND_URL ??
    process.env.NEXT_PUBLIC_MIA_API_URL;
  const backend = configured ? new URL(configured) : new URL(new URL(request.url).origin);
  const ownership = await fetch(
    new URL(`/api/background-jobs/${encodeURIComponent(body.jobId)}`, backend),
    { headers: authorization ? { Authorization: authorization } : {} },
  );
  if (!ownership.ok) {
    return NextResponse.json(
      { detail: "Background job is unavailable" },
      { status: ownership.status },
    );
  }
  if (process.env.MIA_RESEARCH_DISPATCH === "local") {
    const retry = await fetch(
      new URL(`/api/background-jobs/${encodeURIComponent(body.jobId)}/retry`, backend),
      {
        method: "POST",
        headers: authorization ? { Authorization: authorization } : {},
      },
    );
    if (!retry.ok) {
      return NextResponse.json(
        { detail: "Background job could not be requeued" },
        { status: retry.status },
      );
    }
    return NextResponse.json({ jobId: body.jobId, dispatch: "local-worker" }, { status: 202 });
  }

  const run = await start(runDeepResearch, [body.jobId, backend.toString()]);
  return NextResponse.json({ jobId: body.jobId, workflowRunId: run.runId }, { status: 202 });
}
