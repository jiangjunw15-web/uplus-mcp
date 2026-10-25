# rename.md — AI 助手分步引导手册（接入本插件后必读）

> 本文件是给**接入本 MCP 插件的 AI 助手**看的操作剧本，新用户也可以照做。
> 目标：在任意 AI agent（Claude Desktop、Cursor、VS Code MCP、DSH、Doubao 等）上，
> 让 AI 按下面的步骤把用户引导到「登录 → 巡检 → 客观题作答 → 人工接管清单」闭环，
> 全程不写死任何一台机器的路径、账号、学校或课程名。
>
> 约定：
> - `<安装目录>` = 用户存放本项目、里面有 `pyproject.toml` 的那个文件夹（下面用占位符表示）。
> - 命令分三种环境：**Windows PowerShell**、**macOS Terminal**、**Linux**；AI 先判断用户系统再给对应命令，不要混发。
> - 工具名以插件实际注册名为准：U+ 业务族 `uplus_*`，通用浏览器族 `browse_*`。
> - 客观题可自动作答；**简答、编程、附件、实验报告、小组成果物一律交回用户本人**，AI 不得编造答案或伪造上传。

---

## 总览：AI 要带用户走的 10 步

| 步骤 | AI 动作（调用工具 / 执行命令） | 成功信号 |
| --- | --- | --- |
| 0 环境检查 | 确认 Python 版本、项目目录、浏览器 | 三条检查都通过 |
| 1 安装依赖 | 创建虚拟环境并 `pip install -e .` | 无报错，`uplus-mcp` 入口可导入 |
| 2 个人配置（可选） | 复制 `courses.example.json` 为 `courses.json` | 文件存在；留空也能用，自动拉在线课程 |
| 3 注册 MCP 客户端 | 把 stdio 命令填进客户端配置并重启 | 客户端工具列表出现 `uplus_*` / `browse_*` |
| 4 启动浏览器会话 | 调用 `browse_launch_edge` | 返回端口号，`uplus_check_login` 可连接 |
| 5 选择登录方式 | **先问用户**手动 / 自动，再调 `uplus_login` | `logged_in: true`，能看到工作台 |
| 6 巡检 | `uplus_list_courses` → `uplus_patrol` | 返回课程、作业、实验统计与报告路径 |
| 7 客观题作答 | `uplus_inspect_homework` → 模型作答 → `uplus_answer_homework` | 题目填妥；提交前回读核对 |
| 8 人工接管 | 把简答 / 编程 / 附件 / 实验列入清单交付用户 | 清单写明课程、题目、截止时间、需要什么 |
| 9 收尾 | `uplus_close`；定时巡检走 CLI，不答题不提交 | 会话释放，报告落盘 |

---

## 第 0 步：环境检查（AI 先做，不要一上来就让用户操作）

### AI 要检查的三件事

1. **Python 3.10 或更高**
   - Windows PowerShell：`python --version`
   - macOS / Linux：`python3 --version`
   - 低于 3.10：提示用户先安装 Python 3.10+（官网安装器），回到本步重试。

2. **项目目录存在且包含 `pyproject.toml`**
   - 让用户告知解压 / clone 到哪里，或在常见目录定位；确认里面有 `pyproject.toml`、`src/`、`config/`。
   - 找不到时请用户给出 `<安装目录>` 绝对路径，不要替用户猜盘符。

3. **本机装有 Microsoft Edge 或 Google Chrome 任一桌面浏览器**
   - 插件通过 CDP 附加本机已装浏览器；没装任一浏览器时，请用户先安装其中一个。
   - 系统不强制 Edge：Windows 默认探测 Edge，macOS / Linux 会自动找 Edge 或 Chrome。

### AI 对用户说什么

> “我先检查一下你的 Python 版本、项目目录和浏览器，确认环境就绪后再开始配置。”

### 失败回退

- Python 版本不够 → 先装 Python，再继续。
- 找不到目录 → 请用户粘贴文件夹完整路径。
- 无浏览器 → 请用户安装 Edge 或 Chrome 后继续；**不要**在这一步下载独立 Chromium 内核。

---

## 第 1 步：安装依赖

### 跨平台命令（按系统二选一，不要把 Windows 命令发给 macOS 用户）

**Windows PowerShell（在 `<安装目录>` 内）：**

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

### 成功信号

- 安装结束无红色报错；导入自检通过：
  - Windows：`.venv\Scripts\python.exe -c "import uplus_mcp; print(uplus_mcp.__version__)"`
  - macOS / Linux：`.venv/bin/python -c "import uplus_mcp; print(uplus_mcp.__version__)"`

