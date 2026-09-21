"use client";

import { useMemo, useState } from "react";
import type { WorkspaceArtifact } from "@/lib/types";
import { useAuthenticatedFetch } from "@/lib/use-authenticated-fetch";

const GROUP_LABELS: Record<string, string> = {
  search: "Sources", source: "Sources", raw: "Raw", evidence: "Evidence",
  mapping: "Mappings", coverage: "Coverage", review: "Reviews",
  aas: "AAS", validation: "AAS", trace: "Trace", export: "Exports",
};

export function WorkspaceExplorer({ apiUrl, threadId, artifacts }: {
  apiUrl: string; threadId: string | null; artifacts: WorkspaceArtifact[];
}) {
  const authenticatedFetch = useAuthenticatedFetch();
  const [selected, setSelected] = useState<WorkspaceArtifact | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const groups = useMemo(() => {
    const result = new Map<string, WorkspaceArtifact[]>();
    for (const artifact of artifacts) {
      const label = GROUP_LABELS[artifact.kind] ?? artifact.kind;
      result.set(label, [...(result.get(label) ?? []), artifact]);
    }
    return result;
  }, [artifacts]);

  async function open(artifact: WorkspaceArtifact) {
    if (!threadId) return;
    setSelected(artifact);
    setPreview(null);
    if (!artifact.contentType.includes("json") && !artifact.contentType.startsWith("text/")) return;
    setLoading(true);
    try {
      const response = await authenticatedFetch(`${apiUrl}/api/workspaces/${encodeURIComponent(threadId)}/artifacts/${artifact.id}`);
      const text = await response.text();
      setPreview(artifact.contentType.includes("json") ? JSON.stringify(JSON.parse(text), null, 2) : text);
    } finally {
      setLoading(false);
    }
  }

  if (!threadId || artifacts.length === 0) {
    return <p className="pt-16 text-center text-[13px] text-muted">Generated files will appear here as MIA works.</p>;
  }
  const artifactUrl = selected
    ? `${apiUrl}/api/workspaces/${encodeURIComponent(threadId)}/artifacts/${selected.id}`
    : "";
  return (
    <div className="grid gap-5 md:grid-cols-[240px_minmax(0,1fr)]">
      <div className="space-y-2">
        {[...groups.entries()].map(([group, items]) => (
          <details key={group} open className="rounded-lg border border-hairline bg-paper p-2">
            <summary className="cursor-pointer px-2 py-1 text-[12px] font-semibold text-ink">{group} <span className="text-muted">{items.length}</span></summary>
            <div className="mt-1 space-y-0.5">
              {items.map((artifact) => (
                <button key={artifact.id} onClick={() => void open(artifact)} className={`block w-full truncate rounded px-2 py-1.5 text-left font-mono text-[11px] ${selected?.id === artifact.id ? "bg-signalDim text-signal" : "text-muted hover:bg-mist"}`}>
                  {artifact.name}
                </button>
              ))}
            </div>
          </details>
        ))}
        <a href={`${apiUrl}/api/workspaces/${encodeURIComponent(threadId)}/download`} className="block rounded-full bg-ink px-4 py-2 text-center text-[12px] font-medium text-white">Download workspace ZIP</a>
      </div>
      <section className="min-h-80 rounded-xl border border-hairline bg-paper p-4">
        {!selected && <p className="text-[13px] text-muted">Select a file to preview it.</p>}
        {selected && (
          <>
            <div className="flex items-start justify-between gap-3 border-b border-hairline pb-3">
              <div><h2 className="font-mono text-[13px] font-semibold">{selected.name}</h2><p className="mt-1 text-[11px] text-muted">{selected.contentType} · {selected.size.toLocaleString()} bytes</p></div>
              <a href={`${artifactUrl}?download=true`} className="rounded-full border border-hairline px-3 py-1.5 text-[11px] font-medium">Download</a>
            </div>
            {loading && <p className="mt-4 text-[12px] text-muted">Loading preview…</p>}
            {preview !== null && <pre className="mt-4 max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-mist p-4 font-mono text-[11px] leading-relaxed">{preview}</pre>}
            {!loading && preview === null && <p className="mt-4 text-[12px] text-muted">Binary preview is unavailable. Download this file to inspect it.</p>}
          </>
        )}
      </section>
    </div>
  );
}
