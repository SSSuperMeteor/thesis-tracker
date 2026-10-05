# 本机网页工作台（webapp）

本文记录本机网页第一版的架构、页面、接口、任务模型与安全模型。**每节标注
已定 / 待定**；待定项写明为什么还没定。

术语（英文专业术语第一次出现时给中文解释）：

- **access token（访问令牌）**：每次启动随机生成的字符串，等同本机网页的密码。
- **envelope（信封）**：现有三个只读工具共用的统一返回外壳，定义见
  [tool-contract.md](tool-contract.md)。
- **任务（job）**：一次后台工作的持久化记录，含类型、状态、参数与进度事件。
- **片段（segment）**：卡正文的呈现单位，`{"type":"text"}` 或 `{"type":"fact"}`。
- **DNS 重绑定（DNS rebinding）**：恶意页面把某个域名解析到 127.0.0.1，
  借此绕过同源策略访问本机服务。

---

## 1. 形态与技术选型（已定）

```bash
uv run webapp            # 打印本机网址；Ctrl-C 停止
```

- 一个命令启动，默认绑定 `127.0.0.1:8765`，`--port` 可改（`--port 0` 取空闲端口），
  `--host` 只接受回环地址，其它值直接拒绝并返回退出码 2。
- 后端 Python，只用标准库 `http.server.ThreadingHTTPServer`；前端是由同一个后端
  提供的静态文件（`static/index.html`、`app.css`、`app.js`、`favicon.svg`），
  **没有 Node 构建链**，没有打包器，没有模板引擎。
- **新增运行时依赖：无。** `pyproject.toml` 只增加一个 console script
  `webapp = "thesis_tracker.webapp.cli:main"`。
- 理由：本机单人使用、离线也要能开；标准库 HTTP 服务器足够，静态三件套足够，
  多一个依赖就多一份安装与安全面。分页、路由、渲染都在 200 行以内的自有代码里。

数据来源全部是现有库，且**一律只读**（SQLite `mode=ro` URI）：`data/cache/financial_facts.db`、
`data/cache/prices.db`、`data/decisions/cards.db`、`data/corpus.db`（语料库本轮只用于
确认采集范围，页面不展示检索）。公司列表从库里 `SELECT DISTINCT ticker` 读出，
代码里没有任何硬编码公司或 ticker。

后端是薄封装：价格走 `prices.get_price_history`，指标走 `indicator_tool.get_indicators`，
财务指标走 `financial.tool.get_fundamental_metrics`，卡的显示走
`decision.evidence.display_text` / `display_label` / `fact_category` 与
`decision.core.auto_computed`。网页不重算任何业务逻辑。

---

## 2. 页面（已定）

| 页面 | 路由 | 内容 |
|---|---|---|
| 总览 | `#/overview` | 公司 × 日历季度的覆盖表格；公司列与表头吸顶，右侧三列汇总固定在右边缘；横向滚动只发生在表格容器里 |
| 公司页 | `#/company/<TICKER>` | 财报、价格、可算的财务指标、建议卡四段（不用标签页）；右上角"生成建议卡" |
| 建议卡列表 | `#/cards?ticker=&horizon=` | 周期、动作、倾向、创建时间、一列"规则版本"短标记、"旧规则"标注；可按公司与周期筛选 |
| 建议卡详情 | `#/cards/<CARD_ID>` | 左阅读栏与右证据面板作为一组居中；证据按来源分组，吸顶并自身滚动 |
| 任务 | `#/jobs`、`#/jobs/<JOB_ID>` | 任务列表与逐步进度、错误、结果 |

设计约束以 `.agents/skills/thesis-tracker-ui/SKILL.md` 为准：设计令牌、字号阶梯、
状态"文字加形状"、动效只有三处、无障碍底线。补充说明本轮的具体取舍：

- 覆盖表格的列按**财期截止日所在的日历季度**分组（`2026-04-25` → `2026Q2`），
  界面上不出现"第几财季"；原始 `fiscal_period` 只在公司页财报表里作为对照字段出现。
  同一格里出现多个申报时两个字形都画出来，修订申报在角上加一个小点，有财报的格子加
  浅色底（`--surface-2`），缺失格是虚线空框；表格上方一行图例说明这四个记号。
  打开页面时表格默认横向滚动到最新季度一端；**前导**空列（没有任何公司有财报的季度）
  不显示，两个有财报的季度之间的空档保留。
