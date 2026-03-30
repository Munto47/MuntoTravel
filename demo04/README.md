# demo04 · LangGraph

## 核心概念

demo03 的 Agent 循环是我们**手写**的 `for` 循环，用变量管理状态。  
demo04 引入 **LangGraph**，把同样的逻辑变成**有向状态图**：

| 概念   | 说明 |
|--------|------|
| **State** | 流经整张图的共享数据（TypedDict），每个节点读取并更新 State |
| **Node**  | 普通 Python 函数，接受 State，返回要修改的字段 |
| **Edge**  | 节点间的转移规则，可以是固定边（`add_edge`）或条件边（`add_conditional_edges`） |
| **Compile** | 图编译后成为可调用对象，支持 `ainvoke` / `stream` / 可视化 |

## 图结构

```
START
  ↓
[agent 节点]  ← LLM 决策中枢，绑定了工具定义
  ↓ 条件边（should_continue）
  ├─ 有 tool_calls → [tools 节点]  ← LangGraph 内置 ToolNode，自动执行
  │       └──────────────────────────→ 回到 [agent 节点]
  └─ 无 tool_calls → [generate 节点]  ← 专注于 JSON 结构化输出
                          ↓
                         END
```

## 与 demo03 对比

| 方面 | demo03 | demo04 |
|------|--------|--------|
| Agent 循环 | 手写 `for i in range(MAX_TOOL_TURNS)` | LangGraph 图引擎自动驱动 |
| 状态管理 | 手动维护 `messages` 列表 | `TravelAgentState`（TypedDict + `add_messages` reducer）|
| 工具执行 | 手写 `execute_tool()` 分发函数 | `ToolNode(TOOLS)` 自动执行 |
| 工具定义 | 手写 JSON Schema + `execute_tool` | `@tool` 装饰器自动生成 Schema |
| 可视化 | 无 | `/api/graph` 返回 Mermaid 图，前端实时渲染 |
| 可扩展性 | 添加功能需修改循环逻辑 | 添加节点 = 添加函数 + 注册边 |

## 新增文件

| 文件 | 说明 |
|------|------|
| `app/graph.py` | **核心**：LangGraph StateGraph 定义，包含状态、三个节点、条件边 |
| `app/tools.py` | 工具用 `@tool` 装饰器重写，LangChain 自动生成 JSON Schema |
| `app/main.py`  | 新增 `GET /api/graph` 端口，返回 Mermaid 图结构 |
| `app/static/index.html` | 新增 LangGraph 工作流可视化面板（Mermaid.js 渲染） |

复用文件（无变化）：

| 文件 | 来源 |
|------|------|
| `app/schemas.py` | 与 demo03 完全一致 |
| `app/weather_client.py` | 与 demo03 完全一致（QWeather + Open-Meteo 双源） |

## 启动

```bash
# 1. 复制环境变量
copy .env.example .env
# 填写 OPENAI_API_KEY 等

# 2. 安装依赖（首次）
pip install -r requirements.txt

# 3. 启动（端口 8003）
python run.py
```

访问 http://localhost:8003

## 关键设计决策

**为什么用两个 LLM 实例（temperature 不同）？**  
- `agent_node` 用 `temperature=0.3`：工具决策需要精确，不要随机性
- `generate_node` 用 `temperature=0.7`：行程文本需要自然流畅

**为什么 `add_messages` 而不是普通 list？**  
LangGraph 的 reducer 机制：每个节点返回部分更新（如 `{"messages": [new_msg]}`），图引擎用 `add_messages` reducer 把它追加到 State 中，而不是替换整个列表。这是 LangGraph 状态管理的核心设计。

**`ToolNode` 做了什么？**  
接收带 `tool_calls` 的 `AIMessage`，自动调用对应工具函数，把结果包装成 `ToolMessage`，追加到 messages。等价于 demo03 里手写的 `execute_tool()` + 消息追加逻辑。
