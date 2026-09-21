import { auth } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import { start } from "workflow/api";

import { runDeepResearch } from "@/workflows/deep-research";

/** Authenticate ownership before handing execution to the durable workflow service. */
export async function POST(request: Request) {
  const session = await auth();
  if (!session.userId) {
    return NextResponse.json({ detail: "Authentication required" }, { status: 401 });
  }
  const body = (await request.json()) as { jobId?: string };
  if (!body.jobId) {
    return NextResponse.json({ detail: "jobId is required" }, { status: 422 });
  }
  const token = await session.getToken();
  const origin = new URL(request.url).origin;
  const ownership = await fetch(
    new URL(`/api/background-jobs/${encodeURIComponent(body.jobId)}`, origin),
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  );
  if (!ownership.ok) {
    return NextResponse.json(
      { detail: "Background job is unavailable" },
      { status: ownership.status },
    );
  }
  const run = await start(runDeepResearch, [body.jobId, origin]);
  return NextResponse.json({ jobId: body.jobId, workflowRunId: run.runId }, { status: 202 });
}
