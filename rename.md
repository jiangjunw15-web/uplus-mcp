# rename.md — AI 助手分步引导手册（接入本插件后必读）

> 本文件是给**接入本 MCP 插件的 AI 助手**看的操作剧本，新用户也可以照做。
>
> **这个插件是什么**：一个面向青软 U+ 学习平台的 MCP 服务，通过 CDP 附加到你本人已登录的
> Edge / Chrome（不另起无头内核，也不保存密码与 Cookie）。它在真实页面里完成四类工作：
>
> 1. **巡检**：扫描课程、作业与实验并生成 Markdown 报告；已提交作业默认核查漏答 / 多选漏选，
>    已提交实验核查空报告 / 残留占位符 / 错交实验。
> 2. **客观题作答**：单选、多选、判断、填空整理成审题包交给模型；模型给出答案后，在真实作答页
>    点选、回读核对并提交。带图的题可以先截取题目图片给模型看。
> 3. **做实验**：读取实验提交物 / 实验手册 / 实验附件；详情页找不到要求时，去课程「资料」板块
>    （包括递归进入子文件夹）找实验材料与「实验报告模板」并下载；服务端只引用手册与附件原文，
>    实验实现 / 结果分析 / 总结由模型产出，通过空报告 / 残留占位符 / 错交实验三道校验后才提交。
> 4. **页面兜底**：平台接口变化或某一步没有 API 时，用通用 `browse_*` 工具直接打开网页、
>    读快照、点击、填表、上传、截图。
>
> **目标**：在任意 AI agent（Claude Desktop、Cursor、VS Code MCP、DSH、Doubao 等）上，
> 让 AI 按下面的步骤把用户引导到「登录 → 巡检 → 客观题作答 / 实验报告 → 人工接管清单」闭环，
> 全程不写死任何一台机器的路径、账号、学校或课程名。
>
> 约定：
> - `<安装目录>` = 用户存放本项目、里面有 `pyproject.toml` 的那个文件夹（下面用占位符表示）。
> - 命令分三种环境：**Windows PowerShell**、**macOS Terminal**、**Linux**；AI 先判断用户系统再给对应命令，不要混发。
> - 工具名以插件实际注册名为准：U+ 业务族 `uplus_*`，通用浏览器族 `browse_*`。
> - 客观题可自动作答；**简答、编程、附件、小组成果物一律交回用户本人**，AI 不得编造答案或伪造上传。实验报告可用实验流水线生成：服务端只引用实验手册原文，实现与分析由模型产出，且必须通过空报告 / 占位符 / 错交实验三道校验才会提交。
> - 本手册基于 **0.8.0** 编写；0.7.0 起插件收紧了导航与文件路径边界（见「安全边界」），0.7.4 新增提交完整性核查（见「0.7.4 更新了什么」），0.7.5 修掉作答点错题（见「0.7.5 更新了什么」），0.7.6 修掉多选漏选与跳题（见「0.7.6 更新了什么」），0.7.7 修掉 0.7.6 引入的三处缺陷（见「0.7.7 更新了什么」），0.7.8 把已提交作业纳入默认巡检并支持撤回补交（见「0.7.8 更新了什么」），0.8.0 新增做实验流水线（见「0.8.0 更新了什么」）。

---

## 0.8.0 更新了什么（做实验：资料检索 → 模板填充 → 巡检校验 → 提交）

