# demo11 — 地图可视化

> 基于 demo10，新增：行程坐标导出 + 高德 JSAPI 2.0 前端地图渲染

---

## 本 demo 引入的新知识点

| 知识点 | 位置 |
|--------|------|
| 从缓存提取本次行程坐标（`get_trip_coords()`） | `app/route_client.py` |
| API 响应新增结构化坐标字段（`coord_map`） | `app/schemas.py` → `PlanResponse` |
| 服务端通过 `/api/config` 安全地向前端暴露 JS Key | `app/main.py` |
| 前端动态加载第三方 JS SDK（高德 JSAPI 2.0） | `app/static/index.html` |
| `AMap.Polyline` 逐日绘制路线 | `app/static/index.html` |
| `AMap.Marker` + `setInterval` 动画播放 | `app/static/index.html` |

---

## 与 demo10 的架构差异

```
demo10: route_node → {trip_plan}
demo11: route_node → {trip_plan, coord_map}
                          ↓
                 PlanResponse.coord_map
                          ↓
               前端 initTripMap(coordMap, tripData)
                          ↓
           AMap.Map + Polyline(每天一色) + Marker(逐站)
                          ↓
              [动画播放] setInterval 逐站 reveal + 平移
```

### 关键设计决策

1. **两个 AMAP Key 分开**：`AMAP_API_KEY`（Web 服务，服务端调用路线/POI）和 `AMAP_JS_KEY`（Web JS，前端渲染地图）是不同类型的 Key，须在控制台分别申请。
2. **`/api/config` 端点**：JS Key 设计上允许前端使用（会暴露在浏览器），通过专用端点暴露而不是硬编码在 HTML 中，便于配置管理。不暴露 `OPENAI_API_KEY` / `AMAP_API_KEY`。
3. **坐标从缓存提取**：`route_node` 完成路线计算后，`_coord_cache` 已包含所有本次地点坐标，直接提取即可，无需额外 API 调用。
4. **无坐标时降级**：未配置 `AMAP_API_KEY` 时 `coord_map` 为空字典，前端显示提示而非报错，地图以外的功能不受影响。

---

## 快速启动

```bash
# 1. 安装依赖（复用 demo10 的 venv 也可以）
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 必填：OPENAI_API_KEY
# 地图功能：AMAP_API_KEY（路线坐标）+ AMAP_JS_KEY（前端渲染）
# 说明：两者在高德控制台申请不同类型 Key

# 3. 启动
python run.py
# → http://localhost:8011
```

---

## 申请高德 JSAPI Key

1. 登录 [高德开放平台控制台](https://console.amap.com/dev/key/app)
2. 创建新应用 → 添加 Key → 服务平台选 **「Web端(JS API)」**
3. 将生成的 Key 填入 `.env` 的 `AMAP_JS_KEY=`
4. 重启服务即可看到地图

> `AMAP_API_KEY` 是「Web服务」类型（服务端调用），`AMAP_JS_KEY` 是「Web端(JS API)」类型（前端渲染），两者不可互换。

---

## 地图功能说明

| 功能 | 说明 |
|------|------|
| 逐日路线折线 | 每天用不同颜色（橙/蓝/绿/紫…）绘制地点连线 |
| 地点 Marker | 圆形标记，第1个带「D1」等天数标记 |
| 图例 | 右上角显示每天对应颜色 |
| 动画播放 | 点击「▶ 动画播放」后每秒显示一个地点，地图跟随平移 |
| 无 Key 降级 | `AMAP_JS_KEY` 未配置时显示申请指引，不影响行程规划 |
| 无坐标降级 | `AMAP_API_KEY` 未配置时坐标为空，地图面板提示 |

---

## API 变更

### 新增端点

`GET /api/config`
```json
{ "amap_js_key": "你的JS Key（或空字符串）" }
```

### PlanResponse 新增字段

```json
{
  "coord_map": {
    "西湖断桥": "120.151441,30.259823",
    "楼外楼": "120.148293,30.258611",
    ...
  }
}
```
