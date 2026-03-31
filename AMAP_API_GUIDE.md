# 高德地图 Web 服务 API 快速上手指南

> 本文档由实测经验整理，面向需要快速上手高德地图 REST API 的开发者/AI 助手。  
> 测试环境：Python 3.12 + requests，测试城市：江西省新余市，测试时间：2026-03-30

---

## 目录

1. [官方资源](#1-官方资源)
2. [核心概念速查](#2-核心概念速查)
3. [POI 搜索（v3）](#3-poi-搜索-v3)
4. [POI 搜索 2.0（v5）](#4-poi-搜索-20-v5)
5. [路径规划（v3/v4）](#5-路径规划-v3v4)
6. [路径规划 2.0（v5）](#6-路径规划-20-v5)
7. [IP 定位](#7-ip-定位)
8. [坐标转换](#8-坐标转换)
9. [其他常用接口速查](#9-其他常用接口速查)
10. [通用注意事项与踩坑记录](#10-通用注意事项与踩坑记录)
11. [错误码速查表](#11-错误码速查表)

---

## 1. 官方资源

| 资源 | 地址 |
|------|------|
| Web 服务 API 文档首页 | https://lbs.amap.com/api/webservice/summary |
| 控制台（Key 管理/服务开通） | https://console.amap.com/dev/key/app |
| 坐标系说明 | https://lbs.amap.com/faq/web/javascript-api/use/43361 |
| 错误码大全 | https://lbs.amap.com/api/webservice/guide/tools/info |
| POI 分类编码表 | https://lbs.amap.com/api/webservice/download |
| 行政区划 adcode 表 | https://lbs.amap.com/api/webservice/download |

**Key 类型说明**：在控制台创建 Key 时须选择 **"Web 服务"** 类型，JS API Key 不能用于 REST 调用。

---

## 2. 核心概念速查

### 2.1 坐标系

| 坐标系 | 名称 | 说明 |
|--------|------|------|
| WGS84 | 国际标准 GPS | 手机 GPS 原始坐标、Google Earth |
| GCJ02 | 火星坐标 | **高德所有接口输入/输出均使用此坐标系** |
| BD09 | 百度坐标 | 仅百度地图使用 |

> ⚠️ 直接把 GPS 坐标传给高德接口会有约 100~500m 的偏移，必须先做坐标转换。

### 2.2 请求通用结构

```
GET https://restapi.amap.com/{版本}/{服务}/{方法}
    ?key=YOUR_KEY
    &{业务参数}
    &output=JSON
```

**通用响应字段**：

```json
{
  "status": "1",      // "1" = 成功，"0" = 失败
  "info": "OK",       // 错误描述
  "infocode": "10000" // 10000 = 成功
}
```

> ⚠️ **v4 骑行接口例外**：响应字段是 `errcode`（0=成功）+ `errmsg`，无 `status` 字段。

### 2.3 adcode 说明

adcode 是高德行政区代码，天气、IP 定位等接口常用：

- 省级：6 位，如江西省 `360000`
- 市级：6 位，如新余市 `360500`
- 县级：6 位，如渝水区 `360502`

---

## 3. POI 搜索（v3）

### 3.1 关键字搜索

```
GET https://restapi.amap.com/v3/place/text
```

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `key` | 必填 | API Key | — |
| `keywords` | 必填 | 搜索关键词 | `仙女湖` |
| `types` | 可选 | POI 类型代码（分类表见官方文档） | `110000`（餐饮） |
| `city` | 可选 | 城市名/adcode/citycode，**强烈建议填写** | `新余` |
| `citylimit` | 可选 | `true` = 强制限制在 city 范围内 | `true` |
| `offset` | 可选 | 每页条数，最大 25 | `10` |
| `page` | 可选 | 页码，从 1 开始 | `1` |
| `extensions` | 可选 | `base`=基础信息，`all`=包含营业时间/评分等 | `all` |
| `output` | 可选 | 返回格式 | `JSON` |

**响应关键字段**：

```json
{
  "status": "1",
  "count": "99",         // 总结果数（字符串）
  "pois": [
    {
      "id": "B0FFFLX672",
      "name": "仙女湖风景名胜区",
      "type": "风景名胜;风景名胜;国家级风景名胜区",
      "typecode": "110202",
      "address": "天仙路",
      "location": "114.805290,27.823600",   // GCJ02
      "tel": "4009158099",
      "distance": "",        // 周边搜索时有值（单位：米）
      "pname": "江西省",
      "cityname": "新余市",
      "adname": "分宜县"
    }
  ]
}
```

**实测经验**：
- `count` 是**字符串**，不是整数，做判断时注意转换
- 不设 `citylimit=true` 时，搜索结果可能混入其他城市 POI
- `extensions=all` 返回数据更丰富但稍慢，一般测试用 `base` 足够
- 新余市"仙女湖"关键词可搜到 99 条结果，数据覆盖完整

### 3.2 周边搜索

```
GET https://restapi.amap.com/v3/place/around
```

新增参数：

| 参数 | 说明 | 示例 |
|------|------|------|
| `location` | 中心坐标（GCJ02） | `114.9171,27.8174` |
| `radius` | 搜索半径（米），最大 50000 | `1000` |
| `sortrule` | 排序规则：`distance`（距离）/ `weight`（权重） | `distance` |

响应中每条 POI 会有 `distance` 字段（单位：米）。

---

## 4. POI 搜索 2.0（v5）

```
GET https://restapi.amap.com/v5/place/text
GET https://restapi.amap.com/v5/place/around
```

v5 与 v3 的主要差异：

| 对比项 | v3 | v5（2.0） |
|--------|----|-----------|
| API 路径 | `/v3/place/text` | `/v5/place/text` |
| 分页参数 | `offset` + `page` | `page_size` + `page_num` |
| 坐标字段 | `location` 字符串 | `location.lng` + `location.lat` 对象 |
| 距离字段 | `distance`（字符串，单位米） | `distance`（数字） |
| 类型筛选 | `types` | `types`（同，但编码体系有扩展） |
| 营业时间 | `extensions=all` 才有 | 默认携带更多信息 |
| 评分字段 | 不稳定 | `biz_ext.rating` |

**v5 响应示例**：

```json
{
  "status": "1",
  "pois": [
    {
      "id": "B0FFFLX672",
      "name": "仙女湖风景名胜区",
      "location": {
        "lng": "114.805290",
        "lat": "27.823600"
      },
      "distance": 5200,
      "business_area": "仙女湖",
      "biz_ext": {
        "rating": "4.8",
        "open_time": "08:00-18:00"
      }
    }
  ]
}
```

> 当前本地测试代码使用的是 v3，若需要 v5 只需改 URL 和调整响应解析字段。

---

## 5. 路径规划（v3/v4）

### 5.1 接口地址汇总

| 出行方式 | URL | 版本 |
|----------|-----|------|
| 驾车 | `https://restapi.amap.com/v3/direction/driving` | v3 |
| 步行 | `https://restapi.amap.com/v3/direction/walking` | v3 |
| 公交/地铁 | `https://restapi.amap.com/v3/direction/transit/integrated` | v3 |
| 骑行 | `https://restapi.amap.com/v4/direction/bicycling` | **v4（格式不同！）** |

### 5.2 通用参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `origin` | 起点坐标（GCJ02），格式 `经度,纬度` | `114.9171,27.8174` |
| `destination` | 终点坐标（GCJ02） | `114.9519,27.7998` |
| `output` | `JSON` | `JSON` |

### 5.3 驾车（v3）额外参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `strategy` | 路线策略：0=速度优先，1=费用优先，2=距离优先，4=躲避拥堵 | `0` |
| `extensions` | `base`/`all`（all 包含完整步骤和路段信息） | `all` |
| `waypoints` | 途经点，多个用 `;` 分隔 | `114.92,27.81` |

**驾车响应关键字段**：

```json
{
  "route": {
    "origin": "114.9171,27.8174",
    "destination": "114.9519,27.7998",
    "paths": [
      {
        "distance": "4892",     // 米，字符串
        "duration": "545",      // 秒，字符串
        "tolls": "0",           // 过路费，元
        "steps": [
          {
            "instruction": "向东行驶123米向左前方行驶进入主路",
            "road": "仙来中大道",
            "distance": "123",
            "duration": "15"
          }
        ]
      }
    ]
  }
}
```

### 5.4 公交/地铁（v3）额外参数

| 参数 | 说明 |
|------|------|
| `city` | **必填**，起点所在城市（市名或 adcode） |
| `cityd` | 终点城市（跨城时填写） |
| `nightflag` | `0`=不考虑夜班车，`1`=考虑夜班车 |

**公交响应关键字段**：

```json
{
  "route": {
    "transits": [
      {
        "duration": "2760",
        "cost": "1.0",
        "walking_distance": "2011",
        "segments": [
          {
            "bus": {
              "buslines": [
                {
                  "name": "昌坊-新余(昌坊度假村--新余大桥下)",
                  "departure_stop": { "name": "竹山路口" },
                  "arrival_stop": { "name": "火车站" }
                }
              ]
            }
          }
        ]
      }
    ]
  }
}
```

### 5.5 骑行（v4）⚠️ 格式特殊

v4 接口**没有** `status`/`info` 字段，改用：

```json
{
  "errcode": 0,         // 0 = 成功（整数）
  "errmsg": "OK",
  "data": {
    "origin": "...",
    "destination": "...",
    "paths": [
      {
        "distance": 5012,   // 注意：v4 是整数，v3 是字符串
        "duration": 1140,
        "steps": []
      }
    ]
  }
}
```

**解析 v4 骑行响应的正确方式**：

```python
resp = requests.get(url, params=params).json()
errcode = resp.get("errcode", -1)          # 不要用 resp.get("status")
if errcode == 0:
    paths = resp.get("data", {}).get("paths", [])
```

---

## 6. 路径规划 2.0（v5）

```
GET https://restapi.amap.com/v5/direction/driving
GET https://restapi.amap.com/v5/direction/walking
GET https://restapi.amap.com/v5/direction/transit/integrated
GET https://restapi.amap.com/v5/direction/bicycling
```

v5 与 v3 的主要差异：

| 对比项 | v3/v4 | v5（2.0） |
|--------|----|-----------|
| 坐标传入 | `origin=lng,lat` 字符串 | `origin=lng,lat` 字符串（兼容） |
| 距离/时间字段 | **字符串** `"4892"` | **整数** `4892` |
| 骑行 | v4 路径，errcode | v5 路径，status（与 v3 统一） |
| 多方案 | `paths` 数组 | `paths` 数组（更多方案） |
| 步行 | 无室内规划 | 支持室内步行规划 |
| 驾车附加信息 | 基础 | 支持充电桩、限行等更多参数 |

---

## 7. IP 定位

```
GET https://restapi.amap.com/v3/ip
```

| 参数 | 说明 |
|------|------|
| `ip` | 要查询的 IPv4 地址（不填则查当前请求 IP） |
| `output` | `JSON` |

**响应**：

```json
{
  "status": "1",
  "province": "江西省",
  "city": "南昌市",
  "adcode": "360100",
  "rectangle": "115.6786,28.4818;116.1597,28.8672"
}
```

### 踩坑记录

1. **部分 IP 返回空数组 `[]`**：高德 IP 库覆盖不全，某些网段（如 `222.231.0.1`）无归属地数据，会返回 `"province": []`，这是正常现象，不是接口错误（`status` 仍为 `"1"`）。

2. **验证有效江西 IP**：经测试 `59.53.0.1`（江西南昌电信）可正常返回省市信息。

3. **不传 ip 参数时**：接口会尝试定位请求发起方的 IP，在服务端调用时返回服务器 IP 的归属地，不是用户 IP。

4. **返回值类型陷阱**：有数据时 `province` 是字符串；无数据时是空数组 `[]`，不是 `null` 或空字符串，需用 `isinstance(val, list)` 或 `if not val` 来判断。

---

## 8. 坐标转换

```
GET https://restapi.amap.com/v3/assistant/coordinate/convert
```

| 参数 | 说明 | 示例 |
|------|------|------|
| `locations` | 待转换坐标，多个用 `|` 分隔，最多 40 个 | `114.9107,27.8141` |
| `coordsys` | 来源坐标系 | `gps` / `mapbar` / `baidu` |
| `output` | `JSON` | `JSON` |

**响应**：

```json
{
  "status": "1",
  "locations": "114.915543348525,27.810641004775"  // 转换后的 GCJ02 坐标
}
```

### 坐标系对照与转换规则

| `coordsys` 值 | 来源坐标系 | 典型场景 |
|--------------|-----------|---------|
| `gps` | WGS84 | 手机 GPS、国际地图、GPX 轨迹文件 |
| `mapbar` | 图吧坐标 | 图吧地图数据 |
| `baidu` | BD09 | 百度地图 API 返回的坐标 |

**实测数据（新余市政府附近）**：

```
GPS 原始：   114.9107,27.8141
GCJ02 转换：114.9155,27.8106
偏移量：    Δlng=+0.0048, Δlat=-0.0035（约 440m）
```

> ⚠️ 高德返回的 `locations` 是**字符串**，多坐标时用 `|` 分隔，需自行 split 解析。

---

## 9. 其他常用接口速查

### 地理编码（地址 → 坐标）

```
GET https://restapi.amap.com/v3/geocode/geo
参数：address=江西省新余市渝水区人民路 &city=新余 &output=JSON
```

响应中 `geocodes[0].location` 为 GCJ02 坐标，`level` 表示精度（国家/省/市/区/街道/道路/门牌号等）。

### 逆地理编码（坐标 → 地址）

```
GET https://restapi.amap.com/v3/geocode/regeo
参数：location=114.9171,27.8174 &radius=500 &extensions=all &output=JSON
```

`extensions=all` 时返回周边 POI、道路、道路交叉口等丰富数据。

### 天气查询

```
GET https://restapi.amap.com/v3/weather/weatherInfo
参数：city=360500（adcode）&extensions=base（实时）/all（预报）
```

### 行政区查询

```
GET https://restapi.amap.com/v3/config/district
参数：keywords=新余市 &subdistrict=2（下钻2级，到乡镇）&extensions=base
```

### 交通态势（需特殊权限）

```
GET https://restapi.amap.com/v3/traffic/status/rectangle
参数：rectangle=左下角lng,lat;右上角lng,lat &level=1~6 &extensions=all
```

> ⚠️ 该接口需要在控制台单独申请**交通态势**服务权限，否则返回 `infocode=20003`（UNKNOWN_ERROR）。

---

## 10. 通用注意事项与踩坑记录

### 10.1 Windows 中文编码问题

Python 在 Windows 终端输出中文时，默认编码为 GBK，会导致 `UnicodeEncodeError`。

**解决方案**（在脚本最顶部加）：

```python
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
```

### 10.2 字段类型陷阱

高德 v3 接口中，**数字型数据大量使用字符串返回**，直接做算术运算会出错：

```python
# 错误写法
duration_min = path["duration"] / 60    # TypeError: str / int

# 正确写法
duration_min = int(path["duration"]) // 60
distance_km  = int(path["distance"]) / 1000
```

v5（2.0）接口中，距离和时间已改为整数，注意版本区别。

### 10.3 请求频率限制

- 免费配额：QPS（每秒请求数）默认 **50次/秒**（个人开发者）
- 批量操作时建议 `time.sleep(0.3)` 间隔，避免被限速（返回 `infocode=10044`）
- 坐标转换批量传入时，最多 40 个坐标，用 `|` 分隔

### 10.4 接口版本混用

| 接口 | 当前稳定版 | 注意事项 |
|------|-----------|---------|
| POI 搜索 | v3 / v5 | v5 响应结构不同，字段名有变化 |
| 路径规划（驾步公） | v3 / v5 | v5 数字字段不再是字符串 |
| **骑行路线** | **v4** | **无 status 字段，用 errcode 判断** |
| 地理/逆地理编码 | v3 | 无 v5 |
| 天气 | v3 | 无 v5 |

### 10.5 city 参数的多种写法

高德 `city` 参数支持多种格式，效果相同：

```
city=新余         # 城市名（中文）
city=360500       # adcode（推荐，唯一确定）
city=0790         # citycode（部分接口支持）
```

### 10.6 矩形区域大小限制

交通态势、地图瓦片等接口的矩形区域有大小限制：
- 实测 `114.85,27.75;115.00,27.88`（约15×14km）会返回 `INVALID_PARAMS`
- 建议控制在 **5km × 5km** 以内：`114.895,27.793;114.942,27.840`

---

## 11. 错误码速查表

| infocode | info | 原因及处理方式 |
|----------|------|--------------|
| `10000` | OK | 成功 |
| `10001` | INVALID_USER_KEY | Key 不存在或被禁用，检查控制台 |
| `10002` | SERVICE_NOT_AVAILABLE | 服务未开通，去控制台申请对应服务 |
| `10003` | DAILY_QUERY_OVER_LIMIT | 日配额用完，升级套餐或次日重试 |
| `10004` | ACCESS_TOO_FREQUENT | QPS 超限，降低请求频率 |
| `10008` | INVALID_USER_IP | 请求 IP 不在白名单，去控制台配置 |
| `10009` | INVALID_USER_SCODE | 数字签名错误（启用签名校验时） |
| `10010` | INVALID_USER_DOMAIN | 请求来源域名不在白名单 |
| `10021` | INVALID_PARAMS | 参数格式错误，检查坐标格式/必填参数 |
| `10044` | OVER_DIRECTION_RANGE | 起终点超出服务范围（通常是坐标不在中国） |
| `20003` | UNKNOWN_ERROR | 无服务权限（如交通态势），去控制台申请 |
| `20800` | OUT_OF_SERVICE` | 请求坐标不在服务区（海外坐标） |

---

## 12. 本次实测新余市数据质量小结

| 接口 | 结果 | 数据质量评估 |
|------|------|------------|
| 地理编码 | 3条结果，精确到道路级别 | ✅ 良好 |
| 逆地理编码 | 精确到门牌号，周边30个POI | ✅ 良好 |
| 天气实时 | 15°C，雾，湿度98%，数据及时 | ✅ 良好 |
| 天气预报 | 4天预报完整 | ✅ 良好 |
| IP 定位 | 部分IP无归属地数据（返回`[]`） | ⚠️ 需选有效IP |
| POI 搜索 | "仙女湖"搜到99条，覆盖完整 | ✅ 良好 |
| 周边搜索 | 1km内12家餐厅，距离信息完整 | ✅ 良好 |
| 驾车规划 | 新余市政府→火车站 4.9km/9分钟 | ✅ 良好 |
| 步行规划 | 5.0km/66分钟，7步骤 | ✅ 良好 |
| 公交规划 | 1条线路，票价¥1.0，数据真实 | ✅ 良好 |
| 骑行规划 | 5.0km/19分钟，需用v4解析方式 | ✅ 良好（注意版本） |
| 交通态势 | 权限未开通（infocode=20003） | ❌ 需申请权限 |
| 行政区查询 | 2个区县（分宜县/渝水区），含乡镇 | ✅ 完整 |
| 坐标转换 | GPS→GCJ02偏移约440m，符合预期 | ✅ 正常 |
| 输入提示 | "仙女"返回10条新余本地结果 | ✅ 良好 |
| 静态地图 | 返回PNG图片，内容类型正确 | ✅ 正常 |