- **实验流水线**：`uplus_inspect_experiment(course_id, exp_id, stu_exp_id)` 读取实验提交物 / 实验手册 / 实验附件；详情页找不到要求时，自动去课程「资料」板块找，**包括递归进入子文件夹**，按「实验N / 实验报告模板」匹配并下载模板。返回的审题包区分 `auto_sections`（手册可推导、服务端原样引用）与 `needed_sections`（模型补写）。
- **`uplus_answer_experiment(course_id, exp_id, stu_exp_id, sections)`**：合并服务端章节与模型章节 → 填充下载的模板 → 三道校验（正文不足=空报告 / 残留占位符 / 报告主题与实验不符）→ 上传 → 提交 → 回读页面确认。缺任一 `needed_sections` 或校验不过即**拒绝提交**并列出原因。
- **`uplus_audit_experiment`**：只读核查已提交实验；能取回文件就回读检查，取不回就只核对页面并明确说明，绝不把未核查的报告当干净。
- **`uplus_download_experiment_materials`**：只下载实验附件与资料模板，不提交。
- **列表补 id**：`uplus_list_experiments` 现在返回 `exp_id` / `stu_exp_id`，可直接驱动上述工具。
- **姓名 / 学号**：在 gitignored 的 `config/courses.json` 配置 `student_name` / `student_id`；工具参数可按次覆盖；只写入本地报告 .docx。
- **巡检**：未提交实验产出 `experiment_briefs`（待写章节清单，不自动下载/提交）；已提交实验跑只读核查，报告新增「实验提交完整性核查」小节。

## 0.7.8 更新了什么（已提交作业默认核查 + 多选漏选 + 撤回补交）

| 变化 | 具体内容 | AI 该怎么用 |
| --- | --- | --- |
| **巡检默认核查已提交作业** | `uplus_patrol` 的 `audit` 默认 `true`；CLI 默认核查，`--no-audit` 才跳过 | 巡检结束后先看 `incomplete_homeworks` 与 `repair_briefs`，不要只看未提交作业 |
| **多选漏选判定** | 用平台标准答案 `answer` 对比实答：少选是 `partial`（带 `missing_letters`），多选/错选是 `wrong`；顺序不同不算错 | 看到 `partial` 就必须处理；`wrong` 也建议人工确认 |
| **待批改的答案来源** | 平台待批改时不给 `answer`，工具用本地答案簿 `reports/answer_journal.json` 对比；两者都没有时题目进入 `unjudged`，**不能当成已完成** | `unjudged` 不是「没问题」，而是「平台还没公布答案、本地也没有提交记录」；需要彻底排除漏选时走 `verify_unjudged` |
| **新增 `uplus_repair_homework`** | 撤回 → 补齐 → 重新提交一次完成；撤回前必须为所有已知漏答/漏选题目准备好答案，否则不撤回；已出分拒绝 | `repair_briefs` 里 `needs_model_answer: true` 的题目先作答，把 `{question_id: answer}` 传给 `uplus_repair_homework`；`journal_answer` 已给的不用再答 |
| **新答案会校验** | 若新答案仍缺标准答案中的选项，工具拒绝撤回并指出缺哪些字母 | 按返回的「仍缺 …」补齐后重试，不要反复提交不完整答案 |
| **可选逐题复核** | `verify_unjudged=True` 会把待批改中无法核对的多选也重新作答并提交 | 用户要求「彻底排除漏选」时使用；会撤回并重新提交，先向用户说明 |
| **代码块选项** | `A、<property>`、`C、<constructor-arg>` 这类 `<code>` 选项能正常读取，也能按 input value 唯一点击 | 无需特殊处理；审题包里会显示完整文本 |
| **测试** | 新增 24 个用例；全量 250 通过 | — |

**每轮巡检后的标准动作（AI 必须做）**：

1. 调 `uplus_patrol()`（默认已带核查），读 `incomplete_homeworks` 与 `repair_briefs`；
2. 对 `repair_briefs` 中 `withdrawable: true` 的作业，把 `needs_model_answer: true` 且 `optional` 不为 `true` 的题目逐题作答（`optional` 是待批改、平台尚未公布答案的多选；用户要求彻底排除漏选时再作答并传 `verify_unjudged=True`）；
3. 调 `uplus_repair_homework(task_id, course_id, answers)`；工具只在所有已知漏答/漏选都有答案时才撤回，随后补齐并重新提交；
4. 检查返回的 `audit_after.complete`；若仍有 `unjudged`，说明平台还没出分，批改后再复核；
5. 已出分（`withdrawable: false`）的作业不要尝试撤回，把漏题清单交给用户。

