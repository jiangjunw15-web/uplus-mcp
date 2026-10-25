# MCP 客户端接入配置

本服务以 stdio 方式运行，兼容所有支持 Model Context Protocol 的桌面客户端。首次使用前先完成「安装依赖 → 启动附加浏览器并登录 → 配置客户端」三步；分步剧本见仓库根目录 [`rename.md`](../rename.md)。

> 下文 `<安装目录>` 指包含 `pyproject.toml` 的项目根目录绝对路径。Windows JSON 配置里反斜杠要写成 `\\`。

## 0. 准备工作

**从 GitHub Release 附件 wheel 安装（推荐，无需克隆源码）：**

打开仓库 [Releases 页面](https://github.com/jiangjunw15-web/uplus-mcp/releases)，下载最新版本附带的 `uplus_mcp-<版本>-py3-none-any.whl`，然后：

```bash
pip install <下载路径>/uplus_mcp-0.5.0-py3-none-any.whl
```

安装后直接使用 `uplus-mcp` 控制台入口即可，可跳到下方对应客户端章节。

**源码安装（开发或最新未发布代码）：**

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

复制个人配置（gitignored，不会进入仓库）：

**Windows：**

```powershell
Copy-Item config\courses.example.json config\courses.json
```

**macOS / Linux：**

```bash
cp config/courses.example.json config/courses.json
```

### 登录（两种方式，调用 `uplus_login` 前先问用户选哪种）

- **手动模式（`mode="manual"`）**：打开门户，由用户在附加的浏览器窗口自行完成登录；工具轮询等待 token 出现后返回登录态，无需账号密码参数。
- **自动模式（`mode="auto"`）**：模型先向用户询问账号、密码与学校名称，再填学校远程搜索、账号、密码，提交并校验会话。凭据仅用于当次调用，不写入配置、报告或日志，也不会在工具结果里回显；找不到输入框、学校下拉匹配失败，或遇到图形 / 短信验证码、密码错误时，回退手动模式把窗口交还用户。

调试端口未监听时，先调用 `browse_launch_edge`：它复制专用 profile 副本并带 9333 端口启动浏览器（备选 9222），登录态随 profile 保留。该工具名为兼容历史版本保留，实际按配置启动 Edge 或 Chrome。

## 1. 工具清单

### U+ 业务工具

| 工具 | 作用 |
| --- | --- |
| `uplus_login` | 手动接管或自动填表登录；调用前先询问用户选择方式；自动模式另需询问学校名称 |
| `uplus_check_login` | 检查登录态是否有效 |
| `uplus_list_courses` | 列出账号下当前学期课程（已截止课程默认跳过） |
| `uplus_list_homeworks` | 列出课程作业与状态；`refresh_page_state=true` 时打开页面复核按钮文案 |
| `uplus_list_experiments` | 列出课程实验，返回已提交 / 未提交 / 状态未知三态 |
| `uplus_inspect_homework` | 读取作业状态与题目审题包，供客户端模型作答 |
| `uplus_answer_homework` | 按 `{question_id: answer}` 填写客观题；题目未答完或出错时自动暂缓提交，`force_submit=true` 可人工强制提交 |
| `uplus_upload_attachment` | 上传附件到指定作业 |
| `uplus_patrol` | 全量巡检并生成 Markdown 报告，返回待答审题包与待接管清单 |
| `uplus_close` | 关闭本进程浏览器会话 |

### 通用浏览器工具（`browse_*`）

单步工具：`browse_launch_edge`（兼容名，启动 Edge/Chrome）、`browse_open`、`browse_snapshot`、`browse_get_text`、`browse_click`、`browse_fill`、`browse_type`、`browse_press`、`browse_scroll`、`browse_select_option`、`browse_upload`、`browse_screenshot`、`browse_eval_js`、`browse_wait_for`、`browse_tabs`、`browse_switch_tab`、`browse_back`。

`browse_act_batch`：一次提交一小段串行动作（点击、填表、按键、悬停、滚动、等待、提取、导航、后退），失败或发生导航即停，返回 completed / remaining / failure / navigated 回执；`observe="summary"` 跳过动作后的快照回读；密码框取值一律不回显。

## 2. Claude Desktop

配置文件位置：

- Windows：`%APPDATA%\Claude\claude_desktop_config.json`
- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`
- Linux：`~/.config/Claude/claude_desktop_config.json`

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

已通过 Release wheel 或 editable 源码安装后，也可直接调用控制台入口：

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

重启 Claude Desktop 后，在对话中让它「巡检 U+ 未完成作业」即可触发 `uplus_patrol`。

## 3. Cursor / VS Code（MCP 插件）

`.cursor/mcp.json`（Cursor）或 VS Code MCP 配置：

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

## 4. 其他 MCP 兼容客户端

按 stdio 方式注册同一命令即可：

- `command`：虚拟环境解释器绝对路径，或安装后的 `uplus-mcp` 入口。
  - Windows：`<安装目录>\.venv\Scripts\python.exe`
  - macOS / Linux：`<安装目录>/.venv/bin/python`
- `args`：`["-m", "uplus_mcp.server"]`（安装后也可直接调用入口 `uplus-mcp`）。
- `cwd`：`<安装目录>`。
- `env.UPLUS_CONFIG`：可选，指向个人课程配置文件。

## 5. 定时巡检（不依赖 MCP 对话）

CLI 只巡检、出报告、导出待答审题包，**不会自动提交作业**；自动提交由 MCP 会话在模型给出答案后执行。

**Windows：**

```powershell
cd <安装目录>
.venv\Scripts\python.exe -m uplus_mcp.daily_patrol
```

**macOS / Linux：**

```bash
cd <安装目录>
.venv/bin/python -m uplus_mcp.daily_patrol
```

### 接入系统定时任务

- **Windows 任务计划程序**：创建基本任务，触发器「每天」`21:30`；操作「启动程序」，程序为 `.venv\Scripts\python.exe`，参数 `-m uplus_mcp.daily_patrol`，起始于 `<安装目录>`。
- **Linux cron**：`crontab -e` 加入一行（按实际路径替换）：
  ```cron
  30 21 * * * cd <安装目录> && .venv/bin/python -m uplus_mcp.daily_patrol
  ```
- **macOS launchd**：在 `~/Library/LaunchAgents` 下写 `Label`、`ProgramArguments`、`StartCalendarInterval`（Hour 21、Minute 30）的 plist 后 `launchctl load`。

登录态过期时退出码为 `2`，可在脚本外层记录告警；运行时故障退出码为 `1`。

## 6. 常见问题

**Q: 启动后提示登录态已过期？**
A: 调用 `uplus_login`（`mode="manual"`），在弹出的浏览器窗口重新登录 eduplus.net；工具检测到 token 后返回登录成功。自动模式遇到学校下拉匹配失败、验证码或密码错误时同样回退手动登录。

**Q: 工具能看到课程但作业列表为空？**
A: 老师尚未发布作业时列表即为空，属于正常状态；「AI 练习」是自主练习工具，不计入必交作业。已截止课程默认不在巡检范围内（`include_expired_courses=true` 可放开）。

**Q: 为什么有些题被归到「需要人工接管」？**
A: 简答、编程、实验报告、小组 PPT 与附件题（`qsnType=7`）需要本人原创内容或本地文件，服务端不会编造答案或上传伪造文件；实验状态无法从页面识别时归入「状态未知」，由本人人工复核。

**Q: 浏览器窗口是否可以后台运行？**
A: 本服务附加用户本机 Edge / Chrome 的可见会话，不另起独立无头内核；所有操作都在用户眼前的窗口里进行，便于人工接管登录、验证码与附件上传。

**Q: 本机既没 Edge 也没 Chrome 怎么办？**
A: 安装任一桌面浏览器后重启客户端；或在项目目录执行 `playwright install chromium`，并在 `config/courses.json` 里把 `"browser"` 设为 `"chromium"`。
