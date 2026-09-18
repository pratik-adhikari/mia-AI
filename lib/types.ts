export type MappingStatus = "auto" | "review" | "approved" | "rejected";
export type MappingOrigin = "deterministic" | "semantic_agent" | "human";
export type Severity = "info" | "warning" | "error";
export type ValidationCategory = "metamodel" | "template" | "policy";
export type RequirementKind = "value" | "structural";
export type CoverageStatus =
  | "satisfied"
  | "candidate"
  | "ambiguous"
  | "missing";
export type EvidenceStatus =
  | "observed"
  | "inferred"
  | "verified"
  | "conflicting"
  | "rejected";

export interface SourceLocation {
  page: number | null;
  selector: string | null;
  jsonPointer: string | null;
  excerpt: string | null;
  table: string | null;
  cell: string | null;
}

export interface EvidenceRecord {
  id: string;
  predicate: string;
  /** Label exactly as presented by the source, before semantic mapping. */
  sourceLabel: string | null;
  /** Optional understood meaning; null means MIA has not assigned one. */
  canonicalPredicate: string | null;
  value: unknown;
  unit: string | null;
  contextPath: string[];
  sourceType: "website" | "human";
  sourceUri: string;
  sourceContentSha256: string;
  sourceLocation: SourceLocation;
  extractionMethod: string;
  extractorName: string;
  extractorVersion: string;
  status: EvidenceStatus;
  acquiredAt: string;
}

export interface ReferenceKey {
  type: string;
  value: string;
}

export interface SemanticReference {
  type: string;
  keys: ReferenceKey[];
}

export type MappingBasis = "exact" | "semantic" | "human";

export interface MappingAssessment {
  basis: MappingBasis;
  reviewRequired: boolean;
  reason: string;
  uncertainties: string[];
}

export interface TemplateRelease {
  key: string;
  family: string;
  release: string;
  repositoryCommit: string;
  sourcePath: string;
  sourceSha256: string;
  metamodelVersion: string;
}

export interface MappingTarget {
  templateKey: string;
  templateRelease: string;
  templatePath: string[];
  instancePath: string[];
  idShort: string;
  semanticId: SemanticReference;
}

export interface FieldMapping {
  id: string;
  evidenceId: string;
  /** Field name as it appears in the manufacturer's own system. */
  sourceField: string;
  sourceValue: string;
  target: MappingTarget;
  assessment: MappingAssessment;
  reasoning: string;
  status: MappingStatus;
  mappingOrigin: MappingOrigin;
  humanReviewed: boolean;
  llmReview?: {
    conclusion: string;
    rationale: string;
    evidenceIds: string[];
    alternativeTargetIds: string[];
    uncertainties: string[];
  } | null;
  humanComment?: string | null;
}

export type ProposedFieldMapping = FieldMapping;

export interface ProductKnowledgePackage {
  productId: string;
  productName: string;
  sourceArtifactIds: string[];
  evidence: EvidenceRecord[];
}

export interface Requirement {
  id: string;
  templateKey: string;
  templateRelease: string;
  templatePath: string[];
  idShort: string | null;
  semanticId: SemanticReference | null;
  supplementalSemanticIds: SemanticReference[];
  modelType: string;
  valueType: string | null;
  cardinality: "One" | "ZeroToOne" | "OneToMany" | "ZeroToMany" | null;
  kind: RequirementKind;
  required: boolean;
  conditional: boolean;
  unit: string | null;
  allowedValues: string[];
  description: string | null;
  wildcard: boolean;
}

export interface RequirementInventory {
  selectedTemplates: TemplateRelease[];
  requirements: Requirement[];
}

export interface RequirementCoverage {
  requirementId: string;
  status: CoverageStatus;
  supportingEvidenceIds: string[];
  candidateEvidenceIds: string[];
  matchMethod: string;
  explanation: string;
}

