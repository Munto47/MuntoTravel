# demo09 — 两阶段 Multi-Agent + 城市内路线规划

## 核心演进（demo08 → demo09）

| 维度 | demo08 | demo09 |
|------|--------|--------|
| 图阶段 | 一阶段 | **两阶段** |
| 路线规划 | 无 | **route_node：景点间步行/公交** |
| 住宿字段 | 无 | **hotel（每日出行起点）** |
| locations | 无 | **LLM 输出有序地点列表** |
| 前端 | 时间线 | **时间线 + 路线连接器胶囊** |

## 两阶段图结构

```
START
  │ dispatch_agents (Send / 并行 Fan-out)
  ├──→ weather_agent   ─┐
  ├──→ poi_agent        ├─ Phase 1：并行数据采集（无 LLM）
  └──→ transport_agent ─┘
                        │ Fan-in
                  planner_node    Phase 2a：LLM 决策（去哪里、什么顺序）
                        │ add_edge（顺序）
                   route_node     Phase 2b：工具执行（怎么走、花多久）
                        │
                       END
```

## 新 LangGraph 知识点

| 知识点 | 位置 | 说明 |
|--------|------|------|
| `add_edge` 顺序边 | `graph.py · build_graph()` | planner → route，与并行 Fan-out 完全不同 |
| 状态读-改-写 | `graph.py · route_node` | 读 trip_plan，修改，写回 state |
| 两阶段分工 | 整体架构 | LLM 决策 + 工具执行，各司其职 |

## route_client.py 路线策略

| 距离 | API | 降级 |
|------|-----|------|
| ≤ 2km | 高德步行 API | 距离/80m·min 估算 |
| > 2km | 高德公交 API | 距离/500m·min 估算 |
| 无 key | — | 全部估算 |

并发 geocode：每日所有地点并行解析坐标，再并发请求每段路线。

## 前端路线连接器

```
🌅 早餐  [楼外楼]
   ┊ 🚶 步行 8分钟  楼外楼 → 西湖断桥
☀️ 上午  [西湖断桥、雷峰塔...]
   ┊ 🚌 公交 15分钟  西湖断桥 → 知味观
🍱 午餐  [知味观]
```

## 快速启动

```bash
cd demo09
cp .env.example .env
pip install -r requirements.txt
python run.py
# http://localhost:8009
```
