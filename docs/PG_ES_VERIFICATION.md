# PostgreSQL / Elasticsearch / 单 Agent 改造验收记录

日期：2026-10-18–19。工作分支：`codex/pg-es-agent-rag`。

本文记录第一阶段的 15 项种子资料和 hash 向量验证。后续 BGE 模型、完整文档库及实际检索验收见 [BGE 全库验收记录](BGE_FULL_CORPUS_VERIFICATION.md)。

改造前的未提交工作已保留，原始源码归档和 tracked diff 另存于本机临时备份目录 `C:/Users/28927/AppData/Local/Temp/hydrobridge-before-pg-es-20261018-221809/`。没有执行 Git reset、提交或推送。

## 已完成并运行的验证

| 项目 | 结果 | 范围 |
| --- | --- | --- |
| 后端 pytest | 91 passed（52.90s） | SQLite / fake Redis / hash embedding；测试清空真实模型密钥；含模型跨事件循环、TRL 格式及追问匹配范围回归 |
| 后端 Ruff | 通过 | app、tests、新增迁移 |
| Alembic 从空库升级到 head | 通过 | SQLite 和真实 PostgreSQL 16.15 均完成三次 revision |
| 旧索引字段迁移 | 通过 | 保留旧文档标识及匹配记录，新增证据表，重复执行不破坏数据 |
| 前端 TypeScript / ESLint | 通过 | 类型检查与静态检查 |
| 前端生产构建及 HTTP 测试 | 2 passed | SSR，以及生产服务器实际提供 JS/CSS；隐藏 manifest 保持不可访问 |
| 浏览器交互 | 通过 | Headless Edge、模拟 API：连续追问携带 conversation_id，新会话清空，引用展开，页面无 JS 异常 |
| 移动宽度 | 通过 | 390px 视口无横向溢出；截图在 output/pg-es-ui-mobile.png |
| Compose 配置 | 通过 | docker compose config --quiet |
| 真实 PG / ES 基础链路 | 7项全部通过 | PostgreSQL、IK、Outbox 重放、混合检索、版本滞后拒绝、撤销审批、检查点重连恢复；宿主机与 API 镜像中均通过，使用独立临时 schema / 索引 |
| 检索消融 | 已保存 | evaluation/results/local-hash.json；15项资料、15道暂定标签题 |
| 真实 ES 检索消融 | 已保存 | evaluation/results/es-hash.json；ES 8.19.3 + IK + hash，15项资料和15道暂定标签题 |
| 完整 Compose 部署 | 通过 | 9个服务均运行，API readiness=ready；含 IK、MinIO、前端生产镜像 |
| 初始资料索引 | 通过 | 15项成果、159个原文片段；45条 Outbox 事件全部完成，0条失败 |
| 真实模型连续问答 | 通过 | 已配置 DeepSeek 服务原生工具调用；同会话范围与轮次延续；未知 TRL 最多两轮检索后说明证据缺口 |
| 真实证据问答 | 通过 | 割草机技术原理，grounded_llm 模式、2轮检索、8条经过原文校验的引用 |
| 人工补充后恢复 | 通过 | matching → waiting_review → 提交补充 → 同一运行 completed；真实 PostgreSQL 检查点、Celery 与模型 |
| 报告存取 | 通过 | 真实 MinIO 保存，HTTP 下载 SHA-256 与元数据一致；浏览器下载成功 |
| 真实页面联调 | 通过 | Headless Edge 连接真实服务，无 API mock；登录、结果、引用展开、下载；无页面 JS 异常或 HTTP 4xx/5xx，390px 无横向溢出 |

主要新增后端测试覆盖：原文字段位置、稳定 chunk ID、按成果版本失效、审批/组织/显式范围过滤、ES 故障不静默降级、RRF、分块索引幂等、三态约束、未知 TRL、无效引文拒绝、两轮检索上限、原生工具调用绑定、会话记忆、需求更新归属、API 业务上下文防伪造。

Windows 浏览器验收发现并修复了 vinext 0.0.50 的静态缓存路径分隔符问题：此前 SSR 正常但 JS 404，页面停在连接状态。兼容层位于 `scripts/start-web.mjs`，不修改 node_modules 文件。