### 失败回退

- `pip install -e .` 网络失败：换国内镜像源重试，提示用户确认网络。
- 提示缺少 `mcp` / `playwright`：重新执行安装命令；安装后重启 MCP 客户端让新依赖生效。

---

## 第 2 步：个人课程配置（可跳过，强烈建议先跳过跑通主流程）

### AI 动作

1. 复制示例配置（个人文件已被 `.gitignore`，不会进入公开仓库）：
   - Windows：`Copy-Item config\courses.example.json config\courses.json`
   - macOS / Linux：`cp config/courses.example.json config/courses.json`
2. 打开 `config/courses.json`，可选填写真实 `courseId`、课程名、教师名。
3. **不填 `courses` 也能正常使用**：登录后插件会自动拉取账号下当前学期课程。

### 成功信号

- `config/courses.json` 存在；或用户选择「先不配置，登录后自动拉课程」。

### 失败回退

- 配置 JSON 格式错误（引号 / 逗号）：提示用户对照 `courses.example.json` 修正，或直接删除该文件用默认在线列表。

### 安全提醒

- 课程 id、教师名只留在本机 `config/courses.json`，不要复制进公开仓库、聊天记录或提交到 GitHub。

---

## 第 3 步：把插件注册进 AI 客户端（stdio 方式）

### 3.1 注册内容（核心命令）

MCP 配置里 `command` 指向虚拟环境解释器，`args` 启动服务模块；`cwd` 设为 `<安装目录>`。

**Windows 示例（Claude Desktop / Cursor / VS Code 的 JSON 配置同理）：**

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

**macOS 示例：**

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

**Linux 示例**：解释器路径把上面的 `.venv/bin/python` 保持为同样形式即可。

### 3.2 安装过 console script 的简化写法

完成第 1 步后，`uplus-mcp` 入口已安装；若解释器在 PATH 中，也可直接：

```json
{
  "mcpServers": {
    "uplus": {
      "command": "uplus-mcp",
      "cwd": "<安装目录>",
      "env": { "UPLUS_CONFIG": "config/courses.json" }
    }
  }
}
```

### 3.3 各客户端配置文件位置（详见 `docs/mcp-clients.md`）

