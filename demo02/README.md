# MuntoTravel Demo02 · 天气感知的旅行规划

## 这一版做了什么

demo02 在 demo01 的基础上引入了第一个**真实外部数据源**：天气预报。

核心新概念：**上下文注入模式（Context Enrichment）**

```
demo01 的流程：
  用户请求 → LLM → 行程

demo02 的流程：
  用户请求 → 外部 API（天气） → 注入 Prompt → LLM → 行程
```

LLM 本身无法感知真实世界的实时状态（天气、时间、价格……）。
上下文注入模式是解决这个问题的最基础方法，也是后续 Tool Use（demo03）的思想前身。

## 新增文件

```
demo02/
├── app/
│   ├── weather_client.py   ← NEW：调用 Open-Meteo 免费天气 API
│   ├── schemas.py          ← 新增 WeatherInfo、DayWeather；TripPlan 新增 weather_context
│   ├── planner.py          ← 先取天气，再调 LLM；天气失败不阻断主流程
│   ├── llm_client.py       ← 全中文 prompt；接受 weather 参数注入上下文
│   ├── main.py             ← 基本不变
│   └── static/index.html   ← 新增天气横幅展示
├── requirements.txt        ← 依赖不变，httpx 已覆盖
└── .env.example
```

## 关键设计决策

### 1. 天气查询失败不阻断主流程

```python
weather = await get_weather(request.city, request.travel_days)
# weather 可能是 None，但 create_trip_plan 会继续执行
```

天气是「增强信息」，不是「必须项」。网络抖动、城市名无法识别、
API 临时不可用——任何失败都只是退化到「没有天气上下文」的状态，
不影响行程生成。这是生产级服务的容错设计原则。

### 2. to_prompt_text() 在 Schema 层完成转换

```python
class WeatherInfo(BaseModel):
    def to_prompt_text(self) -> str:
        ...  # 把结构化数据转成 LLM 能理解的自然语言描述
```

数据模型负责自己的「对外表达」，而不是在 llm_client 里做字符串拼接。
这让 llm_client 保持干净：只关心「怎么调 LLM」，不关心「天气数据长什么样」。

### 3. weather_context 写回 TripPlan

```python
plan = plan.model_copy(update={"weather_context": weather.to_prompt_text()})
```

把我们「注入给 LLM 的天气文本」也写入响应，前端可以直接展示。
这让用户看到「系统为什么这么规划」，提升可解释性。

## 使用的外部 API

两个完全免费、无需注册、无需 API Key 的接口：

| 接口 | 用途 |
|---|---|
| `geocoding-api.open-meteo.com` | 城市名 → 经纬度 |
| `api.open-meteo.com/v1/forecast` | 经纬度 → 天气预报 |

## 启动方式

```bash
cd demo02
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # 填入 API Key
python run.py
```

打开 http://127.0.0.1:8001

生成后注意观察：
- 结果页顶部是否出现「实时天气上下文」横幅
- 行程建议是否针对天气做了调整（如雨天安排室内景点、晴天安排户外）
- packing_tips 是否包含天气相关建议

## 与 demo01 的对比

| 方面 | demo01 | demo02 |
|---|---|---|
| LLM 的信息来源 | 仅用户输入 | 用户输入 + 实时天气 |
| 外部 API | 无 | Open-Meteo（免费） |
| Prompt 语言 | 英文 | 中文 |
| 容错设计 | 仅 LLM 失败 fallback | 天气失败优雅降级 + LLM 失败 fallback |
| 可解释性 | 无 | 前端展示天气上下文 |

## 下一步：demo03

demo02 的问题：**我们手动决定了「去哪里拿数据」**。
每次想加一个新数据源，就要改 planner.py 的代码。

demo03 将引入 **Tool Use（工具调用）**：
让 LLM 自己决定调用哪个工具、什么时候调用、用什么参数。
这是从「编排式 AI」迈向「自主式 Agent」的关键一步。
