# MuntoTravel Demo01

`demo01` 是 MuntoTravel 的第一版最小闭环 Demo。

这一版的目标很明确：

- 用户输入旅行需求
- 后端生成结构化行程 JSON
- 前端把结果渲染出来

它还没有引入复杂工作流和多智能体编排，重点是先把主链路跑通，为后续迭代留出清晰骨架。

## 目录结构

```text
demo01/
├── app/
│   ├── main.py
│   ├── planner.py
│   ├── llm_client.py
│   ├── schemas.py
│   └── static/
│       └── index.html
├── .env.example
├── requirements.txt
└── run.py
```

## 启动方式

1. 创建虚拟环境并安装依赖

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2. 可选：配置模型环境变量

```bash
copy .env.example .env
```

如果不配置 `OPENAI_API_KEY`，系统会自动使用内置 fallback 规划器，方便先演示主链路。

3. 启动服务

```bash
python run.py
```

4. 打开浏览器

```text
http://127.0.0.1:8000
```

## 第一版包含什么

- 一个 `POST /api/trip/plan` 接口
- 一个简单但可用的静态前端页面
- 一个结构化的 `TripRequest` / `TripPlan`
- 一个真实模型调用入口
- 一个无 Key 也可演示的 fallback 方案

## 建议的下一步迭代

- 增加日期、住宿、交通方式等字段
- 接入真实 POI / 天气数据
- 将 fallback 和 LLM 输出都约束到更严格的 schema
- 再逐步引入 HelloAgents 或多 Agent 编排
