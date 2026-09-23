import { FatalError, RetryableError } from "workflow";

type ResearchJob = {
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  metadata?: Record<string, unknown>;
};

/** Resume the same catalogue-owned crawl job until every bounded batch is checkpointed. */
export async function runDeepResearch(jobId: string, backendOrigin: string) {
  "use workflow";

  let job: ResearchJob | null = null;
  // The backend owns the durable cursor; the Workflow only schedules the next bounded slice.
  for (let iteration = 0; iteration < 50; iteration += 1) {
    job = await executeResearchBatch(jobId, backendOrigin);
    if (job.status === "completed" || job.status === "cancelled") return job;
    if (job.status !== "queued") {
      throw new RetryableError(`Research job returned unexpected status ${job.status}`, {
        retryAfter: "30s",
      });
    }
  }
  throw new FatalError("Research job exceeded the bounded workflow iteration limit");
}

async function executeResearchBatch(jobId: string, backendOrigin: string): Promise<ResearchJob> {
  "use step";

  const secret = process.env.MIA_WORKFLOW_SECRET;
  if (!secret) {
    throw new FatalError("MIA_WORKFLOW_SECRET is not configured");
  }
  const response = await fetch(
    new URL(`/api/background-jobs/${encodeURIComponent(jobId)}/execute`, backendOrigin),
    {
      method: "POST",
      headers: { "x-mia-workflow-secret": secret },
    },
  );
  if (response.status === 429 || response.status >= 500) {
    throw new RetryableError(`Research worker returned ${response.status}`, {
      retryAfter: "30s",
    });
  }
  if (!response.ok) {
    throw new FatalError(`Research worker returned ${response.status}`);
  }
  return response.json() as Promise<ResearchJob>;
}

executeResearchBatch.maxRetries = 5;
