# MuntoTravel 项目交接文件

> 版本：2026-03-31  
> 维护者：项目团队 / Cursor AI Assistant  
> 当前主线：**demo11 已完成**（地图可视化）  
> 对话背景参考（Cursor agent-transcripts）：`f3774bdf-e0f1-4b89-a018-2c1a7b43cda7`（学习路线、demo00–08、问卷与多 Agent 等）、`383be103-6619-4bb4-9fab-0e8a33647cfc`（迭代方案、高德优化、demo10 修复与 demo11 启动）。

---

## 一、项目目标（总览）

### 1.1 学习目标（产品形态）

从零到一构建一个**面向国内中文用户**的智能旅行规划应用，同时作为**渐进式学习模板**：技术路线为「直接 LLM → FastAPI → 工具调用 → LangGraph → Multi-Agent → 生产化」。每个 `demo` 引入一个可讲清楚的概念，便于后来者按目录学习。

### 1.2 产品愿景（中长期，分阶段落地）

依据项目讨论中形成的**完整愿景**（不必一次实现），包括但不限于：

1. **城际交通**：出发地/目的地、多种方式（自驾、火车/高铁等），结合地图或列车信息生成往返方案；困难时用本地数据保底。  
2. **问卷与画像**：「多份答案 → 一份规划」；问卷需易答、有场景题；画像结果**显式**体现在行程或说明中（用户感到「没白答」）。  
3. **端到端路线**：从 A 到 B 的城际方案 + 目的地城内景点/餐厅/酒店之间的**可执行路线**（非空话）。  
4. **地图与参与感**：规划完成后在地图上**动态演示**行程（可含逐站强调、未来可扩展配图等）。  
5. **数据质量**：POI/榜单/实时性（如扫街榜、多源筛选）在能力范围内逐步接入；备注（如带老人、素食）需被各专员消费。  
6. **价格与档位**：接入价格或规则后，按消费等级组合美食/出行/住宿方案。

当前代码已覆盖其中 **3、4 的部分能力**（城内路线 + 坐标 + 前端地图动画），其余见「未完成事项」与路线图。

---

## 二、预期实现功能与预期效果

### 2.1 已具备的用户可见效果（主线 demo10 / demo11）

| 能力 | 预期效果 |
|------|----------|
| 表单输入 | 目的地、可选出发地/住宿、天数、偏好、预算、备注；阳光系 UI。 |
| 多 Agent 并行 | 天气、POI、（可选）城际交通并行采集，时间线展示各 Agent 状态。 |
| 两阶段规划 | Planner（LLM）生成结构化行程；Route（无 LLM）用高德 API 计算相邻点路线段。 |
| 问卷（可选） | `GET /api/questionnaire` + `POST /api/profile` 生成 `profile_note`，注入规划（用户可不填问卷仅用兴趣）。 |
| 富 POI / 交通卡片 | 结构化展示候选 POI、驾车/列车参考等（策略随版本在 `transport_client` / `agents` 中定义）。 |
| **地图（demo11）** | 响应中带 `coord_map`；前端加载高德 JSAPI 2.0，按日 Polyline、地点 Marker，支持「动画播放」逐站高亮。 |

### 2.2 配置到位时的预期

- **`OPENAI_API_KEY`**：行程 JSON 可生成并通过 Pydantic 校验。  
- **`AMAP_API_KEY`（Web 服务）**：POI、geocode、城内步行/骑行/公交路线可用；坐标进入缓存，`coord_map` 有内容。  
- **`AMAP_JS_KEY`（Web 端 JS API）**：浏览器内**交互地图**可用；未配置时仍有文字行程与静态示意（若有），地图区显示配置说明而非崩溃。

### 2.3 与「完整愿景」的差距（诚实预期）

- 列车/高铁**实时时刻与票价**仍以参考或 12306 引导为主，非全量真实 API。  
- **小红书/美团/豆瓣等榜单**未接入；POI 以高德为主，质量依赖检索词与 prompt。  
- **问卷**已重启为 demo10 方案，与早期 16 题设想不同；个性化仍可能因 POI 固化等问题需持续优化（见已知问题）。  
- 地图动画当前为 **Marker + 折线 + 播放**，**逐站配图**属未来扩展。

---

## 三、已完成内容（按 demo）

| demo | 状态 | 端口 | 核心交付 |
|------|------|------|----------|
| demo00 | ✅ | — | Jupyter：直连 LLM |
| demo01 | ✅ | 8001 | FastAPI 基础规划 |
| demo02 | ✅ | 8002 | 天气上下文注入 |
| demo03 | ✅ | 8003 | QWeather + 高德 POI |
| demo04 | ✅ | 8004 | LangGraph 工具循环 |
| demo05 | ⚠️ 旧版暂停 | 8005 | 原问卷分叉，参考用 |
| demo06 | ✅ | 8006 | 城际交通基础 |
| demo07 | ✅ | 8007 | 精细交通 + 日志 |
| demo08 | ✅ | 8008 | Multi-Agent Fan-out/in |
| demo09 | ✅ | 8009 | 两阶段图 + 城内路线 |
| demo10 | ✅ | 8010 | 问卷重启 + 画像注入 + AMAP 模块收拢等 |
| demo11 | ✅ | 8011 | `coord_map` + 高德 JSAPI 地图 + 动画播放 |

**demo11 关键文件**：`demo11/app/route_client.py`（`get_trip_coords`）、`demo11/app/graph.py`、`demo11/app/schemas.py`、`demo11/app/main.py`（`/api/config`）、`demo11/app/static/index.html`、`demo11/README.md`。