## Docker 与部署故障处理

2026-10-18 23:20–23:25，Docker Desktop 后台 WSL 故障，初次排查结果：

- 重启前 Docker Engine API 返回 500，后台日志报告到内部引擎 `192.168.65.7:2375` 的路由不可达；VM 日志最后停在暂停 cgroup 的步骤。暂停与故障存在时间关联，尚不能认定为根因。
- 已执行 Docker Desktop 官方 CLI 正常重启，随后出现 `Unexpected WSL error`；连 `wsl.exe -l -v --all` 都超时。
- Docker 退出后执行 `wsl --shutdown`，仍长时间没有返回。
- 尝试重启 `WslService`，当前非管理员会话没有访问权限，命令失败；系统服务未被重启。
- 已结束本次无响应的 `wsl --shutdown` 命令客户端，避免留下诊断命令一直等待。
- 用户随后重启了 Windows，WSL 命令恢复响应。没有执行卸载、注销 WSL 发行版或删除数据卷。

重启后的第二处故障是 Docker 无法清理 `run/userAnalyticsOtlpHttp.sock`。该文件是零字节 ReparsePoint，Windows 对其查询也返回错误 1920。确认 Docker 已停止、`run` 目录仅包含此临时文件后，保留整个目录并改名为 `C:/Users/28927/AppData/Local/Docker/run.stale-20261018-233413/`，没有删除该文件。Docker 新建运行目录后成功启动，Engine 28.0.1 的版本和容器 API 均正常响应。这是本次启动恢复，不代表已经根治 Windows/Docker 的通信文件故障。

随后 PostgreSQL 完成迁移至 `20261018_0003`，导入15项成果。最初仅以模型 `/models` 返回200确认配置可达；随后实际运行问答、原生工具调用、人工中断与恢复，结果见下文。

部署中发现原 MinIO 镜像在 Docker Hub 不可拉取，Quay 同样拒绝访问，官方旧二进制下载返回410。已改为在 `infra/minio/Dockerfile` 从官方最后发布版本 `RELEASE.2025-10-15T17-29-55Z` 对应的固定提交 `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a` 构建。源码仓库已归档；这是开发环境兼容方案，不能据此声称有上游持续维护。

检查点迁至 PostgreSQL 后，运行时 Redis 不再调用 RedisJSON/搜索模块，因此改用普通 `redis:7.4-alpine`，保留 AOF、每秒刷盘和 noeviction。已有旧 Redis Stack 数据卷需要单独评估兼容性；本次是在空 Docker 存储中创建新卷。

同时修正了前端镜像构建上下文：`.dockerignore` 包含生产启动所需的 `scripts/start-web.mjs`，排除临时镜像缓存与源码归档。前端基础镜像换为 `node:22-bookworm-slim`，满足 workerd 的 glibc 要求。生产镜像已成功构建并实际提供页面、JS/CSS 和登录交互。

镜像仓库与包下载存在连接超时；使用本机已有代理完成下载。单独下载的镜像层校验了注册表 SHA-256 和解压层 diff_id，最后一个 npm 包校验了 package-lock 的 SHA-512；没有关闭 TLS 证书验证。临时下载缓存保留在 `tmp/image-cache/` 和 `tmp/frontend-cache/`，已从 Git 和 Docker 构建上下文排除。

## 真实联调发现并修复的问题

1. **第二轮模型调用失败：** Celery 每个任务使用新的 asyncio 事件循环，LangChain 默认 HTTP 客户端缓存复用了上轮连接，出现 `Event loop is closed`。模型网关现为每次运行创建独立 AsyncOpenAI 客户端，由运行上下文在循环关闭前释放。新增测试使用本地 HTTP keepalive 服务，在同一线程连续运行两个独立事件循环；真实两轮模型调用也通过。修复前的失败运行保留供排查，未伪装成成功记录。
2. **TRL 下限格式不一致：** 真实模型曾输出 `{"TRL": ">=8"}`，无法进入 `min_maturity_level` 数值筛选。提示词明确规定字段，并在结构化结果中归一化明确下限；模糊的“成熟度高”、上限或精确等级不擅自改成下限。回归验证 TRL=7 在要求至少8时判定违反，资料缺失仍为未知。
3. **追问转为匹配时的范围继承：** 匹配流程现在也读取上一轮保存的成果范围，显式空范围保持为空，不因切换工具扩展到全库；增加有结果、空范围、范围内成果不存在三种回归测试。