export interface CoverageStatistics {
  selectedTemplates: number;
  requirements: number;
  requiredRequirements: number;
  requiredSatisfied: number;
  requiredCandidate: number;
  requiredAmbiguous: number;
  requiredMissing: number;
  optionalRequirements: number;
  optionalSatisfied: number;
  optionalCandidate: number;
  optionalAmbiguous: number;
  optionalMissing: number;
  evidenceRecords: number;
  evidenceUsed: number;
  unmatchedEvidence: number;
}

export interface CoverageReport {
  inventory: RequirementInventory;
  coverage: RequirementCoverage[];
  analyzedEvidenceIds: string[];
  unmatchedEvidenceIds: string[];
  statistics: CoverageStatistics;
}

export interface MappingResult {
  mapped: ProposedFieldMapping[];
  ambiguous: ProposedFieldMapping[];
  rejected: ProposedFieldMapping[];
  unmatchedEvidenceIds: string[];
  irrelevantEvidenceIds: string[];
  rejectedEvidenceIds: string[];
  outcomes: EvidenceOutcome[];
}

export type EvidenceOutcomeStatus =
  | "mapped"
  | "uncertain"
  | "unmapped"
  | "irrelevant"
  | "rejected";

export interface EvidenceOutcome {
  evidenceId: string;
  status: EvidenceOutcomeStatus;
  requirementId: string | null;
  alternativeRequirementIds: string[];
  reason: string;
  mappingOrigin: MappingOrigin;
}

export interface MappingKnowledgeEntry {
  id: string;
  sourceField: string;
  exampleValues: string[];
  targetTemplate: string;
  targetPath: string[];
  semanticId: string;
  manufacturer: string | null;
  domain: string | null;
  productFamily: string | null;
  llmReviewSummary: string | null;
  humanComments: string[];
  confirmations: number;
  corrections: number;
  rejections: number;
  createdAt: string;
  updatedAt: string;
  status: "candidate" | "trusted";
}

export interface Gap {
  templatePath: string[];
  message: string;
  severity: Severity;
}

export interface GapReport {
  templateKey: string;
  gaps: Gap[];
  blocksDeployment: boolean;
}

export interface ValidationFinding {
  category: ValidationCategory;
  code: string;
  message: string;
  severity: Severity;
  instancePath: string[];
  templatePath: string[];
  expected: string | null;
  actual: string | null;
}

export interface ValidationReport {
  valid: boolean;
  templateKey: string;
  templateRelease: string;
  artifactSha256: string;
  validatorVersions: Record<string, string>;
  findings: ValidationFinding[];
}

