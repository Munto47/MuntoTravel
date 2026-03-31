# MuntoTravel 项目交接文件

> 版本：2026-03-30  
> 上一位开发者：Cursor AI Assistant  
> 当前进度：demo09 已完成并通过验证

---

## 一、项目概述

MuntoTravel 是一个渐进式学习项目，目标是从零开始构建一个生产级智能旅行规划 Agent。
每个 demo 都引入一个新的技术概念，形成完整的学习路线。

**目标用户**：国内旅行用户，主要面向中文市场。

**技术路线**：`直接 LLM 调用 → FastAPI → 工具调用 → LangGraph → Multi-Agent → 生产化`

---

## 二、项目结构

```
MuntoTravel/
├── .gitignore          # API Key 防泄漏配置
├── HANDOVER.md         # 本文件
├── demo00/             # Jupyter：直接调用 LLM API
├── demo01/             # FastAPI + 基础规划
├── demo02/             # 天气工具（上下文注入）
├── demo03/             # QWeather + 高德 POI
├── demo04/             # LangGraph 基础 Agent（工具调用循环）
├── demo05/             # 问卷系统 + 用户画像（已暂停）
├── demo06/             # 城际交通规划（基础版）
├── demo07/             # 精细交通 + 日志系统
├── demo08/             # Multi-Agent 并行图（Fan-out/Fan-in）
└── demo09/             # 两阶段图 + 城市内路线规划（当前最新）
```

每个 demo 都是独立的 FastAPI 应用，有自己的 `.env`、`requirements.txt`、`README.md`。

---

## 三、各 demo 状态速览

| demo | 核心功能 | 状态 | 端口 | 关键文件 |
|------|---------|------|------|---------|
| demo00 | Jupyter 直接 LLM | ✅ 完成 | — | PlanWithLLM.ipynb |
| demo01 | FastAPI 基础规划 | ✅ 完成 | 8001 | app/main.py |
| demo02 | 天气上下文注入 | ✅ 完成 | 8002 | app/weather_client.py |
| demo03 | QWeather + 高德 POI | ✅ 完成 | 8003 | app/weather_client.py, tools.py |
| demo04 | LangGraph Agent | ✅ 完成 | 8004 | app/graph.py |
| demo05 | 问卷系统 | ⚠️ 暂停 | 8005 | app/profiler.py, schemas.py |
| demo06 | 城际交通基础版 | ✅ 完成 | 8006 | app/transport_client.py |
| demo07 | 精细交通 + 日志 | ✅ 完成 | 8007 | app/logger.py, transport_client.py |
| demo08 | Multi-Agent 并行 | ✅ 完成 | 8008 | app/graph.py, agents.py |
| demo09 | 两阶段图 + 路线 | ✅ 完成 | 8009 | app/graph.py, route_client.py |

---

## 四、技术栈全览

### 后端
- **Python 3.11+**
- **FastAPI** — Web 框架
- **Pydantic v2** — 数据验证与序列化
- **LangGraph** — Multi-Agent 图编排（核心）
- **LangChain-OpenAI** — LLM 调用适配
- **httpx** — 异步 HTTP 客户端
- **python-dotenv** — 环境变量管理
- **uvicorn** — ASGI 服务器

### 前端
- 纯 HTML + CSS + Vanilla JS（无框架）
- 阳光/活力色系（橙 + 天蓝渐变主题）

### 外部 API
| API | 用途 | Key 变量 | 免费限制 |
|-----|------|---------|---------|
| OpenAI | LLM 生成 | `OPENAI_API_KEY` | 按 token 计费 |
| 高德地图 | POI/路线/geocode | `AMAP_API_KEY` | 每日 5000 次免费 |
| 和风天气 | 天气预报 | `QWEATHER_API_KEY` | 每日 1000 次免费 |
| Open-Meteo | 天气备源 | 无需 Key | 免费 |

### LangGraph 核心概念（已覆盖）
| 概念 | 首次出现 | 代码位置 |
|------|---------|---------|
| StateGraph + TypedDict | demo04 | graph.py |
| @tool + ToolNode | demo04 | tools.py |
| agentic loop (should_continue) | demo04 | graph.py |
| bind_tools | demo04 | graph.py |
| Send() Fan-out | demo08 | graph.py::dispatch_agents |
| Annotated + operator.add reducer | demo08 | graph.py::TravelState |
| Fan-in 自动同步 | demo08 | graph.py::build_graph |
| 顺序 add_edge | demo09 | graph.py::build_graph |
| 状态读-改-写 | demo09 | graph.py::route_node |

---

## 五、demo09 核心架构详解（最新版）

