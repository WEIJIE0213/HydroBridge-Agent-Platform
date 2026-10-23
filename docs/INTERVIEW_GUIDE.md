# HydroBridge 面试材料入口

当前主讲材料是 [水利科技成果转化 Agent 面试题库（41题）](INTERVIEW_QA.md)，对应 `3.1.0-three-business-tools`。

项目统一为一个主 Agent 加三个业务子图：成果搜寻与评价、需求分析与匹配、成果答疑问答。模型看到三个业务工具，子图执行完成后均回到 Agent 决策。需求局部修改、候选序号、上下文摘要、错误与降级边界见题库。

实现细节见 [架构说明](ARCHITECTURE.md)。历史检索数据见 [BGE 全库验收记录](BGE_FULL_CORPUS_VERIFICATION.md)；这些数据不能代表当前 Agent 改造已经通过端到端验收。

仓库内较早的学习手册可能描述 MySQL、Qdrant、多 Agent 或八工具架构，不作为当前面试口径。本文件原来的提纲保留在 Git 历史中。