export interface DppPackage {
  productName: string;
  generatedAt: string;
  submodel: Record<string, unknown>;
  environment: Record<string, unknown>;
  passportId: string;
  artifactSha256: string;
  template: TemplateRelease;
  gapReport: GapReport;
  validationReport: ValidationReport;
  deployable: boolean;
  evidence: EvidenceRecord[];
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface SemanticReviewItem {
  id: string;
  evidenceId: string;
  status: EvidenceOutcomeStatus;
  requirementId: string | null;
  alternativeRequirementIds: string[];
  reason: string;
  mapping: ProposedFieldMapping | null;
}

export interface AgentReviewDecision {
  reviewId: string;
  decision:
    | "keep"
    | "change_target"
    | "unmapped"
    | "irrelevant"
    | "reject";
  correctedRequirementId?: string | null;
  correctedValue?: string | null;
  comment?: string | null;
}

export type AgentStatus =
  | "running"
  | "awaiting_company"
  | "awaiting_product"
  | "awaiting_review"
  | "awaiting_input"
  | "awaiting_optional_choice"
  | "completed"
  | "failed";

export interface CompanyCandidate {
  id: string;
  name: string;
  officialUrl: string;
  domain: string;
  description: string;
  sourceUri: string;
  identityVerified: boolean;
}

export interface ProductCandidate {
  id: string;
  name: string;
  officialUrl: string;
  description: string;
  family: string | null;
  model: string | null;
  thumbnailUrl: string | null;
  sourceUri: string;
}

export interface ProductSourceCandidate {
  id: string;
  productId: string;
  title: string;
  url: string;
  description: string;
  authoritativeDomain: boolean;
  sourceUri: string;
}

export interface AgentTraceEvent {
  id: string;
  threadId: string;
  eventType: string;
  status: "started" | "completed" | "failed";
  timestamp: string;
  summary: string;
  toolName: string | null;
  productId: string | null;
  inputSummary: string | null;
  outputSummary: string | null;
  sourceIds: string[];
  durationMs: number | null;
  metadata: Record<string, string | number | boolean | null>;
}

export interface AgentProductWork {
  productId: string;
  status: "queued" | "in_progress" | "awaiting_review" | "completed" | "failed";
  candidate: ProductCandidate | null;
  sourceCandidates: ProductSourceCandidate[];
  productName: string | null;
  sourceUrls: string[];
  sourceArtifactIds: string[];
  evidence: EvidenceRecord[];
  mappingResult: MappingResult | null;
  templateIndex: RequirementInventory | null;
  coverageReport: CoverageReport | null;
  pendingReviews: SemanticReviewItem[];
  mappingCycleId: string | null;
  confirmedMappingCycleIds: string[];
  aasArtifactSha256: string | null;
  artifactIds: string[];
}

export interface HumanRequest {
  kind: "mapping_review" | "requirement_value";
  productId: string;
  summary: string;
  requirementId: string | null;
}

export interface WorkspaceArtifact {
  id: string;
  kind: "search" | "source" | "raw" | "evidence" | "mapping" | "coverage" | "review" | "aas" | "validation" | "export";
  name: string;
  relativePath: string;
  createdAt: string;
  createdBy: string;
  contentType: string;
  sha256: string;
  size: number;
  productId: string | null;
  sourceUrl: string | null;
  derivedFrom: string[];
  downloadable: boolean;
}

export interface AgentResponse {
  threadId: string;
  reply: string;
  status: AgentStatus;
  decisionSummary: string;
  companyCandidates: CompanyCandidate[];
  selectedCompany: CompanyCandidate | null;
  productCandidates: ProductCandidate[];
  selectedProductIds: string[];
  currentProduct: AgentProductWork | null;
  traceEvents: AgentTraceEvent[];
  pendingHumanRequest: HumanRequest | null;
  artifactCount: number;
}

export type ProductRunStatus =
  | "running"
  | "awaiting_human"
  | "completed"
  | "incomplete"
  | "failed"
  | "reused";

export interface ProductRecord {
  id: string;
  canonicalUrl: string;
  originalUrl: string;
  manufacturer: string | null;
  name: string | null;
  manufacturerProductId: string | null;
  imageUrl: string | null;
  imageArtifactId: string | null;
  createdAt: string;
  updatedAt: string;
  lastVerifiedAt: string | null;
}

export interface ProductRun {
  id: string;
  productId: string;
  threadId: string;
  status: ProductRunStatus;
  refreshRequested: boolean;
  reusedFromRunId: string | null;
  startedAt: string;
  finishedAt: string | null;
  error: string | null;
  metrics: Record<string, string | number | boolean | null>;
}

export interface DppVersionRecord {
  id: string;
  productId: string;
  runId: string;
  version: number;
  dppArtifactId: string;
  aasArtifactId: string | null;
  validationArtifactId: string | null;
  sourceFingerprint: string | null;
  deployable: boolean;
  createdAt: string;
}

export interface StoredArtifact {
  id: string;
  key: string;
  contentType: string;
  sha256: string;
  size: number;
  storageUri: string;
  productId: string | null;
  runId: string | null;
  derivedFrom: string[];
  createdAt: string;
}

export interface ProductLibraryItem {
  product: ProductRecord;
  latestDpp: DppVersionRecord | null;
  runCount: number;
}

export interface ProductDetail {
  product: ProductRecord;
  runs: ProductRun[];
  dppVersions: DppVersionRecord[];
  artifacts: StoredArtifact[];
}

export interface StoredChatMessage {
  id: string;
  threadId: string;
  runId: string | null;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: string;
}
