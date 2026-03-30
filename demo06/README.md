# demo06 · 交通出行方案 + 用户偏好画像

## 本 demo 新增内容

在 demo05（问卷 + 个性化画像）的基础上，新增「出发城市」字段，
LangGraph Agent 会自动调用 `plan_transport` 工具规划跨城交通方案，
并将交通安排融入第一天和最后一天的行程叙述中。

---

## 新增技术要点

### 1. `transport_client.py`：双源交通规划

| 情况 | 数据来源 |
|------|----------|
| 有 `AMAP_API_KEY` | 高德地图 Geocoding + Driving + Transit 实时 API |
| 无 API Key | 内置 13 个常见城市对的参考数据库（自动降级）|

**内置数据涵盖：**
北京↔上海、上海↔杭州、北京↔西安、北京↔成都、成都↔重庆、
上海↔南京、广州↔深圳、北京↔天津、上海↔苏州、西安↔成都、
广州↔桂林、上海↔黄山、北京↔青岛

### 2. `tools.py`：新增 `plan_transport` 工具

```python
@tool
async def plan_transport(origin: str, destination: str) -> str:
    """规划从出发城市到目的地的交通方案，对比自驾和高铁/公共交通两种方式..."""
```

`@tool` 装饰器让 LLM 知道何时调用此工具：
当用户消息包含「出发城市」字段时，LangGraph Agent 会自动决策并调用。

### 3. `graph.py`：更新 System Prompt

```
工作要求：
1. 若用户消息中包含出发城市，必须第一个调用 plan_transport
2. 调用 get_weather 获取天气
3. 多次调用 get_attractions 获取景点
4. 第一天写明从出发地出发的安排，最后一天写明返程参考
```

LLM 根据 System Prompt 指令，自主决定工具调用顺序（plan_transport → get_weather → get_attractions）。

### 4. `schemas.py`：新增交通相关数据模型

```python
class TransportOptionSchema(BaseModel):
    mode: str        # "driving" / "transit"
    mode_name: str   # "自驾" / "高铁/公共交通"
    summary: str     # 一句话概括
    tips: List[str]

class TransportInfo(BaseModel):
    origin: str
    options: List[TransportOptionSchema]
    recommendation: str  # 推荐语
```

`TransportInfo` 嵌入 `TripPlan`，由 LLM 的 `generate_node` 填写。

---

## 与 demo05 的对比

| 维度 | demo05 | demo06 |
|------|--------|--------|
| 工具数量 | 2（天气 + 景点）| **3（天气 + 景点 + 交通）** |
| 出发城市字段 | 无 | **TripRequest.origin** |
| JSON 输出字段 | city/days/profile/days | + **transport_info** |
| 前端新组件 | 画像横幅 + 每日画像注 | + **交通方案卡片** |

---

## 快速启动

```bash
cd demo06
# 复制并填写 API Key
copy .env.example .env

# 安装依赖（如未安装）
pip install -r requirements.txt

# 启动服务
python run.py
```

访问 [http://localhost:8005](http://localhost:8005)

### 体验流程

1. 选择预算档位（节省 / 均衡 / 体验）
2. 完成 16 道情境题（约 3 分钟）
3. 查看旅行画像揭晓
4. **填写出发城市**（可选）+ 目的地 + 天数
5. AI 规划：自动调用交通 → 天气 → 景点工具
6. 查看结果：**交通方案卡片** + 画像应用横幅 + 每日行程

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/questionnaire/analyze` | 分析问卷，返回画像 |
| POST | `/api/trip/plan-with-profile` | 画像 + 交通 + 行程（主接口）|
| POST | `/api/trip/plan` | 无问卷的快速规划 |
| GET  | `/api/graph` | 获取 LangGraph Mermaid 图 |
| GET  | `/health` | 健康检查 |

### 主接口请求体

```json
{
  "answers": {
    "q1": 3, "q2": 2, "q3": 4, "...", "q16": 2,
    "budget_level": "medium"
  },
  "city": "杭州",
  "origin": "上海",
  "travel_days": 3,
  "notes": ""
}
```
