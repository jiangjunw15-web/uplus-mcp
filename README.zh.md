# uplus-mcp

一个面向青软 U+ 学习平台的 MCP 服务，同时内置通用浏览器驾驶（browse）工具：既能自动巡检课程、作业与实验，把客观题整理成审题包交给模型作答并在真实作答页填写提交；也能让 AI 直接打开网页、读快照、点击、填表、截图——平台接口变了或流程没有 API 时，模型可以亲自操作页面兜底。简答题、编程题、实验报告与附件上传一律交回本人处理。

> **学术诚信声明**：本工具仅作为个人学习提醒与题目梳理助手。请用它核对题目、跟踪截止时间、整理自己的作业清单；实验报告、小组作品、编程作业与开放题必须由本人完成，并遵守学校考试诚信规定与任课教师要求。考试、闭卷测验或任何禁止辅助的考核场景下不得使用本工具。

> **新用户 / AI 助手先读 [rename.md](rename.md)**：那是一份分步引导剧本，写清了从环境检查、安装、注册客户端、两种登录方式、巡检、客观题作答到人工接管的每一步该调用什么工具、向用户说什么、成功信号与失败回退。

## 0.5.0 版本更新（通用化发布）

- **跨平台、脱离本机写死路径**：启动器在 Windows / macOS / Linux 上自动探测已安装的 Microsoft Edge 或 Google Chrome，PATH 命令兜底；不再只认 Windows 上的 Edge 路径。调试端口、浏览器引擎、专用 profile 目录均可配置（`browser`、`cdp_port`、`profile_dir`、`auto_launch_browser`），并支持环境变量 `UPLUS_BROWSER`、`UPLUS_CDP_PORT`、`UPLUS_PROFILE_DIR` 覆盖。
- **浏览器表述通用化**：会话附加到用户本机已装的任一桌面浏览器；原工具名 `browse_launch_edge` 为兼容历史版本保留，实际按配置启动 Edge 或 Chrome。
- **新增 `rename.md` 引导手册**：任意 AI agent 接入后先读它，再按步骤带新用户完成安装与闭环。

## 0.4.2 版本更新

- **双模式登录**（`uplus_login`）：调用前先问用户选哪种方式——`manual` 打开门户、等用户在浏览器窗口自行登录；`auto` 由模型先询问账号、密码与学校名称，依次填学校下拉、账号、密码，提交并校验会话。自动模式凭据仅用于当次调用，不写入配置、报告或日志，结果中也不回显；找不到输入框、学校下拉匹配失败，或遇到验证码 / 短信 / 密码错误时回退手动模式。
- **批量动作闭环**（`browse_act_batch`）：一次提交一小段串行动作（点击、填表、按键、悬停、滚动、等待、提取、导航、后退），失败或发生导航即停，返回 completed / remaining / failure / navigated 回执；`observe="summary"` 跳过动作后快照；密码框取值不回显。原有 17 个单步 `browse_*` 工具签名保持不变。
- **附件题（`qsnType=7`）** 与实验三态（已提交 / 未提交 / 状态未知）：无法识别的实验卡片列入人工复核；默认跳过已截止课程。

## 0.4.0 版本更新

本版合并了原 browse 插件能力并修复三个导致旧版不可用的问题：

| 问题 | 说明 | 修复 |
| --- | --- | --- |
| MCP 工具全部报 `Playwright Sync API inside the asyncio loop` | FastMCP 在事件循环线程里直接调用同步工具函数，Playwright 同步 API 拒绝在该线程运行 | 新增 `worker.py`：所有浏览器操作路由到唯一专职工作线程，工具改为 async 等待结果 |
| 只读接口一律返回 401 | U+ 平台要求每个 API 请求带 `x-access-token` 头（值取自 `localStorage.AccessToken`），旧版只带了 Cookie | `browser.py` 的页内 fetch 自动附带该头 |
| 带 `--remote-debugging-port` 启动浏览器后端口仍不监听 | Chromium 136+ 在**默认用户目录**上会忽略调试端口参数 | 新增 `launcher.py`：自动把 profile 复制到独立目录再带端口启动浏览器，登录态保留；也可用 `browse_launch_edge` 工具一键启动 |

新增 browse 工具集（与 uplus 工具共用同一个浏览器会话）：
`browse_launch_edge / browse_open / browse_snapshot / browse_get_text / browse_click / browse_fill / browse_type / browse_press / browse_scroll / browse_select_option / browse_upload / browse_screenshot / browse_eval_js / browse_wait_for / browse_tabs / browse_switch_tab / browse_back / browse_act_batch`

## 功能特性

