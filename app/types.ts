export type ViewKey =
  | "workbench"
  | "achievements"
  | "requirements"
  | "runs"
  | "approvals"
  | "system";

export interface User {
  id: string;
  email: string;
  display_name: string;
  is_superuser: boolean;
  organization_id: string | null;
  roles: string[];
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Timestamped {
  id: string;
  created_at: string;
  updated_at: string;
}

export interface Achievement extends Timestamped {
  title: string;
  abstract: string;
  technical_principle: string;
  application_scenarios: string[];
  organizations: string[];
  maturity_level: number | null;
  advantages: string[];
  limitations: string[];
  metrics: Array<Record<string, unknown>>;
  tags: string[];
  evidence_score: number;
  status: string;
  version: number;
  search_indexed: boolean;
}

export interface Requirement extends Timestamped {
  title: string;
  raw_input: string;
  normalized_description: string;
  scenario: string;
  problem: string;
  objectives: string[];
  hard_constraints: Record<string, unknown>;
  soft_preferences: Record<string, unknown>;
  known_conditions: Record<string, unknown>;
  search_terms: string[];
  missing_fields: string[];
  completeness_score: number;
  status: string;
}

export interface AchievementEvaluation extends Timestamped {
  achievement_id: string;
  rubric_id: string;
  agent_run_id: string | null;
  evaluation_version: number;
  status: string;
  dimension_scores: Record<string, number>;
  dimension_details: Record<string, string>;
  total_score: number;
  level: string;
  summary: string;
  strengths: string[];
  risks: string[];
  evidence: Array<Record<string, unknown>>;
  missing_information: string[];
  recommendations: string[];
  confidence: number;
  model_name: string;
  rubric_snapshot: Record<string, unknown>;
  verification_status: string;
}

export interface AchievementWorkflowSummary {
  achievement_id: string;
  evaluation: AchievementEvaluation | null;
}

export interface RequirementRefinement extends Timestamped {
  requirement_id: string;
  agent_run_id: string | null;
  refinement_version: number;
  status: string;
  profile: Record<string, unknown>;
  completeness_score: number;
  confirmed: boolean;
}

export interface RequirementWorkflowSummary {
  requirement_id: string;
  latest_refinement_status: string | null;
  latest_match_id: string | null;
  latest_match_status: string | null;
  latest_match_score: number | null;
  latest_match_candidate_count: number;
  latest_match_at: string | null;
}

export interface MatchRunSummary extends Timestamped {
  requirement_id: string;
  agent_run_id: string | null;
  status: string;
  summary: string;
  confidence: number;
  candidate_count: number;
  top_score: number | null;
}

export interface MatchItem extends Timestamped {
  match_id: string;
  achievement_id: string;
  achievement_title: string;
  rank: number;
  final_score: number;
  hard_constraints_passed: boolean;
  eligibility: "satisfied" | "violated" | "unknown";
  score_kind: string;
  constraint_checks: Array<{constraint: string; status: "satisfied" | "violated" | "unknown"; reason: string; quote: string}>;
  dimension_scores: Record<string, number>;
  hard_constraint_violations: string[];
  reasons: string[];
  gaps: string[];
  risks: string[];
  evidence: Array<Record<string, unknown>>;
  recommendation: string;
  decision_status: string;
  decided_at: string | null;
}

export interface MatchRunDetail extends MatchRunSummary {
  rubric_snapshot: Record<string, unknown>;
  items: MatchItem[];
}

export interface Run extends Timestamped {
  thread_id: string;
  conversation_id: string;
  task_type: string;
  status: string;
  current_agent: string | null;
  current_step: string | null;
  input_payload: {
    message?: string;
    context?: Record<string, unknown>;
  };
  output_payload: {
    answer?: string;
    citations?: Array<{ title: string; url: string; chunk_id?: string; field?: string; version?: number; quote?: string; document_id?: string; locator?: string }>;
    verification?: Record<string, unknown>;
    assessments?: Array<Record<string, unknown>>;
    achievement_evaluation?: Record<string, unknown>;
    research_ingestion?: Record<string, unknown>;
    ingested_achievement_ids?: string[];
    artifact_id?: string | null;
  };
  error_code: string | null;
  error_message: string;
  latency_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost: number;
}

export interface Approval extends Timestamped {
  run_id: string;
  requested_by_agent: string;
  assigned_to: string | null;
  risk_level: string;
  reason: string;
  payload: Record<string, unknown>;
  status: string;
  decision: string | null;
  decision_comment: string;
}

export interface TraceStep extends Timestamped {
  sequence: number;
  agent_name: string;
  status: string;
  output_summary: Record<string, unknown>;
  latency_ms: number;
  error_message: string;
}

export interface ToolCall extends Timestamped {
  tool_name: string;
  status: string;
  input_payload: Record<string, unknown>;
  output_summary: Record<string, unknown>;
  latency_ms: number;
  retry_count: number;
  error_message: string;
}

export interface RunTrace {
  run: Run;
  steps: TraceStep[];
  tool_calls: ToolCall[];
}

export interface RunEvent {
  event_id: string;
  event_type: string;
  timestamp: string;
  agent: string;
  step: string;
  data: Record<string, unknown>;
}

export interface Overview {
  achievements: {
    total: number;
    approved: number;
    average_evidence_score: number;
  };
  runs: Record<string, number>;
  requirements: number;
  pending_approvals: number;
}

export interface Readiness {
  status: "ready" | "degraded";
  checks: {
    database: boolean;
    redis: { runtime: boolean; cache: boolean } | boolean;
    elasticsearch: boolean;
    minio: boolean;
    tavily_configured: boolean;
    model_configured: boolean;
  };
}
