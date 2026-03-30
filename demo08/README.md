# demo08 — Multi-Agent 并行旅行规划

## 核心变化（对比 demo07）

| 维度 | demo07 | demo08 |
|------|--------|--------|
| 架构 | 单 LLM Agent + 工具循环 | **多专家 Agent 并行 + LLM 综合** |
| 问卷 | 16题问卷系统 | **停用，简化为兴趣多选** |
| 数据采集 | LLM 主动决定调哪些工具 | **专家 Agent 并行自主采集** |
| LLM 调用 | 多轮（每轮一次工具调用） | **一次（综合所有数据生成行程）** |
| 执行可见性 | 日志在后端 | **前端显示 Agent 执行时间线** |

## Multi-Agent 图结构

```
START
  │  dispatch_agents()  —— 按需激活专家
  ├──→ weather_agent   （天气专员 · 无 LLM）
  ├──→ poi_agent       （景点专员 · 无 LLM）
  └──→ transport_agent （交通专员 · 无 LLM，仅当 origin != city）

        三者并行执行，operator.add 自动合并 context_pieces / agent_logs
        LangGraph Fan-in：等待所有并行节点完成

  planner_node  （行程规划师 · LLM 一次性生成）
  │
 END
```

## 关键 LangGraph 知识点

1. **`Send(node, payload)`** — Fan-out 并行调度，向特定节点发送独立 payload
2. **`Annotated[list, operator.add]`** — reducer 声明，自动合并并行节点的 list 结果
3. **Fan-in 自动同步** — 当多个节点都有边指向同一目标时，LangGraph 等所有节点完成再执行目标
4. **专家与规划分离** — 专家 Agent 不需要 LLM，数据采集快且稳定；LLM 只做一次高质量生成

## 快速启动

```bash
cd demo08
cp .env.example .env       # 填写 OPENAI_API_KEY（其他为可选）
pip install -r requirements.txt
python run.py
# 访问 http://localhost:8008
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/trip/plan` | Multi-Agent 规划（主接口） |
| GET  | `/api/health` | 健康检查 |

### 请求示例

```json
{
  "city": "杭州",
  "origin": "上海",
  "travel_days": 3,
  "preferences": ["历史文化", "自然风景", "美食探索"],
  "budget_level": "medium",
  "notes": "带老人，避免长途步行"
}
```

### 响应结构

```json
{
  "success": true,
  "message": "行程规划完成",
  "data": { /* TripPlan */ },
  "transport_detail": { /* 结构化交通数据，用于前端卡片渲染 */ },
  "agent_logs": [
    { "agent": "weather", "label": "天气专员", "icon": "🌤️",
      "status": "ok", "duration_ms": 320, "detail": "杭州 3天 · Open-Meteo", "source": "..." },
    { "agent": "poi",       "label": "景点专员", ... },
    { "agent": "transport", "label": "交通专员", ... },
    { "agent": "planner",   "label": "行程规划师", ... }
  ]
}
```

## 问卷系统说明

问卷系统（demo05/06/07）的 16 题画像采集已暂停启用。
原因：问卷设计需进一步优化，确保问题场景化、答案自然、画像映射准确后再重新集成。

当前方案（简单兴趣多选）作为临时替代，保留完整的 Multi-Agent 架构演示价值。
