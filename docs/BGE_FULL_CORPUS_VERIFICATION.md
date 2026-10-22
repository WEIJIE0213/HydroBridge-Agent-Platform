# BGE 与完整文档库验收记录

验收日期：2026-10-19；分支：`codex/pg-es-agent-rag`。这是在已有 PG/ES/单 Agent 改造之上的全库导入与模型接入，历史阶段见 [PG/ES 验收记录](PG_ES_VERIFICATION.md)。

## 数据与来源

用户确认的范围为 `E:\水利部` 下的成果原始材料、成果目录及对应结构化 JSON，合并重复文件，排除代码、汇报稿和知识图谱学习论文。原文件未修改。ZIP/RAR 内层材料、旧 DOC、DOCX 正文与内嵌扫描图、PDF 页、XLSX、HTML、JSON 和图片均经过清点及适用的解析/OCR。

| 项目 | 实际结果 |
| --- | ---: |
| 成果记录 | 3,860 |
| 去重原始文档 | 324 |
| 已核对原件总字节数 | 614,536,091 |
| 原始文档来源片段（DocumentChunk） | 28,788 |
| BGE 检索片段（AchievementChunk / ES） | 30,266 / 30,266 |
| 来源关联 | 19,535（本次全库 19,520，加原有种子 15） |
| 导入原文中参与索引的字符 | 9,624,487 |
| 未建立索引的成果 | 0 |
| 待处理或失败的 Outbox 事件 | 0 |

文件类型为 DOCX 78、PDF 80、JSON 72、XLSX 5、JPG 21、DOC 65、HTM 1、PNG 1、JPEG 1。原始成果目录包含 3,966 个序号，规范化精确标题去重后为 3,824 项，补充成果原始材料后为 3,860 项。目录序号与原始 XLSX、HTML、4,711 页 PDF 交叉核对，PDF 覆盖全部序号。

去重只合并相同 SHA-256 文件和规范化后完全一致的成果标题。短标题、长标题及相似技术没有仅凭相似度强行合并。来源保留全部原件，搜索侧跳过重复文本及通用材料清单。10 个申请材料包的成果归属由申请表、公报和评价意见核对，映射保存在 [water_corpus_mappings.json](../evaluation/water_corpus_mappings.json)。

332 个唯一候选文件经内容检查后接受 324 个，排除 8 个：开发说明、无关图片、Office 所有者临时文件和一个损坏副本等。其中截断 DOCX 的 3,670,016 字节与完整原件的前缀逐字节一致，完整原件 3,974,197 字节已入库，未把该损坏副本计作成功解析。没有未处置的映射或导入错误。外层 1.08 GB 压缩包的全部 11 个成员也已和解压目录中的文件核对，未遗漏独有成员。

全量核验重新读取 MinIO 的 **324 个原件**并核对 SHA-256 和字节数，核对 **3,860 项成果指纹**，重新从 PostgreSQL 原文生成片段，并与 PG 和 ES 的 **30,266 个片段 ID、版本及内容哈希**逐条比较。所有校验通过。重复执行导入显示 324 个文档和 3,860 项成果全部 unchanged。

本机证据：`output/corpus/verification-final.json`、`idempotency.json`、`assembly-summary.json`、`archive-verification.json`、`inventory.json`。包含完整原文的导入包和缓存仅保存在本机，已忽略 Git。导入前 PG 备份为 `output/backups/before-full-corpus-20261019.dump`。

## 模型与服务

| 项目 | 配置 |
| --- | --- |
| 向量模型 | BAAI/bge-m3，1024 维，CLS + L2 归一化 |
| 重排模型 | BAAI/bge-reranker-v2-m3，cross-encoder sigmoid 分数 |
| 运行设备 | RTX 4060 Laptop，CUDA，FP16 |
| 运行库 | PyTorch 2.6.0 cu124，transformers 4.51.3 |
| 推理限制 | batch 8，最多 1024 token；文本片段默认 700 字符、100 字符重叠 |
| 索引 | hydrobridge-chunks-bge-v4，知识索引版本 4 |
| 搜索 | Elasticsearch 8.19.3 / IK，BM25 + BGE dense，RRF 后用 BGE 重排 |

