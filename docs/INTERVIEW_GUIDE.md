# 面试讲解提纲

## 30 秒项目介绍

“我做的是一个水利科技成果发现与工程供需匹配的多 Agent 平台。前端是中文工作台，后端用 FastAPI、LangGraph 和 Celery。MySQL 保存业务事实和完整审计轨迹，Redis Runtime 保存检查点、SSE 和任务队列，独立 Redis 做 Tavily 缓存，Qdrant 做稠密加稀疏混合检索，MinIO 保存资料与最终报告。系统支持人工审核、断点恢复、事务 outbox 和可验证的报告下载。”

## 为什么 Python 项目仍然需要 MySQL 和 Redis

语言与基础设施并不冲突。Python 负责业务和 Agent 编排；MySQL 解决强一致业务记录、关系查询和审计；Redis 解决低延迟运行状态、消息队列、事件流和检查点。Agent 项目如果只有 Python 脚本和模型调用，通常缺少生产系统最关键的持久化、并发、恢复和追踪能力。

## 为什么要两套 Redis

LangGraph 检查点和 Celery 消息不能被淘汰，而搜索缓存应该在内存压力下淘汰。把两类数据放一起，要么关闭淘汰导致缓存挤爆运行态，要么开启淘汰导致 Agent 无法恢复。因此 Runtime 使用 AOF 与 noeviction，Cache 使用 volatile-lfu。

## 如何保证 MySQL 与 Qdrant 一致

不做 MySQL 事务内的远程双写。成果审核和 OutboxEvent 一起提交到 MySQL，Celery 再异步写 Qdrant。写失败记录重试次数和下次执行时间，超过上限进入 dead 状态；索引版本变化也通过 outbox 重建。这是最终一致性，并以 MySQL 为事实来源。

## Agent 与普通工作流有什么区别

稳定步骤仍然显式建模，但 Supervisor 会根据任务类型、已有结果、错误和审核状态动态选择节点。需求不完整时图会中断；人工补充后从 Redis checkpoint 恢复。每个节点有状态契约、重试边界和最大迭代次数，不让“自主”变成不可控循环。

## 如何降低幻觉

- 外部信息只通过 Tavily 工具进入，保留来源 URL。
- 本地成果来自已审核结构化数据，候选携带来源和证据分。
- 证据整理 Agent 去重和分级。
- 独立核验 Agent 检查引用覆盖、冲突和高风险结论。
- 报告区分已知事实、推断、风险和待现场核实项。
- 无证据时降低置信度，而不是补写看似合理的数字。

## 检索为什么采用混合方案

水利成果中既有自然语言语义，也有“TRL、泵站、防渗墙、农村饮水”等精确术语。只用向量可能漏掉精确词，只用关键词难以召回近义表达。因此使用稠密和稀疏候选、RRF 融合，再用水利领域规则或 BGE Reranker 重排。

## 如何处理重复任务和失败

- ToolCall 有幂等键和重试记录。
- Celery 使用 late ack、worker 丢失后重投和 prefetch=1。
- AgentRun 是状态机，完成、失败、取消、审核等待均显式保存。
- Redis Stream 支持 Last-Event-ID，前端断线可续读。
- 外部调用有超时、重试和失败落库。
- 运行总超时和最大迭代次数防止无限循环。

## 安全设计

- Argon2 密码哈希。
- JWT Access Token + 数据库存指纹的 Refresh Token。
- 刷新时令牌轮换，旧令牌立即撤销，阻断重放。
- Artifact、Run、Conversation 等资源检查所有者。
- Key 仅放 `.env`，不进入 Git、日志或前端 bundle。
- 报告通过 API 鉴权代理下载，不把 MinIO 内部地址直接暴露给浏览器。

## 可以主动说明的边界

- 离线向量适合无 Key 演示，不等同于生产语义模型；生产应安装 BGE-M3 或使用稳定 Embedding API，并基于标注集评估 Recall@K、MRR 和 NDCG。
- 单机 Docker Compose 适合面试和本地开发；生产可迁移到托管 MySQL/Redis、Kubernetes、对象存储和可观测平台。
- 最终推荐是决策支持，不替代水利工程现场勘察、合规审查和专家签字。
