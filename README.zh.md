# uplus-mcp

一个面向青软 U+ 学习平台的 MCP 服务，同时内置通用浏览器驾驶（browse）工具：既能自动巡检课程、作业与实验，把客观题整理成审题包交给模型作答并在真实作答页填写提交；也能让 AI 直接打开网页、读快照、点击、填表、截图——平台接口变了或流程没有 API 时，模型可以亲自操作页面兜底。简答题、编程题、实验报告与附件上传一律交回本人处理。

> **学术诚信声明**：本工具仅作为个人学习提醒与题目梳理助手。请用它核对题目、跟踪截止时间、整理自己的作业清单；实验报告、小组作品、编程作业与开放题必须由本人完成，并遵守学校考试诚信规定与任课教师要求。考试、闭卷测验或任何禁止辅助的考核场景下不得使用本工具。

## 0.4.0 版本更新（必读）

本版合并了原 browse 插件能力并修复三个导致旧版不可用的问题：

| 问题 | 说明 | 修复 |
| --- | --- | --- |
| MCP 工具全部报 `Playwright Sync API inside the asyncio loop` | FastMCP 在事件循环线程里直接调用同步工具函数，Playwright 同步 API 拒绝在该线程运行 | 新增 `worker.py`：所有浏览器操作路由到唯一专职工作线程，工具改为 async 等待结果 |
| 只读接口一律返回 401 | U+ 平台要求每个 API 请求带 `x-access-token` 头（值取自 `localStorage.AccessToken`），旧版只带了 Cookie | `browser.py` 的页内 fetch 自动附带该头 |
| 按文档带 `--remote-debugging-port=9333` 启动 Edge 后端口仍不监听 | Edge/Chromium 136+ 在**默认用户目录**上会忽略调试端口参数 | 新增 `launcher.py`：自动把 profile 复制到独立目录再带端口启动 Edge，登录态保留；也可用 `browse_launch_edge` 工具一键启动 |

新增 browse 工具集（与 uplus 工具共用同一个 Edge 会话）：
`browse_launch_edge / browse_open / browse_snapshot / browse_get_text / browse_click / browse_fill / browse_type / browse_press / browse_scroll / browse_select_option / browse_upload / browse_screenshot / browse_eval_js / browse_wait_for / browse_tabs / browse_switch_tab / browse_back`

## 功能特性

- 通过 CDP 附加到你本人已登录的 Edge；仓库内不保存任何密码与 Cookie。
- 只读接口复用页面会话并自动附带 `x-access-token`；写操作走真人路径（点选选项、确认弹窗、提交后按钮状态回读校验）。
- 单选、多选、判断、填空等客观题可在客户端模型返回规范化答案后填写并提交。
- 生成 Markdown 巡检报告：已提交 / 待批改 / 未完成作业、实验状态与人工接管清单。
- 通用 browse 工具让 AI 直接操作任意网页：打开 URL、读可交互元素快照、点击、填表、按键、滚动、截图、执行 JS、管理标签页。
- 兼容 Claude Desktop、Cursor、VS Code MCP、DSH 等所有支持 MCP 的客户端。
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
                          browser.py（CDP 附加 Edge）
                              │
                       launcher.py（Edge 调试端口自动启动）
```

模块职责见 [`docs/architecture.md`](docs/architecture.md)；接口、题型编码与状态口径见 [`docs/api-mapping.md`](docs/api-mapping.md)；接入其他平台见 [`docs/adapter-guide.md`](docs/adapter-guide.md)。

## 快速开始（一步步来）

### 第 1 步：下载项目

方式 A（推荐，已装 Git）：

```powershell
git clone https://github.com/3995019608-cyber/uplus-mcp.git
cd uplus-mcp
```

方式 B（GitHub 网页）：打开仓库主页 → 绿色 **Code** 按钮 → **Download ZIP** → 解压到任意目录（本文以 `D:\uplus-mcp` 为例，下同）。

### 第 2 步：安装 Python 依赖

需要 Python 3.10+（`python --version` 确认）。在项目目录执行：

```powershell
cd D:\uplus-mcp
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