---

## 0.7.7 更新了什么（0.7.6 加固 + 图形题）

对 0.7.6 的复查发现三处真实缺陷，并新增图形题截图能力；既有工具签名与调用方式不变：

| 变化 | 具体内容 | AI 该怎么用 |
| --- | --- | --- |
| **新增 `uplus_capture_question_image`** | 平台把示意图放在题干富文本的 `<img>` 里，`strip_html` 会丢掉标签，模型看不到图。新工具按题干定位题目后截取整道题的容器，以 MCP 图片内容返回，同时给出 PNG 路径；不填写、不提交 | 审题包某题带 `images`/`figure_note` 时，先 `uplus_capture_question_image(task_id, course_id, order=题号)` 看图再作答；图形判断题**不得盲猜** |
| **题干定位失败的列表页不再误读邻题** | 0.7.6 的多选页面核对在题干定位不到时会退回整页，而整页有每道题的勾选框，核对时会把别人的选项点掉。现在明确报「无法核对」并退回按接口答案增量处理 | 无需操作；单题视图行为不变 |
| **`userAnswer` 非字符串不再崩** | 该值来自 JSON，可能是数字/数组/布尔；旧代码直接 `.upper()` 会在填到一半时抛异常 | 无需操作 |
| **实验名兜底收紧** | `实验要求：…`、`提交物：…` 这类句子不再被当成实验名；只认短且不含 `：`/`:` 的标题行 | 巡检报告里的实验名比旧版更准 |
| **测试** | 新增 29 个回归用例（图形题 18 + 加固 11）；全量 226 通过 | — |

---

## 0.7.6 更新了什么（多选漏选与跳题修复）

本次只修作答正确性，工具签名不变：

| 变化 | 具体内容 | AI 该怎么用 |
| --- | --- | --- |
| **多选按页面勾选状态核对** | 旧逻辑按「模型答案 − 接口已答」增量点击：页面已勾但接口不知道的选项会被**点掉**，造成漏选。现在先读页面真实勾选，只补缺的、只取消多的，点完回读校验；仍不一致就报错并阻止自动提交 | 照旧调用 `uplus_answer_homework`。若返回里出现「选项未能全部选中」，**不要 force_submit**，把错误交给用户或人工核对 |
| **已答多选会复核** | 平台标记为已答的多选，只要模型答案不同就会重新核对/补齐；没给模型答案的已答多选按「无法核对」计入未完成，阻止自动提交 | 调用前尽量为每道客观题都给出答案（`inspect_homework` 的 brief 含全部题目，包括已答的） |
| **跳转验题干** | 点答题卡题号后回读当前题干，与目标不一致就跳过并报「跳转后题干校验不一致」，绝不写错题。位置 1 不再提前返回（该缺陷会把答案写到最后一题上） | 看到该错误说明页面题序/题干异常，交用户人工确认，不要反复重试 |
| **重复题干可区分** | 题干完全相同的两道题不再互相覆盖位置映射，各自可达 | — |
| **列表视图按题定位** | 一次显示全部题目的页面上，选项/判断/填空只在当前题目的容器内操作，不再总命中第一题 | — |
| **已答只认 `userAnswer`** | `hwAnswerId` / `isAnswer` 在仅打开题目时也会生成，旧口径导致审计把空题当已答（漏题）。审计、巡检答案包、作答循环现在都只认非空 `userAnswer` | 审计结果比旧版更严格，属于修正；看到「漏题」就按下方顺序处理 |
| **答案解析** | 模型答案夹带说明文字时只取开头选项串，并按该题实际选项过滤；空的选择题答案直接报错 | 仍建议按 brief 要求只输出字母组合，减少歧义 |
| **测试** | 新增 `tests/test_answer_fixes.py` 等回归用例；全量 195 通过 | — |

**作答后的自查（AI 必须做）**：提交后跑一次 `uplus_audit_homework`，确认「漏题 0」且状态为 `submitted_pending`/`submitted_graded`，不要只看工具返回的 `submitted: true`。

