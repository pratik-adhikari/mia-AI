"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import type { AcquiredSource, AgentProductWork, AgentResponse, EvidenceRecord } from "@/lib/types";
import { useAuthenticatedFetch } from "@/lib/use-authenticated-fetch";

const API_URL = process.env.NEXT_PUBLIC_MIA_API_URL ?? "";

function valueText(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value) ?? String(value);
}

function httpUrl(value: string | null | undefined) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

function liveVariantUrl(source: AcquiredSource | undefined, record: EvidenceRecord) {
  const base = httpUrl(source?.finalUrl ?? record.sourceUri);
  if (!base) return null;
  const variant = record.contextPath.at(-1);
  if (!source?.renderedHtml || !variant) return base;
  const document = new DOMParser().parseFromString(source.renderedHtml, "text/html");
  const anchor = Array.from(document.querySelectorAll<HTMLAnchorElement>("a[href]"))
    .find((item) => item.textContent?.trim() === variant);
  if (!anchor) return base;
  try {
    const linked = httpUrl(new URL(anchor.getAttribute("href") ?? "", base).href);
    return linked?.origin === base.origin ? linked : base;
  } catch {
    return base;
  }
}

function highlightUrl(url: URL | null, value: string) {
  if (!url || !value) return null;
  const linked = new URL(url);
  linked.hash = `:~:text=${encodeURIComponent(value)}`;
  return linked.href;
}

function capturedLines(source: AcquiredSource | undefined, record: EvidenceRecord) {
  if (!source) return null;
  const value = valueText(record.value);
  if (!value) return null;
  const lines = source.markdown.split(/\r?\n/);
  const variant = record.contextPath.at(-1)?.toLocaleLowerCase() ?? "";
  let bestIndex = -1;
  let bestScore = -1;
  let contextMatched = false;
  for (let index = 0; index < lines.length; index += 1) {
    if (!lines[index].includes(value) || lines[index].includes("http")) continue;
    const nearby = lines.slice(Math.max(0, index - 8), index + 1).join(" ").toLocaleLowerCase();
    const sameContext = Boolean(variant && nearby.includes(variant));
    const score =
      (lines[index].trim() === value ? 100 : 0) +
      (sameContext ? 20 : 0);
    if (score > bestScore) {
      bestIndex = index;
      bestScore = score;
      contextMatched = sameContext;
    }
  }
  if (bestIndex >= 0) {
    const start = Math.max(0, bestIndex - 5);
    const end = Math.min(lines.length, bestIndex + 5);
    return {
      lines: lines.slice(start, end),
      markedIndex: bestIndex - start,
      medium: "readable page text",
      contextMatched,
    };
  }
  const document = new DOMParser().parseFromString(source.renderedHtml, "text/html");
  document.querySelectorAll("script, style").forEach((element) => element.remove());
  const pageText = document.body.textContent ?? "";
  const htmlIndex = pageText.indexOf(value);
  if (htmlIndex < 0) return null;
  return {
    lines: [pageText.slice(Math.max(0, htmlIndex - 180), htmlIndex + value.length + 180)],
    markedIndex: 0,
    medium: "captured page text",
    contextMatched: Boolean(variant && pageText.slice(Math.max(0, htmlIndex - 200), htmlIndex).toLocaleLowerCase().includes(variant)),
  };
}

function MarkedLine({ line, value }: { line: string; value: string }) {
  const index = line.indexOf(value);
  if (index < 0) return <>{line}</>;
  return <>{line.slice(0, index)}<mark className="rounded bg-yellow-200 px-0.5 text-ink">{value}</mark>{line.slice(index + value.length)}</>;
}