- 通过 CDP 附加到你本人已登录的 Edge 或 Chrome；`uplus_login` 支持手动接管与 AI 自动填表两种登录方式；仓库内不保存任何密码与 Cookie。
- 只读接口复用页面会话并自动附带 `x-access-token`；写操作走真人路径（点选选项、确认弹窗、提交后按钮状态回读校验）。
- 单选、多选、判断、填空等客观题可在客户端模型返回规范化答案后填写并提交。
- 生成 Markdown 巡检报告：已提交 / 待批改 / 未完成作业，实验按已提交 / 未提交 / 状态未知三态归类，附件题、简答、编程、实验报告进入人工接管清单。
- 通用 browse 工具让 AI 直接操作任意网页：打开 URL、读可交互元素快照、点击、填表、按键、滚动、截图、执行 JS、管理标签页；`browse_act_batch` 支持一小段串行动作批量执行、失败或导航即停并返回回执。
- 兼容 Claude Desktop、Cursor、VS Code MCP、DSH、Doubao 等所有支持 MCP 的客户端。
- 提供无人值守 CLI 用于定时巡检；CLI 本身不会作答或提交。

## 架构

```
MCP 客户端  ──stdio──▶  server.py（FastMCP async 工具）
                              │
                       worker.py（专职浏览器线程）
                              │
                          engine.py（编排层）
              ┌───────────────┼────────────────┐
       platform.py       operator.py        report/solver
       只读 REST 客户端    页面 UI 写入       报告渲染 + 答案规范化
              └───────────────┼────────────────┘
       browse.py（AI 直接驾驶页面）
                          browser.py（CDP 附加本机浏览器）
                              │
                       launcher.py（调试端口自动启动 Edge/Chrome）
```

模块职责见 [`docs/architecture.md`](docs/architecture.md)；接口、题型编码与状态口径见 [`docs/api-mapping.md`](docs/api-mapping.md)；接入其他平台见 [`docs/adapter-guide.md`](docs/adapter-guide.md)。

## 快速开始（一步步来）

> 下文 `<安装目录>` 指包含 `pyproject.toml` 的项目根目录绝对路径，按你的实际位置替换；Windows 路径在 JSON 配置里反斜杠要写成 `\\`。

### 第 1 步：获取项目

方式 A（已装 Git）：

```bash
git clone https://github.com/<your-account>/uplus-mcp.git
cd uplus-mcp
```

方式 B（GitHub 网页）：打开仓库主页 → 绿色 **Code** 按钮 → **Download ZIP** → 解压到任意目录。

### 第 2 步：安装 Python 依赖

需要 Python 3.10+（`python --version` 或 `python3 --version` 确认）。

**Windows PowerShell：**

```powershell
cd <安装目录>
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

**macOS / Linux：**

```bash
cd <安装目录>
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

### 第 3 步：验证安装成功

**Windows：**

```powershell
.venv\Scripts\python.exe -m pytest -q
```

**macOS / Linux：**

```bash
.venv/bin/python -m pytest -q
```

看到 `73 passed`（或更多）即安装成功。

### 第 4 步：把 MCP 服务注册进你的 AI 客户端

在客户端的 MCP 配置里加一段（以 JSON 为例，DSH 的 `cordis.patch.yml` 同理）。

**Windows：**

```json
{
  "mcpServers": {
    "uplus": {
      "command": "<安装目录>\\.venv\\Scripts\\python.exe",
      "args": ["-m", "uplus_mcp.server"],
      "cwd": "<安装目录>",
      "env": { "UPLUS_CONFIG": "config/courses.json" }
    }
  }
}
```

**macOS / Linux：**

```json
{
  "mcpServers": {
    "uplus": {
      "command": "<安装目录>/.venv/bin/python",
      "args": ["-m", "uplus_mcp.server"],
      "cwd": "<安装目录>",
      "env": { "UPLUS_CONFIG": "config/courses.json" }
    }
  }
}
```

保存后**重启客户端**，工具列表里应出现 `uplus_*` 与 `browse_*` 两族工具。各客户端配置文件位置见 [`docs/mcp-clients.md`](docs/mcp-clients.md)。

### 第 5 步：启动浏览器并登录（两种方式任选，先让 AI 问你选哪种）

先把调试端口的浏览器起起来：直接对 AI 说「启动 U+ 浏览器」会触发 `browse_launch_edge`；也可手动执行：

**Windows：**

```powershell
# 检查调试端口状态
.venv\Scripts\python.exe -m uplus_mcp.launcher
# 复制 profile 并启动带调试端口的浏览器（仅首次复制较慢）
.venv\Scripts\python.exe -m uplus_mcp.launcher --launch
```

**macOS / Linux：**

```bash
.venv/bin/python -m uplus_mcp.launcher
.venv/bin/python -m uplus_mcp.launcher --launch
```

然后让 AI 调用 `uplus_login`，**先问你选哪种方式**：

