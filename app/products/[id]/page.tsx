"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import type { ProductDetail, ProductRun } from "@/lib/types";

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
  const params = useParams<{ id: string }>();
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = async () => {
      try {
        const response = await fetch(`${API_URL}/api/products/${encodeURIComponent(params.id)}`);
        if (!response.ok) throw new Error(`Backend returned ${response.status}`);
        setDetail((await response.json()) as ProductDetail);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not load product");
      }
    };
    void load();
  }, [params.id]);

  if (error) {
    return <main className="min-h-screen bg-mist p-8 text-sm text-red-700">{error}</main>;
  }
  if (!detail) {
    return <main className="min-h-screen bg-mist p-8 text-sm text-muted">Loading product…</main>;
  }

  const { product, runs, dppVersions, artifacts } = detail;
  const title = product.name ?? product.manufacturerProductId ?? "Unnamed product";

  return (
    <main className="min-h-screen bg-mist">
      <header className="border-b border-hairline bg-paper">
        <div className="mx-auto flex h-14 max-w-shell items-center justify-between px-6">
          <Link href="/products" className="text-[13px] text-muted hover:text-ink">
            ← Product library
          </Link>
          <Link href="/workspace" className="rounded-full bg-ink px-3 py-1.5 text-[12px] text-white">
            Open workspace
          </Link>
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

        <div className="mt-8 grid gap-8 lg:grid-cols-2">
          <section className="rounded-2xl border border-hairline bg-paper p-6">
            <h2 className="text-lg font-semibold text-ink">DPP versions</h2>
            <div className="mt-4 space-y-3">
              {dppVersions.length === 0 && <p className="text-sm text-muted">No completed DPP yet.</p>}
              {dppVersions.map((version) => (
                <div key={version.id} className="rounded-xl border border-hairline p-4">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <p className="font-medium text-ink">Version {version.version}</p>
                      <p className="mt-1 text-xs text-muted">{time(version.createdAt)}</p>
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
