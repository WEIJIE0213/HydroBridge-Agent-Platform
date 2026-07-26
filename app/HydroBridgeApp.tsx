"use client";

import {
  Activity,
  ArrowRight,
  Bot,
  BrainCircuit,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  ClipboardCheck,
  Clock3,
  Database,
  Download,
  FileSearch,
  Gauge,
  GitBranch,
  Layers3,
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
  Sparkles,
  Square,
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
  REFRESH_TOKEN_KEY,
  streamRunEvents,
} from "./lib/api";
import type {
  Achievement,
  Approval,
  Overview,
  Page,
  Readiness,
  Requirement,
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
  { key: "achievements", label: "科技成果库", icon: Library },
  { key: "requirements", label: "需求中心", icon: FileSearch },
  { key: "runs", label: "运行记录", icon: Activity },
  { key: "approvals", label: "人工审核", icon: ClipboardCheck },
  { key: "system", label: "系统状态", icon: ServerCog },
];

const taskTypes = [
  { value: "auto", label: "智能路由" },
  { value: "matching", label: "成果匹配" },
  { value: "research", label: "联网调研" },
  { value: "evaluation", label: "成果评估" },
  { value: "qa", label: "知识问答" },
];

const prompts = [
  "为高寒地区农村供水工程匹配低维护、成熟度高的技术成果",
  "评估超疏水混凝土外加剂在水工结构修复中的适用性",
  "调研近三年智慧泵站运行调度技术，并核验来源",
];

const eventLabels: Record<string, string> = {
  "run.created": "任务已创建",
  "run.started": "工作流开始",
  "run.resuming": "工作流恢复",
  "plan.created": "Supervisor 完成任务规划",
  "agent.started": "Agent 开始处理",
  "agent.completed": "Agent 完成处理",
  "tool.started": "工具调用开始",
  "tool.completed": "工具调用完成",
  "tool.failed": "工具调用失败",
  "approval.required": "等待人工审核",
  "run.completed": "任务已完成",
  "run.failed": "任务失败",
  "run.cancelled": "任务已取消",
};

