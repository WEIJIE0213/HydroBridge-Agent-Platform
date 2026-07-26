# 测试与验收

## 自动化回归

```powershell
Set-Location E:\HydroBridge-Agent-Platform
.\scripts\test.ps1
```

脚本依次执行：

1. `ruff check app tests`
2. 后端 `pytest`
3. 前端 ESLint
4. vinext 生产构建
5. 服务端渲染 HTML 断言

后端测试使用独立 SQLite 数据库和测试环境配置，不污染 Docker 中的 MySQL 数据。

## 容器冒烟测试

```powershell
.\scripts\test.ps1 -Smoke
```

该检查要求容器已经启动，并验证：

- Compose 中所有服务处于运行状态。
- `/api/v1/health` 返回 `ok`。
- `/api/v1/readiness` 返回 `ready`。
- MySQL、两套 Redis、Qdrant 和 MinIO 均可访问。

LLM 和 Tavily 未配置不会让核心 readiness 失败；它们在响应中单独显示为 `false`。

## 已完成的真实链路验收

交付前实际通过 Docker 环境执行了以下检查：

- 登录、刷新令牌轮换、旧刷新令牌重放阻断、退出撤销。
- UTF-8 中文任务创建与 MySQL 原样保存。
- Celery 接收任务并执行完整 LangGraph。
- Qdrant 从 15 项成果中将“农村饮水安全工程关键技术创新与应用”排在第一。
- 最终报告同时写入 MinIO 与 MySQL Artifact。
- 通过 JWT 下载报告；下载内容、响应头和 MySQL 三处 SHA-256 一致。
- 人工审核暂停后，通过 Redis Checkpoint 从原节点恢复并完成。
- Redis Cache 停机时 readiness 降级，恢复后自动回到 ready。
- Redis Runtime 重启前后运行态键数量一致，Celery 自动重连并继续消费 outbox。

## 手工体验清单

1. 打开 http://localhost:3000 并使用默认管理员登录。
2. 在工作台执行默认“高寒地区农村供水”问题。
3. 观察右侧实时轨迹和 Agent 节点。
4. 完成后确认第一候选、评分、风险、引用和最终报告。
5. 点击“下载报告”，检查 Markdown 文件。
6. 用非常短且缺少条件的需求触发人工审核。
7. 在“人工审核”页面确认后，查看原任务继续执行。
8. 在“运行记录”检查步骤与工具调用。
9. 在“系统状态”检查所有核心服务为正常。

## 真实外部能力验收

填入 `.env` 的 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`TAVILY_API_KEY` 后：

1. 重建 `api`、`worker`、`scheduler`。
2. 在“系统状态”确认两项外部能力均已配置。
3. 执行 `research` 类型任务。
4. 检查 ToolCall 中存在 Tavily 调用、引用 URL 可打开、最终结论引用相符。
5. 检查 MySQL ModelUsage 中的模型、Token、耗时和成本记录。

真实 Key 不进入代码和 Git，只保存在已忽略的 `.env`。
