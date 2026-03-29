# MuntoTravel Demo03 · Tool Use —— 让 Agent 自主决策

## 这一版做了什么

demo03 引入了 **Tool Use（工具调用）**，这是从「编排式 AI」迈向「自主 Agent」的关键一步。

### 三个版本的核心区别

| 版本 | 谁决定调哪些工具？ | 谁决定调几次？ |
|---|---|---|
| demo02 | 我们的代码（planner.py 写死） | 我们的代码（固定一次） |
| demo03 | LLM 自主决定 | LLM 自主决定 |

### 两阶段设计

```
Phase 1：工具收集阶段
  ┌─ LLM 读取工具定义 ─────────────────────────────────────────┐
  │  LLM 决定：「我需要天气数据」→ 返回 tool_calls             │
  │  我们执行：get_weather("成都", 3) → 返回天气文本           │
  │  LLM 决定：「我需要景点数据」→ 返回 tool_calls             │
  │  我们执行：get_attractions("成都", "美食") → 返回景点列表   │
  │  LLM 决定：「信息足够了」→ finish_reason = "stop"          │
  └────────────────────────────────────────────────────────────┘
         ↓
Phase 2：结构化生成阶段
  ┌─ 带完整上下文调用 LLM（response_format: json_object）──────┐
  │  LLM 综合天气 + 景点 + 用户需求 → 输出完整行程 JSON        │
  └────────────────────────────────────────────────────────────┘
```

### 为什么分两阶段？

OpenAI API 的限制：`tools` 和 `response_format: json_object` 不能在同一次调用中同时使用。
Phase 1 允许工具调用但不约束输出格式；Phase 2 关闭工具调用、开启 JSON 模式，专注输出。

## 新增文件

```
demo03/
├── app/
│   ├── tools.py      ← NEW：工具定义（TOOL_DEFINITIONS）+ 执行器（execute_tool）
│   ├── agent.py      ← NEW：两阶段 Agent 循环（替代 demo02 的 planner.py）
│   ├── schemas.py    ← TripPlanResponse 新增 agent_log 字段
│   ├── weather_client.py  （与 demo02 相同）
│   └── main.py / static/
```

## 关键概念

### Tool Use 的消息格式

```python
# 1. 正常用户消息
{"role": "user", "content": "规划成都3天行程"}

# 2. LLM 决定调工具（role: assistant，带 tool_calls）
{"role": "assistant", "tool_calls": [
    {"id": "call_abc", "function": {"name": "get_weather", "arguments": '{"city":"成都","days":3}'}}
]}

# 3. 工具执行结果（role: tool）
{"role": "tool", "tool_call_id": "call_abc", "content": "【成都 出行天气预报】..."}

# 4. LLM 再次调工具 or 最终停止（finish_reason: "stop"）
```

### MAX_TOOL_TURNS 防死循环

```python
for turn in range(MAX_TOOL_TURNS):   # 最多 6 轮
    if finish_reason == "tool_calls":
        # 执行工具，继续循环
    else:
        break  # 正常退出
```

防止极端情况：LLM 一直调工具不停止，无限消耗 token。

### temperature 的两阶段差异

```python
# Phase 1：工具调用，temperature=0.3（低，保证参数精准）
# Phase 2：内容生成，temperature=0.7（正常，保证内容有创意）
```

## 启动方式

```bash
cd demo03
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python run.py
```

服务运行在 **8002 端口**。生成完成后，注意观察页面的「Agent 推理过程」区块，
可以看到 Agent 实际调用了哪些工具——这就是 LLM 的自主决策轨迹。

## 与 demo02 的对比

| 对比项 | demo02 | demo03 |
|---|---|---|
| 数据收集决策者 | 我们的代码 | LLM |
| 工具调用次数 | 固定（天气 1 次） | 动态（LLM 按需决定） |
| 景点数据 | 无 | LLM 主动查询 |
| 可解释性 | 天气横幅 | Agent 推理过程（工具调用记录） |
| LLM 调用次数 | 1 次 | 2~N 次（1轮工具 + 1次生成） |

## 下一步：demo04

demo03 的工具都是在同一个进程里手写的函数。
demo04 将引入 **LangGraph**，用图结构来定义更复杂的工作流，
并开始接入高德地图等真实 POI API。