- 唯一强调手法是卡正文里的**事实标记**（1px 虚线下划线、强调色）：悬停显示 fact_id，
  点击把右栏对应行滚入视野并高亮 1.2 秒；`prefers-reduced-motion` 下改为静态底色。
- 价格过期提示放在页面顶部，附"复制命令"按钮；**界面不执行任何命令**。总览顶栏写
  "价格最新至 YYYY-MM-DD（落后 N 天）"，日期取**各公司最新价格日里最早的那个**，
  所以一家落后的公司不会被更快的公司盖住；过期阈值只在悬停提示里说明，数值取自
  `PRICE_STALENESS_DAYS`。
- 所有时间戳都由后端格式化为本机时区的 `YYYY-MM-DD HH:MM`（见第 3.3 节），
  前端不做任何时间计算；任务参数显示为"公司 NVDA｜周期 短期｜分析截至 2026-10-04"，
  存档编号只显示前 8 位并提供"复制完整编号"。
- 生成建议卡前必须经过确认对话框，对话框里的平均用量来自 `/api/usage`，
  前端不写死任何金额或 token 数。

---

## 3. HTTP 接口（已定）

全部返回 JSON（静态资源除外）。所有接口都要访问令牌。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 页面外壳；带 `?token=` 时回一个 302 并设置 Cookie |
| GET | `/api/overview` | 公司覆盖、价格起止与过期标记、卡数量 |
| GET | `/api/companies` | 有财报的公司代码列表 |
| GET | `/api/companies/<TICKER>?as_of=` | 公司页全部数据 |
| GET | `/api/cards?ticker=&horizon=` | 建议卡列表 |
| GET | `/api/cards/<CARD_ID>` | 卡详情（结构化） |
| GET | `/api/usage` | 最近几次分析的平均 token 用量，不含金额 |
| GET | `/api/jobs`、`/api/jobs/<JOB_ID>` | 任务列表与详情 |
| POST | `/api/analyze` | 创建分析任务；请求体必须含 `"confirm": true` |

**所有数字和标签都是字符串**，由后端沿用现有 display 规则算好，前端只渲染
（`display_text`：单位中文化、百分比/倍数/百分点分类、2 位小数等，见
[decision-mode.md](decision-mode.md) 第 13 节）。

### 3.1 卡详情为什么是结构化的（已定）

`GET /api/cards/<CARD_ID>` 不返回一整段文本，而是返回判断区字段、事实表、
价位带、自动计算、数据缺口、免责声明原文、尝试记录与版本信息，正文以片段数组给出：

```json
"reasons": [[{"type": "text", "value": "收盘价 "},
             {"type": "fact", "fact_id": "tiingo|AAPL|2026-10-02|daily",
              "display": "333.69 美元/股"},
             {"type": "text", "value": " 仍站在短均线之上。"}]]
```

片段由 `core.PLACEHOLDER` 的替换结果与 `evidence.display_text` 生成，**没有改动
`decision/core.py` 的渲染逻辑**。测试
（`tests/test_webapp_card_detail.py`）把片段按顺序拼接，与 `core.render_card` 的
对应部分逐字节比较：当前校验器版本能回放的每张存档卡都逐字节一致；用当前版本
校验会被拒的旧卡（v1/v2）按卡自己的版本回放，并在测试里计数，不静默跳过。

显示存档卡绝不重新调用任何模型：`tests/test_webapp_card_detail.py` 里把
`DeepSeekClient.__init__` 换成抛异常的函数，卡详情仍然正常返回。

### 3.2 价位带（已定）

`price_band()` 只对"买入/分批/持有"返回价位带：把止损、买点下沿、收盘价、买点上沿、
目标按价格排序，给出 0-100 的 `position`、0-1 的 `fraction`，以及标签要占的
`label_row` / `label_offset_px`。前端只把它们排到轴上，**不计算价格、不判断顺序**。
"观望/减仓/回避"没有价位，接口返回 `null`，页面写"此动作没有价位"。

### 3.3 显示字符串的两条共用规则（已定）

`webapp/display.py` 是这两个规则的唯一来源，页面只渲染它的输出：