export default function SourceVerifier() {
  const params = useSearchParams();
  const threadId = params.get("thread");
  const evidenceId = params.get("evidence");
  const authenticatedFetch = useAuthenticatedFetch();
  const requestKey = `${threadId ?? ""}:${evidenceId ?? ""}`;
  const [result, setResult] = useState<{
    key: string;
    product: AgentProductWork | null;
    error: string | null;
  } | null>(null);
  const current = result?.key === requestKey ? result : null;
  const missingIds = !threadId || !evidenceId;
  const product = current?.product ?? null;
  const error = missingIds ? "A thread and evidence ID are required." : current?.error ?? null;
  const loading = !missingIds && current === null;

  useEffect(() => {
    if (!threadId || !evidenceId) return;
    let cancelled = false;
    void authenticatedFetch(`${API_URL}/api/threads/${encodeURIComponent(threadId)}`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Saved source could not be loaded (${response.status}).`);
        return response.json() as Promise<AgentResponse>;
      })
      .then((response) => {
        if (cancelled) return;
        setResult({
          key: requestKey,
          product: response.currentProduct,
          error: response.currentProduct ? null : "No saved product work is available in this thread.",
        });
      })
      .catch((reason) => {
        if (!cancelled) setResult({
          key: requestKey,
          product: null,
          error: reason instanceof Error ? reason.message : "Saved source could not be loaded.",
        });
      });
    return () => { cancelled = true; };
  }, [authenticatedFetch, evidenceId, requestKey, threadId]);

  const record = product?.evidence.find((item) => item.id === evidenceId);
  const source = product?.acquiredSources.find((item) => item.contentSha256 === record?.sourceContentSha256);
  const value = record ? valueText(record.value) : "";
  const excerpt = record ? capturedLines(source, record) : null;
  const liveUrl = record ? liveVariantUrl(source, record) : null;
  const highlightedLiveUrl = highlightUrl(liveUrl, value);
  const related = product && record ? product.evidence.filter((item) =>
    item.id !== record.id &&
    item.sourceLabel === record.sourceLabel &&
    item.sourceContentSha256 === record.sourceContentSha256
  ) : [];

  return (
    <main className="mx-auto min-h-screen max-w-4xl px-5 py-8 text-ink">
      <a href={threadId ? `/workspace?thread=${encodeURIComponent(threadId)}` : "/workspace"} className="text-[12px] text-signal underline">← Back to workspace</a>
      <h1 className="mt-5 text-2xl font-semibold">Verify source fact</h1>
      {loading && <p className="mt-6 text-sm text-muted">Loading saved source…</p>}
      {error && <p className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</p>}
      {!loading && !error && !record && <p className="mt-6 text-sm text-muted">This evidence record is unavailable in the selected thread.</p>}
      {record && (
        <div className="mt-6 space-y-5">
          <section className="rounded-xl border border-hairline bg-paper p-5 shadow-sm">
            <p className="text-xs text-muted">{record.contextPath.join(" / ") || "Product source"}</p>
            <h2 className="mt-2 text-lg font-semibold">{record.sourceLabel ?? record.predicate}</h2>
            <p className="mt-2 text-xl font-semibold"><mark className="rounded bg-yellow-200 px-1 text-ink">{value}</mark>{record.unit ? ` ${record.unit}` : ""}</p>
            <p className="mt-3 text-xs text-muted">Evidence ID: {record.id}</p>
            <p className="mt-1 break-all text-xs text-muted">Captured source SHA-256: {record.sourceContentSha256}</p>
          </section>

          <section className="rounded-xl border border-hairline bg-paper p-5 shadow-sm">
            <h2 className="font-semibold">Saved source snapshot</h2>
            <p className="mt-1 text-xs text-muted">
              {excerpt
                ? `Exact value found in ${excerpt.medium} captured at ${source?.acquiredAt ?? record.acquiredAt}.${excerpt.contextMatched ? " The variant label also appears beside it." : " The variant association comes from the extracted source hierarchy."}`
                : "The exact value could not be located in the retained page text. The extractor excerpt alone does not verify it."}
            </p>
            {excerpt ? (
              <div className="mt-4 overflow-x-auto rounded-lg border border-hairline bg-mist/50 p-4 font-mono text-xs leading-6">
                {excerpt.lines.map((line, index) => (
                  <div key={index} className="min-h-6 whitespace-pre-wrap break-all">
                    {index === excerpt.markedIndex ? <MarkedLine line={line} value={value} /> : line}
                  </div>
                ))}
              </div>
            ) : record.sourceLocation.excerpt ? (
              <p className="mt-4 rounded-lg bg-mist p-4 font-mono text-xs">Extractor excerpt: {record.sourceLocation.excerpt}</p>
            ) : null}
            <div className="mt-4 flex flex-wrap gap-3 text-xs">
              {highlightedLiveUrl && <a href={highlightedLiveUrl} target="_blank" rel="noopener noreferrer" className="rounded-full bg-ink px-4 py-2 font-medium text-white">Open live {liveUrl?.href !== record.sourceUri ? "variant" : "page"} ↗</a>}
              {httpUrl(record.sourceUri) && <a href={record.sourceUri} target="_blank" rel="noopener noreferrer" className="rounded-full border border-hairline px-4 py-2 font-medium">Open captured page URL ↗</a>}
            </div>
            <p className="mt-3 text-xs text-muted">The live site may change. Browser text highlighting on the live site depends on its support for text fragments; the yellow mark above comes from the saved crawl.</p>
          </section>

          {related.length > 0 && (
            <section className="rounded-xl border border-hairline bg-paper p-5 shadow-sm">
              <h2 className="font-semibold">Other “{record.sourceLabel}” facts on this captured page</h2>
              <p className="mt-1 text-xs text-muted">The source lists separate variants. Their values are retained as separate evidence records.</p>
              <ul className="mt-3 divide-y divide-hairline text-sm">
                {related.map((item) => (
                  <li key={item.id} className="flex flex-wrap justify-between gap-2 py-2">
                    <span>{item.contextPath.join(" / ") || "Product"}</span>
                    <a href={`/workspace/source?thread=${encodeURIComponent(threadId ?? "")}&evidence=${encodeURIComponent(item.id)}`} className="font-mono text-signal underline">{valueText(item.value)} ↗</a>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </main>
  );
}
