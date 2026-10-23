"use client";

import {
  Activity,
  ArrowRight,
  Award,
  Bot,
  BrainCircuit,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  ClipboardCheck,
  Clock3,
  Database,
  Download,
  FileSearch,
  Gauge,
  GitBranch,
  Layers3,
  ListChecks,
  Library,
  LogOut,
  Menu,
  MessageSquareText,
  Network,
  Play,
  Plus,
  RefreshCw,
  Search,
  Send,
  ServerCog,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Square,
  Star,
  X,
  XCircle,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import {
  ACCESS_TOKEN_KEY,
  api,
  ApiError,
  downloadArtifact,
  downloadSource,
  REFRESH_TOKEN_KEY,
  streamRunEvents,
} from "./lib/api";
import type {
  Achievement,
  AchievementEvaluation,
  AchievementWorkflowSummary,
  Approval,
  MatchItem,
  MatchRunDetail,
  MatchRunSummary,
  Overview,
  Page,
  Readiness,
  Requirement,
  RequirementRefinement,
  RequirementWorkflowSummary,
  Run,
  RunEvent,
  RunTrace,
  User,
  ViewKey,
} from "./types";

const navigation: Array<{
  key: ViewKey;
  label: string;
  icon: typeof Bot;
}> = [
  { key: "workbench", label: "Agent 工作台", icon: BrainCircuit },
  { key: "achievements", label: "成果库管理", icon: Library },
  { key: "requirements", label: "需求与匹配", icon: FileSearch },
  { key: "runs", label: "运行记录", icon: Activity },
  { key: "approvals", label: "审核中心", icon: ClipboardCheck },
  { key: "system", label: "系统状态", icon: ServerCog },
];

const taskTypes = [
  { value: "auto", label: "智能路由" },
  { value: "requirement_analysis", label: "需求规范化" },
  { value: "matching", label: "成果匹配" },
  { value: "research", label: "联网调研" },
  { value: "evaluation", label: "成果评估" },
  { value: "qa", label: "成果答疑问答" },
];

const prompts = [
  "为高寒地区农村供水工程匹配低维护、成熟度高的技术成果",
  "先帮我整理农村供水泵站的改造需求，暂时不要匹配",
  "联网搜寻近三年智慧泵站运行调度成果，并核验来源",
];

const eventLabels: Record<string, string> = {
  "run.created": "任务已创建",
  "run.started": "任务开始",
  "run.resuming": "任务恢复",
  "plan.created": "Agent 完成任务规划",
  "agent.started": "Agent 开始处理",
  "agent.completed": "Agent 完成处理",
  "tool.started": "工具调用开始",
  "tool.completed": "工具调用完成",
  "tool.failed": "工具调用失败",
  "approval.required": "等待人工审核",
  "run.waiting_review": "等待补充信息或审核",
  "run.completed": "任务已完成",
  "run.failed": "任务失败",
  "run.cancelled": "任务已取消",
};

const agentNames: Record<string, string> = {
  supervisor: "历史调度记录",
  hydrobridge_agent: "成果助手",
  context_summary: "对话上下文整理",
  qa_retrieve: "原文检索",
  qa_generate: "证据问答生成",
  qa_validate: "引用与充分性核验",
  get_achievement: "成果详情",
  update_requirement: "需求更新",
  requirement_patch: "需求局部更新",
  tools: "工具执行",
  match_achievements: "成果匹配",
  constraint_validation: "约束核验",
  matching_report: "证据报告",
  requirement_analyst: "需求规范化 Agent",
  research_agent: "科技情报 Agent",
  evidence_curator: "证据整理 Agent",
  assessment_matching_agent: "评估匹配 Agent",
  verification_agent: "独立核验 Agent",
  final_report_agent: "报告生成 Agent",
  human_review: "人工审核",
};

const evidenceFieldLabels: Record<string, string> = {
  title: "成果名称", abstract: "内容摘要", technical_principle: "技术原理",
  application_scenarios: "应用场景", advantages: "技术优势", limitations: "实施难点",
  metrics: "技术指标", cost_description: "成本资料", maturity_level: "成熟度",
  organizations: "完成单位", tags: "主题标签",
};

function formatTime(value?: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "运行中",
    waiting_review: "等待审核",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    approved: "已审核",
    candidate: "候选",
    rejected: "已驳回",
    archived: "已归档",
    pending: "待处理",
    draft: "草稿",
    refining: "规范化中",
    matching: "匹配中",
    matched: "已匹配",
    passed: "已通过",
    revision_required: "需要修订",
    human_review_required: "需要人工复核",
    ready: "就绪",
    degraded: "部分就绪",
  };
  return labels[status] ?? status;
}

function LoginScreen({
  onLogin,
  busy,
  error,
}: {
  onLogin: (email: string, password: string) => void;
  busy: boolean;
  error: string;
}) {
  const [email, setEmail] = useState("admin@hydrobridge.cn");
  const [password, setPassword] = useState("ChangeMe123!");
  return (
    <main className="login-shell">
      <div className="login-atmosphere" />
      <section className="login-story">
        <div className="brand-mark large">
          <span className="brand-wave" />
        </div>
        <p className="eyebrow light">HYDROBRIDGE INTELLIGENCE</p>
        <h1>
          让水利科技成果
          <br />
          找到真正需要它的工程
        </h1>
        <p className="login-lead">
          面向成果发现、证据核验、供需匹配与工程决策的可恢复Agent 平台。
        </p>
        <div className="login-capabilities">
          <span><GitBranch size={17} /> LangGraph Agent 编排</span>
          <span><Database size={17} /> PostgreSQL + Redis 可靠运行</span>
          <span><ShieldCheck size={17} /> 人工审核与完整审计</span>
        </div>
      </section>
      <section className="login-card">
        <div className="login-card-head">
          <p className="eyebrow">欢迎回来</p>
          <h2>进入决策工作台</h2>
          <p>使用本地管理员账号体验全部功能</p>
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onLogin(email, password);
          }}
        >
          <label>
            邮箱
            <input
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
            />
          </label>
          <label>
            密码
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
            />
          </label>
          {error && <div className="form-error"><CircleAlert size={16} />{error}</div>}
          <button className="primary-button login-button" disabled={busy}>
            {busy ? <RefreshCw className="spin" size={18} /> : <ArrowRight size={18} />}
            {busy ? "正在连接平台" : "登录工作台"}
          </button>
        </form>
        <div className="demo-credential">
          <CheckCircle2 size={16} />
          首次启动会自动创建演示账号并导入 15 份真实水利成果
        </div>
      </section>
    </main>
  );
}

function EmptyState({
  icon: Icon,
  title,
  body,
}: {
  icon: typeof Bot;
  title: string;
  body: string;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon"><Icon size={24} /></div>
      <strong>{title}</strong>
      <p>{body}</p>
    </div>
  );
}