### 图结构
```
START
  │ dispatch_agents() → Send()
  ├──→ weather_agent   ─┐  Phase 1: 并行数据采集（无 LLM，快）
  ├──→ poi_agent        ├─ Fan-in（LangGraph 自动同步）
  └──→ transport_agent ─┘
                 ↓
         planner_node   Phase 2a: LLM 决策（决定去哪里）
                 ↓ add_edge（顺序）
          route_node    Phase 2b: 工具执行（计算怎么走）
                 ↓
               END
```

### 关键数据流
```python
# 1. dispatch 传 payload（不是完整 state）
Send("weather_agent", {"city": ..., "days": ..., "notes": ...})

# 2. 专家返回 list 字段（operator.add 自动合并）
return {"context_pieces": ["天气数据..."], "agent_logs": [{...}]}

# 3. planner 读 context_pieces，写 trip_plan（含 locations 列表）
state["context_pieces"]  # 所有专家数据
return {"trip_plan": plan.model_dump()}

# 4. route_node 读 trip_plan，改 route_segments，写回
trip_plan["days"][i]["route_segments"] = [seg.model_dump()]
return {"trip_plan": trip_plan}  # 覆盖写回
```

### 关键设计决策记录

1. **问卷系统暂停**：原16题问卷需要重新设计，临时用"兴趣多选"替代
   - 恢复时间：当问卷设计更合理后（见 demo10 计划）
   - 代码保留在 demo05/06/07 中，可参考 `app/profiler.py`

2. **transport 预计算而非工具调用**：demo07 开始，交通数据在 LLM 之前计算
   - 原因：结构化数据（列车班次、价格）LLM 容易"幻想"，工具更可靠
   - 交通详细卡片直接传给前端，不经过 LLM

3. **LLM 不做路线计算**：route_node 完全不调用 LLM
   - 原因：路线是精确计算，LLM 不知道实时交通
   - API 层：高德步行 API（≤2km）+ 公交 API（>2km）+ 距离估算降级

4. **地名清洗**：`route_client._clean_location_name()`
   - 原因：LLM 生成地名带括号注释（如"楼外楼（西湖醋鱼·老字号）"）导致 geocode 失败
   - 策略：先用原名，失败后剥离括号再试

---

## 六、已知问题与 TODO

### 已知问题
| 问题 | 位置 | 严重性 | 建议 |
|------|------|--------|------|
| LLM 地名含括号导致 geocode 偶发失败 | route_client.py | 🟡 中 | `_clean_location_name` 已修复大部分，可继续优化 |
| 广州→杭州 火车 DB 无记录 | transport_client.py `_TRAIN_DB` | 🟡 中 | 补充南方城市数据 |
| 问卷系统 demo05-07 与 demo08-09 代码分叉 | schemas.py | 🟡 中 | demo10 统一 |
| planner 偶尔生成 `meals: []` 老格式 | graph.py | 🟡 中 | Prompt 已加强，可继续监控 |
| Windows 终端 GBK 导致日志显示乱码 | logger.py | 🟢 低 | 不影响功能，数据正确 |
| route_node 对长名景点 geocode 成功率约 70% | route_client.py | 🟡 中 | 需要更智能的名称清洗 |

### 近期 TODO（按优先级）
- [ ] `_TRAIN_DB` 补充南方城市（广州、深圳、厦门、南京等互相连接）
- [ ] `_clean_location_name` 增加更多清洗规则（去掉"推荐"、"特色"等形容词）
- [ ] `route_client` geocode 坐标缓存持久化（Redis 或本地 JSON），避免同一城市重复请求
- [ ] 前端路线连接器：当 geocode 失败（估算）时显示"(估算)"标注（已有字段，前端已加 rp-est）

---

## 七、环境配置（快速启动任意 demo）

```bash
# 1. 进入对应 demo 目录
cd demo09

# 2. 配置 .env（复制示例文件）
cp .env.example .env
# 必填：OPENAI_API_KEY
# 选填：AMAP_API_KEY（不填则 POI/路线降级）、QWEATHER_API_KEY（不填则用 Open-Meteo）

# 3. 安装依赖（推荐 venv）
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt

# 4. 启动
python run.py

# 5. 访问
# http://localhost:8009（demo09 端口，其他 demo 对应 8001~8008）
```