const agentNames: Record<string, string> = {
  supervisor: "Supervisor",
  requirement_analyst: "需求分析 Agent",
  research_agent: "科技情报 Agent",
  evidence_curator: "证据整理 Agent",
  assessment_matching_agent: "评估匹配 Agent",
  verification_agent: "独立核验 Agent",
  final_report_agent: "报告生成 Agent",
  human_review: "人工审核",
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
          面向成果发现、证据核验、供需匹配与工程决策的可恢复多 Agent 平台。
        </p>
        <div className="login-capabilities">
          <span><GitBranch size={17} /> LangGraph 多 Agent 编排</span>
          <span><Database size={17} /> MySQL + Redis 可靠运行</span>
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
  const [selectedAchievement, setSelectedAchievement] =
    useState<Achievement | null>(null);
  const [selectedRun, setSelectedRun] = useState<Run | null>(null);
  const [trace, setTrace] = useState<RunTrace | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [prompt, setPrompt] = useState(prompts[0]);
  const [taskType, setTaskType] = useState("auto");
  const [running, setRunning] = useState(false);
  const [achievementQuery, setAchievementQuery] = useState("");
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
      ] = await Promise.all([
        api<Overview>("/overview", {}, accessToken),
        api<Page<Achievement>>("/achievements?page_size=100", {}, accessToken),
        api<Page<Requirement>>("/requirements?page_size=100", {}, accessToken),
        api<Page<Run>>("/runs?page_size=100", {}, accessToken),
        api<Page<Approval>>("/approvals?status=pending&page_size=100", {}, accessToken),
        api<Readiness>("/readiness", {}, accessToken),
      ]);
      setOverview(overviewData);
      setAchievements(achievementData.items);
      setRequirements(requirementData.items);
      setRuns(runData.items);
      setApprovals(approvalData.items);
      setReadiness(readinessData);
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

  const executeRun = async () => {
    if (!prompt.trim() || running) return;
    setRunning(true);
    setError("");
    setEvents([]);
    setTrace(null);
    try {
      const run = await api<Run>(
        "/runs",
        {
          method: "POST",
          body: JSON.stringify({
            message: prompt.trim(),
            task_type: taskType,
            context: { source: "web_workbench" },
          }),
        },
        token,
      );
      setSelectedRun(run);
      setRuns((current) => [run, ...current.filter((item) => item.id !== run.id)]);
      const controller = new AbortController();
      abortRef.current = controller;
      await streamRunEvents(
        run.id,
        token,
        (event) => setEvents((current) => [...current, event]),
        controller.signal,
      );
      const [updated, updatedTrace] = await Promise.all([
        api<Run>(`/runs/${run.id}`, {}, token),
        api<RunTrace>(`/runs/${run.id}/trace`, {}, token),
      ]);
      setSelectedRun(updated);
      setTrace(updatedTrace);
      await refreshAll(token);
      notify(
        updated.status === "waiting_review"
          ? "任务已转入人工审核"
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

  const decideApproval = async (approval: Approval, decision: "approve" | "reject") => {
    await api(
      `/runs/${approval.run_id}/decisions`,
      {
        method: "POST",
        body: JSON.stringify({
          approval_id: approval.id,
          decision,
          comment: decision === "approve" ? "已核对信息，同意继续" : "当前证据不足，驳回",
        }),
      },
      token,
    );
    notify(decision === "approve" ? "已批准，Agent 将继续运行" : "已驳回审核任务");
    await refreshAll(token);
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
    await api(
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
    setShowNewRequirement(false);
    notify("工程需求已保存");
    await refreshAll(token);
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

  const filteredAchievements = useMemo(() => {
    const query = achievementQuery.trim().toLowerCase();
    if (!query) return achievements;
    return achievements.filter(
      (item) =>
        item.title.toLowerCase().includes(query) ||
        item.abstract.toLowerCase().includes(query) ||
        item.tags.some((tag) => tag.toLowerCase().includes(query)),
    );
  }, [achievementQuery, achievements]);

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
              cancelRun={cancelRun}
              selectedRun={selectedRun}
              events={events}
              trace={trace}
              openRun={openRun}
              downloadReport={downloadReport}
            />
          )}
          {view === "achievements" && (
            <AchievementsView
              achievements={filteredAchievements}
              query={achievementQuery}
              setQuery={setAchievementQuery}
              open={setSelectedAchievement}
              create={() => setShowNewAchievement(true)}
            />
          )}
          {view === "requirements" && (
            <RequirementsView
              requirements={requirements}
              create={() => setShowNewRequirement(true)}
              runMatching={(requirement) => {
                setPrompt(requirement.raw_input);
                setTaskType("matching");
                setView("workbench");
              }}
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
          close={() => setSelectedAchievement(null)}
          approve={approveAchievement}
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
              <label>成熟度（1–9）<input name="maturity_level" type="number" min={1} max={9} defaultValue={6} /></label>
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
        <FormModal title="新建工程需求" close={() => setShowNewRequirement(false)}>
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
              <button className="primary-button"><Plus size={17} />保存需求</button>
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
  cancelRun,
  selectedRun,
  events,
  trace,
  openRun,
  downloadReport,
}: {
  overview: Overview | null;
  runs: Run[];
  prompt: string;
  setPrompt: (value: string) => void;
  taskType: string;
  setTaskType: (value: string) => void;
  running: boolean;
  executeRun: () => void;
  cancelRun: () => void;
  selectedRun: Run | null;
  events: RunEvent[];
  trace: RunTrace | null;
  openRun: (run: Run) => void;
  downloadReport: (artifactId: string) => void;
}) {
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
            <span className="live-chip"><Sparkles size={15} /> 多 Agent 决策引擎</span>
            <h2>今天需要分析哪项水利工程问题？</h2>
            <p>系统会自动规划任务，调度专业 Agent，检索成果并独立核验证据。</p>
          </div>
          <div className="prompt-box">
            <div className="task-select">
              <GitBranch size={16} />
              <select value={taskType} onChange={(event) => setTaskType(event.target.value)}>
                {taskTypes.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
              <ChevronDown size={15} />
            </div>
            <textarea
              aria-label="输入 Agent 任务"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="描述工程场景、现有问题、技术约束或需要调研的主题…"
            />
            <div className="prompt-footer">
              <span><ShieldCheck size={15} /> 高风险结论会自动进入人工审核</span>
              {running ? (
                <button className="stop-button" onClick={cancelRun}><Square size={14} fill="currentColor" />停止</button>
              ) : (
                <button className="send-button" onClick={executeRun} disabled={!prompt.trim()}>
                  开始分析 <Send size={17} />
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
                  <a key={`${citation.url}-${index}`} href={citation.url} target="_blank" rel="noreferrer">
                    <span>{index + 1}</span>{citation.title || citation.url}<ArrowRight size={14} />
                  </a>
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
          <p>Agent 团队</p>
          <div>
            {["supervisor", "requirement_analyst", "research_agent", "assessment_matching_agent", "verification_agent"].map((agent) => (
              <span key={agent} title={agentNames[agent]}>{agentNames[agent].slice(0, 2)}</span>
            ))}
            <em>+2</em>
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

function AchievementsView({ achievements, query, setQuery, open, create }: { achievements: Achievement[]; query: string; setQuery: (value: string) => void; open: (item: Achievement) => void; create: () => void }) {
  return (
    <div className="page-stack">
      <section className="page-intro">
        <div><p className="eyebrow">VERIFIED KNOWLEDGE</p><h2>水利科技成果知识库</h2><p>结构化成果、工程案例与证据来源共同支撑 Agent 检索和匹配。</p></div>
        <button className="primary-button" onClick={create}><Plus size={17} />录入成果</button>
      </section>
      <section className="table-panel">
        <div className="toolbar">
          <div className="search-input"><Search size={17} /><input placeholder="搜索成果、摘要或标签" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
          <span>{achievements.length} 条成果</span>
        </div>
        <div className="achievement-grid">
          {achievements.map((item) => (
            <button className="achievement-card" key={item.id} onClick={() => open(item)}>
              <div className="achievement-top">
                <span className={`status ${item.status}`}>{statusLabel(item.status)}</span>
                <span className="evidence-score"><ShieldCheck size={14} />证据 {Math.round(item.evidence_score * 100)}</span>
              </div>
              <h3>{item.title}</h3>
              <p>{item.abstract || "暂无成果摘要"}</p>
              <div className="tag-row">{item.tags.slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}</div>
              <div className="achievement-foot">
                <span><Gauge size={15} /> TRL {item.maturity_level ?? "—"}</span>
                <span><Database size={15} /> {item.metrics.length} 项指标</span>
                <ArrowRight size={16} />
              </div>
            </button>
          ))}
        </div>
        {!achievements.length && <EmptyState icon={Library} title="没有找到成果" body="尝试调整搜索词，或录入一条新的候选成果。" />}
      </section>
    </div>
  );
}

function RequirementsView({ requirements, create, runMatching }: { requirements: Requirement[]; create: () => void; runMatching: (item: Requirement) => void }) {
  return (
    <div className="page-stack">
      <section className="page-intro">
        <div><p className="eyebrow">DEMAND INTELLIGENCE</p><h2>工程需求中心</h2><p>沉淀工程问题和约束，形成可复用、可追踪的匹配输入。</p></div>
        <button className="primary-button" onClick={create}><Plus size={17} />新建需求</button>
      </section>
      <section className="table-panel">
        <div className="requirement-list">
          {requirements.map((item) => (
            <article className="requirement-card" key={item.id}>
              <div className="requirement-index"><FileSearch size={21} /></div>
              <div className="requirement-body">
                <div><h3>{item.title}</h3><span>{formatTime(item.created_at)}</span></div>
                <p>{item.raw_input}</p>
                <div className="tag-row">
                  {item.scenario && <span>{item.scenario}</span>}
                  <span>完整度 {Math.round(item.completeness_score * 100)}%</span>
                  <span className={`status ${item.status}`}>{statusLabel(item.status)}</span>
                </div>
              </div>
              <button className="outline-button" onClick={() => runMatching(item)}><Play size={15} />开始匹配</button>
            </article>
          ))}
          {!requirements.length && <EmptyState icon={FileSearch} title="还没有工程需求" body="创建需求后可直接交给多 Agent 完成诊断与成果匹配。" />}
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

function ApprovalsView({ approvals, decide }: { approvals: Approval[]; decide: (item: Approval, decision: "approve" | "reject") => void }) {
  return (
    <div className="page-stack">
      <section className="page-intro">
        <div><p className="eyebrow">HUMAN IN THE LOOP</p><h2>人工审核队列</h2><p>补充关键信息、确认高风险结论，让自动化始终处于人的控制之下。</p></div>
        <div className="review-count"><ShieldCheck size={21} /><strong>{approvals.length}</strong><span>项待处理</span></div>
      </section>
      <section className="approval-grid">
        {approvals.map((item) => (
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
    { name: "MySQL", desc: "业务数据与审计记录", ok: !!checks?.database, icon: Database },
    { name: "Redis Runtime", desc: "事件流、检查点与任务队列", ok: typeof checks?.redis === "object" && checks.redis.runtime, icon: Zap },
    { name: "Redis Cache", desc: "Tavily 搜索结果缓存", ok: typeof checks?.redis === "object" && checks.redis.cache, icon: Layers3 },
    { name: "Qdrant", desc: "稠密 + 稀疏混合检索", ok: !!checks?.qdrant, icon: Network },
    { name: "MinIO", desc: "原始资料与报告制品", ok: !!checks?.minio, icon: Database },
    { name: "LLM", desc: "OpenAI 兼容模型服务", ok: !!checks?.model_configured, icon: BrainCircuit, optional: true },
    { name: "Tavily", desc: "联网检索与网页抽取", ok: !!checks?.tavily_configured, icon: Search, optional: true },
  ];
  return (
    <div className="page-stack">
      <section className="system-hero">
        <div className={`system-orb ${readiness?.status === "ready" ? "healthy" : ""}`}><Activity size={28} /></div>
        <div><p className="eyebrow">SYSTEM READINESS</p><h2>{readiness?.status === "ready" ? "所有核心服务运行正常" : "核心平台可用，外部能力待配置"}</h2><p>外部模型和 Tavily 属于联调配置；MySQL、Redis、Qdrant、MinIO 是平台核心依赖。</p></div>
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
                {service.ok ? "正常" : service.optional ? "待配置" : "异常"}
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
            ["LangGraph", "多 Agent 状态机"],
            ["Celery", "异步与重试"],
            ["MySQL / Redis", "持久化与恢复"],
            ["Qdrant", "混合知识检索"],
          ].map(([name, desc], index, all) => (
            <div key={name}><span>{index + 1}</span><strong>{name}</strong><small>{desc}</small>{index < all.length - 1 && <ArrowRight size={17} />}</div>
          ))}
        </div>
      </section>
    </div>
  );
}

function AchievementDrawer({ achievement, close, approve }: { achievement: Achievement; close: () => void; approve: (item: Achievement) => void }) {
  return (
    <div className="drawer-layer" role="dialog" aria-modal="true">
      <button className="drawer-scrim" onClick={close} aria-label="关闭" />
      <aside className="drawer">
        <div className="drawer-head"><div><span className={`status ${achievement.status}`}>{statusLabel(achievement.status)}</span><p>成果详情 · V{achievement.version}</p></div><button className="icon-button" onClick={close}><X size={19} /></button></div>
        <h2>{achievement.title}</h2>
        <div className="drawer-kpis"><span><strong>{Math.round(achievement.evidence_score * 100)}</strong>证据质量</span><span><strong>{achievement.maturity_level ?? "—"}</strong>技术成熟度</span><span><strong>{achievement.metrics.length}</strong>量化指标</span></div>
        <section><h3>成果摘要</h3><p>{achievement.abstract || "暂无摘要"}</p></section>
        <section><h3>技术原理</h3><p>{achievement.technical_principle || "暂无技术原理说明"}</p></section>
        {!!achievement.organizations.length && <section><h3>完成单位</h3><div className="tag-row">{achievement.organizations.map((item) => <span key={item}>{item}</span>)}</div></section>}
        {!!achievement.application_scenarios.length && <section><h3>应用与工程场景</h3><ul>{achievement.application_scenarios.slice(0, 6).map((item, index) => <li key={index}>{item}</li>)}</ul></section>}
        {!!achievement.metrics.length && <section><h3>关键指标</h3><div className="metrics-table">{achievement.metrics.slice(0, 8).map((metric, index) => <div key={index}><span>{String(metric.name ?? "指标")}</span><strong>{String(metric.value ?? "—")}</strong></div>)}</div></section>}
        {achievement.status === "candidate" && <button className="primary-button drawer-approve" onClick={() => approve(achievement)}><ShieldCheck size={17} />审核通过并建立索引</button>}
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