---

## 0.7.5 更新了什么（作答定位修复）

两个缺陷都在真实随堂测验上复现并已修复，工具签名没有变化：

| 变化 | 具体内容 | AI 该怎么用 |
| --- | --- | --- |
| **按题干定位，不按页面题号** | 单题视图（有「答题卡」「上一题/下一题」的页面）上，页面题序与接口 `order` 不一致；过去按接口题号点会点到别的题上，导致答案错位 | 直接照旧调用 `uplus_answer_homework`；工具内部会先建「题干 → 页面位置」映射。若某题在页面找不到对应题干，结果里会出现「找不到对应题干，已跳过」，**不要重试硬点**，交用户确认 |
| **选项两种排版都能点** | 部分测验把字母单独放在一个 span（`A` + `开闭原则`），没有「A、」文本，旧定位器必然超时，那些题根本答不了 | 无需额外操作；工具会先按文本匹配，再退回 `label:has(input[value="A"])` |
| **测试** | 新增 `tests/test_stem_position.py`，覆盖题干归一化、页面题序错位、最长前缀取舍、找不到题干；全量 179（178 通过 / 1 跳过） | — |

**作答后的自查（AI 必须做）**：提交后跑一次 `uplus_audit_homework`，确认「漏题 0」且状态为 `submitted_pending`/`submitted_graded`，不要只看工具返回的 `submitted: true`。

---

## 0.7.4 更新了什么（本版新增能力）

本次发布只增加「已交作业的漏题核查」，既有工具的契约一个没改。

| 变化 | 具体内容 | AI 该怎么用 |
| --- | --- | --- |
| **新工具 `uplus_audit_homework`** | 入参 `task_id`、`course_id`，返回该作业的题量、客观题 / 主观题数量、已答数量、漏题清单（题号 + 题型 + 题干前 80 字）以及 `withdrawable` | 用户问「这份作业有没有漏题」时直接调用；**它不撤回、不提交、不改动答卷**，待批改和已出分的作业都能安全跑 |
| **巡检带核查** | `uplus_patrol(audit=True)`、CLI `uplus-patrol --audit` | 做全量体检时打开这个开关；漏题的作业会自动进入报告与待接管清单 |
| **报告新章节「提交完整性核查」** | 课程 / 作业 / 漏答题数 / 题号 / 可否撤回 | 巡检报告里直接能看到哪份已交作业还差题，不用逐份点开 |
| **版本号对齐** | `__version__` 之前停在 0.7.0（0.7.1–0.7.3 只改了 `pyproject.toml`），现统一为 0.7.4 | 核对版本时以 `uplus_mcp.__version__` 为准 |
| **判定以页面为准** | `withdrawable` 来自作答页真实按钮状态（接口对已交作业仍可能返回「未提交」）；页面读失败才退回接口状态，并在 `page_state_note` 里说明 | 不要相信 `api_status` 字段，只看 `status` 与 `withdrawable`；两者不一致说明接口低估了，属正常 |
| **测试** | 新增 `tests/test_audit.py` 9 个用例，全量 173 通过 / 1 跳过（共 174） | — |

**判定口径（AI 必须知道）**：一题算「已答」当且仅当学生端 detail 的 `userAnswer` 非空（与 `models._is_answered()` 同一口径）。0.7.6 起 `hwAnswerId` / `isAnswer` 不再单独作为已答依据——它们会在仅打开题目时生成，旧口径会漏报空题。**简答、编程、附件题单独统计，不算漏题**，也不会让作业被判成不完整——它们本来就该由用户本人完成。

