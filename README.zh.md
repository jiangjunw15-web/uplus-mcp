# uplus-mcp

一个面向青软 U+ 学习平台的 MCP 服务：自动巡检课程、作业与实验，把客观题整理成结构化审题包，交给连接本服务的 MCP 客户端模型作答，再由服务端在真实作答页填写、回读核对并提交；简答题、编程题、实验报告与附件上传一律交回本人处理。

> **学术诚信声明**：本工具仅作为个人学习提醒与题目梳理助手。请用它核对题目、跟踪截止时间、整理自己的作业清单；实验报告、小组作品、编程作业与开放题必须由本人完成，并遵守学校考试诚信规定与任课教师要求。考试、闭卷测验或任何禁止辅助的考核场景下不得使用本工具。

## 功能特性

- 通过 Playwright `storage_state` 持久化登录态；仓库内不保存任何密码与 Cookie。
- 只读接口复用页面会话；写操作走真人路径（点选选项、确认弹窗、提交后按钮状态回读校验）。
- 单选、多选、判断、填空等客观题可在客户端模型返回规范化答案后填写并提交。
- 生成 Markdown 巡检报告：已提交 / 待批改 / 未完成作业、实验状态与人工接管清单。
- 兼容 Claude Desktop、Cursor、VS Code MCP、豆包 Agent 等所有支持 MCP 的客户端。
- 提供无人值守 CLI 用于定时巡检；CLI 本身不会作答或提交。

## 架构

```
MCP 客户端  ──stdio──▶  server.py（FastMCP 工具）
                              │
                          engine.py（编排层）
              ┌───────────────┼────────────────┐
       platform.py       operator.py        report/solver
       只读 REST 客户端    页面 UI 写入       报告渲染 + 答案规范化
              └───────────────┼────────────────┘
                          browser.py（Playwright 会话）
```

模块职责见 [`docs/architecture.md`](docs/architecture.md)；接口、题型编码与状态口径见 [`docs/api-mapping.md`](docs/api-mapping.md)；接入其他平台见 [`docs/adapter-guide.md`](docs/adapter-guide.md)。

## 快速开始

### 1. 安装

```powershell
cd D:\u+小助手
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

### 2. 配置个人课程（已被 git 忽略）

```powershell
Copy-Item config\courses.example.json config\courses.json
```

在 `config/courses.json` 中填写你的真实 `courseId` 与本地路径。

### 3. 登录（完全由本人完成，插件不碰登录）

插件**不启动浏览器、不保存登录态**：它直接附加到你本人正在使用的 Edge 浏览器，在你已登录的真实会话里工作。

一次性准备（每次重启 Edge 后需要重做）：

1. 完全退出 Edge（任务栏图标右键退出，确保无后台进程）；
2. 用带调试端口的方式启动 Edge：
   ```powershell
   & "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9333
   ```
3. 在这个 Edge 窗口里正常打开并登录 `eduplus.net`。

之后所有巡检、作答都会直接在这个浏览器里的 U+ 标签页上进行——因为用的就是你本人的真实会话，U+ 的网页跳转/弹回问题不会再出现。

### 4. 在 MCP 客户端中注册服务

Claude Desktop、Cursor、VS Code 配置示例见 [`docs/mcp-clients.md`](docs/mcp-clients.md)。最小 stdio 注册：

```json
{
  "mcpServers": {
    "uplus": {
      "command": "uv",
      "args": ["--directory", "C:\\Users\\you\\projects\\uplus-mcp", "run", "python", "-m", "uplus_mcp.server"],
      "env": { "UPLUS_CONFIG": "config/courses.json" }
    }
  }
}
```

## 工具清单

| 工具 | 作用 |
| --- | --- |
| `uplus_check_login` | 校验浏览器中的 U+ 登录态是否有效 |
| `uplus_list_courses` | 列出当前账号课程 |
| `uplus_list_homeworks` | 列出作业状态；可选打开作答页以页面按钮为准复核 |
| `uplus_list_experiments` | 列出实验与提交状态 |
| `uplus_inspect_homework` | 返回作业状态与题目审题包，供客户端模型作答 |
| `uplus_answer_homework` | 填写客观题，可选提交 |
| `uplus_upload_attachment` | 向附件题上传本地文件 |
| `uplus_patrol` | 全量巡检，生成报告、待答审题包与接管清单 |
| `uplus_close` | 释放本进程持有的浏览器会话 |

## 每晚定时巡检（无人值守）

```powershell
.venv\Scripts\python.exe -m uplus_mcp.daily_patrol
```

CLI 会生成 `reports/patrol_YYYYMMDD_HHMM.md` 报告与待答客观题 `.brief.json`。退出码 `3` 表示未检测到带调试端口的 Edge，`2` 表示登录态过期，`1` 表示运行时错误。

> 前提：Edge 正以调试端口运行且已登录 U+（见第 3 步）；若报告登录过期，在浏览器里重新登录即可。

## 配置项说明

`config/courses.json`：

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `base_url` | `https://www.eduplus.net` | 平台根地址 |
| `nav_timeout_ms` | `30000` | 页面导航超时 |
| `action_timeout_ms` | `15000` | UI 操作超时 |
| `deadline_warning_hours` | `72` | 截止时间预警窗口（小时） |
| `report_dir` | `reports` | 报告输出目录 |
| `courses` | 内置兜底清单 | 每门课的 `id` / `name` / `teacher` |

## 开发

```powershell
pip install -e ".[dev]"
pytest
```

- 模型、配置、答案规范化等纯逻辑由单元测试覆盖。
- 浏览器与平台交互在只读巡检模式下使用真实会话验证；写入操作必须在授权账号与课程截止期内进行。

## 目录结构

```
src/uplus_mcp/
  browser.py      CDP 附加到用户已登录的 Edge（不启动浏览器）
  platform.py     只读 REST 客户端
  operator.py     页面 UI 操作
  engine.py       共享编排层
  models.py       领域对象与枚举
  config.py       配置加载
  solver.py        审题包生成与答案规范化
  report.py       巡检报告聚合与 Markdown 渲染
  utils.py        HTML 清洗、时间与答案规范化工具
  server.py       FastMCP 工具注册
  daily_patrol.py 巡检 CLI
docs/             架构、接口映射、客户端配置、适配器指南
config/           个人配置示例（真实文件已忽略）
reports/          巡检报告（已忽略）
tests/            单元测试
```

## 安全与隐私

- 插件不保存、不传输任何账号凭证；登录完全由本人在自己的 Edge 里完成。
- `config/courses.json`、`reports/` 保持本地，已列入 `.gitignore`。
- 不要提交学号、姓名、手机号、邮箱、Cookie 或令牌。

## 许可证

MIT，见 [`LICENSE`](LICENSE)。
