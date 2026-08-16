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

### 2026-08-16 同题模型 A/B 结果

固定题目为“调研 2024 年以来智慧泵站智能调度与预测性维护的技术进展，
优先政府、科研院所和高校来源，核验来源可靠性，并给出三条可落地建议”。
两种模型使用相同的来源治理 v3、Tavily 工具、结构化输出 schema 和验收规则。

受控短输入证据判断中，两种模型都正确接受政府与学术来源，拒绝市场规模报告与
含盐污水离题来源，也都没有选择编造性能指标：

| 模型 | 结果 | 延迟 | 输入 / 输出 Token |
| --- | --- | ---: | ---: |
| `doubao-seed-2-0-mini-260428` | 正确 | 4.301 秒 | 502 / 514 |
| `doubao-seed-2-1-turbo-260628` | 正确 | 30.488 秒 | 499 / 1771 |

完整多 Agent 链路中：

- mini 完成 Supervisor、Research、Evidence Curator、Verification、Final Report 五个节点，
  5 次 Tavily Search 与 1 次 Extract 均成功、零重试；总计 46,774 / 11,856 Token，
  运行耗时 114.012 秒。
- mini 的独立核验结果为 `passed`，置信度 0.85；最终仅暴露策展 Agent 接受的 3 个来源，
  16 个引用标记全部在有效编号范围内，离题/市场报告命中为 0。
- turbo 能完成 Supervisor 和 Research，但两次完整链路都在长结构化证据策展调用中被
  上游接口超时或断开；即使压缩输入并把单次超时提高至 180 秒，也未稳定完成。
- 因此本机交付配置默认使用 mini；turbo 保留为可选实验模型，不作为可靠默认值。

保留的成功外部验收运行 ID 为 `abd3dd01-87fa-4d26-a52a-9a2356ea1bd6`。
最终报告已写入 MinIO，下载内容与 MySQL Artifact 保存的 SHA-256 一致。