- **时间**：`format_timestamp()` 把库里存的 UTC ISO 时间戳转成运行本软件的这台机器
  的本地时区，输出 `YYYY-MM-DD HH:MM`（无秒、无微秒、无时区后缀）。时区可注入，
  测试用固定时区断言，不依赖运行环境。无法解析的值原样显示，不做隐藏。
- **参数与版本**：`parameter_labels()` 把任务参数变成中文标签（公司 / 周期 / 分析截至），
  周期用 `decision.evidence.HORIZON_LABELS` 这一处既有映射；`version_mark()` 把两个长
  版本字符串确定性解析成"提示词 v5 / 校验 3"，解析不了就显示原字符串；
  `is_current_rules()` 与代码里的 `VALIDATOR_VERSION` / `PROMPT_VERSION` 常量比较，
  不一致的卡在列表里标"旧规则"（文字加形状），完整版本字符串只在详情页底部。

### 3.4 证据面板与价位带（已定）

- 阅读栏与证据面板是**一组**：整组居中，阅读栏 `640px`、证据面板 `300–440px`、
  栏间距为阅读栏宽度的 2%（1440px 窗口下实测 23.8px，上限 64px）。
  窄屏（<1099px）证据面板移到正文下方并取消吸顶。
- 证据面板按事实自带的来源分组（行情与指标 / 财报指标 / 派生），每个事实两行：
  第一行名称与值（值右对齐、`tabular-nums`），第二行来源标识与日期（日期不换行）。
  `fact_id` 不出现在可见文字里，只放在行的 `title` 与 `data-fact-id`；点击事实标记
  滚入视野并高亮 1.2 秒，同时把该行设为焦点，键盘可达。面板自身可纵向滚动
  （`max-height: calc(100vh - 48px)`），吸顶保留。
- 价位带：横轴铺满阅读栏宽度并在两端内缩，收盘价用菱形、止损与目标用竖线、
  买点区间用一条横杠连接两个端点，**形状与文字足以区分，不依赖颜色**；旁边一行图例。
  标签在轴上下交错摆放；后端按标签实际宽度决定每个标签的侧别与行号
  （`label_side` / `label_row` / `label_offset_px`），并把轴宽（`column_px`）与内缩
  （`inset_px`）一并给出，CSS 的 `calc()` 与它用的是同一组数字，所以"后端算出的位置"
  与"浏览器画出的位置"不会漂移。窄于假设轴宽时横轴保持尺寸、在自己的容器里横向滚动，
  与宽表格一致；无价位的动作仍然只写"此动作没有价位"。

### 3.5 自动计算与提示行（已定，阈值待检验）

自动计算一节由 `core.auto_computed` 产出数值，卡片页只缩短两处重复标签的措辞：
`距52周高点` 一行只留"低于 25.71%"，盈亏比写成"2.2 : 1"。除此之外逐字使用渲染器
的输出（测试逐行比较，允许这两处已说明的缩短）。

新增一行**提示**，只显示、不参与校验，阈值是命名常量（`webapp/service.py`）：

| 常量 | 值 | 触发文案 |
|---|---|---|
| `MIN_REWARD_RISK` | 1.5 | `盈亏比偏低：1.2 : 1，低于经验阈值 1.5` |
| `MIN_STOP_ATR_MULTIPLE` | 2.0 | `止损距离只有 1.5 倍 ATR，正常波动就可能触发；这是经验阈值，还没有用到期结果检验过` |

**这两个阈值是经验值，待用到期结果检验**，文案里也这样写，不说成事实。
ATR 取自快照的事实索引而不是卡自身引用的编号——卡只携带它引用过的编号，ATR 通常
不在其中，只看卡的清单会让这条提示永远不触发。

---

## 4. 任务模型（已定）

任务持久化在 `data/webapp/jobs.db`（SQLite，建表即用，无 ORM、无迁移工具）。
**不放在 `cache` 目录**：排队中的分析是用户要求的工作，必须跨重启保留；
`.gitignore` 忽略 `data/webapp/`，用 `git check-ignore` 证明。

字段：`job_id`、`kind`、`status`、`parameters_json`、`created_at`、`started_at`、
`finished_at`、`progress_json`、`result_json`、`error`。

| status | 含义 |
|---|---|
| `queued` | 已确认、排队中 |
| `running` | 正在运行 |
| `succeeded` | 完成，`result_json` 指向存档卡 |
| `failed` | 失败，`error` 是给人看的一句话 |
| `interrupted` | 软件重启时该任务仍在运行 |

