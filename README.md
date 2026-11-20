# 用户标签画像与行为分析平台

面向用户行为埋点采集、标签体系构建、人群圈选与行为特征分析的数据应用平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 事件埋点 | `event_track` | 埋点事件 | 事件编号、事件名称、事件分类 |
| 标签定义 | `tag_define` | 标签规则 | 标签编号、标签名称、标签层级 |
| 标签计算 | `tag_compute` | 计算任务 | 任务编号、关联标签、计算范围 |
| 人群圈选 | `crowd_select` | 圈选条件 | 条件编号、条件名称、标签组合 |
| 行为路径 | `behavior_path` | 路径模型 | 模型编号、起始事件、目标事件 |
| 留存计算 | `retention_calc` | 留存配置 | 配置编号、初始行为、回访行为 |
| 用户分群 | `user_segment` | 分群定义 | 分群编号、分群名称、基准行为 |
| 生命周期价值 | `lifetime_value` | LTV模型 | 模型编号、用户分群、价值指标 |
| 漏斗分析 | `funnel_analyze` | 漏斗定义 | 漏斗编号、漏斗名称、步骤序列 |
| 事件清洗 | `event_cleanse` | 清洗规则 | 规则编号、事件名称、校验字段 |
| 会话切割 | `session_split` | 切割策略 | 策略编号、策略名称、超时阈值 |
| 画像卡片 | `profile_card` | 卡片配置 | 卡片编号、卡片名称、展示标签 |
| 属性字典 | `attr_dict` | 属性定义 | 属性编号、属性名称、数据类型 |
| 交集分析 | `overlap_analysis` | 交集任务 | 任务编号、人群A、人群B |
| 标签下钻 | `tag_drill` | 下钻配置 | 配置编号、父标签、子标签集 |
| 事件聚合 | `event_aggregate` | 聚合配置 | 配置编号、事件名称、聚合维度 |
| 标签快照 | `tag_snapshot` | 快照记录 | 快照编号、快照日期、用户基数 |
| 行为热力 | `behavior_heatmap` | 热力配置 | 配置编号、行为事件、时间范围 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 后端路由统一使用连字符前缀（如 `/api/attr-dict`），前端历史上的下划线路径
  （如 `/api/attr_dict`）由 `app/main.py` 的兼容中间件改写，无需在前端逐个改名。

## 属性字典版本回滚与标签复核

属性字段定义按生效时间保留为半开区间版本（`[effective_from, effective_to)`），
同一时点每个属性只命中一个生效版本，保证时点校验读不到相互矛盾的字段类型。
相关规则在 `app/services/attr_version.py`，涉及三张表：

- `attr_version`：属性字段定义的历史版本（含 `initial/update/rollback` 变更类型）。
- `attr_withdraw`：撤回记录，把**真正撤回的区间**（`withdrawn_intervals`）与
  **需复核的候选标签**（`review_candidates`）分开留痕；旧类型仍支持的条件放在
  `unaffected_tags`，冲突条件带字段类型冲突说明。
- `tag_condition_binding`：已确认标签条件，锁定确认时点与原字段来源版本；
  撤回只附加冲突证据，不改写时点和来源。

主要接口（均同时支持连字符与下划线前缀）：

- `GET /api/attr-dict/versions/{attr_id}`：版本时间线
- `POST /api/attr-dict/versions/{attr_id}/publish`：按生效时间写入字段版本
- `GET /api/attr-dict/versions/{attr_id}/as-of?point_in_time=...`：时点版本查询
- `GET /api/attr-dict/versions/{attr_id}/periods?period_grain=day&start=...&end=...`：按周期回查可用版本
- `POST /api/attr-dict/withdraws/{attr_id}`：撤回当前定义并回滚到目标版本
- `GET /api/attr-dict/withdraws[?attr_id=...]`：撤回记录回查
- `POST /api/attr-dict/withdraws/{withdraw_id}/reopen-validation`：重新打开标签校验
- `POST /api/attr-dict/bindings/{tag_id}/confirm`：确认标签条件（锁定时点与来源）
- `GET /api/tag-define/{tag_id}/validate-at?point_in_time=...`：标签条件时点校验

领域：数据工程
子方向：数据分析与数据应用
