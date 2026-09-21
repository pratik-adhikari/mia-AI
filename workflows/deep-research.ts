import { FatalError, RetryableError } from "workflow";

/** Run the catalogue-owned research job in a durable, retryable Workflow step. */
export async function runDeepResearch(jobId: string, backendOrigin: string) {
  "use workflow";

  return executeResearchJob(jobId, backendOrigin);
}

async function executeResearchJob(jobId: string, backendOrigin: string) {
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
  return response.json();
}

executeResearchJob.maxRetries = 5;