- `kind` 是自由文本，本轮只有 `analyze`；后续加"抓取财报"等类型不需要改表结构。
- **同一时间只运行一个分析任务**：`claim_next()` 在一个 `BEGIN IMMEDIATE` 事务里
  先查有没有 `running`，再取最旧的 `queued` 置为 `running`，所以并发调用最多只有一个成功。
  其余任务排队。
- **重启把 `running` 标为 `interrupted`**，不重试：无法确认那次模型调用到底有没有完成、
  有没有计费，重试可能重复花钱。`WebApp.start()` 调用 `mark_interrupted()`，
  启动行会报告数量。
- 分析任务调用与命令行 `uv run analyze` **完全相同**的 `run_analysis` 路径，沿用同样的
  限额（16 轮、12 次工具调用、2 次修正、累计 token 硬上限 1,500,000）与预算。
- 进度事件由可选的 `progress` 回调产生（`prefetch`、`round`、`tool_call`、
  `draft_rejected`、`passed`、`rejected`）。事件只带计数、工具名、参数字典、字节数、
  状态与规则编号，**不带模型原文、不带 reasoning 内容、不带密钥**。
- `round` 在**每次模型响应之后**发出，带本轮用量（`round_*_tokens`）与发出时的累计
  用量。此前的 `round_start` 在请求之前发出，界面因此长期显示"累计输入 0｜输出 0"，
  而结果区显示真实用量；那个事件已删除，界面显示的是响应后的真实累计值。旧任务里
  存下的 `round_start` 事件按文字渲染为"第 N 轮（旧记录，没有用量）"。

### 4.1 进度回调不改变默认行为（已定）

`run_analysis(..., progress=None)` 是默认值；不传时不发任何事件，返回值与统计字段
与改动前逐项一致（`tests/test_webapp_progress.py` 断言了两者等同，并复用现有
`tests/test_decision_agent.py` 的全部行为）。回调抛异常会被吞掉——进度显示出问题
不应该让已经花钱的分析作废。

为了让网页用同一路径，`capture_snapshot` / `prepare_evidence` / `history_view` /
`run_analysis` 增加了**可选**的数据库路径参数，默认值就是工具原本使用的模块常量，
不传时行为不变。

---

## 5. 安全模型（已定）

本机网页可以被浏览器里打开的任何网站向 `127.0.0.1` 发请求，而分析接口会花钱调用
DeepSeek，所以按下面几层做：

1. **访问令牌**：每次启动用 `secrets.token_urlsafe(32)` 生成，打印在网址里
   （`http://127.0.0.1:8765/?token=…`）。根路径带正确令牌时回 302、设置
   `HttpOnly; SameSite=Strict` 的 Cookie，并把令牌从地址栏去掉。**所有接口**
   （包括只读）都校验令牌，可用 Cookie、`Authorization: Bearer`、`X-DSH-Token`
   或 `?token=` 传；比较用 `secrets.compare_digest`。令牌不写进日志：
   `log_message` 被重写成空实现，错误响应也不回显令牌。
2. **Host 校验**：`Host` 必须是 `127.0.0.1` 或 `localhost` **加正确端口**，
   否则 403。这是防 DNS 重绑定的那一层。
3. **Origin 校验**：带了 `Origin` 就必须是本站（scheme、主机、端口、无额外路径），
   否则 403；**不发送任何 CORS 响应头**，所以浏览器不会把本站内容交给别的源。
4. **POST-only**：会改变状态或花钱的接口（`/api/analyze`）只接受 POST，
   GET/HEAD 返回 405。
5. **CSP**：所有响应带
   `default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'`。
   不加载任何外部资源。`script-src` 仍是 `'self'`（不允许内联脚本），
   只有样式属性放宽，因为价位带要用内联 `style` 放后端算出的标记位置。
6. **不返回环境变量**：没有任何接口读取或返回 `.env` / `DEEPSEEK_API_KEY` /
   `TIANGO_API_KEY` / `EDGAR_IDENTITY`；API key 只存在于后端进程里。
   测试用哨兵假 key（`sk-sentinel-…`）断言它不出现在任何响应、日志、任务记录中。
