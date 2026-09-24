"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { UserButton } from "@clerk/nextjs";
import type {
  AgentTraceEvent,
  AgentReviewDecision,
  AgentResponse,
  CompanyCandidate,
  ChatMessage,
  CoverageReport,
  DppPackage,
  EvidenceRecord,
  FieldMapping,
  HumanRequest,
  MappingResult,
  Requirement,
  SemanticReviewItem,
  ProductCandidate,
  WorkspaceArtifact,
  MappingKnowledgeEntry,
  BackgroundJob,
  ThreadRecord,
  ProductDetail,
} from "@/lib/types";
import { CoveragePanel } from "@/components/CoveragePanel";
import { EvidencePanel } from "@/components/EvidencePanel";
import { MappingReviewRow, MappingRow } from "@/components/MappingRow";
import { DppView } from "@/components/DppView";
import { AgentActivity } from "@/components/AgentActivity";
import { ChatMarkdown } from "@/components/ChatMarkdown";
import { LiveActivity } from "@/components/LiveActivity";
import { WorkspaceExplorer } from "@/components/WorkspaceExplorer";
import { IntegrationGraph } from "@/components/IntegrationGraph";
import { LiveGraphDebugPanel } from "@/components/LiveGraphDebugPanel";
import { useAuthenticatedFetch } from "@/lib/use-authenticated-fetch";
import { useAuthenticationEnabled } from "@/lib/app-providers";

const API_URL = process.env.NEXT_PUBLIC_MIA_API_URL ?? "";
type WorkspaceTab =
  | "mappings"
  | "evidence"
  | "coverage"
  | "process"
  | "data"
  | "graph";

const SAMPLES = [
  {
    label: "Pressure gauge",
    text: "Create a DPP for our AFRISO pressure gauge, model RF100-16, serial number 2024-8871, built 2024 at our Güglingen plant, protection class IP65, measuring range 0-16 bar, material number 63820.",
  },
  {
    label: "Rotary table",
    text: "DPP for a FIBRO rotary indexing table, type FB-320-NC, serial 887201-B, year of construction 2023, made in Weinsberg Germany, IP54, article nr 2470.12.320.",
  },
  {
    label: "Sparse data",
    text: "SCHUNK clamping module, order code JGZ-100-1, 2022.",
  },
];

