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
  qdrant_indexed: boolean;
}

export interface Requirement extends Timestamped {
  title: string;
  raw_input: string;
  normalized_description: string;
  scenario: string;
  hard_constraints: Record<string, unknown>;
  soft_preferences: Record<string, unknown>;
  missing_fields: string[];
  completeness_score: number;
  status: string;
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
    citations?: Array<{ title: string; url: string }>;
    verification?: Record<string, unknown>;
    assessments?: Array<Record<string, unknown>>;
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
    qdrant: boolean;
    minio: boolean;
    tavily_configured: boolean;
    model_configured: boolean;
  };
}