---

## 四、关键架构与设计决策

1. **渐进式 demo**：每版可独立运行、自有 `.env` / `requirements.txt`，避免「只有一个大仓库看不懂」。  
2. **LLM 决策 vs 工具执行**：行程结构由 LLM；**精确路线、列车/驾车等结构化数据由 API/本地库**，减少幻觉。  
3. **两阶段图（demo09+）**：Phase1 并行采集 → Phase2 `planner_node` → `route_node` 顺序；`route_node` **不调用 LLM**。  
4. **问卷可选**：画像系统为增强项；未答问卷时仍以兴趣标签为主规划（见 demo10 设计）。  
5. **交通策略**：城际以驾车/参考列车 + 文案为主；全量 12306 类 API 非本仓库目标。  
6. **地名与 geocode**：`route_client._clean_location_name` 等缓解括号、别名导致的失败；缓存见 `data/coord_cache.json`（路径相对运行目录）。  
7. **双 Key（demo11）**：`AMAP_API_KEY` 用于服务端 Web 服务；`AMAP_JS_KEY` 用于前端 JSAPI；类型不同，不可混用。  
8. **配置暴露**：仅 `/api/config` 返回 `amap_js_key`，不暴露服务端密钥。

---

## 五、未完成事项与路线图

### 5.1 已知问题（维护优先级参考）

| 问题 | 位置 | 说明 |
|------|------|------|
| geocode 偶发失败 / 长名成功率有限 | `route_client.py` | 继续增强清洗与缓存策略 |
| 南方城市列车 `_TRAIN_DB` 不全 | `transport_client.py` | 按需补数据 |
| 规划「模板化」、地点固化 | `agents.py` / `graph.py` prompt | 已部分通过 prompt 与检索画像缓解，仍需迭代 |
| Windows 控制台日志中文乱码 | `logger.py` | 显示问题，数据一般正常 |
| LangChain + Python 3.14 警告 | 依赖 | 建议 Python 3.11–3.12 用于开发 |

### 5.2 近期工程 TODO（来自维护列表）

- [ ] `_TRAIN_DB` 补充更多城市对  
- [ ] `_clean_location_name` 扩展规则（如「推荐」「特色」等）  
- [ ] geocode 缓存：Redis 或统一本地 JSON 策略（已有文件缓存可继续规范）  
- [ ] 前端对估算路线「（估算）」一致性展示（字段已部分支持）

### 5.3 规划中的后续 demo（见历史路线图）

| 阶段 | 主题 | 要点 |
|------|------|------|
| demo12 | 价格系统（轻量） | POI `cost`、预算档位联动等 |
| demo13 | Supervisor / HITL | 澄清需求、interrupt、checkpoint |
| demo14 | 生产化 | Docker、限流、Redis 等 |

更长周期：**榜单多源**、**列车实时 API**、**地图逐站配图**、**高并发** 等，需在资源与合规前提下分阶段评估。

---

## 六、相关文件索引（接手阅读顺序）

| 用途 | 路径 |
|------|------|
| 仓库级 API 说明 | `AMAP_API_GUIDE.md` |
| 最新完整功能说明 | `demo11/README.md` |
| 图编排与状态 | `demo11/app/graph.py` |
| 专家 Agent | `demo11/app/agents.py` |
| 城内路线与坐标 | `demo11/app/route_client.py` |
| 数据模型与 API 响应 | `demo11/app/schemas.py` |
| 入口与路由 | `demo11/app/main.py` |
| 问卷与画像 | `demo11/app/questionnaire.py`、`profiler.py` |
| 高德封装 | `demo11/app/amap/*.py` |
| 前端 | `demo11/app/static/index.html` |
| 启动 | `demo11/run.py` |

**接手建议**：在 `demo11` 目录配置 `.env` 后运行 `python run.py`，访问 `http://localhost:8011`；先读 `graph.py` 顶部架构注释，再读 `agents.py`、`route_client.py`。

---

## 七、环境与启动（以 demo11 为例）

```bash
cd demo11
cp .env.example .env
# 必填：OPENAI_API_KEY
# 推荐：AMAP_API_KEY（服务端 POI/路线/geocode）
# 地图页面：AMAP_JS_KEY（高德控制台申请「Web端(JS API)」）
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
python run.py
# 浏览器：http://localhost:8011  （默认 PORT=8011，见 .env.example）
```

最小 `.env` 示例：

```env
OPENAI_API_KEY=sk-xxxxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

---

## 八、技术栈速览

- **后端**：Python 3.11+、FastAPI、Pydantic v2、LangGraph、LangChain-OpenAI、httpx、uvicorn  
- **前端**：纯 HTML/CSS/JS，橙 + 天蓝活力主题  
- **外部能力**：OpenAI 兼容 API、高德（Web 服务 + JS）、和风 / Open-Meteo 天气

### LangGraph 概念覆盖（截至 demo11）

StateGraph、ToolNode、Send Fan-out、Annotated reducer、顺序边、planner/route 读改写、`coord_map` 状态字段。

---

## 九、与学习者的约定

本项目既是**可运行演示**，也是**学习模板**。新 demo 建议在文件头保留：与上一版差异、新引入知识点。注释以**架构与原因为主**，避免无信息增量的流水账。

---

*本文档整合 `HANDOVER.md` 历史版本、项目内实现与对话中达成的目标与迭代共识；后续里程碑完成后请更新「版本」「当前主线」与第三节表格。*