export default function HydroBridgeApp() {
  const [token, setToken] = useState("");
  const [user, setUser] = useState<User | null>(null);
  const [view, setView] = useState<ViewKey>("workbench");
  const [mobileNav, setMobileNav] = useState(false);
  const [booting, setBooting] = useState(true);
  const [loginBusy, setLoginBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [achievements, setAchievements] = useState<Achievement[]>([]);
  const [requirements, setRequirements] = useState<Requirement[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [evaluationSummaries, setEvaluationSummaries] = useState<
    Record<string, AchievementEvaluation>
  >({});
  const [requirementSummaries, setRequirementSummaries] = useState<
    Record<string, RequirementWorkflowSummary>
  >({});
  const [selectedAchievement, setSelectedAchievement] =
    useState<Achievement | null>(null);
  const [achievementDrawerTab, setAchievementDrawerTab] = useState<
    "overview" | "evaluation"
  >("overview");
  const [achievementEvaluations, setAchievementEvaluations] = useState<
    AchievementEvaluation[]
  >([]);
  const [selectedRequirement, setSelectedRequirement] =
    useState<Requirement | null>(null);
  const [requirementDrawerTab, setRequirementDrawerTab] = useState<
    "profile" | "matching" | "history"
  >("profile");
  const [requirementRefinements, setRequirementRefinements] = useState<
    RequirementRefinement[]
  >([]);
  const [requirementMatches, setRequirementMatches] = useState<MatchRunSummary[]>([]);
  const [selectedMatch, setSelectedMatch] = useState<MatchRunDetail | null>(null);
  const [workflowBusy, setWorkflowBusy] = useState("");
  const [selectedRun, setSelectedRun] = useState<Run | null>(null);
  const [trace, setTrace] = useState<RunTrace | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [prompt, setPrompt] = useState(prompts[0]);
  const [taskType, setTaskType] = useState("auto");
  const [running, setRunning] = useState(false);
  const [achievementQuery, setAchievementQuery] = useState("");
  const [achievementPage, setAchievementPage] = useState(1);
  const [achievementTotal, setAchievementTotal] = useState(0);
  const catalogFilters = useRef({ query: "", page: 1 });
  const [showNewAchievement, setShowNewAchievement] = useState(false);
  const [showNewRequirement, setShowNewRequirement] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const notify = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 2800);
  }, []);

  const refreshAll = useCallback(
    async (accessToken: string) => {
      const [
        overviewData,
        achievementData,
        requirementData,
        runData,
        approvalData,
        readinessData,
        evaluationData,
        requirementSummaryData,
      ] = await Promise.all([
        api<Overview>("/overview", {}, accessToken),
        api<Page<Achievement>>(`/achievements?page_size=24&page=${catalogFilters.current.page}&query=${encodeURIComponent(catalogFilters.current.query)}`, {}, accessToken),
        api<Page<Requirement>>("/requirements?page_size=100", {}, accessToken),
        api<Page<Run>>("/runs?page_size=100", {}, accessToken),
        api<Page<Approval>>("/approvals?status=pending&page_size=100", {}, accessToken),
        api<Readiness>("/readiness", {}, accessToken),
        api<AchievementWorkflowSummary[]>(
          "/achievements/evaluations/latest",
          {},
          accessToken,
        ),
        api<RequirementWorkflowSummary[]>(
          "/requirements/workflow-summaries",
          {},
          accessToken,
        ),
      ]);
      setOverview(overviewData);
      setAchievements(achievementData.items);
      setAchievementTotal(achievementData.total);
      setRequirements(requirementData.items);
      setRuns(runData.items);
      setApprovals(approvalData.items);
      setReadiness(readinessData);
      setEvaluationSummaries(
        Object.fromEntries(
          evaluationData
            .filter((item) => item.evaluation)
            .map((item) => [item.achievement_id, item.evaluation!]),
        ),
      );
      setRequirementSummaries(
        Object.fromEntries(
          requirementSummaryData.map((item) => [item.requirement_id, item]),
        ),
      );
    },
    [],
  );

  useEffect(() => {
    const handleTokenRefresh = (event: Event) => {
      setToken((event as CustomEvent<string>).detail);
    };
    window.addEventListener("hydrobridge-token-refreshed", handleTokenRefresh);
    const restoreSession = async () => {
      const savedToken = window.localStorage.getItem(ACCESS_TOKEN_KEY) ?? "";
      const savedRefreshToken = window.localStorage.getItem(REFRESH_TOKEN_KEY);
      if (!savedToken && !savedRefreshToken) return;
      try {
        const me = await api<User>("/auth/me", {}, savedToken);
        const currentToken = window.localStorage.getItem(ACCESS_TOKEN_KEY) ?? savedToken;
        setToken(currentToken);
        setUser(me);
        await refreshAll(currentToken);
      } catch {
        window.localStorage.removeItem(ACCESS_TOKEN_KEY);
        window.localStorage.removeItem(REFRESH_TOKEN_KEY);
      }
    };
    restoreSession().finally(() => setBooting(false));
    return () => {
      window.removeEventListener("hydrobridge-token-refreshed", handleTokenRefresh);
    };
  }, [refreshAll]);

  const login = async (email: string, password: string) => {
    setLoginBusy(true);
    setError("");
    try {
      const session = await api<{
        access_token: string;
        refresh_token: string;
      }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      window.localStorage.setItem(ACCESS_TOKEN_KEY, session.access_token);
      window.localStorage.setItem(REFRESH_TOKEN_KEY, session.refresh_token);
      const me = await api<User>("/auth/me", {}, session.access_token);
      setToken(session.access_token);
      setUser(me);
      await refreshAll(session.access_token);
    } catch (caught) {
      window.localStorage.removeItem(ACCESS_TOKEN_KEY);
      window.localStorage.removeItem(REFRESH_TOKEN_KEY);
      setError(
        caught instanceof ApiError
          ? caught.message
          : "无法连接后端，请确认平台服务已启动",
      );
    } finally {
      setLoginBusy(false);
    }
  };

  const logout = async () => {
    abortRef.current?.abort();
    const refreshToken = window.localStorage.getItem(REFRESH_TOKEN_KEY);
    try {
      if (refreshToken) {
        await api("/auth/logout", {
          method: "POST",
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
      }
    } finally {
      window.localStorage.removeItem(ACCESS_TOKEN_KEY);
      window.localStorage.removeItem(REFRESH_TOKEN_KEY);
      setToken("");
      setUser(null);
    }
  };

  const openRun = async (run: Run) => {
    setSelectedRun(run);
    setEvents([]);
    try {
      setTrace(await api<RunTrace>(`/runs/${run.id}/trace`, {}, token));
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "无法读取运行轨迹");
    }
  };

  const downloadReport = async (artifactId: string) => {
    try {
      await downloadArtifact(artifactId, token);
      notify("报告成果文件已下载");
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "报告下载失败");
    }
  };

  const followRun = async (runId: string) => {
    const controller = new AbortController();
    abortRef.current = controller;
    await streamRunEvents(
      runId,
      token,
      (event) => setEvents((current) => [...current, event]),
      controller.signal,
    );
    const [updated, updatedTrace] = await Promise.all([
      api<Run>(`/runs/${runId}`, {}, token),
      api<RunTrace>(`/runs/${runId}/trace`, {}, token),
    ]);
    setSelectedRun(updated);
    setTrace(updatedTrace);
    await refreshAll(token);
    return updated;
  };

  const executeRun = async () => {
    if (!prompt.trim() || running) return;
    setRunning(true);
    setError("");
    setEvents([]);
    setTrace(null);
    try {
      if (selectedRun?.status === "waiting_review") {
        const approval = approvals.find((item) => item.run_id === selectedRun.id && item.status === "pending");
        const clarification = approval && clarificationReviewPayload(approval);
        if (!approval || !clarification) {
          notify("当前任务需要在审核中心确认，或点击新建会话开始其他任务");
          return;
        }
        const message = [
          `原始需求：${clarification.original_message || selectedRun.input_payload.message || ""}`,
          `用户补充信息：${prompt.trim()}`,
        ].join("\n\n");
        await decideApproval(approval, "edit", { message });
        setPrompt("");
        return;
      }

      const run = await api<Run>(
        "/runs",
        {
          method: "POST",
          body: JSON.stringify({
            conversation_id: selectedRun?.conversation_id,
            message: prompt.trim(),
            task_type: taskType,
            context: { source: "web_workbench" },
          }),
        },
        token,
      );
      setSelectedRun(run);
      setRuns((current) => [run, ...current.filter((item) => item.id !== run.id)]);
      setPrompt("");
      const updated = await followRun(run.id);
      notify(
        updated.status === "waiting_review"
          ? "任务需要补充信息或审核，请查看当前提示"
          : updated.status === "completed"
            ? "Agent 分析已经完成"
            : statusLabel(updated.status),
      );
    } catch (caught) {
      if ((caught as Error).name !== "AbortError") {
        setError(caught instanceof Error ? caught.message : "Agent 任务执行失败");
      }
    } finally {
      setRunning(false);
    }
  };

  const cancelRun = async () => {
    if (!selectedRun) return;
    await api(`/runs/${selectedRun.id}/cancel`, { method: "POST" }, token);
    abortRef.current?.abort();
    setRunning(false);
    notify("任务取消请求已提交");
    await refreshAll(token);
  };

  const decideApproval = async (
    approval: Approval,
    decision: "approve" | "edit" | "reject",
    editedPayload?: Record<string, unknown>,
  ) => {
    const inCurrentChat = view === "workbench" && selectedRun?.id === approval.run_id;
    if (inCurrentChat) setRunning(true);
    try {
      await api(
        `/runs/${approval.run_id}/decisions`,
        {
          method: "POST",
          body: JSON.stringify({
            approval_id: approval.id,
            decision,
            comment:
              decision === "edit"
                ? "已补充需求信息，请继续执行"
                : decision === "approve"
                  ? "已核对信息，同意继续"
                  : "取消当前人工审核任务",
            edited_payload: editedPayload,
          }),
        },
        token,
      );
      notify(
        decision === "edit"
          ? "补充信息已提交，正在自动重新规范化需求"
          : decision === "approve"
            ? "已批准，Agent 将继续运行"
            : "已取消当前审核任务",
      );
      if (inCurrentChat) {
        setEvents([]);
        await followRun(approval.run_id);
      } else {
        await refreshAll(token);
      }
    } finally {
      if (inCurrentChat) setRunning(false);
    }
  };

  const createAchievement = async (form: HTMLFormElement) => {
    const data = new FormData(form);
    await api(
      "/achievements",
      {
        method: "POST",
        body: JSON.stringify({
          title: data.get("title"),
          abstract: data.get("abstract"),
          technical_principle: data.get("technical_principle"),
          organizations: String(data.get("organization") ?? "")
            .split(/[，,]/)
            .filter(Boolean),
          tags: String(data.get("tags") ?? "")
            .split(/[，,]/)
            .filter(Boolean),
          maturity_level: Number(data.get("maturity_level")) || null,
        }),
      },
      token,
    );
    setShowNewAchievement(false);
    notify("候选成果已创建");
    await refreshAll(token);
  };

  const createRequirement = async (form: HTMLFormElement) => {
    const data = new FormData(form);
    const requirement = await api<Requirement>(
      "/requirements",
      {
        method: "POST",
        body: JSON.stringify({
          title: data.get("title"),
          raw_input: data.get("raw_input"),
        }),
      },
      token,
    );
    await api<RequirementRefinement>(
      `/requirements/${requirement.id}/refinements`,
      { method: "POST" },
      token,
    );
    setShowNewRequirement(false);
    notify("工程需求已保存，AI 正在生成规范化需求");
    await refreshAll(token);
  };

  const openAchievement = async (
    achievement: Achievement,
    tab: "overview" | "evaluation" = "overview",
  ) => {
    setSelectedAchievement(achievement);
    setAchievementDrawerTab(tab);
    setAchievementEvaluations(
      evaluationSummaries[achievement.id] ? [evaluationSummaries[achievement.id]] : [],
    );
    try {
      setAchievementEvaluations(
        await api<AchievementEvaluation[]>(
          `/achievements/${achievement.id}/evaluations`,
          {},
          token,
        ),
      );
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "无法读取评价历史");
    }
  };

  const startEvaluation = async (achievement: Achievement) => {
    const key = `evaluation:${achievement.id}`;
    if (workflowBusy === key) return;
    setWorkflowBusy(key);
    try {
      const evaluation = await api<AchievementEvaluation>(
        `/achievements/${achievement.id}/evaluations`,
        { method: "POST" },
        token,
      );
      setEvaluationSummaries((current) => ({ ...current, [achievement.id]: evaluation }));
      setSelectedAchievement(achievement);
      setAchievementDrawerTab("evaluation");
      setAchievementEvaluations((current) => [
        evaluation,
        ...current.filter((item) => item.id !== evaluation.id),
      ]);
      notify("评价任务已提交，完成后结果会自动回写");
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "评价任务提交失败");
    } finally {
      setWorkflowBusy("");
    }
  };

  const loadRequirementWorkspace = async (
    requirement: Requirement,
    tab: "profile" | "matching" | "history" = "profile",
  ) => {
    setSelectedRequirement(requirement);
    setRequirementDrawerTab(tab);
    try {
      const [refinements, matches] = await Promise.all([
        api<RequirementRefinement[]>(
          `/requirements/${requirement.id}/refinements`,
          {},
          token,
        ),
        api<MatchRunSummary[]>(`/requirements/${requirement.id}/matches`, {}, token),
      ]);
      setRequirementRefinements(refinements);
      setRequirementMatches(matches);
      const target = matches[0];
      if (target) {
        setSelectedMatch(
          await api<MatchRunDetail>(
            `/requirements/${requirement.id}/matches/${target.id}`,
            {},
            token,
          ),
        );
      } else {
        setSelectedMatch(null);
      }
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "无法读取需求工作区");
    }
  };

  const startRefinement = async (requirement: Requirement) => {
    const key = `refinement:${requirement.id}`;
    if (workflowBusy === key) return;
    setWorkflowBusy(key);
    try {
      const record = await api<RequirementRefinement>(
        `/requirements/${requirement.id}/refinements`,
        { method: "POST" },
        token,
      );
      setRequirementRefinements((current) => [record, ...current]);
      setRequirementSummaries((current) => ({
        ...current,
        [requirement.id]: {
          ...(current[requirement.id] ?? {
            requirement_id: requirement.id,
            latest_match_id: null,
            latest_match_status: null,
            latest_match_score: null,
            latest_match_candidate_count: 0,
            latest_match_at: null,
          }),
          latest_refinement_status: record.status,
        },
      }));
      setSelectedRequirement(requirement);
      setRequirementDrawerTab("profile");
      notify("AI 需求规范化已开始");
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "需求规范化提交失败");
    } finally {
      setWorkflowBusy("");
    }
  };

  const startMatching = async (requirement: Requirement) => {
    const key = `matching:${requirement.id}`;
    if (workflowBusy === key) return;
    setWorkflowBusy(key);
    try {
      const match = await api<MatchRunSummary>(
        `/requirements/${requirement.id}/matches`,
        { method: "POST" },
        token,
      );
      setRequirementMatches((current) => [
        match,
        ...current.filter((item) => item.id !== match.id),
      ]);
      setSelectedMatch(null);
      setSelectedRequirement(requirement);
      setRequirementDrawerTab("matching");
      notify("Agent 匹配任务已提交");
    } catch (caught) {
      notify(caught instanceof Error ? caught.message : "匹配任务提交失败");
    } finally {
      setWorkflowBusy("");
    }
  };

  const saveRequirement = async (form: HTMLFormElement) => {
    if (!selectedRequirement) return;
    const data = new FormData(form);
    const updated = await api<Requirement>(
      `/requirements/${selectedRequirement.id}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          title: data.get("title"),
          raw_input: data.get("raw_input"),
          scenario: data.get("scenario"),
          problem: data.get("problem"),
        }),
      },
      token,
    );
    setSelectedRequirement(updated);
    setRequirements((current) =>
      current.map((item) => (item.id === updated.id ? updated : item)),
    );
    notify("需求内容已保存");
  };

  const openMatch = async (requirement: Requirement, match: MatchRunSummary) => {
    setSelectedMatch(
      await api<MatchRunDetail>(
        `/requirements/${requirement.id}/matches/${match.id}`,
        {},
        token,
      ),
    );
    setRequirementDrawerTab("matching");
  };

  const decideMatchItem = async (
    item: MatchItem,
    decision: "shortlisted" | "rejected" | "adopted",
  ) => {
    if (!selectedRequirement || !selectedMatch) return;
    const updated = await api<MatchItem>(
      `/requirements/${selectedRequirement.id}/matches/${selectedMatch.id}/items/${item.id}/decision`,
      {
        method: "POST",
        body: JSON.stringify({ decision, comment: "在需求匹配结果中完成业务决策" }),
      },
      token,
    );
    setSelectedMatch({
      ...selectedMatch,
      items: selectedMatch.items.map((current) =>
        current.id === updated.id ? updated : current,
      ),
    });
    notify(
      decision === "adopted"
        ? "已标记为采用方案"
        : decision === "rejected"
          ? "已淘汰该候选"
          : "已加入候选清单",
    );
  };

  const approveAchievement = async (achievement: Achievement) => {
    await api(
      `/achievements/${achievement.id}/decision`,
      { method: "POST", body: JSON.stringify({ decision: "approve", comment: "审核通过" }) },
      token,
    );
    notify("成果已审核，并进入向量索引队列");
    setSelectedAchievement(null);
    await refreshAll(token);
  };

  useEffect(() => {
    catalogFilters.current = { query: achievementQuery, page: achievementPage };
    if (!token || view !== "achievements") return;
    let active = true;
    const timer = window.setTimeout(() => {
      api<Page<Achievement>>(`/achievements?page_size=24&page=${achievementPage}&query=${encodeURIComponent(achievementQuery)}`, {}, token)
        .then((data) => { if (active) { setAchievements(data.items); setAchievementTotal(data.total); } })
        .catch((caught) => { if (active) setError(caught instanceof Error ? caught.message : "成果检索失败"); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [achievementQuery, achievementPage, token, view]);

  useEffect(() => {
    if (!token) return;
    const evaluationActive = Object.values(evaluationSummaries).some((item) =>
      ["queued", "running"].includes(item.status),
    );
    const requirementActive = Object.values(requirementSummaries).some(
      (item) =>
        ["queued", "running"].includes(item.latest_refinement_status ?? "") ||
        ["queued", "running"].includes(item.latest_match_status ?? ""),
    );
    if (!evaluationActive && !requirementActive) return;
    const timer = window.setInterval(() => {
      refreshAll(token)
        .then(async () => {
          if (selectedAchievement) {
            setAchievementEvaluations(
              await api<AchievementEvaluation[]>(
                `/achievements/${selectedAchievement.id}/evaluations`,
                {},
                token,
              ),
            );
          }
          if (selectedRequirement) {
            const [requirement, refinements, matches] = await Promise.all([
              api<Requirement>(
                `/requirements/${selectedRequirement.id}`,
                {},
                token,
              ),
              api<RequirementRefinement[]>(
                `/requirements/${selectedRequirement.id}/refinements`,
                {},
                token,
              ),
              api<MatchRunSummary[]>(
                `/requirements/${selectedRequirement.id}/matches`,
                {},
                token,
              ),
            ]);
            setSelectedRequirement(requirement);
            setRequirements((current) =>
              current.map((item) => (item.id === requirement.id ? requirement : item)),
            );
            setRequirementRefinements(refinements);
            setRequirementMatches(matches);
            if (matches[0]?.status === "completed") {
              setSelectedMatch(
                await api<MatchRunDetail>(
                  `/requirements/${selectedRequirement.id}/matches/${matches[0].id}`,
                  {},
                  token,
                ),
              );
            }
          }
        })
        .catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [
    evaluationSummaries,
    refreshAll,
    requirementSummaries,
    selectedAchievement,
    selectedRequirement,
    token,
  ]);

  if (booting) {
    return (
      <main className="boot-screen">
        <div className="brand-mark large"><span className="brand-wave" /></div>
        <RefreshCw className="spin" size={22} />
        <p>正在连接 HydroBridge AI…</p>
      </main>
    );
  }
  if (!user) {
    return <LoginScreen onLogin={login} busy={loginBusy} error={error} />;
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "open" : ""}`}>
        <div className="sidebar-brand">
          <div className="brand-mark"><span className="brand-wave" /></div>
          <div><strong>HydroBridge</strong><span>水利智能决策平台</span></div>
          <button className="mobile-close" onClick={() => setMobileNav(false)}><X /></button>
        </div>
        <nav>
          <p className="nav-section">工作空间</p>
          {navigation.slice(0, 5).map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                className={view === item.key ? "active" : ""}
                onClick={() => {
                  setView(item.key);
                  setMobileNav(false);
                }}
              >
                <Icon size={19} />
                <span>{item.label}</span>
                {item.key === "approvals" && approvals.length > 0 && (
                  <em>{approvals.length}</em>
                )}
              </button>
            );
          })}
          <p className="nav-section second">管理</p>
          {navigation.slice(5).map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                className={view === item.key ? "active" : ""}
                onClick={() => {
                  setView(item.key);
                  setMobileNav(false);
                }}
              >
                <Icon size={19} /><span>{item.label}</span>
              </button>
            );
          })}
        </nav>
        <div className="sidebar-foot">
          <div className="service-pill">
            <span className={readiness?.status === "ready" ? "online" : "partial"} />
            {readiness?.status === "ready" ? "核心服务正常" : "部分服务待配置"}
          </div>
          <div className="user-card">
            <div className="avatar">{user.display_name.slice(0, 1)}</div>
            <div><strong>{user.display_name}</strong><span>{user.email}</span></div>
            <button onClick={logout} title="退出登录"><LogOut size={17} /></button>
          </div>
        </div>
      </aside>
      {mobileNav && <button className="nav-scrim" onClick={() => setMobileNav(false)} />}

      <main className="main-area">
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setMobileNav(true)}><Menu /></button>
          <div>
            <p className="breadcrumb">HYDROBRIDGE / {navigation.find((item) => item.key === view)?.label}</p>
            <h1>{navigation.find((item) => item.key === view)?.label}</h1>
          </div>
          <div className="top-actions">
            <span className="date-chip"><Clock3 size={15} /> 数据更新于 {formatTime(new Date().toISOString())}</span>
            <button
              className="icon-button"
              title="刷新数据"
              onClick={() => refreshAll(token).then(() => notify("数据已刷新"))}
            ><RefreshCw size={18} /></button>
          </div>
        </header>

        <div className="content">
          {view === "workbench" && (
            <Workbench
              overview={overview}
              runs={runs}
              prompt={prompt}
              setPrompt={setPrompt}
              taskType={taskType}
              setTaskType={setTaskType}
              running={running}
              executeRun={executeRun}
              newConversation={() => { setSelectedRun(null); setPrompt(""); setEvents([]); setTrace(null); setTaskType("auto"); }}
              cancelRun={cancelRun}
              selectedRun={selectedRun}
              approvals={approvals}
              decideApproval={decideApproval}
              events={events}
              trace={trace}
              openRun={openRun}
              downloadReport={downloadReport}
              downloadDocument={(documentId) => { downloadSource(documentId, token).catch((caught) => setError(caught instanceof Error ? caught.message : "下载失败")); }}
            />
          )}
          {view === "achievements" && (
            <AchievementsView
              achievements={achievements}
              evaluations={evaluationSummaries}
              query={achievementQuery}
              setQuery={(value) => { setAchievementPage(1); setAchievementQuery(value); }}
              page={achievementPage}
              total={achievementTotal}
              setPage={setAchievementPage}
              open={openAchievement}
              evaluate={startEvaluation}
              busy={workflowBusy}
              create={() => setShowNewAchievement(true)}
            />
          )}
          {view === "requirements" && (
            <RequirementsView
              requirements={requirements}
              summaries={requirementSummaries}
              create={() => setShowNewRequirement(true)}
              open={loadRequirementWorkspace}
              refine={startRefinement}
              runMatching={startMatching}
              busy={workflowBusy}
            />
          )}
          {view === "runs" && (
            <RunsView
              runs={runs}
              selected={selectedRun}
              trace={trace}
              onSelect={openRun}
              downloadReport={downloadReport}
            />
          )}
          {view === "approvals" && (
            <ApprovalsView approvals={approvals} decide={decideApproval} />
          )}
          {view === "system" && <SystemView readiness={readiness} />}
        </div>
      </main>

      {selectedAchievement && (
        <AchievementDrawer
          achievement={selectedAchievement}
          evaluations={achievementEvaluations}
          tab={achievementDrawerTab}
          setTab={setAchievementDrawerTab}
          close={() => setSelectedAchievement(null)}
          approve={approveAchievement}
          evaluate={startEvaluation}
          openRun={(runId) => {
            const run = runs.find((item) => item.id === runId);
            if (run) {
              setSelectedAchievement(null);
              setView("runs");
              openRun(run);
            }
          }}
          busy={workflowBusy}
        />
      )}
      {selectedRequirement && (
        <RequirementDrawer
          requirement={selectedRequirement}
          refinements={requirementRefinements}
          matches={requirementMatches}
          approvals={approvals}
          selectedMatch={selectedMatch}
          tab={requirementDrawerTab}
          setTab={setRequirementDrawerTab}
          close={() => setSelectedRequirement(null)}
          save={saveRequirement}
          refine={startRefinement}
          runMatching={startMatching}
          openMatch={openMatch}
          decide={decideMatchItem}
          decideApproval={decideApproval}
          openAchievement={async (achievementId) => {
            try {
              const achievement = achievements.find((item) => item.id === achievementId)
                ?? await api<Achievement>(`/achievements/${achievementId}`, {}, token);
              setSelectedRequirement(null);
              await openAchievement(achievement);
            } catch (caught) {
              setError(caught instanceof Error ? caught.message : "无法读取成果详情");
            }
          }}
          openRun={(runId) => {
            const run = runs.find((item) => item.id === runId);
            if (run) {
              setSelectedRequirement(null);
              setView("runs");
              openRun(run);
            }
          }}
          busy={workflowBusy}
        />
      )}
      {showNewAchievement && (
        <FormModal title="录入候选成果" close={() => setShowNewAchievement(false)}>
          <form
            className="stack-form"
            onSubmit={(event) => {
              event.preventDefault();
              createAchievement(event.currentTarget).catch((caught) =>
                notify(caught instanceof Error ? caught.message : "创建失败"),
              );
            }}
          >
            <label>成果名称<input name="title" required minLength={2} /></label>
            <label>成果摘要<textarea name="abstract" required rows={4} /></label>
            <label>技术原理<textarea name="technical_principle" rows={4} /></label>
            <div className="form-grid">
              <label>完成单位<input name="organization" placeholder="多个单位用逗号分隔" /></label>
              <label>成熟度（1–9）<input name="maturity_level" type="number" min={1} max={9} placeholder="资料未提供时留空" /></label>
            </div>
            <label>标签<input name="tags" placeholder="智慧水利，监测，安全" /></label>
            <div className="modal-actions">
              <button type="button" className="ghost-button" onClick={() => setShowNewAchievement(false)}>取消</button>
              <button className="primary-button"><Plus size={17} />创建成果</button>
            </div>
          </form>
        </FormModal>
      )}
      {showNewRequirement && (
        <FormModal title="录入并智能凝练需求" close={() => setShowNewRequirement(false)}>
          <form
            className="stack-form"
            onSubmit={(event) => {
              event.preventDefault();
              createRequirement(event.currentTarget).catch((caught) =>
                notify(caught instanceof Error ? caught.message : "创建失败"),
              );
            }}
          >
            <label>需求标题<input name="title" required minLength={2} /></label>
            <label>工程情况与约束<textarea name="raw_input" required minLength={5} rows={8} placeholder="请描述工程对象、当前问题、预算/施工/环境约束与预期效果…" /></label>
            <div className="modal-actions">
              <button type="button" className="ghost-button" onClick={() => setShowNewRequirement(false)}>取消</button>
              <button className="primary-button"><Sparkles size={17} />保存并开始 AI 规范化</button>
            </div>
          </form>
        </FormModal>
      )}
      {toast && <div className="toast"><CheckCircle2 size={18} />{toast}</div>}
    </div>
  );
}