7. **请求体上限** 64 KiB，超限 413；非 JSON 或非对象 400；未知接口 404，
   任何路径都不泄漏服务器文件路径。静态文件路径做 `resolve()` + `relative_to()`
   校验，`/../` 一律拒绝。

**已知限制（已定）**：令牌放在 URL 里，同机其他用户可以通过进程列表或 shell 历史
看到启动命令行；若要彻底避免，可用 `--token` 从环境变量传入。本机单人使用场景下
接受这一权衡。

---

## 6. 测试（已定）

| 文件 | 覆盖 |
|---|---|
| `tests/webapp_fixtures.py` | 三个运行库的迷你副本；SEC 事实直接取自仓库真实的 `financial_facts.db` |
| `tests/test_webapp_overview.py` | 日历季度分组、10-K 与 10-Q 同季、修订申报、缺失季度、价格过期阈值与 D03 同源、卡数量 |
| `tests/test_webapp_company.py` | 财报列表、价格范围与行数、指标展示、卡列表过滤 |
| `tests/test_webapp_card_detail.py` | 片段与 `render_card` 逐字节一致、价位带顺序、无价位动作不返回价位带、显示存档卡不调模型 |
| `tests/test_webapp_jobs.py` | 任务字段、状态迁移、重启标记中断、同时只运行一个 |
| `tests/test_webapp_progress.py` | 无回调时行为逐项不变、有回调时事件顺序、坏回调不影响分析 |
| `tests/test_webapp_security.py` | 令牌（无/错/头/Cookie/查询参数）、Host、Origin、POST-only、CSP、无哨兵 key、无通配符 CORS、`data/webapp/` 未入 git |
| `tests/test_webapp_analyze.py` | `confirm` 缺失被拒、假 LLM 跑完整任务、失败记录、排队串行、任务记录无密钥 |
| `tests/test_webapp_pages.py` | 静态资源类型、CSP、路径穿越、接口与夹具数据一致 |
| `tests/test_webapp_design_tokens.py` | 读取设计令牌，浅色与深色两套断言文字对比度 ≥ 4.5:1、控件边界与焦点环 ≥ 3:1 |
| `tests/test_webapp_frontend.py` | 前端不得出现 `toFixed`/`toLocaleString`/`Intl.NumberFormat`/百分比乘除、不得硬编码金融数字、无外部资源 |
| `tests/test_webapp_responsive.py` | 360-1920px 不出现整页横向滚动、宽表在自己容器里滚动、窄屏折叠规则 |
| `tests/test_webapp_cli.py` | 一个命令启动、非回环地址被拒、启动行可用 |
| `tests/test_webapp_display.py` | 时间格式化（固定时区、秒/微秒/时区后缀去除、跨日边界）、参数中文映射、版本短标记与解析失败回退 |
| `tests/test_webapp_formatting.py` | 卡列表/卡详情/任务的时间戳都是后端字符串、规则标记与"旧规则"判断跟着代码常量走、任务参数与存档编号短式 |
| `tests/test_webapp_advice.py` | 自动计算两处措辞、提示行的两个阈值边界与措辞、证据分组、价位带形状与标签摆放不重叠 |
| `tests/test_webapp_copy.py` | 界面不出现内部说明措辞、不解析或格式化时间戳、图例存在、有财报格与缺失格样式不同、只有可点击的文字用强调色 |

真实数据核对与截图自查各有一个脚本，见第 7 节。

---

## 7. 真实数据核对与视觉自查（已定）

```bash
uv run webapp --port 8765 --token <启动时打印的令牌>
uv run python scripts/webapp_spot_check.py --port 8765 --token <令牌>
node scripts/webapp_screenshots.mjs --port 8765 --token <令牌> --out <目录>
```

- `webapp_spot_check.py` 只读比对：总览公司列表与财报期间、AAPL/NVDA/TSLA 的
  财务指标最新值（对照 `get_fundamental_metrics`）、价格起止日与行数、最新收盘价、
  过期标记与 D03 阈值、卡数量、以及卡详情片段与 `render_card` 的逐字节一致。
  **不调用 DeepSeek。**
- `webapp_screenshots.mjs` 用环境里已有的 Chromium（Playwright 驱动，不安装任何东西）
  在 1440×900 与 390×844、浅色与深色下对总览/公司页/建议卡详情/建议卡列表/任务页
  截图，并在 360、390、768、900、1024、1099、1280、1440、1920px 下断言整页无横向溢出。