- **手动登录（`mode="manual"`，推荐）**：工具打开 U+ 门户，你在眼前这个浏览器窗口里自己登录 eduplus.net；工具检测到会话 token 后返回登录成功，无需提供账号密码。
- **自动登录（`mode="auto"`）**：AI 先向你询问账号、密码与学校名称，工具依次填学校下拉、账号、密码，点登录并校验会话；密码与学校名仅当次使用，不存盘、不进报告、不在结果里回显。若学校下拉匹配失败、遇到验证码 / 短信校验 / 密码错误，或页面识别不到输入框，会回退到手动登录并把窗口交给你。

浏览器复用本机 profile 副本，登录态保留，之后通常不用再登。

### 第 6 步：验证端到端可用

**Windows：**

```powershell
.venv\Scripts\python.exe scripts\smoke_mcp.py
```

**macOS / Linux：**

```bash
.venv/bin/python scripts/smoke_mcp.py
```

预期输出：列出 `uplus_*` 与 `browse_*` 工具、`uplus_check_login` 返回 `logged_in: true`、`browse_snapshot` 返回页面标题与元素列表。

### 第 7 步：日常使用

对 AI 说：

- 「巡检我所有课程的作业和实验」→ `uplus_patrol`
- 「列出 XX 课程的作业」→ `uplus_list_homeworks`
- 「帮我看看第 N 次作业的题目」→ `uplus_inspect_homework`，AI 审题后用 `uplus_answer_homework` 填写提交
- 「帮我上传本地文件到上传作业」→ `uplus_upload_attachment`（或 `browse_upload`）

CLI 定时巡检（不答题不提交）：

**Windows：**

```powershell
.venv\Scripts\python.exe -m uplus_mcp.daily_patrol
```

**macOS / Linux：**

```bash
.venv/bin/python -m uplus_mcp.daily_patrol
```

## 配置项参考

`config/courses.json`（个人文件已被 `.gitignore`，不进入公开仓库）：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `base_url` | `https://www.eduplus.net` | 平台根地址 |
| `nav_timeout_ms` | `30000` | 页面导航超时（毫秒） |
| `action_timeout_ms` | `15000` | UI 动作超时（毫秒） |
| `deadline_warning_hours` | `72` | 临近截止高亮窗口（小时） |
| `report_dir` | `reports` | 报告输出目录 |
| `auto_launch_browser` | `true` | 端口未监听时自动启动浏览器；旧字段 `auto_launch_edge` 兼容读取 |
| `browser` | `auto` | `auto` / `edge` / `chrome` / `chromium`；`auto` 自动探测已安装浏览器 |
| `cdp_port` | `9333` | 首选调试端口，备选 9222；环境变量 `UPLUS_CDP_PORT` 覆盖 |
| `profile_dir` | 用户家目录专用目录 | 专用浏览器 profile 目录；环境变量 `UPLUS_PROFILE_DIR` 覆盖 |
| `include_expired_courses` | `false` | 是否在巡检中保留已截止课程 |
| `courses` | 空（在线拉取） | 手动列出 `id` / `name` / `teacher` |

环境变量：`UPLUS_CONFIG`（配置文件路径）、`UPLUS_BROWSER`、`UPLUS_CDP_PORT`、`UPLUS_PROFILE_DIR`。

## 常见问题

- **`x-access-token`/401**：登录态过期。调用 `uplus_login`（`mode="manual"`）在插件启动的浏览器窗口重新登录 eduplus.net 即可；自动模式遇到学校下拉匹配失败、验证码或密码错误会回退手动。
- **端口 9333 不通**：先跑 `python -m uplus_mcp.launcher`（macOS / Linux 用 `python3 -m`）看状态；`--launch` 会自动处理。注意 Chromium 136+ 不允许默认用户目录开调试端口，插件已用独立目录绕开。
- **profile 复制太慢 / 想刷新登录态**：`python -m uplus_mcp.launcher --launch --refresh-profile` 重新复制。
- **自动登录的账号密码学校会保存吗**：不会。`uplus_login` 自动模式凭据与学校名仅当次内存使用，不落盘、不进报告、不在工具结果回显；关闭会话即清除。
- **MCP 工具报 Playwright asyncio 错误**：0.4.0 已修复；确认客户端拉起的是新版（`pip install -e .` 后重启客户端）。
- **本机既没 Edge 也没 Chrome**：先安装任一桌面浏览器；或执行 `playwright install chromium` 后在配置中把 `browser` 设为 `chromium`。

## 测试

**Windows：**

```powershell
.venv\Scripts\python.exe -m pytest -q
```

**macOS / Linux：**

```bash
.venv/bin/python -m pytest -q
```

## 维护者：改完代码传回 GitHub

1. 在 GitHub Desktop 左上角选择本仓库（未添加则 File → Add local repository，选择 `<安装目录>`）。
2. 中间左侧列出本次改动文件。
3. 左下角 Summary 填一句话提交说明 → 点 **Commit to main**。
4. 点顶部 **Push origin** 完成推送。

命令行等价操作（Git 已在 PATH 时）：

```bash
git add -A
git commit -m "describe the change"
git push
```

## License

MIT