本次真实记录：

- `output/live-api-smoke.json`：运行 `195dc6a4-aa73-4676-8bec-c618ab55975b` 和 `c688b726-f2e5-486e-a1d0-f2e237f0b896`；详情原文、同范围追问、未知 TRL 与报告校验。
- `output/live-workflow-smoke.json`：证据问答 `bf719d8d-8cd5-4801-9da2-f43319b6b4b1`；人工补充后恢复 `68d8834a-e21b-495e-9df5-8055208b4c7b`。匹配首位为割草机，缺少 TRL 原文时保留 unknown。
- `output/live-trl-normalization.json`：修复后的真实模型运行 `363ba05f-45d6-4483-80ce-b11b0e512bba`，保存 `min_maturity_level: 8`；确认按现有信息继续后完成，10个候选均保持约束未知，没有把缺失 TRL 判成满足。
- `output/live-ui-smoke.json`、`output/live-ui-report.md`：真实页面与下载结果。
- `output/pg-es-live-desktop.png`、`output/pg-es-live-mobile.png`：已人工查看的桌面和手机宽度截图。调整视口后等待侧栏动画结束再截取。

真实模型验证不等于正式向量模型评测；本次检索仍使用 hash 向量。真实联网调研/Tavily 抓取、完整文档库批量导入与专家标签评估不在上述通过项内。

## 数据与效果边界

- 现有15项种子资料由本机原始 JSON 重新归一化；缺少明确 TRL 的记录改为未知，原始文档仍保存在 structured_data.original。
- 旧 MySQL/Qdrant 数据卷未删除；其额外业务数据没有自动迁移至新 PostgreSQL。
- 旧 Redis 检查点不能直接交给新的单 Agent 图恢复。
- ES 搜索默认不允许静默 fallback。正式 BGE 模式失败会报错，hash 模式只用于开发冒烟。
- 当前确定性硬约束核验支持有原文支持的结构化条件；自由文本和无法统一口径的数值保留未知。
- 当前15道题为开发者暂定标签，不是独立专家评测。离线重排的 Recall@1 为 0.8667、MRR@10 为 0.9333，低于本地混合召回；保留实际结果，没有声称正式模型提升。
- 真实 ES + hash 的 Recall@1 / MRR@10：BM25 为0.9333 / 0.9667，dense 为0.8667 / 0.9333，hybrid 为1.0000 / 1.0000，hybrid + 简化重排为0.8667 / 0.9333；四者 Recall@5、Recall@10 均为1。该小样本仍不代表实际线上效果。

## 再次启动与验证

```powershell
.\scripts\start.ps1
.\scripts\test.ps1 -Smoke
docker compose exec api python -m app.verify_infrastructure
docker compose exec api python -m app.evaluate_retrieval --backend elasticsearch --embedding-backend hash --seed /seed-data/achievements.json --cases /evaluation/retrieval_cases.json --output /evaluation/results/es-hash.json
```

本机镜像已经构建，日常启动无需 `-Build`。代码或依赖变化后再执行 `-Build`。首次构建需要拉取基础镜像、IK 插件、MinIO 源码及 npm/Python/Go 依赖；网络需能访问这些来源。启动脚本不会删除旧数据卷。

官方依据：[Docker Desktop 通信文件相关问题](https://github.com/docker/desktop-feedback/issues/531)、[MinIO 官方源码](https://github.com/minio/minio)、[Wrangler/workerd 系统要求](https://developers.cloudflare.com/workers/wrangler/install-and-update/)、[LangChain 客户端配置与实现](https://github.com/langchain-ai/langchain/blob/master/libs/partners/openai/langchain_openai/chat_models/base.py)。本机修复结果以日志和实际运行结果为准。