**发现漏题后的正确顺序**（0.7.8 起优先用 `uplus_repair_homework`，它会先撤回再补齐并重新提交）：
1. `uplus_patrol()` 或 `uplus_audit_homework` 拿到漏答/漏选清单；
2. `withdrawable` 为 `true` 时，按 `repair_briefs` 作答，调 `uplus_repair_homework(task_id, course_id, answers)`；工具只在所有已知缺口都有答案时才撤回；
3. `withdrawable` 为 `false`（已出分）时，**不要尝试撤回**，把清单交给用户，说明平台已不允许改；
4. 补完后检查 `audit_after.complete`，确认漏答/漏选为 0；仍有 `unjudged` 说明平台尚未出分，批改后再复核。

---

---

## 0.7.0 起的安全边界（AI 必须知道，不要尝试绕过）

0.7.0 修掉了两类会让用户数据暴露的问题，以下行为现在会被插件**直接拒绝**。AI 不要把它们当成"需要重试的故障"，更不要尝试用别的写法绕过。

| AI 可能想做的事 | 插件的反应 | AI 该怎么做 |
| --- | --- | --- |
| 让浏览器打开 `file:///…` 读本地文件（HTML、密钥、配置） | 报错「拒绝导航到 file:// 链接：只允许 http/https 网页」 | 只导航网页。要看本地文件内容，让用户直接贴给你 |
| 读或写 `allowed_io_paths` 之外的路径（如 `~/.ssh/`、系统目录） | 报错「路径 … 不在允许的目录内」 | 报告、截图、附件一律放在报告目录或临时目录；确有需要时让用户把目录加进 `allowed_io_paths` |
| 用 `../` 穿越出允许目录 | 报错同上（`..` 会被展开后再判定） | 不要拼相对路径去猜文件位置 |

默认允许目录：**报告目录（`reports/`）与系统临时目录**。`uplus_generate_experiment_report` → `uplus_submit_experiment_report` 这条既有流程开箱可用，无需额外配置。

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
| 6+ 完整性核查 | `uplus_patrol()`（默认核查）或 `uplus_audit_homework` | 已交作业里没有漏答/多选漏选；`unjudged` 单独看待 |
| 6++ 撤回补交 | 按 `repair_briefs` 作答 → `uplus_repair_homework` | 返回 `withdrawn: true`、`submitted: true`、`audit_after.complete: true` |
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
3. `uplus_patrol`：全量巡检作业与实验，生成 Markdown 报告，返回统计摘要、待答审题包、`repair_briefs` 与待接管清单。
   - 本工具**默认不答题、不提交**；不要在未经用户确认时开启自动提交。
   - **已提交作业默认纳入核查**（`audit=true`，CLI 默认核查、`--no-audit` 才跳过）：报告会列出漏答与多选漏选。
   - 对 `repair_briefs` 中可撤回的作业：先回答非 optional 且 `needs_model_answer` 的题目，再调 `uplus_repair_homework` 完成撤回、补齐、重新提交；已出分作业不得撤回。

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
   - 某题带 `images` / `figure_note` 时说明**文字不足以作答**（示意图、流程图、表格截图等）：先调用 `uplus_capture_question_image(task_id, course_id, order=题号)`，工具会把整道题的截图以图片形式返回；看清图后再作答。**不要在没有看图的情况下猜图形判断题。** 题干提到「图 / 模型 / 流程 / 下图」但正文没有对应内容时，同样先截图确认；图形不在题目容器内时加 `full_page=True`。
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
- **简答、编程、附件题、小组 PPT 不进入本步**；AI 不得为这些题编造答案。
- 实验报告走独立流水线：`uplus_inspect_experiment` → 模型补写 needed_sections → `uplus_answer_experiment`。只填手册可推导的章节；缺章节或校验不过**不得提交**，也不得绕过闸门强行提交。

### 失败回退

- 选项乱序 / 题号错位：重新 `uplus_inspect_homework` 取最新审题包；页面被导航打断时用 `browse_open` 回到作答页再续做。

---

## 第 7.5 步：实验报告闭环

### AI 动作

