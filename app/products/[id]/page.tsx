"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import type { ProductDetail, ProductRun } from "@/lib/types";
import { useAuthenticatedFetch } from "@/lib/use-authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_MIA_API_URL ?? "";

function time(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(value)
  );
}

function statusClass(status: ProductRun["status"]) {
  if (status === "completed" || status === "reused") return "bg-ok/10 text-ok";
  if (status === "failed") return "bg-red-50 text-red-700";
  return "bg-warn/10 text-warn";
}

export default function ProductDetailPage() {
  const authenticatedFetch = useAuthenticatedFetch();
  const params = useParams<{ id: string }>();
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = async () => {
      try {
        const response = await authenticatedFetch(
          `${API_URL}/api/products/${encodeURIComponent(params.id)}`
        );
        if (!response.ok) throw new Error(`Backend returned ${response.status}`);
        setDetail((await response.json()) as ProductDetail);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not load product");
      }
    };
    void load();
  }, [params.id, authenticatedFetch]);

  if (error) {
    return <main className="min-h-screen bg-mist p-8 text-sm text-red-700">{error}</main>;
  }
  if (!detail) {
    return <main className="min-h-screen bg-mist p-8 text-sm text-muted">Loading product…</main>;
  }

  const {
    product,
    runs,
    dppVersions,
    artifacts,
    snapshot,
    snapshotHistory,
    humanReviews,
    identifiers,
  } = detail;
  const title = product.name ?? product.manufacturerProductId ?? "Unnamed product";
  const resumableRun = runs.find((run) => run.status === "running" || run.status === "awaiting_human");

  return (
    <main className="min-h-screen bg-mist">
      <header className="border-b border-hairline bg-paper">
        <div className="mx-auto flex h-14 max-w-shell items-center justify-between px-6">
          <Link href="/products" className="text-[13px] text-muted hover:text-ink">
            ← Product library
          </Link>
          <div className="flex items-center gap-2">
            <Link
              href={
                resumableRun
                  ? `/workspace?thread=${encodeURIComponent(resumableRun.threadId)}`
                  : `/workspace?product=${encodeURIComponent(product.id)}&action=continue`
              }
              className="rounded-full bg-ink px-3 py-1.5 text-[12px] text-white"
            >
              {resumableRun ? "Resume workspace" : "Continue saved work"}
            </Link>
            <Link
              href={`/workspace?product=${encodeURIComponent(product.id)}&action=refresh`}
              className="rounded-full border border-hairline px-3 py-1.5 text-[12px] text-ink"
            >
              Refresh sources
            </Link>
          </div>
        </div>
      </header>

      <section className="mx-auto max-w-shell px-6 py-10">
        <div className="grid gap-8 rounded-2xl border border-hairline bg-paper p-6 md:grid-cols-[280px_1fr]">
          <div className="grid min-h-64 place-items-center rounded-xl bg-white">
            {product.imageUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={product.imageUrl} alt={title} className="max-h-64 w-full object-contain p-6" />
            ) : (
              <span className="text-xs uppercase tracking-widest text-muted">No product image</span>
            )}
          </div>
          <div>
            <p className="text-sm text-muted">{product.manufacturer ?? "Unknown manufacturer"}</p>
            <h1 className="mt-1 text-[34px] font-semibold tracking-tight text-ink">{title}</h1>
            {product.manufacturerProductId && (
              <p className="mt-2 font-mono text-sm text-muted">{product.manufacturerProductId}</p>
            )}
            <dl className="mt-7 grid gap-4 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-muted">Source</dt>
                <dd className="mt-1 break-all text-ink">{product.canonicalUrl}</dd>
              </div>
              <div>
                <dt className="text-muted">Last verified</dt>
                <dd className="mt-1 text-ink">{time(product.lastVerifiedAt)}</dd>
              </div>
              <div>
                <dt className="text-muted">Attempts</dt>
                <dd className="mt-1 text-ink">{runs.length}</dd>
              </div>
              <div>
                <dt className="text-muted">DPP versions</dt>
                <dd className="mt-1 text-ink">{dppVersions.length}</dd>
              </div>
            </dl>
          </div>
        </div>

        {identifiers.length > 0 && (
          <section className="mt-8 rounded-2xl border border-hairline bg-paper p-6">
            <h2 className="text-lg font-semibold text-ink">Product identifiers</h2>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {identifiers.map((identifier) => (
                <div key={identifier.id} className="rounded-xl border border-hairline p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="font-mono text-[11px] text-muted">{identifier.scheme}</p>
                    <span className="rounded-full bg-mist px-2 py-0.5 text-[10px] text-muted">
                      {identifier.role}
                    </span>
                  </div>
                  <p className="mt-2 break-all text-sm text-ink">{identifier.value}</p>
                  {identifier.namespace && (
                    <p className="mt-1 text-[11px] text-muted">namespace · {identifier.namespace}</p>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {snapshot && (
          <section className="mt-8 rounded-2xl border border-hairline bg-paper p-6">
            <h2 className="text-lg font-semibold text-ink">Saved product work</h2>
            <div className="mt-4 grid gap-4 text-sm sm:grid-cols-4">
              <div><p className="text-muted">Stage</p><p className="mt-1 text-ink">{snapshot.workflowStage.replaceAll("_", " ")}</p></div>
              <div><p className="text-muted">Snapshot</p><p className="mt-1 font-mono text-ink">v{snapshot.version}</p></div>
              <div><p className="text-muted">Mandatory unresolved</p><p className="mt-1 text-warn">{snapshot.unresolvedRequiredIds.length}</p></div>
              <div><p className="text-muted">Human review pending</p><p className="mt-1 text-ink">{snapshot.humanReviewPending ? "Yes" : "No"}</p></div>
            </div>
            {snapshot.lastError && <p className="mt-4 text-xs text-red-700">{snapshot.lastError}</p>}
          </section>
        )}

        {snapshotHistory.length > 0 && (
          <section className="mt-8 rounded-2xl border border-hairline bg-paper p-6">
            <h2 className="text-lg font-semibold text-ink">Saved state history</h2>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[620px] text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-muted">
                  <tr>
                    <th className="pb-3 font-medium">Version</th>
                    <th className="pb-3 font-medium">Stage</th>
                    <th className="pb-3 font-medium">Run</th>
                    <th className="pb-3 font-medium">Updated</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {[...snapshotHistory].reverse().map((item) => (
                    <tr key={item.id + "-" + item.version}>
                      <td className="py-3 font-mono text-xs text-ink">v{item.version}</td>
                      <td className="py-3 text-ink">{item.workflowStage.replaceAll("_", " ")}</td>
                      <td className="py-3 font-mono text-xs text-muted">{item.runId}</td>
                      <td className="py-3 text-muted">{time(item.updatedAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        <div className="mt-8 grid gap-8 lg:grid-cols-2">
          <section className="rounded-2xl border border-hairline bg-paper p-6">
            <h2 className="text-lg font-semibold text-ink">DPP versions</h2>
            <div className="mt-4 space-y-3">
              {dppVersions.length === 0 && <p className="text-sm text-muted">No completed DPP yet.</p>}
              {dppVersions.map((version) => (
                <div key={version.id} className="rounded-xl border border-hairline p-4">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <div className="flex items-center gap-2">
                        <p className="font-medium text-ink">Version {version.version}</p>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                          version.releaseStatus === "verified"
                            ? "bg-ok/10 text-ok"
                            : "bg-warn/10 text-warn"
                        }`}>
                          {version.releaseStatus}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-muted">{time(version.createdAt)}</p>
                      {version.dummyMappingIds.length > 0 && (
                        <p className="mt-1 text-xs text-violet-700">
                          {version.dummyMappingIds.length} human-approved DUMMY placeholder{version.dummyMappingIds.length === 1 ? "" : "s"}
                        </p>
                      )}
                    </div>
                    <a
                      href={`${API_URL}/api/artifacts/${encodeURIComponent(version.dppArtifactId)}`}
                      className="rounded-full bg-signal px-3 py-1.5 text-xs font-medium text-white"
                    >
                      View DPP
                    </a>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-2xl border border-hairline bg-paper p-6">
            <h2 className="text-lg font-semibold text-ink">Run history</h2>
            <div className="mt-4 space-y-3">
              {runs.map((run) => (
                <div key={run.id} className="rounded-xl border border-hairline p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-mono text-xs text-muted">{run.id}</p>
                      <p className="mt-1 text-sm text-ink">{time(run.startedAt)}</p>
                      {run.error && <p className="mt-2 text-xs text-red-700">{run.error}</p>}
                      {run.seededFromRunId && <p className="mt-2 text-xs text-violet-700">Reused historical work from {run.seededFromRunId}</p>}
                      {(run.status === "running" || run.status === "awaiting_human") && (
                        <Link href={`/workspace?thread=${encodeURIComponent(run.threadId)}`} className="mt-2 inline-flex text-xs font-medium text-signal hover:underline">
                          Resume this run
                        </Link>
                      )}
                    </div>
                    <span className={`rounded-full px-2.5 py-1 text-xs ${statusClass(run.status)}`}>
                      {run.status.replace("_", " ")}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <section className="mt-8 rounded-2xl border border-hairline bg-paper p-6">
          <h2 className="text-lg font-semibold text-ink">Human decision history</h2>
          <div className="mt-4 space-y-3">
            {humanReviews.length === 0 && <p className="text-sm text-muted">No human decisions recorded yet.</p>}
            {humanReviews.map((review) => (
              <div key={review.id} className="rounded-xl border border-violet-100 bg-violet-50/40 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-violet-800">
                      {review.actorName || "Authenticated reviewer"} · {review.action.replaceAll("_", " ")}
                    </p>
                    <p className="mt-1 text-xs text-muted">{time(review.createdAt)}</p>
                    {review.finalRequirementId && <p className="mt-2 font-mono text-[11px] text-ink">{review.finalRequirementId}</p>}
                    {review.comment && <p className="mt-2 text-xs text-ink">{review.comment}</p>}
                  </div>
                  {review.valueKind === "dummy" && (
                    <span className="rounded-full bg-violet-100 px-2.5 py-1 text-[10px] font-semibold text-violet-700">DUMMY VALUE</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-8 rounded-2xl border border-hairline bg-paper p-6">
          <h2 className="text-lg font-semibold text-ink">Artifacts</h2>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th className="pb-3 font-medium">Artifact</th>
                  <th className="pb-3 font-medium">Type</th>
                  <th className="pb-3 font-medium">Size</th>
                  <th className="pb-3 font-medium">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {artifacts.map((artifact) => (
                  <tr key={artifact.id}>
                    <td className="py-3 pr-4">
                      <a
                        href={`${API_URL}/api/artifacts/${encodeURIComponent(artifact.id)}`}
                        className="font-mono text-xs text-signal hover:underline"
                      >
                        {artifact.key}
                      </a>
                    </td>
                    <td className="py-3 pr-4 text-muted">{artifact.contentType}</td>
                    <td className="py-3 pr-4 text-muted">{artifact.size.toLocaleString()} B</td>
                    <td className="py-3 text-muted">{time(artifact.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </section>
    </main>
  );
}
