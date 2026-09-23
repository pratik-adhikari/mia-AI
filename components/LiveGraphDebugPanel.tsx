"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type cytoscape from "cytoscape";
import type { useAuthenticatedFetch } from "@/lib/use-authenticated-fetch";

type AuthenticatedFetch = ReturnType<typeof useAuthenticatedFetch>;
type GraphNode = { id: string; name: string; type: string | null };
type GraphEdge = {
  source: string;
  target: string;
  conditional: boolean;
  label: string;
};
type GraphTopology = { nodes: GraphNode[]; edges: GraphEdge[] };
type NodeStatus = "running" | "completed" | "waiting" | "failed";
type DebugState = {
  nodes: GraphTopology["nodes"];
  edges: GraphTopology["edges"];
  statuses: Record<string, NodeStatus>;
  activeNode: string | null;
  runStatus: string;
  error: string | null;
};

const EMPTY: DebugState = {
  nodes: [],
  edges: [],
  statuses: {},
  activeNode: null,
  runStatus: "connecting",
  error: null,
};

export function LiveGraphDebugPanel({
  threadId,
  busy,
  onClose,
  authenticatedFetch,
}: {
  threadId: string | null;
  busy: boolean;
  onClose: () => void;
  authenticatedFetch: AuthenticatedFetch;
}) {
  const [state, setState] = useState<DebugState>(EMPTY);
  const [layoutReady, setLayoutReady] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const directionRef = useRef<"RIGHT" | "DOWN">("RIGHT");
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    setState({ ...EMPTY, runStatus: busy ? "connecting" : "loading" });

    async function connect() {
      try {
        const url = new URL(
          `${process.env.NEXT_PUBLIC_MIA_API_URL ?? ""}/api/debug/graph/stream`,
          window.location.origin,
        );
        if (threadId) url.searchParams.set("thread_id", threadId);
        const response = await authenticatedFetch(
          url.toString(),
          { headers: { Accept: "text/event-stream" }, signal: controller.signal },
        );
        if (!response.ok || !response.body) {
          throw new Error(response.status === 404 ? "Live graph is available in local development." : `Graph stream returned ${response.status}`);
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!cancelled) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true }).replaceAll("\r", "");
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) consumeFrame(frame);
        }
      } catch (error) {
        if (!cancelled && !(error instanceof DOMException && error.name === "AbortError")) {
          setState((current) => ({
            ...current,
            runStatus: "disconnected",
            error: error instanceof Error ? error.message : "Graph stream disconnected.",
          }));
        }
      }
    }

    function consumeFrame(frame: string) {
      let eventName = "message";
      const data: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).trim());
      }
      if (!data.length) return;
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(data.join("\n")) as Record<string, unknown>;
      } catch {
        return;
      }
      if (eventName === "topology") {
        const topology = payload as unknown as GraphTopology;
        setState((current) => ({ ...current, ...topology }));
      } else if (eventName === "run") {
        setState((current) => ({
          ...current,
          runStatus: typeof payload.status === "string" ? payload.status : current.runStatus,
          error: null,
        }));
      } else if (eventName === "node") {
        const name = String(payload.nodeName ?? "");
        const ns = String(payload.namespace ?? "");
        setState((current) => {
          const nodeId = current.nodes.some((node) => node.id === payload.nodeId)
            ? String(payload.nodeId)
            : current.nodes.find((node) => node.id === `${ns ? `${ns}:` : ""}${name}`)?.id ??
              current.nodes.find((node) => node.id === name || node.id.endsWith(`:${name}`))?.id;
          if (!nodeId) return current;
          const status = payload.status as NodeStatus;
          return {
            ...current,
            activeNode: status === "running" ? nodeId : current.activeNode === nodeId ? null : current.activeNode,
            statuses: { ...current.statuses, [nodeId]: status },
          };
        });
      } else if (eventName === "error") {
        setState((current) => ({
          ...current,
          runStatus: "disconnected",
          error: `Graph stream failed (${String(payload.errorType ?? "unknown error")}).`,
        }));
      }
    }

    void connect();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [authenticatedFetch, busy, threadId]);

  const applyNodeStates = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().removeClass("running completed waiting failed");
    for (const [id, status] of Object.entries(state.statuses)) {
      cy.getElementById(id).addClass(status);
    }
    cy.resize();
  }, [state.statuses]);

  useEffect(() => {
    let destroyed = false;
    async function createGraph() {
      if (!containerRef.current || state.nodes.length === 0) return;
      const [cytoscapeModule, elkModule] = await Promise.all([
        import("cytoscape"),
        import("cytoscape-elk"),
      ]);
      if (destroyed || !containerRef.current) return;
      const cytoscape = cytoscapeModule.default;
      cytoscape.use(elkModule.default);
      cyRef.current?.destroy();
      directionRef.current = layoutDirection(containerRef.current);
      cyRef.current = cytoscape({
        container: containerRef.current,
        layout: { name: "preset" },
        elements: [
          ...state.nodes.map((node) => ({
            data: {
              id: node.id,
              label: node.name.replaceAll(":", " · ") || node.id,
              category: category(node.name || node.id),
            },
          })),
          ...state.edges.map((edge, index) => ({
            data: {
              id: `edge-${index}`,
              source: edge.source,
              target: edge.target,
              label: edge.label,
              conditional: edge.conditional,
            },
            classes: edge.conditional ? "conditional" : "",
          })),
        ],
        style: [
          {
            selector: "node",
            style: {
              label: "data(label)",
              "font-family": "Inter, sans-serif",
              "font-size": 13,
              color: "#172033",
              "text-wrap": "wrap",
              "text-max-width": "190px",
              width: "label",
              height: "label",
              padding: "14px",
              shape: "round-rectangle",
              "background-color": "#e8edf5",
              "border-width": 1,
              "border-color": "#aab6c8",
            },
          },
          { selector: 'node[category = "agent"]', style: { "background-color": "#e8ddff", "border-color": "#8762d6" } },
          { selector: 'node[category = "human"]', style: { "background-color": "#d9efff", "border-color": "#3281bd" } },
          { selector: 'node[category = "crawl"]', style: { "background-color": "#d5f4ef", "border-color": "#198b78" } },
          { selector: 'node[category = "output"]', style: { "background-color": "#dce9ff", "border-color": "#3e6fc2" } },
          {
            selector: "node.running",
            style: {
              "background-color": "#fff16a",
              "border-color": "#ff6b00",
              "border-width": 4,
              "underlay-color": "#ffbd00",
              "underlay-opacity": 0.95,
              "underlay-padding": "12px",
            },
          },
          { selector: "node.completed", style: { "background-color": "#c7f9d4", "border-color": "#159447", "border-width": 2 } },
          { selector: "node.waiting", style: { "background-color": "#ffe5ad", "border-color": "#d58500", "border-width": 3 } },
          { selector: "node.failed", style: { "background-color": "#ffd9dd", "border-color": "#cf3347", "border-width": 3 } },
          {
            selector: "edge",
            style: {
              width: 1.5,
              "line-color": "#a8b3c4",
              "target-arrow-color": "#8e9bae",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
              label: "data(label)",
              "font-size": 10,
              color: "#667085",
              "text-background-color": "#fff",
              "text-background-opacity": 0.9,
              "text-background-padding": "2px",
            },
          },
          { selector: "edge.conditional", style: { "line-style": "dashed" } },
        ],
      });
      cyRef.current.one("layoutstop", () => setLayoutReady(true));
      cyRef.current.layout(elkLayout(directionRef.current)).run();
      cyRef.current?.nodes().removeClass("running completed waiting failed");
      for (const [id, status] of Object.entries(stateRef.current.statuses)) {
        cyRef.current?.getElementById(id).addClass(status);
      }
    }
    setLayoutReady(false);
    void createGraph();
    return () => {
      destroyed = true;
      cyRef.current?.destroy();
      cyRef.current = null;
    };
  }, [state.edges, state.nodes]);

  useEffect(() => {
    const container = containerRef.current;
    const cy = cyRef.current;
    if (!container || !cy || !layoutReady) return;
    const observer = new ResizeObserver(() => {
      cy.resize();
      const direction = layoutDirection(container);
      if (direction !== directionRef.current) {
        directionRef.current = direction;
        cy.layout(elkLayout(direction)).run();
      }
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [layoutReady]);

  useEffect(() => applyNodeStates(), [applyNodeStates]);

  const activeName = state.nodes.find((node) => node.id === state.activeNode)?.name.replaceAll(":", " · ");
  const waitingName = state.nodes.find((node) => state.statuses[node.id] === "waiting")?.name.replaceAll(":", " · ");
  const failedName = state.nodes.find((node) => state.statuses[node.id] === "failed")?.name.replaceAll(":", " · ");
  const activityLabel = activeName
    ? `Running · ${activeName}`
    : state.runStatus === "waiting" && waitingName
      ? `Waiting · ${waitingName}`
      : state.runStatus === "failed" && failedName
        ? `Failed · ${failedName}`
        : state.runStatus === "completed"
          ? "Last run completed"
          : state.runStatus === "idle"
            ? "No backend graph run yet"
            : "Compiled LangGraph topology · live task events";
  return (
    <>
      <button aria-label="Close workflow debug panel" onClick={onClose} className="fixed inset-0 z-40 cursor-default bg-slate-950/25" />
      <aside className="fixed inset-y-14 right-0 z-50 flex w-[min(1280px,98vw)] flex-col border-l border-slate-200 bg-white shadow-2xl">
        <header className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${state.runStatus === "running" ? "animate-pulse bg-amber-500" : state.runStatus === "failed" ? "bg-red-500" : "bg-slate-400"}`} />
              <h2 className="text-sm font-semibold text-slate-900">Workflow debugger</h2>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-slate-600">{state.runStatus}</span>
            </div>
            <p className="mt-1 text-xs text-slate-500">
              {activityLabel}
            </p>
          </div>
          <button onClick={onClose} className="rounded-md px-2 py-1 text-sm text-slate-500 hover:bg-slate-100 hover:text-slate-900">Close</button>
        </header>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-100 px-5 py-3 text-[10px] text-slate-600">
          <Legend color="bg-violet-300" label="Agent / model" />
          <Legend color="bg-sky-300" label="Human input" />
          <Legend color="bg-teal-300" label="Extraction" />
          <Legend color="bg-blue-300" label="AAS output" />
          <Legend color="bg-yellow-300 ring-2 ring-orange-500" label="Active now" />
        </div>
        <div className="relative min-h-0 flex-1 bg-[radial-gradient(#dce3ed_0.8px,transparent_0.8px)] [background-size:18px_18px]">
          <div ref={containerRef} className="absolute inset-0" />
          {!layoutReady && state.nodes.length > 0 && <div className="absolute inset-0 grid place-items-center text-xs text-slate-500">Laying out compiled graph…</div>}
          {state.nodes.length === 0 && <div className="absolute inset-0 grid place-items-center px-8 text-center text-xs text-slate-500">{state.error ?? "Connecting to the local workflow…"}</div>}
          {layoutReady && (
            <div className="absolute bottom-4 right-4 flex gap-1 rounded-lg border border-slate-200 bg-white/95 p-1 shadow-sm">
              <button onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 1.2)} className="h-7 w-7 rounded text-slate-700 hover:bg-slate-100" aria-label="Zoom in">+</button>
              <button onClick={() => cyRef.current?.fit(undefined, 28)} className="h-7 rounded px-2 text-[10px] text-slate-700 hover:bg-slate-100">Fit</button>
              <button onClick={() => cyRef.current?.zoom(cyRef.current.zoom() / 1.2)} className="h-7 w-7 rounded text-slate-700 hover:bg-slate-100" aria-label="Zoom out">−</button>
            </div>
          )}
        </div>
        <footer className="border-t border-slate-200 bg-slate-50 px-5 py-3 text-[11px] leading-relaxed text-slate-500">
          {state.error ?? "Deep-research worker batches run outside this LangGraph and are not shown as graph nodes."}
        </footer>
      </aside>
    </>
  );
}

function category(name: string): string {
  const node = name.split(":").at(-1)?.toLowerCase() ?? name.toLowerCase();
  if (node.startsWith("human_")) return "human";
  if (node.includes("extract")) return "crawl";
  if (["discover_product", "semantic_mapping", "research"].includes(node)) return "agent";
  if (node.includes("aas") || node.includes("store_result")) return "output";
  return "workflow";
}

function layoutDirection(container: HTMLDivElement): "RIGHT" | "DOWN" {
  const { width, height } = container.getBoundingClientRect();
  return width >= height * 1.15 ? "RIGHT" : "DOWN";
}

function elkLayout(direction: "RIGHT" | "DOWN"): cytoscape.LayoutOptions {
  return {
    name: "elk",
    fit: true,
    padding: 48,
    nodeDimensionsIncludeLabels: true,
    elk: {
      algorithm: "layered",
      "elk.direction": direction,
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.spacing.nodeNode": "36",
      "elk.layered.spacing.nodeNodeBetweenLayers": "58",
    },
  } as cytoscape.LayoutOptions;
}

function Legend({ color, label }: { color: string; label: string }) {
  return <span className="inline-flex items-center gap-1.5"><span className={`h-2.5 w-2.5 rounded-sm ${color}`} />{label}</span>;
}