- Claude Desktop：`%APPDATA%\Claude\claude_desktop_config.json`（Windows）；`~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）。
- Cursor：项目内 `.cursor/mcp.json`。
- VS Code：MCP 插件的 `mcp.json`。
- DSH 等其它客户端：按 stdio 协议注册同一 `command` / `args`。

### AI 动作

1. 先问用户使用哪个 AI 客户端，给出对应格式与配置文件位置。
2. 帮用户把 `<安装目录>` 替换成真实绝对路径（JSON 里 Windows 路径反斜杠要写成 `\\`）。
3. 提示用户**保存后必须重启客户端**，新工具才会加载。

### 成功信号

- 重启后客户端工具列表出现 `uplus_login`、`uplus_patrol`、`browse_snapshot` 等 `uplus_*` / `browse_*` 工具。

### 失败回退

- 工具没出现：核对解释器路径是否存在、`cwd` 是否正确、JSON 是否合法；重启客户端。
- 启动报错 `ModuleNotFoundError: uplus_mcp`：回到第 1 步确认 `pip install -e .` 成功。

---

## 第 4 步：启动浏览器并建立 CDP 会话

### AI 动作

先调用 `browse_launch_edge`（工具名保留历史拼写，实际会按配置自动启动 Edge 或 Chrome）：

- 若调试端口已在监听：工具返回 `already_running: true`，直接复用现有浏览器会话。
- 若未监听：工具自动复制一份专用 profile 到独立目录，带调试端口启动浏览器，登录态保留。

### 成功信号

- 返回 `launched: true` 或 `already_running: true`，并给出可用端口（默认 9333，备选 9222）。

### AI 对用户说什么

> “我现在启动带调试端口的浏览器，第一次复制 profile 可能稍慢；窗口保持打开，后面登录和作答都在这个窗口里进行。”

### 失败回退

- 端口始终不监听：调用 `uplus_check_login` 查看错误提示；按第 11 节 FAQ 手动启动浏览器。
- 未检测到 Edge / Chrome：引导用户先安装任一桌面浏览器。

> 说明：Chromium 136+ 会忽略默认用户目录上的调试端口，所以插件使用专用目录启动；这是正常设计，不是故障。

---

## 第 5 步：选择登录方式（AI 必须先问，禁止替用户默认）

### AI 动作（顺序不能乱）

1. **先向用户提问二选一**，把两种方式讲清楚：
   - **手动登录（`manual`，推荐）**：AI 打开门户，用户在眼前浏览器窗口里自己选学校、填账号密码并提交；AI 只等待会话 token 出现，全程不要账号密码。
   - **自动登录（`auto`）**：AI 先分别询问**账号、密码、学校名称**，再依次填写学校远程搜索下拉、账号、密码，提交并校验会话。
2. 用户选定后调用 `uplus_login`：
   - 手动：`uplus_login(mode="manual", wait_timeout_ms=120000)`。
   - 自动：`uplus_login(mode="auto", username=..., password=..., school=..., wait_timeout_ms=120000)`。

### 成功信号

- 工具返回 `logged_in: true`、当前 URL 已离开登录域、`token_len > 0`；再调 `uplus_check_login` 复核一次返回登录有效。

### 隐私红线（AI 必须遵守）

- 自动模式的账号、密码、学校名**仅用于当次调用**：不写进 `courses.json`、报告、日志、聊天记录，不在工具结果里回显，不保存到任何文件。
- 不要主动索要、不要重复抄写、不要把密码放进命令行参数记录。

### 失败回退（自动模式遇到以下任一情况，立即回退手动）

- 找不到账号 / 密码输入框；
- 学校下拉远程搜索后匹配不到选项；
- 出现图形验证码、短信验证码、滑块；
- 提示密码错误、账号锁定。

回退时调用 `uplus_login(mode="manual")`，把窗口交还用户完成登录。

---

## 第 6 步：巡检课程、作业与实验

### AI 动作（按顺序调用）

1. `uplus_check_login`：确认登录态有效；失效则回到第 5 步重新登录。
2. `uplus_list_courses`：列出账号下当前学期课程（已截止课程默认跳过）。
3. `uplus_patrol`：全量巡检作业与实验，生成 Markdown 报告，返回统计摘要、待答审题包与待接管清单。
   - 本工具**默认不答题、不提交**；不要在未经用户确认时开启自动提交。

### 成功信号

- 返回课程清单；巡检摘要包含「作业已提交 / 待批改 / 未完成」「实验已提交 / 未完成 / 状态未知」「待人工接管」计数；报告路径可打开。

### AI 对用户说什么

> “巡检完成：已提交 X 项、未完成 Y 项；实验 Z 项待处理；另有 N 项需要你本人完成。我把明细列给你。”

### 失败回退

- 接口 401：会话过期，回到第 5 步重新登录后重试。
- 课程列表为空：先确认用户账号下当前学期确实有课程；「AI 练习」属于自主练习，不计入必交作业。

---

## 第 7 步：客观题作答闭环（仅单选 / 多选 / 判断 / 填空）

### AI 动作（一份作业走一轮闭环）

1. 对用户确认要处理的作业（课程 + 作业名 + 截止时间）。
2. 调用 `uplus_inspect_homework(task_id, course_id)` 获取题目审题包（题干、选项、题型提示）。
3. AI 逐题作答，按题型规范化答案：
   - 单选：单个字母，如 `"C"`；
   - 多选：去重排序大写字母串，如 `"ABCD"`；
   - 判断：`"true"`（正确）或 `"false"`（错误）；
   - 填空：题目对应的文本。
4. 调用 `uplus_answer_homework(task_id, course_id, answers, submit=False)` **先落页不提交**；工具回读核对已填题目。
5. 核对无误、用户确认后，再以 `submit=True` 提交；提交确认弹窗由工具自动点击，提交后回读按钮状态校验。

### 成功信号

- 落页后未答题数为 0；提交成功后页面按钮由「提交作业」变为「撤回作业」，答题卡显示 `(N/N)`。

### 强制边界

- 题目未答完、部分题目回读失败时，工具会自动暂缓提交；不得擅自 `force_submit=True`，除非用户明确要求并知晓风险。
- **简答、编程、附件题、实验报告、小组 PPT 不进入本步**；AI 不得为这些题编造答案。

### 失败回退

- 选项乱序 / 题号错位：重新 `uplus_inspect_homework` 取最新审题包；页面被导航打断时用 `browse_open` 回到作答页再续做。

---

## 第 8 步：人工接管清单（AI 主动整理交付给用户）

### AI 动作

从巡检 `takeover` 清单中逐项输出，每项写明：课程、作业 / 实验名、题目或材料要求、截止时间、需要用户本人准备什么。

典型必须人工接管的类型：

| 类型 | 原因 | AI 可以做的辅助 |
| --- | --- | --- |
| 简答题 | 需要本人原创观点 | 列出题目、截止时间，提示用户作答 |
| 编程题 | 需要本人代码与运行环境 | 给出题目要求与截止时间 |
| 附件题（`qsnType=7`） | 需要本地真实文件，不能伪造 | 用户选好文件后调 `uplus_upload_attachment` 或 `browse_upload` 上传 |
| 实验报告 / 小组 PPT | 需本人原创与协作成果 | 列出材料要求、提交入口与截止时间 |
| 状态未知实验 | 页面无法判定提交状态 | 请用户打开实验栏目人工复核 |

### 文件上传交接

- 附件题先让用户确认本地文件绝对路径；路径交给 `uplus_upload_attachment(task_id, course_id, file_path)` 或通用工具 `browse_upload(selector, file_path)`。
- 上传完成后仍需按第 7 步确认提交作业；不要只上传文件就认为完成。

### AI 对用户说什么

> “以下 N 项需要你本人完成，我不会代为编造：……你准备好文件或答案后，我再帮你上传或提交。”

---

## 第 9 步：收尾与定时巡检

### 会话收尾

- 一次 MCP 会话结束、当天不再使用时，调用 `uplus_close` 释放浏览器会话；浏览器窗口本身保留，用户可自行关闭。

### 无人值守定时巡检（CLI，只报告、不答题、不提交）

在 `<安装目录>` 内运行：

- Windows：`.venv\Scripts\python.exe -m uplus_mcp.daily_patrol`
- macOS / Linux：`.venv/bin/python -m uplus_mcp.daily_patrol`

- 退出码 `0`：巡检成功；`2`：登录态过期，需要在浏览器里重新登录；`1`：运行时故障。
- 报告写入 `reports/patrol_YYYYMMDD_HHMM.md`，待答客观题另存 `.brief.json`。
- 接入系统定时任务：Windows 任务计划程序；macOS `launchd`；Linux `cron`。具体命令见 `docs/mcp-clients.md` 第 5 节。

---

## 第 10 步：故障自查顺序（AI 按此排查，不要跳步）

1. `uplus_check_login` 是否 `logged_in: true`？否 → 重新登录（第 5 步）。
2. 浏览器调试端口是否监听？调 `browse_launch_edge` 复核；仍失败 → 手动启动（见 `docs/mcp-clients.md` FAQ）。
3. 工具是否在客户端工具列表里？否 → 回到第 3 步核对配置并重启客户端。
4. 依赖是否装好？回到第 1 步重装并重启客户端。
5. 平台接口 401：会话过期，重新登录；前端改版导致选择器失效：用 `browse_snapshot` 读取当前页面结构并对照 `docs/api-mapping.md` 校准。

---

## 配置项速查（`config/courses.json` 可选字段）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `base_url` | `https://www.eduplus.net` | 平台根地址 |
| `nav_timeout_ms` | `30000` | 页面导航超时（毫秒） |
| `action_timeout_ms` | `15000` | UI 动作超时（毫秒） |
| `deadline_warning_hours` | `72` | 临近截止高亮窗口（小时） |
| `report_dir` | `reports` | Markdown 报告输出目录 |
| `auto_launch_browser` | `true` | 端口未监听时自动启动浏览器；旧字段 `auto_launch_edge` 兼容读取 |
| `browser` | `auto` | `auto` / `edge` / `chrome` / `chromium`；`auto` 自动探测已安装浏览器 |
| `cdp_port` | `9333` | 首选调试端口，备选 9222；可用环境变量 `UPLUS_CDP_PORT` 覆盖 |
| `profile_dir` | 空（用户家目录专用目录） | 专用浏览器 profile 目录；可用环境变量 `UPLUS_PROFILE_DIR` 覆盖 |
| `include_expired_courses` | `false` | 是否在巡检中保留已截止课程 |
| `courses` | 空（在线拉取） | 手动列出 `id` / `name` / `teacher` |

环境变量：`UPLUS_CONFIG`（配置文件路径）、`UPLUS_BROWSER`、`UPLUS_CDP_PORT`、`UPLUS_PROFILE_DIR`。

---

## 学术诚信与隐私（AI 必须主动遵守并在首次使用时提示用户）

- 本插件是个人学习提醒与题目梳理助手：用于核对题目、跟踪截止时间、整理作业清单。
- 实验报告、小组作品、编程作业、开放简答必须由本人完成；考试、闭卷测验及任何禁止辅助的考核场景不得使用。
- 服务端不内置题库；客观题答案由接入的 AI 模型生成，提交前用户应自行复核。
- 密码、Cookie、token、学号、姓名、课程 id 等敏感信息不写入仓库、报告或公开配置。
