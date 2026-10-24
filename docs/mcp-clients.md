# MCP 客户端接入配置

本服务以 stdio 方式运行，兼容所有支持 Model Context Protocol 的桌面客户端。首次使用前先完成「安装依赖 → 登录保存会话 → 配置客户端」三步。

## 0. 准备工作

```powershell
# 进入仓库目录
cd D:\u+小助手

# 创建虚拟环境（推荐）
python -m venv .venv
.venv\Scripts\Activate.ps1

# 安装依赖与浏览器内核
pip install -e .
playwright install chromium
```

复制个人配置（gitignored，不会进入仓库）：

```powershell
Copy-Item config\courses.example.json config\courses.json
```

首次登录（弹出可见浏览器，用学号登录后自动保存会话）：

```powershell
python scripts/login.py
```

> `storage_state.json` 与 `config/courses.json` 均已在 `.gitignore` 中，不会被提交到 GitHub。

## 1. 工具清单

| 工具 | 作用 |
| --- | --- |
| `uplus_check_login` | 检查登录态是否有效 |
| `uplus_list_courses` | 列出账号下课程 |
| `uplus_list_homeworks` | 列出课程作业与状态；`refresh_page_state=true` 时打开页面复核按钮文案 |
| `uplus_list_experiments` | 列出课程实验与提交状态 |
| `uplus_inspect_homework` | 读取作业状态与题目审题包，供客户端模型作答 |
| `uplus_answer_homework` | 按 `{question_id: answer}` 填写客观题；题目未答完或出错时自动暂缓提交，`force_submit=true` 可人工强制提交 |
| `uplus_upload_attachment` | 上传附件到指定作业 |
| `uplus_patrol` | 全量巡检并生成 Markdown 报告，返回待答审题包与待接管清单 |
| `uplus_close` | 关闭本进程浏览器会话 |

## 2. Claude Desktop

编辑配置文件：

- Windows：`%APPDATA%\Claude\claude_desktop_config.json`
- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "uplus": {
      "command": "uv",
      "args": [
        "--directory", "C:\\Users\\you\\projects\\uplus-mcp",
        "run", "python", "-m", "uplus_mcp.server"
      ],
      "env": {
        "UPLUS_CONFIG": "config/courses.json"
      }
    }
  }
}
```

已用 `pip install -e .` 安装时，可简化为：

```json
{
  "mcpServers": {
    "uplus": {
      "command": "uplus-mcp",
      "env": { "UPLUS_CONFIG": "C:\\Users\\you\\projects\\uplus-mcp\\config\\courses.json" }
    }
  }
}
```

重启 Claude Desktop 后，在对话中让它「巡检 U+ 未完成作业」即可触发 `uplus_patrol`。

## 3. Cursor / VS Code（MCP 插件）

`.cursor/mcp.json`（Cursor）或 VS Code MCP 配置：

```json
{
  "mcpServers": {
    "uplus": {
      "command": "uv",
      "args": [
        "--directory", "C:\\Users\\you\\projects\\uplus-mcp",
        "run", "python", "-m", "uplus_mcp.server"
      ],
      "env": { "UPLUS_CONFIG": "config/courses.json" }
    }
  }
}
```

## 4. 其他 MCP 兼容客户端

按 stdio 方式注册同一命令即可：

- `command`：`uv` 或解释器绝对路径，例如 `D:\u+小助手\.venv\Scripts\python.exe`
- `args`：`["-m", "uplus_mcp.server"]`（安装后也可直接调用入口 `uplus-mcp`）
- `env.UPLUS_CONFIG`：可选，指向个人课程配置文件

## 5. 定时巡检（不依赖 MCP 对话）

CLI 可直接接入 Windows 任务计划程序或系统 cron：

```powershell
# 每晚 21:30 巡检，报告写入 reports/
.venv\Scripts\python.exe -m uplus_mcp.daily_patrol
```

任务计划程序操作：

1. 创建基本任务，触发器「每天」`21:30`。
2. 操作「启动程序」：程序 `.venv\Scripts\python.exe`，参数 `-m uplus_mcp.daily_patrol`，起始于仓库目录。
3. 登录态过期时退出码为 `2`，可在脚本外层记录告警。

> CLI 只巡检、出报告、导出待答审题包，**不会自动提交作业**；自动提交由 MCP 会话在模型给出答案后执行。

## 6. 常见问题

**Q: 启动后提示登录态已过期？**
A: 重新运行 `python scripts/login.py`，在弹出窗口登录并等待会话保存完成。

**Q: 工具能看到课程但作业列表为空？**
A: 老师尚未发布作业时列表即为空，属于正常状态；「AI 练习」是自主练习工具，不计入必交作业。

**Q: 为什么有些题被归到「需要人工接管」？**
A: 简答、编程、实验报告、小组 PPT 附件题需要本人原创内容或本地文件，服务端不会编造答案或上传伪造文件。

**Q: 浏览器窗口是否可以后台运行？**
A: `config/courses.json` 中 `headless: true` 为无头模式；调试时改为 `false`，或 CLI 加 `--no-headless`。