### .env 最小配置（仅 OpenAI 必填）
```env
OPENAI_API_KEY=sk-xxxxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

### 完整配置（推荐）
```env
OPENAI_API_KEY=sk-xxxxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
AMAP_API_KEY=xxxxxxxx           # 高德开放平台申请
QWEATHER_API_KEY=xxxxxxxx       # dev.qweather.com 申请
QWEATHER_API_HOST=devapi.qweather.com
QWEATHER_AUTH_TYPE=apikey
LOG_LEVEL=INFO
```

---

## 八、后续开发计划与评估

### demo10 — 问卷系统重启（推荐优先）

**目标**：重设计问卷 → 重接入 planner，真正实现个性化，同时注意问卷系统是用户可选选项，当前仍以用户兴趣选择为主

**改进方向**：
- 减至 10 题（5 维度），每题必须是真实旅行场景
- 画像映射改为"直接生成 prompt 片段"（不是分数换标签）
- 在每日行程中用 `profile_note` 显式说明"因为你选了X，所以推荐Y"
- 参考：`demo05/app/profiler.py` + `demo05/app/questionnaire.py`

**技术参考**：`demo05/app/profiler.py` 的 `compute_user_profile()` 结构可复用，需重写维度定义。

---

### demo11 — 地图可视化

**目标**：在前端展示高德 JS SDK 地图，行程路线动态播放

**技术方案**：
```
复用 route_client._coord_cache 中的坐标数据
→ 在响应中新增 coord_map: dict[name, "lng,lat"]
→ 前端加载高德 JS SDK（Web 端 Key，在高德控制台单独申请）
→ 用 Polyline + 逐步添加 Marker 实现动画
```

**关键实现**：
1. `route_node` 结束后，将 `_coord_cache` 中与本次行程相关的坐标打包进响应
2. 前端在结果页底部渲染地图（高德 JSAPI 2.0）
3. 用 `setInterval` 逐站展示

---

### demo12 — 价格系统（轻量版）

**目标**：为餐厅/景点提供价格参考，与预算档位联动

**实际可行方案**：
- 高德 POI 响应中 `cost` 字段 = 人均消费，直接用（现在丢弃了，加回来即可）
- 交通价格已有（demo07 `_TRAIN_DB` + 高德驾车收费）
- 酒店：用 LLM 按预算档位描述价格区间（不接第三方 OTA）
- `agents.py` 中 `poi_agent` 改为请求 `extensions=all`（包含 cost 字段）

---

### demo13 — Supervisor 模式

**目标**：引入需求澄清对话，用户说"去云南"时自动追问细节

**LangGraph 新知识点**：
- `Supervisor Agent`（一个 LLM 节点决定下一步）
- `Human-in-the-loop`（`interrupt_before` / `interrupt_after`）
- Checkpointing（`MemorySaver`）

**参考代码**：
```python
from langgraph.checkpoint.memory import MemorySaver
graph = builder.compile(checkpointer=MemorySaver(), interrupt_before=["planner_node"])
```

---

### demo14 — 生产化部署

**目标**：Docker 容器化 + 云部署 + 基础安全

**最小实现**：
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8009"]
```
- `docker-compose.yml`：FastAPI + Redis（geocode 缓存）
- 腾讯云 CVM 或 Serverless（低成本）
- 加 `slowapi` 限流（防 LLM 超支）

---

## 九、代码质量说明

### 哪些代码可以直接复用
- `app/logger.py` — 所有 demo 共用，无需修改
- `app/weather_client.py` — demo03+ 一致，直接复制
- `app/transport_client.py` — demo07+ 一致，直接复制（需补充南方城市数据）
- `app/agents.py` — demo08+ 一致，`_parse_notes()` 可继续扩展规则

### 哪些需要小心
- `TravelState` TypedDict：新增字段必须同时在 `initial_state` 初始化，否则 LangGraph 静默丢弃
- `planner_node` JSON prompt：字段名必须与 Pydantic schema 完全一致，任何不一致都会导致验证失败
- `transport_client._TRAIN_DB`：城市名必须用中文（"北京"不是"Beijing"），否则查不到

### 测试方式
```bash
# 快速健康检查
curl http://localhost:8009/api/health

# 完整流程测试（Windows PowerShell）
$body = '{"city":"杭州","origin":"上海","hotel":"西湖边","travel_days":2,"preferences":["历史文化","美食探索"],"budget_level":"medium","notes":""}' 
$bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
$resp = Invoke-WebRequest -Uri "http://localhost:8009/api/trip/plan" -Method POST -ContentType "application/json" -Body $bytes -UseBasicParsing
($resp.Content | ConvertFrom-Json).success  # 应输出 True
```

---

## 十、与学习者的约定

本项目有双重身份：**可用产品 + 学习模板**。

每个新 demo 文件的头部注释都有：
- 本 demo 的架构改动说明
- 与上一版的对比
- 新引入的 LangGraph/技术知识点

代码中的注释策略：
- **架构注释**（为什么这么设计）✅ 保留
- **对比注释**（demo07 vs demo08）✅ 保留
- **流水账注释**（"这里调用API"）❌ 不写

---

*接手后建议先从 demo09 运行起来，阅读 `graph.py` 中的架构注释，再看 `agents.py`，最后看 `route_client.py`。*