export default function Workspace() {
  const authenticationEnabled = useAuthenticationEnabled();
  const authenticatedFetch = useAuthenticatedFetch();
  const searchParams = useSearchParams();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [threads, setThreads] = useState<ThreadRecord[]>([]);
  const [input, setInput] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [mappings, setMappings] = useState<FieldMapping[]>([]);
  const [productName, setProductName] = useState("");
  const [dpp, setDpp] = useState<DppPackage | null>(null);
  const [evidence, setEvidence] = useState<EvidenceRecord[]>([]);
  const [mappingResult, setMappingResult] = useState<MappingResult | null>(null);
  const [coverageReport, setCoverageReport] = useState<CoverageReport | null>(null);
  const [mappingKnowledge, setMappingKnowledge] = useState<MappingKnowledgeEntry[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [agentStatus, setAgentStatus] = useState<AgentResponse["status"]>("completed");
  const [companyCandidates, setCompanyCandidates] = useState<CompanyCandidate[]>([]);
  const [productCandidates, setProductCandidates] = useState<ProductCandidate[]>([]);
  const [currentProductId, setCurrentProductId] = useState<string | null>(null);
  const [mappingCycleId, setMappingCycleId] = useState<string | null>(null);
  const [agentActivity, setAgentActivity] = useState<AgentTraceEvent[]>([]);
  const [artifacts, setArtifacts] = useState<WorkspaceArtifact[]>([]);
  const [backgroundJob, setBackgroundJob] = useState<BackgroundJob | null>(null);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [restoredRunNotice, setRestoredRunNotice] = useState<string | null>(null);
  const [researchDispatchError, setResearchDispatchError] = useState<string | null>(null);
  const [semanticReview, setSemanticReview] = useState<SemanticReviewItem[]>([]);
  const [humanRequest, setHumanRequest] = useState<HumanRequest | null>(null);
  const [humanValue, setHumanValue] = useState("");
  const [reviewDecisions, setReviewDecisions] = useState<
    Record<string, AgentReviewDecision>
  >({});
  const [tab, setTab] = useState<WorkspaceTab>("mappings");
  const [debugPanelOpen, setDebugPanelOpen] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const restoredThreadRef = useRef<string | null>(null);
  const productActionRef = useRef<string | null>(null);

  const mergeActivity = useCallback((events: AgentTraceEvent[]) => {
    setAgentActivity((previous) => {
      const known = new Set(previous.map((event) => event.id));
      return [...previous, ...events.filter((event) => !known.has(event.id))];
    });
  }, []);

  const retryBackgroundJob = useCallback(async (jobId: string) => {
    const response = await authenticatedFetch("/research/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jobId }),
    });
    if (response.ok) {
      setResearchDispatchError(null);
      return;
    }
    const detail = await response.text();
    setResearchDispatchError(detail || `Research dispatcher returned ${response.status}`);
  }, [authenticatedFetch]);

  const refreshArtifacts = useCallback(async (activeThreadId: string) => {
    try {
      const response = await authenticatedFetch(
        `${API_URL}/api/workspaces/${encodeURIComponent(activeThreadId)}/artifacts`
      );
      if (!response.ok) {
        const detail = await response.text();
        setWorkspaceError(detail || `Workspace API returned ${response.status}`);
        return;
      }
      setArtifacts((await response.json()) as WorkspaceArtifact[]);
      setWorkspaceError(null);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "Workspace API is unavailable");
    }
  }, [authenticatedFetch]);

  const refreshBackgroundJobs = useCallback(async (activeThreadId: string) => {
    const response = await authenticatedFetch(
      `${API_URL}/api/threads/${encodeURIComponent(activeThreadId)}/background-jobs`
    );
    if (!response.ok) return;
    const jobs = (await response.json()) as BackgroundJob[];
    setBackgroundJob(jobs.at(-1) ?? null);
  }, [authenticatedFetch]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy, agentActivity]);

  useEffect(() => {
    const load = async () => {
      try {
        const response = await authenticatedFetch(`${API_URL}/api/mapping-knowledge`);
        if (response.ok) {
          setMappingKnowledge((await response.json()) as MappingKnowledgeEntry[]);
        }
      } catch {
        /* The empty state remains usable while the backend is unavailable. */
      }
    };
    void load();
  }, [authenticatedFetch]);

  useEffect(() => {
    const loadThreads = async () => {
      const response = await authenticatedFetch(`${API_URL}/api/threads`);
      if (response.ok) setThreads((await response.json()) as ThreadRecord[]);
    };
    void loadThreads();
  }, [authenticatedFetch]);

  useEffect(() => {
    const requestedThread = searchParams.get("thread");
    if (!requestedThread || restoredThreadRef.current === requestedThread) return;
    restoredThreadRef.current = requestedThread;
    void openThread(requestedThread);
  }, [searchParams]);

  useEffect(() => {
    const productId = searchParams.get("product");
    if (!productId || searchParams.get("thread")) return;
    const action = searchParams.get("action") === "refresh" ? "refresh" : "continue";
    const key = `${productId}:${action}`;
    if (productActionRef.current === key) return;
    productActionRef.current = key;

    const start = async () => {
      const response = await authenticatedFetch(
        `${API_URL}/api/products/${encodeURIComponent(productId)}`
      );
      if (!response.ok) {
        setWorkspaceError("The saved product could not be loaded.");
        return;
      }
      const detail = (await response.json()) as ProductDetail;
      await send(
        `Import product website: ${detail.product.canonicalUrl}`,
        { refreshRequested: action === "refresh" }
      );
    };
    void start();
  }, [searchParams, authenticatedFetch]);

  useEffect(() => {
    const researchActive = backgroundJob?.status === "queued" || backgroundJob?.status === "running";
    if ((!busy && !researchActive) || !threadId) return;
    let cancelled = false;
    const poll = async () => {
      const [traceResponse] = await Promise.all([
        authenticatedFetch(`${API_URL}/api/workspaces/${encodeURIComponent(threadId)}/trace`),
        refreshArtifacts(threadId),
        refreshBackgroundJobs(threadId),
      ]);
      if (!cancelled && traceResponse.ok) {
        mergeActivity((await traceResponse.json()) as AgentTraceEvent[]);
      }
    };
    void poll();
    const interval = window.setInterval(() => void poll(), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [
    busy,
    backgroundJob?.status,
    threadId,
    mergeActivity,
    authenticatedFetch,
    refreshArtifacts,
    refreshBackgroundJobs,
  ]);

  function activeThread(): string {
    const active = threadId ?? `thread-${crypto.randomUUID()}`;
    if (!threadId) setThreadId(active);
    return active;
  }

  async function openThread(selectedThreadId: string) {
    setThreadId(selectedThreadId);
    const [messagesResponse, stateResponse] = await Promise.all([
      authenticatedFetch(
        `${API_URL}/api/threads/${encodeURIComponent(selectedThreadId)}/messages`
      ),
      authenticatedFetch(
        `${API_URL}/api/threads/${encodeURIComponent(selectedThreadId)}`
      ),
    ]);
    if (messagesResponse.ok) setMessages((await messagesResponse.json()) as ChatMessage[]);
    if (stateResponse.ok) {
      const state = (await stateResponse.json()) as AgentResponse;
      applyAgentResponse(state);
      setRestoredRunNotice(state.status === "failed" ? state.reply : null);
    }
    void refreshArtifacts(selectedThreadId);
    void refreshBackgroundJobs(selectedThreadId);
  }

  async function deleteThread(selectedThreadId: string) {
    if (!window.confirm("Delete this chat from Chat History? Product evidence and mapping audit data will remain reusable.")) return;
    const response = await authenticatedFetch(
      `${API_URL}/api/threads/${encodeURIComponent(selectedThreadId)}`,
      { method: "DELETE" }
    );
    if (!response.ok) return;
    setThreads((previous) => previous.filter((item) => item.id !== selectedThreadId));
    if (threadId === selectedThreadId) {
      setThreadId(null);
      setMessages([]);
      setSemanticReview([]);
      setHumanRequest(null);
      setRestoredRunNotice(null);
    }
  }

  async function send(
    text: string,
    options: { refreshRequested?: boolean } = {}
  ) {
    const t = text.trim();
    if (!t || busy) return;

    const next: ChatMessage[] = [...messages, { role: "user", content: t }];
    setMessages(next);
    setRestoredRunNotice(null);
    setInput("");
    const activeThreadId = activeThread();
    setBusy(true);

    try {
      const res = await authenticatedFetch("/agent/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          threadId: activeThreadId,
          message: t,
          refreshRequested: options.refreshRequested ?? false,
        }),
      });
      const body = (await res.json()) as AgentResponse | { detail?: string };
      if (!res.ok) {
        throw new Error("detail" in body && body.detail ? body.detail : `Python backend returned ${res.status}`);
      }
      const data = body as AgentResponse;
      const redirectedToExistingThread = data.threadId !== activeThreadId;
      applyAgentResponse(data);

      if (redirectedToExistingThread) {
        await openThread(data.threadId);
      } else {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: data.reply },
        ]);
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            "That request didn't go through. Check your connection and send it again.",
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  async function ingestWebsite() {
    const url = websiteUrl.trim();
    if (!url || busy || semanticReview.length > 0) return;
    const activeThreadId = activeThread();
    setBusy(true);
    setMessages((previous) => [
      ...previous,
      { role: "user", content: `Import product website: ${url}` },
    ]);

    try {
      const response = await authenticatedFetch("/agent/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          threadId: activeThreadId,
          message: `Import product website: ${url}`,
          refreshRequested: false,
        }),
      });
      const responseText = await response.text();
      let body: unknown;
      try {
        body = JSON.parse(responseText) as unknown;
      } catch {
        body = { detail: responseText || `Python backend returned ${response.status}` };
      }
      if (!response.ok) {
        const detail =
          typeof body === "object" &&
          body !== null &&
          "detail" in body &&
          typeof body.detail === "string"
            ? body.detail
            : null;
        throw new Error(
          detail ?? `Python backend returned ${response.status}`
        );
      }
      const data = body as AgentResponse;
      const redirectedToExistingThread = data.threadId !== activeThreadId;
      applyAgentResponse(data);
      setWebsiteUrl("");
      if (redirectedToExistingThread) {
        await openThread(data.threadId);
      } else {
        setMessages((previous) => [
          ...previous,
          { role: "assistant", content: data.reply },
        ]);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setMessages((previous) => [
        ...previous,
        {
          role: "assistant",
          content: `The product website could not be imported: ${message}`,
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  function applyAgentResponse(data: AgentResponse) {
    setThreadId(data.threadId);
    setAgentStatus(data.status);
    setCompanyCandidates(data.companyCandidates);
    setProductCandidates(data.productCandidates);
    setCurrentProductId(data.currentProduct?.productId ?? null);
    setHumanRequest(data.pendingHumanRequest);
    mergeActivity(data.traceEvents);
    void refreshArtifacts(data.threadId);
    void refreshBackgroundJobs(data.threadId);
    setResearchDispatchError(data.researchDispatchError ?? null);
    void refreshMappingKnowledge();
    const product = data.currentProduct;
    if (product?.mappingResult && product.coverageReport) {
      applyWebsiteResult(
        product,
        product.pendingReviews,
        data.status === "awaiting_review"
      );
    }
  }

  async function submitHumanValue(useDummy = false) {
    if (!threadId || !humanRequest?.requirementId || (!useDummy && !humanValue.trim()) || busy) return;
    setBusy(true);
    try {
      const response = await authenticatedFetch(`${API_URL}/api/agent/value`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          threadId,
          productId: humanRequest.productId,
          requirementId: humanRequest.requirementId,
          value: useDummy ? "" : humanValue.trim(),
          useDummy,
        }),
      });
      const body = (await response.json()) as AgentResponse | { detail?: string };
      if (!response.ok) {
        throw new Error("detail" in body && body.detail ? body.detail : "Value was rejected");
      }
      const data = body as AgentResponse;
      applyAgentResponse(data);
      setHumanValue("");
      setMessages((previous) => [
        ...previous,
        { role: "user", content: useDummy ? "Use a human-approved DUMMY placeholder." : humanValue.trim() },
        { role: "assistant", content: data.reply },
      ]);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setMessages((previous) => [
        ...previous,
        { role: "assistant", content: `The value could not be saved: ${message}` },
      ]);
    } finally {
      setBusy(false);
    }
  }

  async function refreshMappingKnowledge() {
    const response = await authenticatedFetch(`${API_URL}/api/mapping-knowledge`);
    if (response.ok) {
      setMappingKnowledge((await response.json()) as MappingKnowledgeEntry[]);
    }
  }

  function applyWebsiteResult(
    product: NonNullable<AgentResponse["currentProduct"]>,
    reviewItems: SemanticReviewItem[],
    awaitingReview: boolean
  ) {
    const resolvedMappings = [
      ...(product.mappingResult?.mapped ?? []),
      ...(product.mappingResult?.ambiguous ?? []),
      ...(product.mappingResult?.rejected ?? []),
    ];
    const resolvedIds = new Set(resolvedMappings.map((mapping) => mapping.id));
    const semantic = reviewItems
      .flatMap((item) => item.mapping ? [item.mapping] : [])
      .filter((mapping) => !resolvedIds.has(mapping.id));
    setProductName(product.productName || product.candidate?.name || "Website product");
    setMappingCycleId(product.mappingCycleId);
    setMappings([...resolvedMappings, ...semantic]);
    setSemanticReview(awaitingReview ? reviewItems : []);
    setReviewDecisions(
      awaitingReview
        ? Object.fromEntries(
            reviewItems
              .filter((item) => item.status !== "uncertain")
              .map((item) => [item.id, { reviewId: item.id, decision: "keep" }])
          )
        : {}
    );
    setEvidence(product.evidence);
    setMappingResult(product.mappingResult);
    setCoverageReport(product.coverageReport);
    setDpp(null);
    setTab("mappings");
  }

  async function confirmSemanticReview() {
    if (!threadId || semanticReview.length === 0 || busy) return;
    const decisions = semanticReview.flatMap((item) => {
      const decision = reviewDecisions[item.id];
      return decision ? [decision] : [];
    });
    const invalid =
      decisions.length !== semanticReview.length ||
      decisions.some(
        (decision) =>
          decision.decision === "change_target" && !decision.correctedRequirementId
      );
    if (invalid) return;

    setBusy(true);
    try {
      if (!currentProductId) throw new Error("No active product is available for review.");
      const response = await authenticatedFetch(`${API_URL}/api/agent/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          threadId,
          productId: currentProductId,
          mappingCycleId,
          decisions,
        }),
      });
      const body = (await response.json()) as AgentResponse | { detail?: string };
      if (!response.ok) {
        throw new Error("detail" in body && body.detail ? body.detail : `Python backend returned ${response.status}`);
      }
      const data = body as AgentResponse;
      applyAgentResponse(data);
      setReviewDecisions({});
      setMessages((previous) => [
        ...previous,
        { role: "assistant", content: data.reply },
      ]);
    } catch (error) {
      const message = error instanceof Error ? error.message : "unknown error";
      setMessages((previous) => [
        ...previous,
        { role: "assistant", content: `The semantic review could not be saved: ${message}` },
      ]);
    } finally {
      setBusy(false);
    }
  }

  function decide(id: string, status: "approved" | "rejected", comment?: string) {
    setMappings((prev) =>
      prev.map((m) => (m.id === id ? { ...m, status } : m))
    );
    const review = semanticReview.find((item) => item.mapping?.id === id);
    if (review) {
      setReviewDecisions((previous) => ({
        ...previous,
        [review.id]: {
          reviewId: review.id,
          decision: status === "approved" ? "keep" : "reject",
          comment: comment?.trim() || null,
        },
      }));
    }
  }

  function correct(
    id: string,
    selected: Requirement,
    correctedValue?: string,
    comment?: string
  ) {
    const mapping = mappings.find((item) => item.id === id);
    if (!mapping) return;
    const corrected: FieldMapping = {
      ...mapping,
      target: {
        templateKey: selected.templateKey,
        templateRelease: selected.templateRelease,
        templatePath: selected.templatePath,
        instancePath: selected.templatePath,
        idShort: selected.idShort ?? selected.templatePath.at(-1) ?? "Target",
        semanticId: selected.semanticId!,
      },
      sourceValue: correctedValue?.trim() || mapping.sourceValue,
      status: "approved",
      mappingOrigin: "human",
      humanReviewed: true,
      humanActorName: "You",
      humanValueKind: "verified",
      humanComment: comment?.trim() || null,
      reasoning: "Corrected by you and awaiting trusted backend persistence.",
    };
    setMappings((previous) =>
      previous.map((item) => (item.id === id ? corrected : item))
    );
    const review = semanticReview.find((item) => item.mapping?.id === id);
    if (review) {
      setReviewDecisions((previous) => ({
        ...previous,
        [review.id]: {
          reviewId: review.id,
          decision: "change_target",
          correctedRequirementId: selected.id,
          correctedValue: correctedValue?.trim() || null,
          comment: comment?.trim() || null,
        },
      }));
    }
  }

  function approveAll() {
    setMappings((prev) =>
      prev.map((m) =>
        m.status === "rejected" ? m : { ...m, status: "approved" }
      )
    );
    setReviewDecisions((previous) => {
      const next = { ...previous };
      for (const item of semanticReview) {
        next[item.id] = { reviewId: item.id, decision: "keep" };
      }
      return next;
    });
  }

  async function generate(
    selectedProduct = productName,
    selectedMappings = mappings,
    selectedEvidence = evidence
  ) {
    try {
      const response = await authenticatedFetch(`${API_URL}/api/dpp`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          productName: selectedProduct || "Product",
          mappings: selectedMappings,
          evidence: selectedEvidence,
          threadId,
          productId: currentProductId,
        }),
      });
      if (!response.ok) {
        throw new Error(`Python backend returned ${response.status}`);
      }
      setDpp(await response.json());
    } catch {
      setMessages((previous) => [
        ...previous,
        {
          role: "assistant",
          content:
            "The Python backend could not build the passport. Check that it is running and try again.",
        },
      ]);
    }
  }

  const pending = mappings.filter((m) => m.status === "review").length;
  const ready = mappings.filter(
    (m) => m.status === "approved" || m.status === "auto"
  ).length;
  const websiteAutoMapped =
    mappingResult?.mapped.filter((mapping) => mapping.status === "auto")
      .length ?? 0;
  const websiteNeedsReview = mappingResult
    ? mappingResult.ambiguous.length +
      mappingResult.mapped.filter((mapping) => mapping.status === "review")
        .length +
      semanticReview.filter(
        (item) =>
          item.status === "uncertain" || item.mapping?.status === "review"
      ).length
    : 0;
  const gaps = coverageReport
    ? coverageReport.coverage.flatMap((item) => {
        const requirement = coverageReport.inventory.requirements.find(
          (candidate) => candidate.id === item.requirementId
        );
        return item.status === "missing" && requirement?.required
          ? [requirement.idShort ?? requirement.templatePath.at(-1) ?? requirement.id]
          : [];
      })
    : [];
  const correctionTargets =
    coverageReport?.inventory.requirements.filter(
      (item) => item.kind === "value" && !item.wildcard && item.semanticId
    ) ?? [];

  return (
    <div className="flex h-full flex-col bg-mist">
      {/* Top bar */}
      <header className="relative z-10 flex h-14 shrink-0 items-center justify-between border-b border-hairline bg-paper px-5 shadow-sm">
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-2">
            <span className="grid h-6 w-6 place-items-center rounded-md bg-ink shadow-sm">
              <span className="h-1.5 w-1.5 rounded-[1px] bg-white" />
            </span>
            <span className="text-[15px] font-semibold tracking-tight">MIA</span>
          </Link>
          <span className="hidden text-[13px] text-muted sm:inline">
            {productName || "New passport"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {process.env.NEXT_PUBLIC_MIA_GRAPH_DEBUG_ENABLED === "true" && (
            <button
              type="button"
              onClick={() => setDebugPanelOpen(true)}
              className="inline-flex items-center gap-2 rounded-full border border-slate-300 bg-white px-3 py-1.5 text-[12px] font-medium text-slate-700 transition-colors hover:border-slate-400 hover:bg-slate-50"
              aria-label="Open live workflow debugger"
            >
              <span className={`h-2 w-2 rounded-full ${busy ? "animate-pulse bg-amber-500" : "bg-slate-400"}`} />
              Debug
            </button>
          )}
          {threads.length > 0 && (
            <div className="hidden items-center gap-1 sm:flex">
              <select
                aria-label="Conversation history"
                value={threadId ?? ""}
                onChange={(event) => void openThread(event.target.value)}
                className="max-w-44 rounded-lg border border-hairline bg-white px-2 py-1.5 text-[12px] text-ink"
              >
                <option value="" disabled>Chat History</option>
                {threads.map((thread) => (
                  <option key={thread.id} value={thread.id}>{thread.title || thread.id}</option>
                ))}
              </select>
              {threadId && (
                <button
                  type="button"
                  onClick={() => void deleteThread(threadId)}
                  className="rounded-lg border border-hairline px-2 py-1.5 text-[11px] text-red-700 hover:bg-red-50"
                >
                  Delete
                </button>
              )}
            </div>
          )}
          <Link href="/products" className="hidden text-[13px] text-muted hover:text-ink sm:inline">
            Products
          </Link>
          <span className="rounded-full bg-signalDim px-2.5 py-1 font-mono text-[11px] text-signal">
            Autonomous agent
          </span>
          <button
            onClick={() => void generate()}
            disabled={ready === 0}
            className="rounded-full bg-ink px-4 py-1.5 text-[13px] font-medium text-white transition-all hover:shadow-md hover:-translate-y-px disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:translate-y-0 disabled:hover:shadow-none"
          >
            Generate passport
          </button>
          {authenticationEnabled && <UserButton />}
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* Chat */}
        <section className="flex min-h-0 flex-1 flex-col border-hairline bg-[#F9FAFB] lg:max-w-[46%] lg:border-r">
          <div className="scroll-quiet flex-1 overflow-y-auto px-5 py-6">
            {messages.length === 0 && (
              <div className="mx-auto max-w-md pt-6">
                <h1 className="text-[26px] font-semibold leading-tight tracking-tight text-ink">
                  Import or describe a product.
                </h1>
                <p className="mt-2 text-[15px] leading-relaxed text-muted">
                  Start from a public product page, or enter manufacturer,
                  model, serial number and technical values manually. MIA stops
                  wherever it needs your decision.
                </p>
                <div className="mt-8 space-y-3">
                  {SAMPLES.map((s) => (
                    <button
                      key={s.label}
                      onClick={() => send(s.text)}
                      className="w-full rounded-xl border border-hairline bg-white p-4 text-left transition-all hover:-translate-y-0.5 hover:border-signal/40 hover:bg-signal/5 hover:shadow-sm"
                    >
                      <p className="text-[14px] font-semibold text-ink">{s.label}</p>
                      <p className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-muted">
                        {s.text}
                      </p>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="mx-auto max-w-md space-y-5">
              {restoredRunNotice && (
                <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
                  <p>{restoredRunNotice}</p>
                  <Link href="/workspace/assets" className="mt-2 inline-block font-medium underline">Open Assets</Link>
                </div>
              )}
              {messages.map((m, i) => (
                <div
                  key={i}
                  className={m.role === "user" ? "flex justify-end" : "flex justify-start"}
                >
                  <div
                    className={
                      m.role === "user"
                        ? "max-w-[85%] rounded-2xl rounded-br-sm bg-gradient-to-tr from-signal to-blue-500 px-4 py-3 text-[14px] leading-relaxed text-white shadow-sm"
                        : "max-w-[92%] rounded-2xl rounded-bl-sm border border-hairline bg-white px-4 py-3 text-[14px] leading-relaxed text-ink shadow-sm"
                    }
                  >
                    {m.role === "assistant" ? (
                      <ChatMarkdown>{m.content}</ChatMarkdown>
                    ) : (
                      m.content
                    )}
                  </div>
                </div>
              ))}
              {agentStatus === "awaiting_company" && companyCandidates.length > 0 && !busy && (
                <div className="space-y-2">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted">
                    Select the company
                  </p>
                  {companyCandidates.map((candidate) => (
                    <button
                      key={candidate.id}
                      onClick={() => void send(`Select company ${candidate.id}: ${candidate.name}`)}
                      className="w-full rounded-xl border border-hairline bg-white p-3 text-left hover:border-signal/40"
                    >
                      <p className="text-sm font-semibold text-ink">{candidate.name}</p>
                      <p className="mt-1 text-xs text-muted">{candidate.domain}</p>
                      {candidate.description && (
                        <p className="mt-1 line-clamp-2 text-xs text-muted">
                          {candidate.description}
                        </p>
                      )}
                    </button>
                  ))}
                </div>
              )}
              {agentStatus === "awaiting_product" && productCandidates.length > 0 && !busy && (
                <div className="space-y-2">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted">
                    Select a product
                  </p>
                  {productCandidates.map((candidate) => (
                    <button
                      key={candidate.id}
                      onClick={() => void send(`Select product ${candidate.id}: ${candidate.name}`)}
                      className="w-full rounded-xl border border-hairline bg-white p-3 text-left hover:border-signal/40"
                    >
                      <p className="text-sm font-semibold text-ink">{candidate.name}</p>
                      <p className="mt-1 line-clamp-2 text-xs text-muted">
                        {candidate.description || candidate.officialUrl}
                      </p>
                    </button>
                  ))}
                </div>
              )}
              {agentStatus === "awaiting_optional_choice" && !busy && (
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => void send("Continue with current data")}
                    className="rounded-full bg-ink px-4 py-2 text-[12px] font-medium text-white"
                  >
                    Continue with current data
                  </button>
                  <button
                    onClick={() => void send("Add optional information")}
                    className="rounded-full border border-hairline bg-white px-4 py-2 text-[12px] font-medium text-ink"
                  >
                    Add optional information
                  </button>
                </div>
              )}
              {humanRequest?.kind === "requirement_value" && !busy && (
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    void submitHumanValue();
                  }}
                  className="rounded-xl border border-warn/20 bg-white p-4"
                >
                  <p className="text-sm font-medium text-ink">{humanRequest.summary}</p>
                  <div className="mt-3 flex gap-2">
                    <input
                      value={humanValue}
                      onChange={(event) => setHumanValue(event.target.value)}
                      className="min-w-0 flex-1 rounded-lg border border-hairline px-3 py-2 text-sm"
                      placeholder="Enter the verified value"
                    />
                    <button type="submit" disabled={!humanValue.trim()} className="rounded-lg bg-ink px-4 py-2 text-xs font-medium text-white disabled:opacity-30">
                      Save value
                    </button>
                    <button
                      type="button"
                      onClick={() => void submitHumanValue(true)}
                      className="rounded-lg border border-violet-200 bg-violet-50 px-3 py-2 text-xs font-medium text-violet-700"
                    >
                      Use DUMMY
                    </button>
                  </div>
                </form>
              )}
              {busy && <LiveActivity events={agentActivity} />}
              <div ref={endRef} />
            </div>
          </div>

          <div className="shrink-0 p-4 pb-6">
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void ingestWebsite();
              }}
              className="mx-auto mb-3 max-w-md"
            >
              <label
                htmlFor="product-url"
                className="mb-1.5 block text-[11px] font-medium text-muted"
              >
                Direct product page URL
              </label>
              <div className="flex gap-2">
                <input
                  id="product-url"
                  type="url"
                  required
                  value={websiteUrl}
                  onChange={(event) => setWebsiteUrl(event.target.value)}
                  placeholder="https://manufacturer.com/products/model"
                  className="min-w-0 flex-1 rounded-xl border border-hairline bg-white px-3 py-2.5 text-[13px] text-ink shadow-sm placeholder:text-muted focus:border-signal focus:outline-none focus:ring-4 focus:ring-signal/10"
                />
                <button
                  type="submit"
                  disabled={busy || semanticReview.length > 0 || !websiteUrl.trim()}
                  className="rounded-xl border border-signal/30 bg-signalDim px-3 text-[12px] font-medium text-signal transition-colors hover:bg-signal/15 disabled:opacity-30"
                >
                  Import
                </button>
              </div>
            </form>
            <p className="mx-auto mb-2 max-w-md text-center text-[10px] uppercase tracking-wider text-muted">
              or describe it manually
            </p>
            <div className="mx-auto flex max-w-md items-end gap-2 rounded-[24px] border border-hairline bg-white p-1.5 shadow-sm transition-all focus-within:border-signal/50 focus-within:ring-4 focus-within:ring-signal/10">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send(input);
                  }
                }}
                rows={1}
                placeholder="Ask MIA, clarify a review, or paste a product URL..."
                className="max-h-32 flex-1 resize-none bg-transparent px-4 py-2.5 text-[14px] leading-relaxed text-ink placeholder:text-muted focus:outline-none"
              />
              <button
                onClick={() => send(input)}
                disabled={busy || !input.trim()}
                className="mb-0.5 mr-0.5 grid h-10 w-10 shrink-0 place-items-center rounded-full bg-ink text-white transition-transform hover:scale-105 disabled:scale-100 disabled:opacity-25"
                aria-label="Send message"
              >
                <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
                  <path
                    d="M8 13V3M8 3L3.5 7.5M8 3l4.5 4.5"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
            </div>
          </div>
        </section>

        {/* Right panel */}
        <section className="flex min-h-0 flex-1 flex-col bg-mist">
          <div className="flex shrink-0 items-center justify-between border-b border-hairline bg-paper px-5">
            <div className="flex">
              {(
                [
                  "mappings",
                  "evidence",
                  "coverage",
                  "process",
                  "data",
                  "graph",
                ] as WorkspaceTab[]
              ).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`relative px-4 py-4 text-[13px] font-medium capitalize transition-colors ${
                    tab === t ? "text-signal" : "text-muted hover:text-ink"
                  }`}
                >
                  {tabLabel(t)}
                  {t === "evidence" && evidence.length > 0 && (
                    <span className="ml-1.5 rounded-full bg-mist px-1.5 py-0.5 font-mono text-[10px] text-ink">
                      {evidence.length}
                    </span>
                  )}
                  {t === "coverage" && coverageReport && (
                    <span className="ml-1.5 rounded-full bg-mist px-1.5 py-0.5 font-mono text-[10px] text-ink">
                      {coverageReport.inventory.requirements.filter(
                        (item) => item.kind === "value" && !item.wildcard
                      ).length}
                    </span>
                  )}
                  {t === "graph" && mappingKnowledge.length > 0 && (
                    <span className="ml-1.5 rounded-full bg-mist px-1.5 py-0.5 font-mono text-[10px] text-ink">
                      {mappingKnowledge.length}
                    </span>
                  )}
                  {t === "data" && artifacts.length > 0 && (
                    <span className="ml-1.5 rounded-full bg-mist px-1.5 py-0.5 font-mono text-[10px] text-ink">
                      {artifacts.length}
                    </span>
                  )}
                  {tab === t && (
                    <span className="absolute inset-x-3 -bottom-px h-[3px] rounded-t-full bg-signal" />
                  )}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-3">
              {(backgroundJob?.status === "queued" || backgroundJob?.status === "running") && (
                <span className="text-[11px] font-medium text-signal">
                  Deep research {backgroundJob.status} · {Number(backgroundJob.metadata.processedSources ?? 0)} / {Number(backgroundJob.metadata.totalSources ?? 0)} sources
                </span>
              )}
              {backgroundJob?.status === "failed" && (
                <span className="max-w-72 truncate text-[11px] font-medium text-red-700" title={backgroundJob.error ?? undefined}>
                  Deep research failed{backgroundJob.error ? `: ${backgroundJob.error}` : ""}
                </span>
              )}
              {researchDispatchError && backgroundJob && (
                <button
                  onClick={() => void retryBackgroundJob(backgroundJob.id)}
                  className="rounded-full border border-red-200 bg-red-50 px-3 py-1 text-[11px] font-medium text-red-700"
                  title={researchDispatchError}
                >
                  Research dispatch failed · Retry
                </button>
              )}
              {tab === "mappings" && pending > 0 && (
                <button
                  onClick={approveAll}
                  className="rounded-full border border-hairline px-4 py-1.5 text-[12px] font-medium transition-all hover:bg-mist hover:shadow-sm"
                >
                  Approve all
                </button>
              )}
            </div>
          </div>

          <div className="scroll-quiet min-h-0 flex-1 overflow-y-auto p-5">
            {tab === "mappings" ? (
              mappings.length === 0 ? (
                <Empty
                  title="No mappings yet"
                  body="Send a product description and the proposed mappings will appear here for your approval."
                />
              ) : (
                <div className="space-y-5">
                  <div className="flex flex-wrap gap-3">
                    {mappingResult ? (
                      <>
                        <Stat
                          label="Extracted facts"
                          value={evidence.length}
                          tone="plain"
                        />
                        <Stat
                          label="Mapped automatically"
                          value={websiteAutoMapped}
                          tone="ok"
                        />
                        <Stat
                          label="Needs semantic reasoning/review"
                          value={websiteNeedsReview}
                          tone="warn"
                        />
                        <Stat
                          label="Currently unmatched"
                          value={mappingResult.unmatchedEvidenceIds.length}
                          tone="plain"
                        />
                      </>
                    ) : (
                      <>
                        <Stat label="Ready" value={ready} tone="ok" />
                        <Stat label="Needs you" value={pending} tone="warn" />
                        <Stat label="Gaps" value={gaps.length} tone="plain" />
                      </>
                    )}
                  </div>

                  {semanticReview.length > 0 && (
                    <div className="rounded-xl border border-signal/20 bg-signal/5 p-4 shadow-sm">
                      <p className="text-[13px] font-semibold text-ink">
                        Human semantic review required
                      </p>
                      <p className="mt-1.5 text-[12px] leading-relaxed text-muted">
                        Every retained fact is shown below. Correct only what is
                        wrong; unchanged rows are already set to keep. Uncertain
                        rows require an explicit choice.
                      </p>
                      <button
                        onClick={() => void confirmSemanticReview()}
                        disabled={semanticReview.some(
                          (item) => !reviewDecisions[item.id]
                        )}
                        className="mt-3 rounded-full bg-ink px-4 py-1.5 text-[12px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-30"
                      >
                        Confirm mappings
                      </button>
                    </div>
                  )}

                  {gaps.length > 0 && (
                    <div className="rounded-xl border border-warn/20 bg-warn/[0.04] p-4 shadow-sm">
                      <p className="text-[13px] font-semibold text-warn">
                        Required elements still missing
                      </p>
                      <p className="mt-1.5 font-mono text-[12px] leading-relaxed text-ink/80">
                        {gaps.join(" · ")}
                      </p>
                      <p className="mt-2 text-[12px] text-muted">
                        Add these values in the chat. MIA will not invent them.
                      </p>
                    </div>
                  )}

                  <div className="space-y-3">
                    {semanticReview.length > 0 ? semanticReview.map((item) => {
                      const record = evidence.find((candidate) => candidate.id === item.evidenceId);
                      return record ? (
                        <MappingReviewRow
                          key={item.id}
                          item={item}
                          evidence={record}
                          targets={correctionTargets}
                          decision={reviewDecisions[item.id]}
                          onChange={(decision) => setReviewDecisions((previous) => ({ ...previous, [item.id]: decision }))}
                        />
                      ) : null;
                    }) : mappings.map((m) => (
                      <MappingRow
                        key={m.id}
                        mapping={m}
                        elements={correctionTargets}
                        onDecide={decide}
                        onCorrect={correct}
                      />
                    ))}
                  </div>

                  {dpp && <DppView dpp={dpp} />}
                </div>
              )
            ) : tab === "evidence" ? (
              <EvidencePanel
                evidence={evidence}
                mappingResult={mappingResult}
              />
            ) : tab === "coverage" ? (
              <CoveragePanel report={coverageReport} evidence={evidence} mappingResult={mappingResult} />
            ) : tab === "process" ? (
              <AgentActivity events={agentActivity} />
            ) : tab === "data" ? (
              <WorkspaceExplorer apiUrl={API_URL} threadId={threadId} artifacts={artifacts} error={workspaceError} />
            ) : (
              <IntegrationGraph entries={mappingKnowledge} />
            )}
          </div>
        </section>
      </div>
      {debugPanelOpen && (
        <LiveGraphDebugPanel
          threadId={threadId}
          busy={busy}
          onClose={() => setDebugPanelOpen(false)}
          authenticatedFetch={authenticatedFetch}
        />
      )}
    </div>
  );
}

function tabLabel(tab: WorkspaceTab): string {
  if (tab === "graph") return "Integration Graph";
  if (tab === "process") return "Process";
  if (tab === "data") return "Workspace Data";
  if (tab === "coverage") return "Coverage";
  if (tab === "evidence") return "Evidence";
  return "Mappings";
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "ok" | "warn" | "plain";
}) {
  const cls =
    tone === "ok"
      ? "text-ok"
      : tone === "warn"
      ? "text-warn"
      : "text-muted";
  return (
    <div className="flex flex-col rounded-xl border border-hairline bg-paper px-4 py-2.5 shadow-sm">
      <span className={`font-mono text-[18px] font-semibold tabular-nums ${cls}`}>
        {value}
      </span>
      <span className="mt-0.5 text-[12px] text-muted">{label}</span>
    </div>
  );
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="mx-auto max-w-xs pt-20 text-center">
      <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-full bg-white shadow-sm border border-hairline text-muted">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
          <line x1="16" y1="13" x2="8" y2="13"></line>
          <line x1="16" y1="17" x2="8" y2="17"></line>
          <polyline points="10 9 9 9 8 9"></polyline>
        </svg>
      </div>
      <p className="text-[15px] font-semibold text-ink">{title}</p>
      <p className="mt-2 text-[13px] leading-relaxed text-muted">{body}</p>
    </div>
  );
}