1. `uplus_list_experiments(course_id)` 取实验，记下 `exp_id` / `stu_exp_id` 与截止时间。
2. `uplus_inspect_experiment(course_id, exp_id, stu_exp_id)`。它会读取手册与附件，并在「资料」里递归找模板。若 `template_error` 非空或 `template` 为空，说明没找到模板——如实告诉用户，仍可走内置格式。
3. 按 `needed_sections`（通常是实验实现 / 实验结果 / 实验总结）逐节写出内容，只依据手册要求与附件，**不编造与实验无关的内容**。`auto_sections` 由服务端原样填入，不用重写。
4. 返回 `{"sections": {"实验实现": "...", "实验结果": "...", "实验总结": "..."}}`，调用 `uplus_answer_experiment(...)`。第一次建议 `submit=False`，把 `report_path` 指给用户过目。
5. 用户确认后以 `submit=True` 调用；工具会在校验通过后上传并提交，然后回读页面确认。

### 成功信号

- `generated=true`、`can_submit=true`、`verification.ok=true`；`submit=True` 时 `submitted=true` 且 `submission.confirmed=true`。

### 强制边界

- `missing_sections` 非空或 `verification.ok=false` 时**不得提交**；按返回的 `reason` 修正后重试，禁止绕过闸门。
- 报告主题与实验不符（交错实验）会被拒绝；不要尝试通过改文件名或替换实验来绕过。
- 姓名 / 学号来自 gitignored 配置或工具参数，不得写进仓库或巡检报告。

### 失败回退

- 资料板块找不到入口或模板：先用 `browse_snapshot` 看课程左侧栏，确认「资料」入口名；仍找不到就请用户手动提供模板路径，用 `uplus_answer_experiment(..., template_path=...)` 指定。
- 已提交文件无法取回：`uplus_audit_experiment` 会明确说明，只有页面信息可核对。

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
| 实验报告 | 可用 `uplus_inspect_experiment` → `uplus_answer_experiment` 生成，并通过空报告 / 占位符 / 错交实验三道校验后提交；用户仍应核对内容属实 | 列出材料要求、提交入口与截止时间；模板或资料缺失时请用户确认 |
| 小组 PPT | 需本人原创与协作成果 | 列出材料要求、提交入口与截止时间 |
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
| `allowed_io_paths` | 报告目录 + 系统临时目录 | 文件类工具（报告输出、截图、附件、实验上传/下载）允许读写的目录白名单，可填多项 |
| `student_name` | 空 | 实验报告抬头姓名；只在本机 gitignored 配置里填写，工具参数可按次覆盖 |
| `student_id` | 空 | 实验报告抬头学号；同上 |
| `courses` | 空（在线拉取） | 手动列出 `id` / `name` / `teacher` |

环境变量：`UPLUS_CONFIG`（配置文件路径）、`UPLUS_BROWSER`、`UPLUS_CDP_PORT`、`UPLUS_PROFILE_DIR`、`UPLUS_ALLOWED_PATHS`（目录白名单，多项用系统路径分隔符 `;`（Windows）/ `:`（macOS、Linux）分隔）。

> 0.7.0 新增 `allowed_io_paths` / `UPLUS_ALLOWED_PATHS`：只影响**文件读写**，不限制网页导航。默认值已覆盖「生成报告 → 提交」流程，通常无需配置。

---

## 学术诚信与隐私（AI 必须主动遵守并在首次使用时提示用户）

- 本插件是个人学习提醒与题目梳理助手：用于核对题目、跟踪截止时间、整理作业清单。
- 实验报告可由本工具依据实验手册与附件生成并通过校验后提交；用户须自行核对内容属实、符合任课教师要求，并遵守学校诚信规定。小组作品、编程作业、开放简答必须由本人完成；考试、闭卷测验及任何禁止辅助的考核场景不得使用。
- 服务端不内置题库；客观题答案由接入的 AI 模型生成，提交前用户应自行复核。
- 密码、Cookie、token、学号、姓名、课程 id 等敏感信息不写入仓库、巡检报告或公开配置。姓名 / 学号只写入 gitignored 的 `config/courses.json` 与本地生成的报告 `.docx`（`*.docx` 已 gitignore）。