权重通过官方 Hugging Face 仓库的固定 revision 下载；每个模型文件的 SHA-256 保存在本机 `models/manifest.json`。模型只读挂载到独立服务，由一个进程持有 GPU，API 和 Celery 共用 HTTP 服务。加载或调用失败明确报错，不静默退回 hash。

- BGE-M3 revision：`5617a9f61b028005a4858fdac845db406aefb181`；主权重 SHA-256：`b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38`。
- reranker revision：`953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`；主权重 SHA-256：`d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286`。

独立模型检查得到 1024 维单位向量：相关文本余弦相似度 0.7176，无关文本 0.2672；重排分数分别为 0.9237 和 0.0000174。结果见 [模型检查 JSON](../evaluation/results/bge-service-smoke.json)。这些值用于确认实际模型工作，不是概率或业务准确率。

全库建索引用时约 1,089 秒；旧 `hydrobridge-chunks-v3` 保留。服务切换后 10 个 Compose 服务均运行，readiness 为 ready，BGE 设备报告 cuda；bootstrap 跳过原有 15 项种子数据，未重复创建索引事件。

## 全库检索对比

40 道问题与目标标签在评测前固定，位于 [full_corpus_cases.json](../evaluation/full_corpus_cases.json)。评测读取当前 3,860 项成果、30,266 个片段的真实 PG/ES 索引；未写入业务数据，也没有使用模拟向量。逐题前 10 名、证据片段、模型标识、标签 SHA-256 和耗时见 [完整评测 JSON](../evaluation/results/full-corpus-bge.json)。

| 检索方式 | Hit@1 | Hit@5 | Hit@10 | MRR@10 | 耗时 p50 | 耗时 p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| IK / BM25 | 67.5%（27/40） | 92.5% | 95.0% | 0.7958 | 93 ms | 279 ms |
| BGE dense | 80.0%（32/40） | 97.5% | 97.5% | 0.8717 | 104 ms | 230 ms |
| BM25 + BGE / RRF | 72.5%（29/40） | 90.0% | 97.5% | 0.8113 | 167 ms | 329 ms |
| 混合召回 + BGE reranker | **80.0%（32/40）** | **100%** | **100%** | **0.8812** | **1,540 ms** | **1,881 ms** |

Hit@k 表示前 k 名中至少命中一个预先标注的目标成果，MRR 使用第一个目标的倒数排名。这里不把 Hit@k 写成“准确率”或穷尽式 Recall。耗时包含本机检索、PG 原文再校验和重排，不包含大模型生成回答；是 40 次顺序调用，不是并发压测。

当前默认的重排方式相较纯关键词检索提高了前五名的目标覆盖率，但首位仍有 8 道未命中指定目标。原始结果保留，不因排名而更改标签。例如：

- q05、q13、q21：短名称成果排在对应长名称成果前，目标排第 2。
- q16：PCCP 检测监测技术排在分布式光纤预警系统之前，目标排第 3。
- q29：灌溉需水预测和配水技术排在指定时空诊断成果之前，目标排第 4。
- q09、q37、q38：相近护坡、河湖监测、泥沙资源利用技术排在目标前。

这说明检索已能覆盖这批验收问题，但仍需要更大、独立、专家标注的问答集来评估真实业务效果；同一技术的异名成果关系也可继续治理。

## 应用与回归检查