### 第 3 步：验证安装成功

```powershell
.venv\Scripts\python.exe -m pytest -q
```

看到 `21 passed`（或更多）即安装成功。

### 第 4 步：把 MCP 服务注册进你的 AI 客户端

在客户端的 MCP 配置里加一段（以 DSH 的 `cordis.patch.yml` 为例，Claude Desktop / Cursor 的 JSON 格式同理）：

```yaml
- id: uplus
  command: D:\uplus-mcp\.venv\Scripts\python.exe
  args: ["-m", "uplus_mcp.server"]
  cwd: D:\uplus-mcp
```

保存后重启客户端，工具列表里应出现 `uplus_*` 与 `browse_*` 两族工具。

### 第 5 步：首次运行（两种方式任选）

方式 A —— 全自动（推荐）：

直接对 AI 说「巡检我的 U+ 作业」。第一个工具调用会触发 `launcher.py` 自动复制 Edge profile（约 600MB，几十秒，仅首次）并带调试端口启动 Edge，你的 U+ 登录态保留。之后所有操作都在你眼前这个 Edge 窗口里进行。

方式 B —— 手动确认每一步：

```powershell
# 检查调试端口状态
.venv\Scripts\python.exe -m uplus_mcp.launcher
# 复制 profile 并启动带调试端口的 Edge（仅首次复制较慢）
.venv\Scripts\python.exe -m uplus_mcp.launcher --launch
```

启动的 Edge 会自动打开 U+ 工作台。如果显示未登录，就在该窗口里登录一次 eduplus.net（profile 持久化，之后不用再登）。

### 第 6 步：验证端到端可用

```powershell
.venv\Scripts\python.exe scripts\smoke_mcp.py
```

预期输出：列出 26 个工具、`uplus_check_login` 返回 `logged_in: true`、`browse_snapshot` 返回页面标题与元素列表。

### 第 7 步：日常使用

对 AI 说：

- 「巡检我所有课程的作业和实验」→ `uplus_patrol`
- 「列出 XX 课程的作业」→ `uplus_list_homeworks`
- 「帮我看看第 N 次作业的题目」→ `uplus_inspect_homework`，AI 审题后用 `uplus_answer_homework` 填写提交
- 「帮我上传 D:\xxx.pptx 到上传PPT作业」→ `uplus_upload_attachment`（或 `browse_upload`）

CLI 定时巡检（不答题不提交）：

```powershell
.venv\Scripts\python.exe -m uplus_mcp.daily_patrol
```

### 第 8 步：改完代码传回 GitHub（用 GitHub Desktop）

1. 打开 GitHub Desktop，左上角仓库选择 `uplus-mcp`（File → Add local repository 若未添加）。
2. 中间左侧会列出本次改动（`browser.py`、`server.py`、`browse.py`、`launcher.py`、`worker.py`、`README.zh.md` 等）。
3. 左下角 Summary 填一句话（如 `merge browse toolset; fix asyncio, auth header, Edge CDP`）→ 点 **Commit to main**。
4. 点顶部 **Push origin**。完成。

命令行等价操作（效果相同）：

```powershell
git add -A
git commit -m "merge browse toolset; fix asyncio, auth header, Edge CDP"
git push
```

## 常见问题

- **`x-access-token`/401**：登录态过期。在插件启动的 Edge 窗口重新登录 eduplus.net 即可。
- **端口 9333 不通**：先跑 `python -m uplus_mcp.launcher` 看状态；`--launch` 会自动处理。注意 Edge 136+ 不允许默认用户目录开调试端口，插件已用独立目录绕开。
- **profile 复制太慢/想刷新登录态**：`python -m uplus_mcp.launcher --launch --refresh-profile` 重新复制。
- **MCP 工具报 Playwright asyncio 错误**：0.4.0 已修复；确认客户端拉起的是新版（`pip install -e .` 后重启客户端）。

## 测试

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## License

MIT