function Workbench({
  overview,
  runs,
  prompt,
  setPrompt,
  taskType,
  setTaskType,
  running,
  executeRun,
  newConversation,
  cancelRun,
  selectedRun,
  approvals,
  decideApproval,
  events,
  trace,
  openRun,
  downloadReport,
  downloadDocument,
}: {
  overview: Overview | null;
  runs: Run[];
  prompt: string;
  setPrompt: (value: string) => void;
  taskType: string;
  setTaskType: (value: string) => void;
  running: boolean;
  executeRun: () => void;
  newConversation: () => void;
  cancelRun: () => void;
  selectedRun: Run | null;
  approvals: Approval[];
  decideApproval: ApprovalDecision;
  events: RunEvent[];
  trace: RunTrace | null;
  openRun: (run: Run) => void;
  downloadReport: (artifactId: string) => void;
  downloadDocument: (documentId: string) => void;
}) {
  const pendingApproval = selectedRun?.status === "waiting_review"
    ? approvals.find((item) => item.run_id === selectedRun.id && item.status === "pending")
    : undefined;
  const clarification = pendingApproval && clarificationReviewPayload(pendingApproval);
  const waitingForReview = selectedRun?.status === "waiting_review" && !clarification;
  const activeEvents =
    events.length > 0
      ? events
      : trace?.steps.map((step) => ({
          event_id: step.id,
          event_type: step.status === "completed" ? "agent.completed" : "agent.started",
          timestamp: step.created_at,
          agent: step.agent_name,
          step: step.agent_name,
          data: step.output_summary,
        })) ?? [];
  return (
    <div className="workbench-grid">
      <section className="workbench-main">
        <div className="hero-panel">
          <div className="hero-copy">
            <span className="live-chip"><Sparkles size={15} /> Agent 决策引擎</span>
            <h2>今天需要分析哪项水利工程问题？</h2>
            {selectedRun && <p>继续当前会话，可追问已找到的成果。<button className="text-button" disabled={running} onClick={newConversation}>新建会话</button></p>}
            <p>直接描述你要做的事，助手会选择相应流程；资料维护、入库审核和候选决策可在管理页面处理。</p>
            <div className="capability-grid">
              <div><strong>成果搜寻与评价</strong><span>联网搜寻 · 入库处理 · 成果评价</span></div>
              <div><strong>需求分析与匹配</strong><span>需求整理 · 信息澄清 · 匹配与调整</span></div>
              <div><strong>成果答疑问答</strong><span>库内检索 · 详情解读 · 追问与对比</span></div>
            </div>
          </div>
          {pendingApproval && clarification && (
            <section className="chat-clarification" aria-live="polite">
              <strong><MessageSquareText size={17} />请补充当前需求</strong>
              <p>{clarification.question}</p>
              <p>直接在下方回复，补充后继续本次任务。</p>
              <details>
                <summary>也可以按字段补充</summary>
                <ClarificationApprovalCard key={pendingApproval.id} item={pendingApproval} decide={decideApproval} compact />
              </details>
            </section>
          )}
          {waitingForReview && <p className="chat-clarification">本次任务等待入库或结果审核，请在审核中心确认。也可以新建会话继续其他任务。</p>}
          <div className="prompt-box">
            {!clarification && <details className="task-options">
              <summary>{taskType === "auto" ? "自动选择任务 · 高级选项" : `指定任务：${taskTypes.find((item) => item.value === taskType)?.label}`}</summary>
              <div className="task-select">
                <GitBranch size={16} />
                <select aria-label="指定任务类型" disabled={running || waitingForReview} value={taskType} onChange={(event) => setTaskType(event.target.value)}>
                  {taskTypes.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
                <ChevronDown size={15} />
              </div>
            </details>}
            <textarea
              aria-label="输入 Agent 任务"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              disabled={running || waitingForReview}
              placeholder={clarification ? "补充工程条件、说明限制或回答上方问题…" : "搜寻成果、评价技术、整理需求、匹配方案，或直接提问…"}
            />
            <div className="prompt-footer">
              <span><ShieldCheck size={15} /> 结论附原文证据，未知约束保留待确认</span>
              {running ? (
                <button className="stop-button" onClick={cancelRun}><Square size={14} fill="currentColor" />停止</button>
              ) : (
                <button className="send-button" onClick={executeRun} disabled={!prompt.trim() || waitingForReview}>
                  {clarification ? "补充并继续" : "发送任务"} <Send size={17} />
                </button>
              )}
            </div>
          </div>
          <div className="prompt-suggestions">
            <span>试试：</span>
            {prompts.map((item) => (
              <button key={item} onClick={() => setPrompt(item)}>{item}</button>
            ))}
          </div>
        </div>

        {selectedRun?.output_payload.answer ? (
          <section className="result-panel">
            <div className="panel-heading">
              <div><span className={`status ${selectedRun.status}`}>{statusLabel(selectedRun.status)}</span><h3>Agent 分析报告</h3></div>
              <div className="report-actions">
                <span>{selectedRun.latency_ms ? `${(selectedRun.latency_ms / 1000).toFixed(1)} 秒` : "刚刚完成"}</span>
                {selectedRun.output_payload.artifact_id && (
                  <button
                    className="download-button"
                    onClick={() => downloadReport(selectedRun.output_payload.artifact_id!)}
                  >
                    <Download size={15} />下载报告
                  </button>
                )}
              </div>
            </div>
            <article className="markdown-body">
              <ReactMarkdown>{selectedRun.output_payload.answer}</ReactMarkdown>
            </article>
            {!!selectedRun.output_payload.citations?.length && (
              <div className="citation-list">
                <strong>引用来源</strong>
                {selectedRun.output_payload.citations.map((citation, index) => (
                  <details key={`${citation.chunk_id ?? citation.url}-${index}`} className="evidence-detail">
                    <summary><span>{index + 1} · </span>{citation.title || "成果原文"}{citation.locator ? ` · ${citation.locator}` : citation.field ? ` · ${evidenceFieldLabels[citation.field] ?? citation.field} · 版本 ${citation.version}` : ""}</summary>
                    {citation.quote && <blockquote>{citation.quote}</blockquote>}
                    {citation.document_id && <button className="text-button" onClick={() => downloadDocument(citation.document_id!)}><Download size={14} />下载原始文档</button>}
                    {citation.url && <a href={citation.url} target="_blank" rel="noreferrer">打开来源<ArrowRight size={14} /></a>}
                  </details>
                ))}
              </div>
            )}
          </section>
        ) : (
          <section className="dashboard-section">
            <div className="section-heading"><div><p className="eyebrow">KNOWLEDGE PULSE</p><h3>平台今日概览</h3></div></div>
            <div className="stat-grid">
              <Stat icon={Library} label="成果知识库" value={overview?.achievements.total ?? 0} meta={`${overview?.achievements.approved ?? 0} 项已审核`} tone="blue" />
              <Stat icon={Gauge} label="平均证据质量" value={`${Math.round((overview?.achievements.average_evidence_score ?? 0) * 100)}%`} meta="基于资料完整性" tone="green" />
              <Stat icon={Activity} label="已完成任务" value={overview?.runs.completed ?? 0} meta={`${overview?.runs.running ?? 0} 项正在运行`} tone="amber" />
              <Stat icon={ClipboardCheck} label="待人工审核" value={overview?.pending_approvals ?? 0} meta="人机协同控制点" tone="purple" />
            </div>
            <div className="recent-panel">
              <div className="panel-heading"><div><h3>最近运行</h3><p>查看 Agent 的执行状态与结果</p></div></div>
              {runs.length ? runs.slice(0, 5).map((run) => (
                <button className="run-row" key={run.id} onClick={() => openRun(run)}>
                  <div className="run-icon"><Bot size={19} /></div>
                  <div className="run-title"><strong>{run.input_payload.message ?? "Agent 任务"}</strong><span>{formatTime(run.created_at)} · {taskTypes.find((item) => item.value === run.task_type)?.label ?? run.task_type}</span></div>
                  <span className={`status ${run.status}`}>{statusLabel(run.status)}</span>
                  <ArrowRight size={17} />
                </button>
              )) : <EmptyState icon={MessageSquareText} title="还没有运行记录" body="在上方输入一个问题，创建第一条可追踪的 Agent 任务。" />}
            </div>
          </section>
        )}
      </section>

      <aside className="execution-panel">
        <div className="execution-head">
          <div><p className="eyebrow">LIVE TRACE</p><h3>执行轨迹</h3></div>
          {running && <span className="running-pulse"><i />实时</span>}
        </div>
        {activeEvents.length ? (
          <div className="timeline">
            {activeEvents.map((event, index) => (
              <div className={`timeline-item ${event.event_type.includes("failed") ? "failed" : ""}`} key={`${event.event_id}-${index}`}>
                <div className="timeline-marker">
                  {event.event_type === "agent.completed" || event.event_type === "run.completed" ? <Check size={13} /> : event.event_type === "tool.started" || event.event_type === "tool.completed" ? <Zap size={13} /> : <span />}
                </div>
                <div>
                  <strong>{eventLabels[event.event_type] ?? event.event_type}</strong>
                  <p>{agentNames[event.agent] ?? (event.agent || "系统工作流")}</p>
                  <time>{formatTime(event.timestamp)}</time>
                </div>
              </div>
            ))}
            {running && <div className="timeline-item current"><div className="timeline-marker"><span /></div><div><strong>正在处理下一步</strong><p>结果将通过事件流实时返回</p></div></div>}
          </div>
        ) : (
          <EmptyState icon={Network} title="等待任务开始" body="Agent 的规划、检索、评分、核验和工具调用会实时显示在这里。" />
        )}
        <div className="agent-roster">
          <p>成果助手可用工具</p>
          <div className="tool-labels">
            {["联网搜寻", "成果评价", "需求规范化", "成果搜索", "需求匹配", "成果详情", "成果对比", "需求更新"].map((label) => (
              <span key={label}>{label}</span>
            ))}
          </div>
        </div>
      </aside>
    </div>
  );
}

function Stat({ icon: Icon, label, value, meta, tone }: { icon: typeof Bot; label: string; value: string | number; meta: string; tone: string }) {
  return (
    <div className="stat-card">
      <div className={`stat-icon ${tone}`}><Icon size={20} /></div>
      <div><span>{label}</span><strong>{value}</strong><p>{meta}</p></div>
    </div>
  );
}

const evaluationDimensionLabels: Record<string, string> = {
  innovation: "创新性",
  effectiveness: "技术有效性",
  maturity: "工程成熟度",
  evidence_quality: "证据充分性",
  promotion_value: "推广复制价值",
  implementation_feasibility: "经济与实施可行性",
};

const matchDimensionLabels: Record<string, string> = {
  scenario_fit: "场景适配",
  effectiveness: "技术效果",
  maturity: "成熟度",
  evidence_quality: "证据质量",
  cost_fit: "成本适配",
  implementation_risk: "实施风险",
};

function workflowStatus(status: string) {
  return ["queued", "running"].includes(status) ? "处理中" : statusLabel(status);
}

function ScoreBadge({ score, label }: { score: number; label?: string }) {
  return (
    <span className={`score-badge ${score >= 80 ? "high" : score >= 65 ? "medium" : "low"}`}>
      <strong>{Math.round(score)}</strong>
      {label ?? "分"}
    </span>
  );
}

function AchievementsView({
  achievements,
  evaluations,
  query,
  setQuery,
  open,
  evaluate,
  busy,
  create,
  page,
  total,
  setPage,
}: {
  achievements: Achievement[];
  evaluations: Record<string, AchievementEvaluation>;
  query: string;
  setQuery: (value: string) => void;
  open: (item: Achievement, tab?: "overview" | "evaluation") => void;
  evaluate: (item: Achievement) => void;
  busy: string;
  create: () => void;
  page: number;
  total: number;
  setPage: (page: number) => void;
}) {
  return (
    <div className="page-stack">
      <section className="page-intro">
        <div><p className="eyebrow">VERIFIED KNOWLEDGE</p><h2>水利科技成果知识库</h2><p>结构化成果、工程案例与证据来源共同支撑 Agent 检索和匹配。</p></div>
        <button className="primary-button" onClick={create}><Plus size={17} />录入成果</button>
      </section>
      <section className="table-panel">
        <div className="toolbar">
          <div className="search-input"><Search size={17} /><input placeholder="搜索全部成果标题或摘要" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
          <span>共 {total} 条 · 第 {page} / {Math.max(1, Math.ceil(total / 24))} 页</span>
          <button className="outline-button compact" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
          <button className="outline-button compact" disabled={page * 24 >= total} onClick={() => setPage(page + 1)}>下一页</button>
        </div>
        <div className="achievement-grid">
          {achievements.map((item) => {
            const evaluation = evaluations[item.id];
            const active = evaluation && ["queued", "running"].includes(evaluation.status);
            return (
              <article className="achievement-card enhanced" key={item.id}>
                <button className="card-main" onClick={() => open(item)}>
                  <div className="achievement-top">
                    <div className="badge-cluster">
                      <span className={`status ${item.status}`}>{statusLabel(item.status)}</span>
                      {active && <span className="status running"><RefreshCw className="spin" size={12} />评价中</span>}
                    </div>
                    {evaluation?.status === "completed" ? (
                      <ScoreBadge score={evaluation.total_score} />
                    ) : (
                      <span className="evidence-score"><ShieldCheck size={14} />证据 {Math.round(item.evidence_score * 100)}</span>
                    )}
                  </div>
                  <h3>{item.title}</h3>
                  <p className="clamp-three">{item.abstract || "暂无成果摘要"}</p>
                  <div className="tag-row">{item.tags.slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}</div>
                  <div className="achievement-foot">
                    <span><Gauge size={15} /> TRL {item.maturity_level ?? "—"}</span>
                    <span><Database size={15} /> {item.metrics.length} 项指标</span>
                    <span><ShieldCheck size={15} /> 证据 {Math.round(item.evidence_score * 100)}</span>
                  </div>
                </button>
                <div className="card-actions">
                  <button className="text-button" onClick={() => open(item)}><FileSearch size={15} />查看详情</button>
                  {evaluation?.status === "completed" && (
                    <button className="text-button" onClick={() => open(item, "evaluation")}><Award size={15} />查看评价</button>
                  )}
                  <button
                    className="outline-button compact"
                    onClick={() => evaluate(item)}
                    disabled={active || busy === `evaluation:${item.id}`}
                  >
                    {active || busy === `evaluation:${item.id}` ? <RefreshCw className="spin" size={15} /> : <Sparkles size={15} />}
                    {evaluation?.status === "completed" ? "重新评价" : "智能评价"}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
        {!achievements.length && <EmptyState icon={Library} title="没有找到成果" body="尝试调整搜索词，或录入一条新的候选成果。" />}
      </section>
    </div>
  );
}

function RequirementsView({
  requirements,
  summaries,
  create,
  open,
  refine,
  runMatching,
  busy,
}: {
  requirements: Requirement[];
  summaries: Record<string, RequirementWorkflowSummary>;
  create: () => void;
  open: (item: Requirement, tab?: "profile" | "matching" | "history") => void;
  refine: (item: Requirement) => void;
  runMatching: (item: Requirement) => void;
  busy: string;
}) {
  return (
    <div className="page-stack">
      <section className="page-intro">
        <div><p className="eyebrow">DEMAND INTELLIGENCE</p><h2>需求与匹配管理</h2><p>将模糊工程描述整理为可复用、可追踪、可直接用于成果匹配的规范化需求。</p></div>
        <button className="primary-button" onClick={create}><Plus size={17} />新建需求</button>
      </section>
      <section className="table-panel">
        <div className="requirement-list">
          {requirements.map((item) => (
            <article className="requirement-card" key={item.id}>
              <div className="requirement-index"><FileSearch size={21} /></div>
              <div className="requirement-body">
                <div><h3>{item.title}</h3><span>{formatTime(item.updated_at)}</span></div>
                <p className="clamp-two">{item.normalized_description || item.raw_input}</p>
                <div className="tag-row">
                  {item.scenario && <span>{item.scenario}</span>}
                  <span>完整度 {Math.round(item.completeness_score * 100)}%</span>
                  {item.completeness_score < 0.7 && (
                    <span className="quality-warning"><CircleAlert size={13} />信息待补齐</span>
                  )}
                  <span>硬约束 {Object.keys(item.hard_constraints).length} 项</span>
                  <span className={`status ${item.status}`}>{statusLabel(item.status)}</span>
                </div>
                {summaries[item.id]?.latest_match_status && (
                  <div className="requirement-result-line">
                    <Network size={14} />最近匹配：{workflowStatus(summaries[item.id].latest_match_status ?? "")}
                    {summaries[item.id].latest_match_score !== null && (
                      <strong>最高 {Math.round(summaries[item.id].latest_match_score ?? 0)} 分</strong>
                    )}
                    <span>{summaries[item.id].latest_match_candidate_count} 个候选</span>
                  </div>
                )}
              </div>
              <div className="requirement-actions">
                <button className="text-button" onClick={() => open(item)}><FileSearch size={15} />详情与编辑</button>
                <button
                  className="text-button"
                  onClick={() => refine(item)}
                  disabled={busy === `refinement:${item.id}`}
                ><Sparkles size={15} />重新规范化</button>
                {summaries[item.id]?.latest_match_id && (
                  <button className="text-button" onClick={() => open(item, "history")}><Clock3 size={15} />匹配历史</button>
                )}
                <button
                  className="outline-button"
                  onClick={() => runMatching(item)}
                  disabled={busy === `matching:${item.id}`}
                >
                  {busy === `matching:${item.id}` ? <RefreshCw className="spin" size={15} /> : <Play size={15} />}
                  {item.missing_fields.length ? "补充后匹配" : item.completeness_score < 0.7 ? "探索性匹配" : "开始匹配"}
                </button>
              </div>
            </article>
          ))}
          {!requirements.length && <EmptyState icon={FileSearch} title="还没有工程需求" body="创建需求后可直接交给Agent 完成诊断与成果匹配。" />}
        </div>
      </section>
    </div>
  );
}

function RunsView({
  runs,
  selected,
  trace,
  onSelect,
  downloadReport,
}: {
  runs: Run[];
  selected: Run | null;
  trace: RunTrace | null;
  onSelect: (run: Run) => void;
  downloadReport: (artifactId: string) => void;
}) {
  return (
    <div className="split-view">
      <section className="list-pane">
        <div className="pane-heading"><p className="eyebrow">RUN HISTORY</p><h2>全部运行</h2><span>{runs.length} 条</span></div>
        <div className="run-list">
          {runs.map((run) => (
            <button className={selected?.id === run.id ? "selected" : ""} key={run.id} onClick={() => onSelect(run)}>
              <div className="run-icon"><Bot size={18} /></div>
              <div><strong>{run.input_payload.message ?? "Agent 任务"}</strong><span>{formatTime(run.created_at)}</span></div>
              <span className={`status ${run.status}`}>{statusLabel(run.status)}</span>
            </button>
          ))}
        </div>
      </section>
      <section className="detail-pane">
        {selected ? (
          <>
            <div className="detail-head">
              <div><span className={`status ${selected.status}`}>{statusLabel(selected.status)}</span><h2>{selected.input_payload.message}</h2></div>
              <div className="metric-strip">
                <span>耗时<strong>{selected.latency_ms ? `${(selected.latency_ms / 1000).toFixed(1)}s` : "—"}</strong></span>
                <span>Token<strong>{selected.total_input_tokens + selected.total_output_tokens}</strong></span>
                {selected.output_payload.artifact_id && (
                  <button
                    className="download-button compact"
                    onClick={() => downloadReport(selected.output_payload.artifact_id!)}
                  >
                    <Download size={15} />下载报告
                  </button>
                )}
              </div>
            </div>
            <div className="trace-summary">
              <h3>执行链路</h3>
              <div className="trace-chain">
                {trace?.steps.map((step, index) => (
                  <div key={step.id}>
                    <span className={`trace-node ${step.status}`}><Check size={14} /></span>
                    <strong>{agentNames[step.agent_name] ?? step.agent_name}</strong>
                    <small>{step.latency_ms}ms</small>
                    {index < trace.steps.length - 1 && <i />}
                  </div>
                ))}
              </div>
              {!!trace?.tool_calls.length && (
                <div className="tool-calls">
                  <h4>工具调用</h4>
                  {trace.tool_calls.map((tool) => <span key={tool.id}><Zap size={14} />{tool.tool_name}<em>{tool.latency_ms}ms · 重试 {tool.retry_count}</em></span>)}
                </div>
              )}
            </div>
            <article className="markdown-body detail-report">
              {selected.output_payload.answer ? <ReactMarkdown>{selected.output_payload.answer}</ReactMarkdown> : <EmptyState icon={Clock3} title="任务尚未产生最终报告" body={selected.error_message || `当前状态：${statusLabel(selected.status)}`} />}
            </article>
          </>
        ) : <EmptyState icon={Activity} title="选择一条运行记录" body="这里会展示完整 Agent 链路、工具调用、耗时和最终报告。" />}
      </section>
    </div>
  );
}

type ResearchReviewCandidate = {
  candidate_id: string;
  title: string;
  abstract?: string;
  organizations?: string[];
  confidence: number;
  score_components?: Record<string, number>;
  review_reasons?: string[];
  source_urls?: string[];
};

type ApprovalDecision = (
  item: Approval,
  decision: "approve" | "edit" | "reject",
  editedPayload?: Record<string, unknown>,
) => Promise<void>;

type ClarificationField = {
  key: string;
  label: string;
  reason?: string;
  placeholder?: string;
  suggestions?: string[];
  input_type?: string;
  required?: boolean;
};

type ClarificationReviewPayload = {
  title: string;
  description: string;
  question: string;
  completeness_score: number;
  fields: ClarificationField[];
  current_profile: Record<string, unknown>;
  original_message: string;
  resume_action: string;
};

const clarificationSuggestionDefaults: Record<string, string[]> = {
  scenario_and_problem: [
    "北方农村供水泵站，冬季低温，人工巡检成本高且故障发现不及时",
    "老旧城区供水管网漏损率较高，爆管定位慢，夜间巡检困难",
    "大中型灌区渠系调度依赖人工经验，来水波动时配水不准确",
  ],
  engineering_object: ["农村供水泵站及其机电设备", "老旧城区输配水管网", "大中型灌区干支渠和闸门群"],
  core_problem: [
    "设备依赖人工巡检，故障发现不及时，非计划停机风险较高",
    "管网漏损定位慢，缺少分区计量和异常用水识别能力",
    "调度依赖人工经验，来水变化时配水效率和准确性不足",
  ],
  site_constraints: [
    "冬季最低温度较低，设备需适应高寒和室外潮湿环境",
    "工程需要连续供水，改造期间不能长时间停水",
    "现场通信条件一般，部分点位供电和网络不稳定",
  ],
  hard_constraints: [
    "必须适用于低温环境，并支持室外长期稳定运行",
    "实施期间不得造成长时间停水，需支持分段改造",
    "方案必须兼容现有设备和通信协议，不能整体更换系统",
  ],
  budget_constraints: [
    "预算有限，优先利用现有设备进行低成本改造",
    "可以分阶段建设，首期优先覆盖高风险和关键点位",
    "更关注全生命周期成本，优先选择低维护、易运维方案",
  ],
};

function approvalWorkflowPayload(item: Approval) {
  const payload = item.payload.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  return payload as Record<string, unknown>;
}

function clarificationReviewPayload(item: Approval): ClarificationReviewPayload | null {
  const payload = approvalWorkflowPayload(item);
  if (!payload || payload.type !== "clarification") return null;
  const missingFields = Array.isArray(payload.missing_fields)
    ? payload.missing_fields.map(String)
    : [];
  const suppliedFields = Array.isArray(payload.fields)
    ? payload.fields as ClarificationField[]
    : [];
  const rawFields: ClarificationField[] = suppliedFields.length
    ? suppliedFields
    : missingFields.map((label, index) => ({
        key: `additional_context_${index + 1}`,
        label,
        reason: "该信息会影响候选成果的筛选和匹配结果。",
        placeholder: `请补充${label}`,
        input_type: "textarea",
        required: true,
      }));
  const fields = rawFields.map((field) => ({
    ...field,
    suggestions: field.suggestions?.length
      ? field.suggestions
      : clarificationSuggestionDefaults[field.key] ?? [],
  }));
  return {
    title: String(payload.title || "需求信息还不完整"),
    description: String(payload.description || "补充关键信息后，系统会自动继续原来的任务。"),
    question: String(payload.question || item.reason),
    completeness_score: Number(payload.completeness_score || 0),
    fields,
    current_profile:
      payload.current_profile && typeof payload.current_profile === "object" && !Array.isArray(payload.current_profile)
        ? payload.current_profile as Record<string, unknown>
        : {},
    original_message: String(payload.original_message || ""),
    resume_action: String(payload.resume_action || "requirement_matching"),
  };
}

function ClarificationApprovalCard({
  item,
  decide,
  compact = false,
}: {
  item: Approval;
  decide: ApprovalDecision;
  compact?: boolean;
}) {
  const payload = clarificationReviewPayload(item);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  if (!payload) return null;
  const requiredComplete = payload.fields
    .filter((field) => field.required !== false)
    .every((field) => answers[field.key]?.trim());
  const profile = payload.current_profile;
  const objectives = Array.isArray(profile.objectives) ? profile.objectives.map(String) : [];
  const submit = async () => {
    const additions = payload.fields
      .map((field) => `${field.label}：${answers[field.key]?.trim() || "未提供"}`)
      .join("\n");
    const message = [
      payload.original_message && `原始需求：${payload.original_message}`,
      additions && `用户补充信息：\n${additions}`,
    ].filter(Boolean).join("\n\n");
    setSubmitting(true);
    setSubmitError("");
    try {
      await decide(item, "edit", { message, fields: answers });
    } catch (caught) {
      setSubmitError(caught instanceof Error ? caught.message : "补充信息提交失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  };

  const reject = async () => {
    setSubmitting(true);
    setSubmitError("");
    try {
      await decide(item, "reject");
    } catch (caught) {
      setSubmitError(caught instanceof Error ? caught.message : "任务取消失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <article className={`approval-card clarification-review-card ${compact ? "compact" : ""}`}>
      <div className="approval-head">
        <span className="risk"><MessageSquareText size={15} />需求补充</span>
        <time>{formatTime(item.created_at)}</time>
      </div>
      <div className="clarification-title-row">
        <div>
          <p className="eyebrow">需求规范化 · HUMAN IN THE LOOP</p>
          <h3>{payload.title}</h3>
          <p>{payload.description}</p>
        </div>
        <div className="clarification-score">
          <strong>{Math.round(payload.completeness_score * 100)}%</strong>
          <span>当前完整度</span>
        </div>
      </div>
      {(profile.scenario || profile.problem || objectives.length > 0) && (
        <section className="clarification-understanding">
          <strong>系统已经理解</strong>
          <div>
            {Boolean(profile.scenario) && <span><small>工程场景</small>{String(profile.scenario)}</span>}
            {Boolean(profile.problem) && <span><small>核心问题</small>{String(profile.problem)}</span>}
            {objectives.length > 0 && <span><small>工程目标</small>{objectives.join("、")}</span>}
          </div>
        </section>
      )}
      {payload.question && <p className="clarification-question"><CircleAlert size={16} />{payload.question}</p>}
      <div className="clarification-fields">
        {payload.fields.map((field, index) => (
          <label key={field.key}>
            <span>{index + 1}. {field.label}{field.required !== false && <em>必填</em>}</span>
            {field.reason && <small>{field.reason}</small>}
            {!!field.suggestions?.length && (
              <div className="clarification-suggestions">
                <p>推荐示例 <small>点击即可填入，也可以继续修改</small></p>
                <div>
                  {field.suggestions.map((suggestion) => (
                    <button
                      type="button"
                      className={answers[field.key] === suggestion ? "selected" : ""}
                      key={suggestion}
                      onClick={() => setAnswers((current) => ({
                        ...current,
                        [field.key]: current[field.key] === suggestion ? "" : suggestion,
                      }))}
                    >
                      <Check size={14} />{suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <textarea
              rows={compact ? 2 : 3}
              value={answers[field.key] ?? ""}
              placeholder={field.placeholder || `请补充${field.label}`}
              onChange={(event) => setAnswers((current) => ({ ...current, [field.key]: event.target.value }))}
            />
          </label>
        ))}
      </div>
      {submitError && <p className="clarification-submit-error"><CircleAlert size={15} />{submitError}</p>}
      <div className="approval-actions clarification-actions">
        <span>提交后将自动重新规范化需求，并继续{payload.resume_action === "requirement_matching" ? "成果匹配" : "生成规范化结果"}</span>
        <button className="reject-button" disabled={submitting} onClick={reject}><XCircle size={17} />取消本次任务</button>
        <button className="approve-button" disabled={!requiredComplete || submitting} onClick={submit}>
          {submitting ? <RefreshCw className="spin" size={17} /> : <Send size={17} />}
          {submitting ? "正在重新规范化…" : "补充完成并继续"}
        </button>
      </div>
    </article>
  );
}

function researchReviewPayload(item: Approval) {
  const typedPayload = approvalWorkflowPayload(item);
  if (!typedPayload) return null;
  if (typedPayload.type !== "research_ingestion" || !Array.isArray(typedPayload.candidates)) {
    return null;
  }
  return {
    candidates: typedPayload.candidates as ResearchReviewCandidate[],
    summary: (typedPayload.summary ?? {}) as Record<string, number>,
  };
}

function ResearchIngestionApprovalCard({ item, decide }: { item: Approval; decide: ApprovalDecision }) {
  const payload = researchReviewPayload(item);
  const candidates = useMemo(() => payload?.candidates ?? [], [payload]);
  const [selectedIds, setSelectedIds] = useState<string[]>(() =>
    candidates.map((candidate) => candidate.candidate_id),
  );
  const selected = new Set(selectedIds);
  const toggle = (candidateId: string) => {
    setSelectedIds((current) =>
      current.includes(candidateId)
        ? current.filter((id) => id !== candidateId)
        : [...current, candidateId],
    );
  };

  if (!payload) return null;
  return (
    <article className="approval-card research-review-card">
      <div className="approval-head">
        <span className="risk"><CircleAlert size={15} />灰区成果批量审核</span>
        <time>{formatTime(item.created_at)}</time>
      </div>
      <p className="eyebrow">成果找寻 · HUMAN IN THE LOOP</p>
      <h3>{item.reason}</h3>
      <div className="review-routing-summary">
        <span><strong>{payload.summary.auto_approved ?? 0}</strong>高分自动入库</span>
        <span><strong>{candidates.length}</strong>待人工确认</span>
        <span><strong>{payload.summary.rejected ?? 0}</strong>低分自动淘汰</span>
      </div>
      <div className="research-review-list">
        {candidates.map((candidate) => (
          <label className={`research-review-item ${selected.has(candidate.candidate_id) ? "selected" : ""}`} key={candidate.candidate_id}>
            <input
              type="checkbox"
              checked={selected.has(candidate.candidate_id)}
              onChange={() => toggle(candidate.candidate_id)}
            />
            <span className="review-check"><Check size={13} /></span>
            <span className="review-candidate-main">
              <span className="review-candidate-title">
                <strong>{candidate.title}</strong>
                <em>{Number(candidate.confidence).toFixed(1)} 分</em>
              </span>
              {!!candidate.organizations?.length && <small>{candidate.organizations.join(" · ")}</small>}
              {candidate.abstract && <p>{candidate.abstract.slice(0, 150)}</p>}
              {!!candidate.review_reasons?.length && (
                <span className="review-reasons">
                  {candidate.review_reasons.map((reason) => <i key={reason}>{reason}</i>)}
                </span>
              )}
              {!!candidate.source_urls?.length && (
                <span className="review-source-links">
                  {candidate.source_urls.slice(0, 3).map((url, index) => (
                    <a key={url} href={url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>
                      来源 {index + 1}
                    </a>
                  ))}
                </span>
              )}
            </span>
          </label>
        ))}
      </div>
      <div className="approval-actions review-batch-actions">
        <span>已选择 {selectedIds.length}/{candidates.length} 项</span>
        <button className="reject-button" onClick={() => decide(item, "reject")}><XCircle size={17} />全部淘汰</button>
        <button
          className="approve-button"
          disabled={!selectedIds.length}
          onClick={() => decide(item, "approve", { approved_candidate_ids: selectedIds })}
        ><CheckCircle2 size={17} />入库所选成果</button>
      </div>
    </article>
  );
}

function ApprovalsView({ approvals, decide }: { approvals: Approval[]; decide: ApprovalDecision }) {
  return (
    <div className="page-stack">
      <section className="page-intro">
        <div><p className="eyebrow">HUMAN IN THE LOOP</p><h2>人工审核队列</h2><p>补充关键信息、确认高风险结论，让自动化始终处于人的控制之下。</p></div>
        <div className="review-count"><ShieldCheck size={21} /><strong>{approvals.length}</strong><span>项待处理</span></div>
      </section>
      <section className="approval-grid">
        {approvals.map((item) => clarificationReviewPayload(item) ? (
          <ClarificationApprovalCard item={item} decide={decide} key={item.id} />
        ) : researchReviewPayload(item) ? (
          <ResearchIngestionApprovalCard item={item} decide={decide} key={item.id} />
        ) : (
          <article className="approval-card" key={item.id}>
            <div className="approval-head">
              <span className="risk"><CircleAlert size={15} />{item.risk_level === "medium" ? "中等风险" : item.risk_level}</span>
              <time>{formatTime(item.created_at)}</time>
            </div>
            <p className="eyebrow">{agentNames[item.requested_by_agent] ?? item.requested_by_agent}</p>
            <h3>{item.reason}</h3>
            <pre>{JSON.stringify(item.payload, null, 2)}</pre>
            <div className="approval-actions">
              <button className="reject-button" onClick={() => decide(item, "reject")}><XCircle size={17} />驳回</button>
              <button className="approve-button" onClick={() => decide(item, "approve")}><CheckCircle2 size={17} />确认并继续</button>
            </div>
          </article>
        ))}
        {!approvals.length && <div className="full-empty"><EmptyState icon={CheckCircle2} title="审核队列已经清空" body="当前没有需要人工介入的 Agent 任务。" /></div>}
      </section>
    </div>
  );
}

function SystemView({ readiness }: { readiness: Readiness | null }) {
  const checks = readiness?.checks;
  const services = [
    { name: "PostgreSQL", desc: "业务数据与审计记录", ok: !!checks?.database, icon: Database },
    { name: "Redis Runtime", desc: "事件流与任务队列", ok: typeof checks?.redis === "object" && checks.redis.runtime, icon: Zap },
    { name: "Redis Cache", desc: "Tavily 搜索结果缓存", ok: typeof checks?.redis === "object" && checks.redis.cache, icon: Layers3 },
    { name: "Elasticsearch", desc: "BM25 + 向量分块检索", ok: !!checks?.elasticsearch, icon: Network },
    { name: "MinIO", desc: "原始资料与报告制品", ok: !!checks?.minio, icon: Database },
    { name: "LLM", desc: "OpenAI 兼容模型服务", ok: !!checks?.model_configured, icon: BrainCircuit, optional: true },
    { name: "Tavily", desc: "联网检索与网页抽取", ok: !!checks?.tavily_configured, icon: Search, optional: true },
  ];
  return (
    <div className="page-stack">
      <section className="system-hero">
        <div className={`system-orb ${readiness?.status === "ready" ? "healthy" : ""}`}><Activity size={28} /></div>
        <div><p className="eyebrow">SYSTEM READINESS</p><h2>{readiness?.status === "ready" ? "所有核心服务运行正常" : "部分核心服务尚未连接"}</h2><p>“未连接”表示当前运行环境没有启动对应服务，并不代表 Agent 业务逻辑出错。完整环境需要 PostgreSQL、Redis、Elasticsearch 和 MinIO 全部就绪。</p></div>
      </section>
      <section className="service-grid">
        {services.map((service) => {
          const Icon = service.icon;
          return (
            <article key={service.name}>
              <div className="service-icon"><Icon size={20} /></div>
              <div><h3>{service.name}</h3><p>{service.desc}</p></div>
              <span className={service.ok ? "service-ok" : service.optional ? "service-wait" : "service-error"}>
                {service.ok ? <CheckCircle2 size={16} /> : service.optional ? <Clock3 size={16} /> : <CircleAlert size={16} />}
                {service.ok ? "正常" : service.optional ? "待配置" : "未连接"}
              </span>
            </article>
          );
        })}
      </section>
      <section className="architecture-card">
        <div><p className="eyebrow">ARCHITECTURE</p><h3>一条任务如何流经平台</h3></div>
        <div className="architecture-flow">
          {[
            ["Web 工作台", "需求与反馈"],
            ["FastAPI", "认证与 SSE"],
            ["LangGraph", "Agent 状态机"],
            ["Celery", "异步与重试"],
            ["PostgreSQL / Redis", "持久化与恢复"],
            ["Elasticsearch", "混合知识检索"],
          ].map(([name, desc], index, all) => (
            <div key={name}><span>{index + 1}</span><strong>{name}</strong><small>{desc}</small>{index < all.length - 1 && <ArrowRight size={17} />}</div>
          ))}
        </div>
      </section>
    </div>
  );
}

function AchievementDrawer({
  achievement,
  evaluations,
  tab,
  setTab,
  close,
  approve,
  evaluate,
  openRun,
  busy,
}: {
  achievement: Achievement;
  evaluations: AchievementEvaluation[];
  tab: "overview" | "evaluation";
  setTab: (tab: "overview" | "evaluation") => void;
  close: () => void;
  approve: (item: Achievement) => void;
  evaluate: (item: Achievement) => void;
  openRun: (runId: string) => void;
  busy: string;
}) {
  const latest = evaluations[0];
  const evaluating = latest && ["queued", "running"].includes(latest.status);
  return (
    <div className="drawer-layer" role="dialog" aria-modal="true">
      <button className="drawer-scrim" onClick={close} aria-label="关闭" />
      <aside className="drawer business-drawer">
        <div className="drawer-head">
          <div><span className={`status ${achievement.status}`}>{statusLabel(achievement.status)}</span><p>成果详情 · V{achievement.version}</p></div>
          <button className="icon-button" onClick={close}><X size={19} /></button>
        </div>
        <div className="drawer-title-row">
          <div><h2>{achievement.title}</h2><p>{achievement.organizations.join(" · ") || "完成单位待补充"}</p></div>
          {latest?.status === "completed" && <ScoreBadge score={latest.total_score} label={latest.level} />}
        </div>
        <div className="drawer-tabs">
          <button className={tab === "overview" ? "active" : ""} onClick={() => setTab("overview")}><Layers3 size={16} />成果概览</button>
          <button className={tab === "evaluation" ? "active" : ""} onClick={() => setTab("evaluation")}><Award size={16} />智能评价{evaluations.length > 0 && <em>{evaluations.length}</em>}</button>
        </div>

        {tab === "overview" ? (
          <div className="drawer-scroll-area">
            <div className="drawer-kpis"><span><strong>{Math.round(achievement.evidence_score * 100)}</strong>证据质量</span><span><strong>{achievement.maturity_level ?? "—"}</strong>技术成熟度</span><span><strong>{achievement.metrics.length}</strong>量化指标</span><span><strong>{evaluations.length}</strong>评价版本</span></div>
            <section><h3>成果摘要</h3><p>{achievement.abstract || "暂无摘要"}</p></section>
            <section><h3>技术原理</h3><p>{achievement.technical_principle || "暂无技术原理说明"}</p></section>
            {!!achievement.organizations.length && <section><h3>完成单位</h3><div className="tag-row">{achievement.organizations.map((item) => <span key={item}>{item}</span>)}</div></section>}
            {!!achievement.application_scenarios.length && <section><h3>应用与工程场景</h3><ul>{achievement.application_scenarios.slice(0, 8).map((item, index) => <li key={index}>{item}</li>)}</ul></section>}
            {!!achievement.metrics.length && <section><h3>关键指标</h3><div className="metrics-table">{achievement.metrics.slice(0, 10).map((metric, index) => <div key={index}><span>{String(metric.name ?? "指标")}</span><strong>{String(metric.value ?? "—")}</strong></div>)}</div></section>}
          </div>
        ) : (
          <div className="drawer-scroll-area evaluation-workspace">
            {!latest && (
              <EmptyState icon={Award} title="尚未评价" body="启动智能评价后，系统会保存六维评分、证据、风险和每一次评价版本。" />
            )}
            {evaluating && (
              <div className="workflow-progress-card">
                <RefreshCw className="spin" size={24} />
                <div><strong>Agent 正在评价这项成果</strong><p>成果分析、六维评分、独立核验和报告生成完成后会自动回写。</p></div>
                <span>{workflowStatus(latest.status)}</span>
              </div>
            )}
            {latest?.status === "waiting_review" && (
              <div className="workflow-progress-card warning"><CircleAlert size={24} /><div><strong>评价结果等待人工审核</strong><p>证据或风险需要专家确认，可以到“审核中心”中处理。</p></div></div>
            )}
            {latest?.status === "failed" && (
              <div className="workflow-progress-card danger"><XCircle size={24} /><div><strong>本次评价失败</strong><p>可重新评价；原有成功版本不会被覆盖。</p></div></div>
            )}
            {latest?.status === "completed" && (
              <>
                <div className="evaluation-hero">
                  <div className="evaluation-score"><strong>{Math.round(latest.total_score)}</strong><span>/ 100</span><em>{latest.level}</em></div>
                  <div><p className="eyebrow">LATEST EVALUATION · V{latest.evaluation_version}</p><h3>{latest.summary || "成果综合评价已完成"}</h3><div className="evaluation-meta"><span>置信度 {Math.round(latest.confidence * 100)}%</span><span>核验：{statusLabel(latest.verification_status)}</span><span>{formatTime(latest.created_at)}</span></div></div>
                </div>
                <section><h3>六维评分</h3><div className="dimension-list">{Object.entries(latest.dimension_scores).map(([key, score]) => <div className="dimension-card" key={key}><div><strong>{evaluationDimensionLabels[key] ?? key}</strong><span>{Math.round(score)} 分</span></div><div className="score-track"><i style={{ width: `${Math.max(0, Math.min(100, score))}%` }} /></div><p>{latest.dimension_details[key] || "暂无维度说明"}</p></div>)}</div></section>
                {!!latest.strengths.length && <section><h3>主要优势</h3><ul className="insight-list positive">{latest.strengths.map((item, index) => <li key={index}><CheckCircle2 size={16} />{item}</li>)}</ul></section>}
                {!!latest.risks.length && <section><h3>风险与限制</h3><ul className="insight-list warning">{latest.risks.map((item, index) => <li key={index}><CircleAlert size={16} />{item}</li>)}</ul></section>}
                {!!latest.missing_information.length && <section><h3>待补充信息</h3><ul className="insight-list">{latest.missing_information.map((item, index) => <li key={index}><ListChecks size={16} />{item}</li>)}</ul></section>}
                {!!latest.recommendations.length && <section><h3>改进建议</h3><ul>{latest.recommendations.map((item, index) => <li key={index}>{item}</li>)}</ul></section>}
              </>
            )}
            {evaluations.length > 1 && (
              <section><h3>评价历史</h3><div className="history-list">{evaluations.map((item) => <button key={item.id} className={item.id === latest?.id ? "active" : ""}><span>V{item.evaluation_version} · {formatTime(item.created_at)}</span><strong>{item.status === "completed" ? `${Math.round(item.total_score)} 分 · ${item.level}` : workflowStatus(item.status)}</strong></button>)}</div></section>
            )}
          </div>
        )}
        <div className="drawer-sticky-actions">
          {achievement.status === "candidate" && <button className="outline-button" onClick={() => approve(achievement)}><ShieldCheck size={17} />审核并建立索引</button>}
          {latest?.agent_run_id && <button className="text-button" onClick={() => openRun(latest.agent_run_id!)}><GitBranch size={16} />查看执行轨迹</button>}
          <button className="primary-button" onClick={() => evaluate(achievement)} disabled={Boolean(evaluating) || busy === `evaluation:${achievement.id}`}>{evaluating ? <RefreshCw className="spin" size={17} /> : <Sparkles size={17} />}{latest?.status === "completed" ? "重新评价" : "开始智能评价"}</button>
        </div>
      </aside>
    </div>
  );
}

function RequirementDrawer({
  requirement,
  refinements,
  matches,
  approvals,
  selectedMatch,
  tab,
  setTab,
  close,
  save,
  refine,
  runMatching,
  openMatch,
  decide,
  decideApproval,
  openAchievement,
  openRun,
  busy,
}: {
  requirement: Requirement;
  refinements: RequirementRefinement[];
  matches: MatchRunSummary[];
  approvals: Approval[];
  selectedMatch: MatchRunDetail | null;
  tab: "profile" | "matching" | "history";
  setTab: (tab: "profile" | "matching" | "history") => void;
  close: () => void;
  save: (form: HTMLFormElement) => Promise<void>;
  refine: (item: Requirement) => void;
  runMatching: (item: Requirement) => void;
  openMatch: (item: Requirement, match: MatchRunSummary) => void;
  decide: (item: MatchItem, decision: "shortlisted" | "rejected" | "adopted") => void;
  decideApproval: ApprovalDecision;
  openAchievement: (achievementId: string) => void;
  openRun: (runId: string) => void;
  busy: string;
}) {
  const latestRefinement = refinements[0];
  const matching = matches[0] && ["queued", "running"].includes(matches[0].status);
  const relatedRunIds = new Set([
    ...refinements.map((item) => item.agent_run_id).filter(Boolean),
    ...matches.map((item) => item.agent_run_id).filter(Boolean),
  ]);
  const clarificationApproval = approvals.find(
    (item) => relatedRunIds.has(item.run_id) && clarificationReviewPayload(item),
  );
  return (
    <div className="drawer-layer" role="dialog" aria-modal="true">
      <button className="drawer-scrim" onClick={close} aria-label="关闭" />
      <aside className="drawer business-drawer requirement-drawer">
        <div className="drawer-head"><div><span className={`status ${requirement.status}`}>{statusLabel(requirement.status)}</span><p>需求工作区 · {formatTime(requirement.updated_at)}</p></div><button className="icon-button" onClick={close}><X size={19} /></button></div>
        <div className="drawer-title-row"><div><h2>{requirement.title}</h2><p>{requirement.scenario || "应用场景待规范化"}</p></div><div className="completeness-gauge"><strong>{Math.round(requirement.completeness_score * 100)}%</strong><span>需求完整度</span></div></div>
        {requirement.completeness_score < 0.7 && (
          <div className="quality-gate-banner">
            <CircleAlert size={18} />
            {requirement.missing_fields.length ? (
              <div><strong>匹配前需要补充关键信息</strong><p>工程对象、核心问题或硬约束仍不完整；发起匹配后，系统会在 Human Review 中引导补充，并在提交后自动继续。</p></div>
            ) : (
              <div><strong>当前可进行探索性匹配</strong><p>关键字段已经具备，但部分偏好仍可完善；系统会返回候选方向并明确提示信息缺口和风险。</p></div>
            )}
          </div>
        )}
        {clarificationApproval && (
          <ClarificationApprovalCard item={clarificationApproval} decide={decideApproval} compact />
        )}
        <div className="drawer-tabs three">
          <button className={tab === "profile" ? "active" : ""} onClick={() => setTab("profile")}><SlidersHorizontal size={16} />结构化需求</button>
          <button className={tab === "matching" ? "active" : ""} onClick={() => setTab("matching")}><Network size={16} />匹配结果{selectedMatch && <em>{selectedMatch.items.length}</em>}</button>
          <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}><Clock3 size={16} />历史记录{matches.length > 0 && <em>{matches.length}</em>}</button>
        </div>

        <div className="drawer-scroll-area">
          {tab === "profile" && (
            <>
              {latestRefinement && ["queued", "running"].includes(latestRefinement.status) && <div className="workflow-progress-card"><RefreshCw className="spin" size={24} /><div><strong>需求规范化 Agent 正在处理</strong><p>系统正在区分硬约束、软偏好、工程目标和缺失信息。</p></div></div>}
              <form className="stack-form requirement-edit-form" onSubmit={(event) => { event.preventDefault(); save(event.currentTarget).catch(() => undefined); }}>
                <div className="form-grid"><label>需求标题<input name="title" defaultValue={requirement.title} required /></label><label>应用场景<input name="scenario" defaultValue={requirement.scenario} placeholder="例如：高寒地区农村供水" /></label></div>
                <label>原始需求<textarea name="raw_input" defaultValue={requirement.raw_input} rows={5} required /></label>
                <label>核心问题<textarea name="problem" defaultValue={requirement.problem} rows={3} placeholder="AI 规范化后可人工修订" /></label>
                <div className="modal-actions inline"><button type="button" className="outline-button" onClick={() => refine(requirement)} disabled={busy === `refinement:${requirement.id}`}><Sparkles size={16} />重新 AI 规范化</button><button className="primary-button"><Check size={16} />保存修改</button></div>
              </form>
              <section><h3>硬性约束</h3>{Object.keys(requirement.hard_constraints).length ? <div className="constraint-grid hard">{Object.entries(requirement.hard_constraints).map(([key, value]) => <div key={key}><ShieldCheck size={16} /><span>{key}</span><strong>{String(value)}</strong></div>)}</div> : <p className="muted-copy">尚未识别硬约束。建议补充不能违反的地域、工期、施工或性能条件。</p>}</section>
              <section><h3>软性偏好</h3>{Object.keys(requirement.soft_preferences).length ? <div className="constraint-grid soft">{Object.entries(requirement.soft_preferences).map(([key, value]) => <div key={key}><Star size={16} /><span>{key}</span><strong>{String(value)}</strong></div>)}</div> : <p className="muted-copy">尚未识别成本、维护或方案偏好。</p>}</section>
              {!!requirement.objectives.length && <section><h3>工程目标</h3><ul>{requirement.objectives.map((item, index) => <li key={index}>{item}</li>)}</ul></section>}
              {!!requirement.missing_fields.length && <section><h3>需要补充</h3><ul className="insight-list warning">{requirement.missing_fields.map((item, index) => <li key={index}><CircleAlert size={16} />{item}</li>)}</ul></section>}
              {!!requirement.search_terms.length && <section><h3>检索关键词</h3><div className="tag-row">{requirement.search_terms.map((item) => <span key={item}>{item}</span>)}</div></section>}
            </>
          )}

          {tab === "matching" && (
            <div className="matching-workspace">
              {matching && <div className="workflow-progress-card"><RefreshCw className="spin" size={24} /><div><strong>Agent 正在检索和评分</strong><p>需求诊断、混合检索、候选评分和独立核验正在进行。</p></div><span>{workflowStatus(matches[0].status)}</span></div>}
              {!matching && !selectedMatch && <EmptyState icon={Network} title="尚无匹配结果" body="开始匹配后，候选成果排名、六维得分和风险会显示在这里。" />}
              {selectedMatch && (
                <>
                  <div className="match-summary"><div><p className="eyebrow">MATCH RESULT · {formatTime(selectedMatch.created_at)}</p><h3>{selectedMatch.candidate_count} 项候选成果</h3><p>{selectedMatch.summary ? "匹配分析与报告已生成，可查看每项候选的理由、差距和风险。" : "候选成果已完成结构化评分。"}</p></div><span>核验置信度 <strong>{Math.round(selectedMatch.confidence * 100)}%</strong></span></div>
                  <div className="candidate-list">{selectedMatch.items.map((item) => <article className={`candidate-card ${item.decision_status}`} key={item.id}><div className="candidate-rank"><span>#{item.rank}</span><ScoreBadge score={item.final_score} /></div><div className="candidate-content"><button className="candidate-title" onClick={() => openAchievement(item.achievement_id)}>{item.achievement_title}<ChevronRight size={17} /></button><div className="constraint-pass">{item.eligibility === "satisfied" ? <><CheckCircle2 size={15} />已核验约束满足</> : item.eligibility === "violated" ? <><XCircle size={15} />存在不满足的约束</> : <><CircleAlert size={15} />约束证据不足，待确认</>}</div><div className="mini-dimensions">{Object.entries(item.dimension_scores).filter(([key]) => item.score_kind !== "retrieval_relevance" || ["scenario_fit", "evidence_quality", "maturity"].includes(key)).map(([key, value]) => <div key={key}><span>{matchDimensionLabels[key] ?? key}</span><div><i className={key === "implementation_risk" ? "risk" : ""} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div><strong>{Math.round(value)}</strong></div>)}</div><div className="candidate-insights"><div><strong>推荐理由</strong>{item.reasons.slice(0, 3).map((text, index) => <p key={index}><Check size={14} />{text}</p>)}</div><div><strong>差距与风险</strong>{[...item.gaps, ...item.risks].slice(0, 4).map((text, index) => <p key={index}><CircleAlert size={14} />{text}</p>)}</div></div><details className="evidence-detail"><summary>查看约束与原文证据</summary>{item.constraint_checks.map((check, index) => <p key={index}>{check.constraint}：{{satisfied: "满足", violated: "不满足", unknown: "未知"}[check.status]} — {check.reason}{check.quote && <q>{check.quote}</q>}</p>)}{item.evidence.map((evidence, index) => <blockquote key={index}>{String(evidence.text ?? "")}<small>来源字段：{String(evidence.field ?? "")} · 版本 {String(evidence.version ?? "")}</small></blockquote>)}</details><p className="recommendation"><Sparkles size={15} />{item.recommendation}</p><div className="candidate-actions"><button className={item.decision_status === "shortlisted" ? "active" : ""} onClick={() => decide(item, "shortlisted")}><Star size={15} />候选</button><button className={item.decision_status === "rejected" ? "active danger" : ""} onClick={() => decide(item, "rejected")}><X size={15} />淘汰</button><button className={item.decision_status === "adopted" ? "active success" : ""} onClick={() => decide(item, "adopted")}><CheckCircle2 size={15} />采用</button></div></div></article>)}</div>
                  {selectedMatch.agent_run_id && <button className="text-button trace-link" onClick={() => openRun(selectedMatch.agent_run_id!)}><GitBranch size={16} />查看本次Agent 执行轨迹</button>}
                </>
              )}
            </div>
          )}

          {tab === "history" && (
            <div className="history-workspace">
              <section><h3>匹配历史</h3>{matches.length ? <div className="history-list rich">{matches.map((match) => <button key={match.id} className={selectedMatch?.id === match.id ? "active" : ""} onClick={() => openMatch(requirement, match)}><div><Network size={17} /><span>{formatTime(match.created_at)}<em>{workflowStatus(match.status)}</em></span></div><strong>{match.top_score !== null ? `最高 ${Math.round(match.top_score)} 分` : `${match.candidate_count} 项候选`}<ChevronRight size={16} /></strong></button>)}</div> : <p className="muted-copy">暂无历史匹配记录。</p>}</section>
              <section><h3>需求规范化版本</h3>{refinements.length ? <div className="history-list rich">{refinements.map((item) => <button key={item.id}><div><Sparkles size={17} /><span>规范化 V{item.refinement_version}<em>{formatTime(item.created_at)}</em></span></div><strong>{workflowStatus(item.status)} · 完整度 {Math.round(item.completeness_score * 100)}%</strong></button>)}</div> : <p className="muted-copy">暂无 AI 规范化记录。</p>}</section>
            </div>
          )}
        </div>
        <div className="drawer-sticky-actions"><button className="outline-button" onClick={() => refine(requirement)} disabled={busy === `refinement:${requirement.id}`}><Sparkles size={16} />AI 需求规范化</button><button className="primary-button" onClick={() => runMatching(requirement)} disabled={Boolean(matching) || busy === `matching:${requirement.id}`}>{matching ? <RefreshCw className="spin" size={17} /> : <Network size={17} />}{requirement.missing_fields.length ? "补充后成果匹配" : requirement.completeness_score < 0.7 ? "探索性成果匹配" : "开始成果匹配"}</button></div>
      </aside>
    </div>
  );
}

function FormModal({ title, close, children }: { title: string; close: () => void; children: React.ReactNode }) {
  return (
    <div className="modal-layer" role="dialog" aria-modal="true">
      <button className="modal-scrim" onClick={close} aria-label="关闭" />
      <section className="modal-card">
        <div className="modal-head"><div><p className="eyebrow">HYDROBRIDGE DATA</p><h2>{title}</h2></div><button className="icon-button" onClick={close}><X size={19} /></button></div>
        {children}
      </section>
    </div>
  );
}