- `webapp_layout_check.mjs` 做量化断言并顺带截图：1440px 下两栏间距 ≤ 64px、
  证据面板里没有任何日期换行、9 个宽度下整页横向溢出为 0、11 个宽度下价位带标签
  两两不相交（并报告横轴是否在自己的容器里滚动）。

---

## 8. 已定与待定

**已定**：技术选型与零新增依赖；页面清单与路由；接口形状；卡详情为结构化片段；
价位带由后端定位（含标签侧别/行号与轴宽、内缩两个几何量）；任务模型与单槽串行；
重启标中断；令牌/Host/Origin/POST-only/CSP/无密钥外泄；`data/webapp/` 位置与
gitignore；进度回调可选且默认行为不变；时间戳统一由后端按本机时区格式化；
任务参数与版本短标记的中文映射；证据面板按来源分组、两行一条、自身可滚动；
总览默认滚到最新季度并去掉前导空列。

**已定（阈值待检验）**：提示行的两个经验阈值 `MIN_REWARD_RISK = 1.5` 与
`MIN_STOP_ATR_MULTIPLE = 2.0`。它们只影响一行显示文字，不参与校验；到期评估
（见下）落地后应当用真实到期结果检验或替换它们，文案里已写明这一点。

**已定（本轮明确不做）**：自然语言添加公司、聊天框、新闻、定时刷新价格、
任何执行命令的界面按钮。价格由用户自己在终端刷新；界面只显示是否过期并给出命令。

**待定**：

- **到期评估**：卡的到期对照走势、SPY 与 buy-and-hold 仍未实现（沿用
  [decision-mode.md](decision-mode.md) 第 8 节的待定）。
- **任务并发度**：现在是单槽串行。将来若要并行，需要先决定 DeepSeek 的并发与
  费用上限，以及任务之间的资源争用规则。
- **`kind` 的后续取值**：表结构已可扩展，但"抓取财报"等类型的具体字段与失败语义
  尚未设计。
- **卡与分析的持久关联**：`decision_cards` 没有 `analysis_id` 列，卡详情靠
  "创建时间之前最近一次通过的尝试"推断所属分析。若要更硬的关联，需要一次
  明确的 schema 决定（属于卡存档结构，本轮不动）。
- **筛选与分页**：卡列表与任务列表目前一次返回全部；数据量变大后需要分页。
- **历史区间**：覆盖表格固定显示库里全部季度（去年份范围选择器；前导空列已去掉，
  当前 2024Q1 起 11 列），没有年份范围选择器。
- **窄窗下的价位带**：横轴按 640px 的假设轴宽排布，窗口更窄时保持尺寸并在自己的容器里
  横向滚动（不重叠，但需要横向滚动才能看全两端）。更好的做法是按容器宽度改用紧凑标签，
  需要先决定窄窗下哪些标签可以省略。
- **证据面板的日期**：目前显示 `date_or_period` 原值（含财期的 `period_end`）。
  财期类事实是否改写成"财期截止 YYYY-MM-DD"的措辞尚未决定。

---

## 9. 后续轮次的预留扩展点（已定）

- **自然语言添加公司**：新增一个 `kind`（例如 `add_company`）与一个 POST 接口，
  沿用同一套令牌/Host/Origin/POST-only 与任务持久化；采集仍然调用现有
  `scripts/collect_fundamental_snapshots` 的采集路径，不把网络逻辑写进网页层。
- **聊天框**：新增一个 `kind`（例如 `ask`）与一个消息表；Stage 2 的
  `qa.sec_qa.answer_sec_question` 已经是现成入口。会话必须同样经过令牌校验，
  并且不能绕过 citation 规则。
- **抓取财报**：`kind=ingest_filings`，进度事件复用现有形状（工具名、参数、字节、
  状态），失败语义复用现有 failure taxonomy。
- **页面扩展**：`app.js` 的路由表加一个 `head` 分支即可；样式继续只用设计令牌，
  并且必须通过 `test_webapp_design_tokens.py` 与前端扫描测试。

新增任何 `kind` 或页面时，第 6 节的四类测试（安全、片段一致、令牌对比度、
前端数字扫描）都必须继续通过。
