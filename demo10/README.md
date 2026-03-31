# demo10 — 旅行规划 Agent（用户画像问卷 + AMAP 精确化）

## 项目介绍

MuntoTravel demo10 是一个**个性化智能旅行规划系统**，在 demo09 两阶段 Multi-Agent 图的基础上新增了两大能力：

1. **旅行画像问卷（可选）**：用户填写 5 维度 10 道题问卷，系统自动生成用户画像并注入 LLM 规划提示，实现真正个性化行程
2. **高德 POI 2.0 + 分区提示**：按「住宿 / 景点 / 餐饮」检索 v5 POI，返回 `rich_poi_catalog` 与区县聚合说明供 Planner 参考；市内路线在有 `citycode` 时公交可走 v5 transit；可选服务端静态地图预览

---

## 快速启动

```bash
# 1. 进入 demo10 目录
cd demo10

# 2. 配置环境变量
cp .env.example .env
# 必填：OPENAI_API_KEY
# 强烈建议填写：AMAP_API_KEY（否则路线全部降级为估算）

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动服务
python run.py

# 5. 访问
# 浏览器打开 http://localhost:8010
```

### 最小 .env 配置

```env
OPENAI_API_KEY=sk-xxxxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

### 完整 .env 配置（推荐）

```env
OPENAI_API_KEY=sk-xxxxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
AMAP_API_KEY=xxxxxxxx       # 高德开放平台申请（Web服务类型 Key）
QWEATHER_API_KEY=xxxxxxxx   # dev.qweather.com 申请（可选）
PORT=8010
LOG_LEVEL=INFO
```

---

## 架构图

```
START
  │ dispatch_agents（并行 Fan-out）
  ├──→ weather_agent   ─┐
  ├──→ poi_agent        ├─ Phase 1：并行数据采集（无 LLM，快速）
  └──→ transport_agent ─┘
                        │ Fan-in（自动等待全部完成）
                  planner_node    Phase 2a：LLM 决策（去哪里、顺序）
                        │ add_edge（顺序边）
                   route_node     Phase 2b：工具计算（路线、时间）
                        │
                       END
```

---

## 新增特性（vs demo09）

| 维度 | demo09 | demo10 |
|------|--------|--------|
| POI 搜索 API | v3 基础版 | **v5 精准版**（含评分/时间/入口坐标） |
| 路线 API | v3 步行/公交 | **v5 步行/骑行 + 公交（v5 含 citycode）/ 兜底 v3** |
| POI 结构 | 按兴趣偏好关键字搜 | **住宿 / 景点 / 餐饮** 三类 + `rich_poi_catalog` |
| 城际列车 | 示例 | **仅北京↔上海** 保留参考车次；其余城市 **12306** 提示 |
| 路线信息 | 仅模式+分钟数 | **含途经主路/线路名** |
| 问卷系统 | 无 | **5 维度 10 题 → 用户画像注入 LLM** |
| 坐标精确度 | POI 中心点 | **POI 入口坐标**（大景区更准确） |
| 骑行模式 | 无 | **1.5~5km 骑行模式** |
| Geocode 质量 | 无过滤 | **精度门控（拒绝省/市级粗糙结果）** |
| 坐标缓存 | 内存（重启丢失） | **持久化 JSON（跨请求复用）** |

---

## 问卷使用流程

```
1. 浏览器访问 http://localhost:8010
2. 填写基本出行信息（目的地、天数、偏好）
3. 展开「旅行画像问卷」（可选，10题约2分钟）
4. 点击「生成我的旅行画像」→ 系统返回 profile_note
5. 点击「启动规划」→ LLM 结合画像生成个性化行程
```

### 问卷维度

| 维度 | 覆盖内容 |
|------|---------|
| 出行节奏 | 深度游 vs 广度游；能否接受排队 |
| 体验深度 | 走马观花 vs 深度体验；打卡 vs 沉浸 |
| 社交风格 | 独处/情侣 / 家庭（含老幼）/ 朋友团 |
| 消费风格 | 街边小吃 / 中等餐厅 / 偶尔高端 |
| 体力水平 | 不爬山 / 轻度徒步 / 高强度户外 |

---

## API 接口说明

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/questionnaire` | 获取问卷题目（10题） |
| POST | `/api/profile` | 提交答案 → 返回 profile_note |
| POST | `/api/trip/plan` | 规划行程（支持 profile_note 字段） |

### POST /api/profile 示例

```bash
curl -X POST http://localhost:8010/api/profile \
  -H "Content-Type: application/json" \
  -d '{"answers": ["Q1A", "Q3B", "Q5A", "Q7B", "Q9B", "Q10B"]}'
```

### POST /api/trip/plan 示例（含画像）

```bash
curl -X POST http://localhost:8010/api/trip/plan \
  -H "Content-Type: application/json" \
  -d '{
    "city": "杭州",
    "origin": "上海",
    "hotel": "西湖边精品酒店",
    "travel_days": 2,
    "preferences": ["历史文化", "美食探索"],
    "budget_level": "medium",
    "notes": "",
    "profile_note": "【用户画像】\n  - 出行节奏：深度游，每天3~4景点\n  - 体力水平：轻度徒步"
  }'
```

---

## 路线规划策略

| 距离 | 交通方式 | API |
|------|---------|-----|
| ≤ 1.5km | 步行 | 高德 v5 Walking（`show_fields=cost`，真实耗时） |
| 1.5~5km | 骑行 | 高德 v5 Bicycling（`show_fields=cost`，真实耗时） |
| > 5km | 公交/地铁 | 高德 v5 Transit（有 `citycode` 时）或 v3 兜底（含线路名） |
| API 失败 | 距离估算 | 基于 Haversine 距离 |

---

## 数据与真实性说明

- **POI 个性化检索**：关键词随预算、偏好、备注/画像与稳定哈希主分页变化；每类 **合并两页** 去重扩大候选；备注与画像中的 **短句** 会拼入餐饮检索；若提及住宿则亦并入住宿词；若判定与景点相关则并入景点词。
- **POI 展示字段**：来自高德官方 `business` / `navi` 等（评分、标签、营业时间、人均等），**不含**用户原创评论。
- **城际火车**：除 **北京↔上海** 外，系统不展示具体车次/时刻，仅提示通过 **12306** 查询；京沪线为示例参考，非实时余票。
- **静态地图**：由服务端拼接 `/v3/staticmap` URL，**Key 仅在服务端**，不在浏览器中配置。

---

## 文件结构

```
demo10/
├── run.py                  # 启动入口（端口 8010）
├── .env / .env.example     # 环境变量
├── requirements.txt        # 依赖列表
└── app/
    ├── main.py             # FastAPI 路由（含问卷接口）
    ├── graph.py            # LangGraph 两阶段图
    ├── agents.py           # 三个专家 Agent（天气/POI/交通）
    ├── route_client.py     # 高德路线规划客户端（v5升级版）
    ├── questionnaire.py    # 问卷题目定义（10题5维度）
    ├── profiler.py         # 问卷答案 → 用户画像转换
    ├── schemas.py          # Pydantic 数据模型
    ├── weather_client.py   # 天气数据客户端
    ├── transport_client.py # 城际交通数据
    ├── logger.py           # 日志配置
    ├── amap/               # 高德封装（POI / 地理编码 / 静态图）
    └── static/index.html   # 前端界面
```
