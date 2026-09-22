"use client";

import { useMemo, useState } from "react";
import type { WorkspaceArtifact } from "@/lib/types";
import { useAuthenticatedFetch } from "@/lib/use-authenticated-fetch";

const GROUP_LABELS: Record<string, string> = {
  extraction: "Seed extraction",
  sources: "Raw sources",
  research: "Deep research",
  evidence: "Evidence",
  images: "Images",
  documents: "Documents",
  mapping: "Mappings",
  coverage: "Coverage",
  review: "Reviews",
  aas: "AAS",
  dpp: "DPP",
  background: "Background jobs",
  trace: "Trace",
  export: "Exports",
};

export function WorkspaceExplorer({
  apiUrl,
  threadId,
  artifacts,
  error,
}: {
  apiUrl: string;
  threadId: string | null;
  artifacts: WorkspaceArtifact[];
  error?: string | null;
}) {
  const authenticatedFetch = useAuthenticatedFetch();
  const [selected, setSelected] = useState<WorkspaceArtifact | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const groups = useMemo(() => {
    const result = new Map<string, WorkspaceArtifact[]>();
    for (const artifact of artifacts) {
      const root = artifact.relativePath.split("/")[0] || artifact.kind;
      const label = GROUP_LABELS[root] ?? GROUP_LABELS[artifact.kind] ?? root;
      result.set(label, [...(result.get(label) ?? []), artifact]);
    }
    return result;
  }, [artifacts]);

  async function open(artifact: WorkspaceArtifact) {
    if (!threadId) return;
    setSelected(artifact);
    setPreview(null);
    if (
      !artifact.contentType.includes("json") &&
      !artifact.contentType.startsWith("text/")
    ) {
      return;
    }
    setLoading(true);
    try {
      const response = await authenticatedFetch(
        `${apiUrl}/api/workspaces/${encodeURIComponent(
          threadId
        )}/artifacts/${artifact.id}`
      );
      if (!response.ok) {
        setPreview(
          `Unable to load artifact: ${response.status} ${await response.text()}`
        );
        return;
      }
      const text = await response.text();
      if (artifact.contentType.includes("json")) {
        try {
          setPreview(JSON.stringify(JSON.parse(text), null, 2));
        } catch {
          setPreview(text);
        }
      } else {
        setPreview(text);
      }
    } finally {
      setLoading(false);
    }
  }

  async function download(url: string, filename: string) {
    setDownloadError(null);
    const response = await authenticatedFetch(url);
    if (!response.ok) {
      setDownloadError(
        `Download failed: ${response.status} ${await response.text()}`
      );
      return;
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(objectUrl);
  }

  if (!threadId) {
    return (
      <p className="pt-16 text-center text-[13px] text-muted">
        Start a conversation to create a durable workspace.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-[12px] text-red-700">
          Workspace API error: {error}
        </div>
      )}
      {downloadError && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-[12px] text-red-700">
          {downloadError}
        </div>
      )}
      {artifacts.length === 0 ? (
        <p className="pt-12 text-center text-[13px] text-muted">
          No workspace artifacts are visible yet. If extraction has completed,
          check the error above.
        </p>
      ) : (
        <div className="grid gap-5 md:grid-cols-[280px_minmax(0,1fr)]">
          <div className="space-y-2">
            {[...groups.entries()].map(([group, items]) => (
              <details
                key={group}
                open
                className="rounded-lg border border-hairline bg-paper p-2"
              >
                <summary className="cursor-pointer px-2 py-1 text-[12px] font-semibold text-ink">
                  {group} <span className="text-muted">{items.length}</span>
                </summary>
                <div className="mt-1 space-y-0.5">
                  {items.map((artifact) => (
                    <button
                      key={artifact.id}
                      onClick={() => void open(artifact)}
                      title={artifact.relativePath}
                      className={`block w-full rounded px-2 py-1.5 text-left font-mono text-[11px] ${
                        selected?.id === artifact.id
                          ? "bg-signalDim text-signal"
                          : "text-muted hover:bg-mist"
                      }`}
                    >
                      <span className="block break-all">
                        {artifact.relativePath}
                      </span>
                    </button>
                  ))}
                </div>
              </details>
            ))}
            <button
              onClick={() =>
                void download(
                  `${apiUrl}/api/workspaces/${encodeURIComponent(
                    threadId
                  )}/download`,
                  `${threadId}-workspace.zip`
                )
              }
              className="block w-full rounded-full bg-ink px-4 py-2 text-center text-[12px] font-medium text-white"
            >
              Download workspace ZIP
            </button>
          </div>

          <section className="min-h-80 rounded-xl border border-hairline bg-paper p-4">
            {!selected && (
              <p className="text-[13px] text-muted">Select a file to preview it.</p>
            )}
            {selected && (
              <>
                <div className="flex items-start justify-between gap-3 border-b border-hairline pb-3">
                  <div>
                    <h2 className="break-all font-mono text-[13px] font-semibold">
                      {selected.relativePath}
                    </h2>
                    <p className="mt-1 text-[11px] text-muted">
                      {selected.contentType} · {selected.size.toLocaleString()} bytes
                    </p>
                  </div>
                  <button
                    onClick={() =>
                      void download(
                        `${apiUrl}/api/workspaces/${encodeURIComponent(
                          threadId
                        )}/artifacts/${selected.id}?download=true`,
                        selected.name
                      )
                    }
                    className="rounded-full border border-hairline px-3 py-1.5 text-[11px] font-medium"
                  >
                    Download
                  </button>
                </div>
                {loading && (
                  <p className="mt-4 text-[12px] text-muted">Loading preview…</p>
                )}
                {preview !== null && (
                  <pre className="mt-4 max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-mist p-4 font-mono text-[11px] leading-relaxed">
                    {preview}
                  </pre>
                )}
                {!loading && preview === null && (
                  <p className="mt-4 text-[12px] text-muted">
                    Binary preview is unavailable. Download this file to inspect it.
                  </p>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