- 后端 Ruff 通过，98 项 pytest 通过（54.13 秒）；离线测试使用隔离数据库和明确的测试向量。
- 前端 TypeScript、ESLint、生产构建通过；2 项真实 HTTP 渲染和静态资源测试通过。
- 独立临时 PG schema / ES 索引的 7 项基础设施检查通过：PG、IK、Outbox 重放、混合检索、旧版本拒绝、撤销审批、检查点重连恢复。该组使用 hash 冒烟向量，独立于上面的真实 BGE 效果评测。
- 前端成果目录改为服务端全库搜索、每页 24 项；列表不携带整篇原文，避免超大响应。匹配结果可以打开当前页之外的成果详情。
- 真实浏览器检查发现批量导入记录的更新时间相同时，分页可能重复；排序补充唯一 ID 后，实际 PG 翻页无重复，固定相同时间戳的回归测试也通过。
- RAG 引用包含文档 ID 和原文定位；原件下载带 JWT 和组织权限校验，并保留原始文件名及 SHA-256 响应头。
- 真实问答验收发现摘要引文缺少原件入口，已补充精确回查：只有与该成果关联的原件中逐字包含引文，才挂接文档 ID 和定位；不能匹配时保留字段引用，避免把无关原件当作证据。回归测试包含关联范围与伪造数字的拒绝场景。

## 真实问答与浏览器验收

经真实 HTTP 登录、Celery、已配置的大模型及 BGE/ES 完成三次运行，详见 [运行与逐字引用核验 JSON](../evaluation/results/live-full-corpus.json)。每次均有实际模型 token 消耗，回答报告已保存 MinIO 并核对下载 SHA-256；共 6 条引文均存在于当前版本 PG 片段，关联原件中也逐字包含引文且定位一致。

| 运行 | 实际结果 |
| --- | --- |
| `0a68df67-20d4-4114-bd27-8f817cebe287` | 限定“超长顶管施工输水岩石隧洞关键技术及应用”，回答单洞 **3224m**，并引用卡管定位、开孔清渣、应急处置及临时中继站等措施；引用 `djby.doc` 原文，1 轮检索 |
| `e3601a5e-6329-4f56-b856-3416f7a8a19d` | 同会话追问 2030 年采购单价和年收入，成果范围保持不变；2 轮后明确证据不足，并说明当前登记数据不能回答未来价格或收入承诺 |
| `ded5b6c3-4579-40ed-bcd0-5886ffccdce8` | 全库检索分布式光纤管道预警，回答单通道 **35km**、漏点定位 **1–2m**，并说明原文中的光纤传感手段；引用 `111.xlsx` 的 `Sheet1!行138:139（序号31）`，1 轮检索 |

以上数字是资料中记载的技术指标，不代表本次重新测量或验证了工程性能。初次验收的原始记录保留在 `evaluation/results/live-full-corpus-before-provenance-fix.json`，其摘要引文下载入口问题已由上述修复及后续验收覆盖。

Headless Edge 使用实际 API（无 mock）验证了登录、3,860 条成果总数、24 条分页、跨页不重复、全库关键词搜索、无结果状态、原文引用展开和原件下载。下载的 `111.xlsx` SHA-256 为 `d95f5e9dc7667e869259a84cac1da64522fe722ae1a4967553ab297c050ac673`，与库中原件一致。页面无 JavaScript 异常，无 HTTP 4xx/5xx；390px 移动端无横向溢出。证据见 [浏览器结果](../evaluation/results/full-corpus-browser.json) 及本机截图 `output/bge-corpus-catalog.png`、`output/bge-corpus-answer.png`、`output/bge-corpus-mobile.png`。

复现真实问答会创建标注为“全库验收”的运行并调用已配置模型：

```powershell
.\backend\.venv\Scripts\python.exe scripts\backend-local.py app.verify_live_qa --output evaluation\results\live-full-corpus.json
```

扫描件 OCR、旧 Word 转换及原材料自身的错字或歧义仍可能影响理解；检索引文逐字验证只证明引文存在于解析文本，不能证明模型释义或成果效果一定正确。关键数字与工程决策应以可下载原件和专业核验为准。
