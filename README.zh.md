# uplus-mcp

[![GitHub release](https://img.shields.io/github/v/release/jiangjunw15-web/uplus-mcp)](https://github.com/jiangjunw15-web/uplus-mcp/releases)
[![Release downloads](https://img.shields.io/github/downloads/jiangjunw15-web/uplus-mcp/total)](https://github.com/jiangjunw15-web/uplus-mcp/releases)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/github/license/jiangjunw15-web/uplus-mcp)](LICENSE)

> 暂未发布到 PyPI：在 `uplus-mcp` 上架 PyPI 之前，请通过 GitHub Release 附件 wheel 安装，或克隆源码安装（见「快速开始」）。

一个面向青软 U+ 学习平台的 MCP 服务，同时内置通用浏览器驾驶（browse）工具：既能自动巡检课程、作业与实验，把客观题整理成审题包交给模型作答并在真实作答页填写提交；也能按实验手册与课程资料生成实验报告、填充下载的模板，并在通过「空报告 / 残留占位符 / 错交实验」三道校验后提交；还能让 AI 直接打开网页、读快照、点击、填表、截图——平台接口变了或流程没有 API 时，模型可以亲自操作页面兜底。简答题、编程题与附件上传仍交回本人处理。

> **学术诚信声明**：本工具仅作为个人学习提醒与题目梳理助手。**实验报告**可以用 `uplus_inspect_experiment` → `uplus_answer_experiment` 生成：服务端只引用实验手册与附件的原文，实验实现、结果分析与总结由模型产出，并在空报告、残留占位符、报告与实验不符三道校验通过前不会提交；请自行核对生成内容是否属实。小组作品、编程作业与开放题必须由本人完成。考试、闭卷测验或任何禁止辅助的考核场景下不得使用本工具。

> **新用户 / AI 助手先读 [rename.md](rename.md)**：那是一份分步引导剧本，写清了从环境检查、安装、注册客户端、两种登录方式、巡检、客观题作答到人工接管的每一步该调用什么工具、向用户说什么、成功信号与失败回退。

## 0.8.2 版本更新（登录、校验与页面加载的四处修复）

0.8.0 的合规核查真正在平台上跑起来后，有四处在实际使用中会卡住，本版逐一修掉。全量 **321 个测试通过**。

- **手机号账号可以自动登录了**：学校登录页默认停在「学号/工号登录」tab，账号框要求「请输入学号/工号」且必须选学校，手机号账号填在这里必然失败。`auto_login` 现在先切到「密码登录」tab（按 `input[placeholder*='手机']` 判断是否已在该 tab），切过去后不再要求选学校；找不到该 tab 的旧登录页仍按原来的单表单流程走。
- **绘图行不再被判成占位符**：`is_placeholder_text` 原来会把整行 `.` / `#` 的栅格地图、状态空间图判成「未填写的占位」而拒收报告。现在整行只由 `. # * · @ +` 组成的绘图行按内容处理，填空线（`____` / `---`）、`（此处填写…）`、`待填写`、`TODO` 仍判为占位。
- **重复的子标题不再截断章节**：正文写「一、算法伪代码」时会命中章节别名「实验实现」，原先把重复出现当成新章节边界，导致上一章节正文被切掉并判空。现在 `_missing_sections` 只把每个章节的首次出现当作标题，重复的别名按正文吸收。
- **实验列表页 / 详情页加载超时会重试**：部分课程的 `ExperimentPage` 首屏 30 秒内渲染不完，`goto` 超时后整门课列不出实验，看起来像「这门课没有实验」。`list_experiment_ids` 与 `open_detail` 统一走 `_open_page`（45 秒超时 + 重试一次），两次都失败才报错并说明原因；`BrowserSession.open` 也支持 `timeout_ms` 参数。
- **测试**：新增 `tests/test_optimizations.py` 11 个用例，全量 321 通过、1 跳过。

## 0.8.0 版本更新（做实验 + 提交前的合规核查）

完整的「做实验」流水线，加上一份编号固定的 19 项合规核查清单：硬伤在提交前就拦下，并修掉"文件刚传上去就报提交成功"的假成功。全量 **310 个测试通过**。

### 合规核查与「假提交」修复

0.8.0 的实验流水线能判断"文档里有没有内容"，判断不了"交上去老师会不会挑毛病"。这一部分新增一份编号固定的 19 项合规核查清单，硬伤在提交前就拦下；同时修掉"文件刚传上去就报提交成功"的假成功。

- **新增模块 `compliance.py`（合规核查清单）**：结果分 `block` / `warn` / `manual` 三级——教师模板封面字段（学校/学院、专业、班级、学号、姓名、辅导教师）、实验时间与学时、「实验名称」字段、章节结构与模板一致、手册要求逐条覆盖、手册要求截图时是否有截图、环境声明有无界面证据、结果可复核（命令/种子/路径/参数）、AI 味与元叙述、章节重复注水、表格与超长行排版、哈希文件名。无法自动判定的 6 项显式列入 `skipped`，不假装通过。
- **校验闸门变严**：`verify_report` 新增 `strict_compliance`（默认 `True`），出现硬伤即拒绝上传，理由带编号与具体缺项；`answer_experiment` / `uplus_answer_experiment` 新增 `force` 参数，可把核查降级为警告。
- **已提交实验不再重复提交**：`answer_experiment` 读到「已提交」状态时只生成报告、不提交，并提示先撤回再重交。
- **修掉假提交**：`upload_report` 只点真正的「提交」按钮（此前按 `div` 文本点击会空点），点后处理确认弹窗并**轮询页面**直到「已提交」+「撤回」出现，15 秒未出现即抛错；`submit_report` 的确认条件收窄为「已提交」且（「撤回」或提交清单里出现该文件），不再把上传后的"转换成功"提示当成提交成功。
- **模板检索覆盖三处来源**：实验附件 → 实验手册（新增 `manual_links` 抓取）→ 资料递归遍历，优先取可下载文件并回报 `source`。
- **测试**：新增 `tests/test_compliance.py` 11 个用例与严格/非严格两种校验用例，全量 310 通过。

### 实验流水线（资料检索 → 模板填充 → 巡检校验 → 提交）

从实验详情页与课程「资料」板块自动找要求、递归进文件夹找报告模板、下载材料、填充模板，并在校验与合规核查通过后才提交。

- **新增模块 `materials.py`（课程「资料」检索）**：`资料` 板块的路由没有固定契约，因此不硬编码——打开课程主页后在左侧栏按「资料 / 资源 / 素材 / 文件」文案找入口，读取当前层文件与文件夹，**递归进入子文件夹**（按 `(名称, 链接)` 去重、可设深度上限），按「实验报告模板 / 报告模板 / 实验N」匹配并下载。
- **新增二进制下载能力**（此前只有上传，完全没有下载）：`BrowserSession.fetch_bytes` 先走页内 `fetch` + `x-access-token`（同源附件），失败退回 `context.request.get`（共享 Cookie、不受 CORS 限制，可下跨域 OSS 签名链接）；无 href 的条目退回 `expect_download` 点击捕获。所有落盘路径都经 `allowed_io_paths` 收敛，下载目录为 `reports/experiment_materials/`。
- **新增模板填充**：`report_generator.fill_template` 打开**下载到的真实 .docx 模板**，按标题定位到其后的空段落 / 空表格单元格填入，识别并替换「（此处填写…）」「待填写」「TODO」「____」等占位行；`describe_template` 先回报模板结构，`derive_sections` 从实验手册里原样抽取可引用的章节。无模板时退回内置报告格式，再走同一套填充逻辑。
- **新增四个 MCP 工具**：
  - `uplus_download_experiment_materials`：下载实验附件 + 资料里的报告模板；
  - `uplus_inspect_experiment`：审题包，区分 `auto_sections`（服务端原样引用手册）与 `needed_sections`（模型补写）；
  - `uplus_answer_experiment`：填充 → 校验 → 上传 → 提交 → 回读确认；
  - `uplus_audit_experiment`：对已提交实验做只读核查。
- **和作业同构的巡检闸门（防止交空的 / 交错的）**：`verify_report` 在提交前拒绝三类报告——文件过小或正文有效内容不足（空报告）、仍残留占位符、报告主题与实验不符（交错实验）；`answer_experiment` 还要求每个必写章节都有内容，缺一节即拒绝生成/提交。提交后**回读页面**确认已生效，未生效即报错，绝不返回假成功。旧的 `uplus_submit_experiment_report` 现在也先过同一道校验。
- **巡检接入**：`uplus_patrol` 为未提交实验产出 `experiment_briefs`（待写章节清单，不自动下载/提交），对已提交实验跑只读核查；报告新增「实验提交完整性核查」小节。CLI 同步打印「待做实验」与已提交实验的问题。
- **补齐一处会让流程断掉的缺口**：`Experiment` 模型与实验列表抓取此前没有带上 `exp_id` / `stu_exp_id`，`uplus_list_experiments` 因此无法驱动任何后续工具；现在两者都会返回。
- **姓名 / 学号**：可在 gitignored 的 `config/courses.json` 配置 `student_name` / `student_id`，工具参数可按次覆盖；只写入本地的报告 .docx（已 gitignore），不进仓库、不进巡检 Markdown。
- **测试**：新增 `tests/test_lab.py` 42 个用例，覆盖资料条目解析、递归遍历与深度上限、两条下载路径与目录白名单、手册章节抽取、模板填充与残留占位符、提交闸门的三类拒绝、`answer_experiment` 缺章节/校验失败不提交、`audit_experiment` 只读判定，以及实验列表带上两个 id。

## 0.7.8 版本更新（已提交作业默认核查 + 多选漏选 + 撤回补交）

巡检不再只看「未提交」的作业：每次巡检都会顺带核查已提交作业，漏答与多选漏选都会被找出来，并能走「撤回 → 补齐 → 重新提交」的闭环。全量 **250 个测试通过**。

- **已提交作业默认纳入巡检**：`uplus_patrol` 的 `audit` 现在默认为 `true`，CLI `uplus-patrol` 默认核查，只有明确加 `--no-audit` 才跳过。报告新增「提交完整性核查」的「漏答 / 多选漏选」两列，并单列无法核对的多选题数量。
- **多选漏选真正能被判出来**：审计把 `userAnswer` 与平台标准答案 `answer` 做集合比较——只选了标准答案的一部分就是「漏选」，会给出漏掉的字母（例如标准答案 `ABC`、实答 `A` → 缺 `BC`）；多选了标准答案之外的选项记为错答。选项顺序不同不算错（`AB` 与 `BA` 等价）。
- **待批改作业也有答案来源**：平台只在出分后才返回 `answer`，待批改时学生端 detail 没有该字段、页面也只有「我的答案」。本版新增本地答案簿（`reports/answer_journal.json`，已被 gitignore）：工具每次成功提交都会记下当时填写的答案。之后核查待批改作业时，用答案簿判断多选是否漏选；两个来源都没有时，题目进入 `unjudged`（无法核对），**不会**再被静默当作完整。
- **新增 `uplus_repair_homework`**：把「撤回 → 补齐 → 重新提交」做成一个安全动作。只有待批改（页面显示「撤回作业」）的作业能撤回；已出分会直接拒绝。撤回前必须先为每一道已知漏答/漏选题目准备好答案（模型传入的 `answers` 优先，本地答案簿兜底），否则**不撤回**，原作业保持已提交状态。撤回后会保留并重新填入原有答案，因此即使平台清空草稿也能恢复；提交后自动再审计一次。
- **漏选修复会校验答案**：如果模型给的新答案仍缺少标准答案里的选项，工具拒绝撤回并说明缺哪些字母，避免「补交后还是漏选」。
- **可选的逐题复核**：传 `verify_unjudged=True` 时，连平台尚未公布答案的多选题也会重新作答一遍再提交，用于彻底排除漏选；不传时只修已知漏答/漏选。
- **代码块选项已确认可读可点**：像 `A、<property>`、`C、<constructor-arg>` 这类用 `<code>` 渲染的选项，接口解析后是完整文本，页面上也能通过 `label:has(input[value="C"])` 唯一定位；新增回归测试固定该行为，无需额外配置。
- **巡检结果新增 `repair_briefs`**：每份需要处理的作业会列出题目、当前答案、标准答案/答案簿答案以及是否需要模型作答；AI 按此作答后调用 `uplus_repair_homework` 即可完成撤回补交。CLI 会把该清单保存为 `*.repair.json`。
- **测试**：新增 24 个用例，覆盖多选漏选判定、答案簿、待批改无法核对、撤回补交、拒绝不完整新答案、代码块选项解析与页面定位、巡检默认核查与修复清单；全量 250 通过。

## 0.7.7 版本更新（0.7.6 加固 + 图形题）

对 0.7.6 的复查发现三处真实缺陷，并补上「图形题看得到图」的能力，文档化行为一个没改。全量 **226 个测试通过**。

- **图形判断题终于能「看到图」**：新增工具 `uplus_capture_question_image(task_id, course_id, order=…)`。平台把示意图放在题干的富文本 `<img>` 里，`strip_html` 会把标签整个丢掉——像「以下模型是非对称密码体制中的两种模型」这种判断题，模型只拿到文字、看不到图，只能瞎猜。现在题目详情会暴露 `images`，审题包带 `figure_note` 点名该工具；工具按题干定位到题目后，把**整道题的容器截图**以 MCP 图片内容返回给模型，并同时给出 PNG 保存路径。它只截图，不填写、不提交。
- **题干定位失败的列表页不再读别人的勾选框**：0.7.6 引入了多选「按页面真实状态核对」。列表视图的作用域来自题干，题干定位不到时会退回整页——而整页上有**每一道题**的勾选框。读它会把本题状态和邻题状态混在一起，核对时就会把别人的选项点掉。现在这种页面明确报「无法核对」，作答退回按接口答案增量处理，不会误伤邻题。（单题视图不受影响：整页只有一道题。）
- **`userAnswer` 可能是非字符串，不再让作答崩掉**：这个值来自 JSON，可能是数字、数组或布尔。`utils.normalize_letters` 直接对它调 `.upper()`，会在填到一半时抛 `AttributeError`。现在先做字符串化，无法识别时降级为「没有字母」。
- **实验名兜底不再把要求句当成标题**：`实验要求：掌握进程同步的基本方法`、`提交物：进程同步实验报告` 这类行此前会被当成实验名报出来。兜底现在要求「短行且不含 `：`/`:`」，只有真正的标题行才会命中。
- **新增 29 个测试**：`tests/test_figure_questions.py` 18 个覆盖图片识别、审题包提示、题目容器截图、路径白名单与「文字 + 图片」工具契约；`tests/test_answer_fixes.py` 11 个覆盖无法定位时拒绝读取、非字符串 `userAnswer` 路径、实验名兜底。

## 0.7.6 版本更新（多选题不再漏选，作答不再落到错题上）

一次以「作答正确性」为主题的修复发布：多选题按页面真实勾选状态核对，跳转后先验题干再落笔，漏题判定只认真实答案。每项修复都有回归测试，全量 195 通过。

- **多选按页面真实状态核对，不再按接口答案增量点击**：旧逻辑点的是「模型答案 − 接口已答」。如果页面上已经勾了接口不知道的选项，这一下会把它**点掉**；新开页面也可能漏掉本应补选的字母——这正是「多选漏选」的直接原因。现在先读页面实际勾选：只补缺的、只取消多的，点完回读校验；仍与目标不一致就报错，由提交闸门拒绝提交，不会再把不完整多选静默交上去。
- **已“作答”的多选题也会复核**：旧循环会跳过平台标记为已答的题，即使模型答案比已答的更多（漏选）。现在只要模型答案与已答不一致就重新核对/补齐；已答多选但没给模型答案时，按「无法核对」计入未完成并阻止自动提交。
- **跳转后先验题干再落笔**：答题卡题号点击可能落空。现在点完回读当前题干并与目标比对，不一致就跳过并报错，绝不把答案写到当前碰巧打开的题上。同时修掉一个真实缺陷：页面位置为 1 的题会在 `order <= 1` 时提前返回而不点击，而题干遍历结束时页面正停在最后一题——这道题的答案会写到最后一题上。
- **重复题干不再互相覆盖**：两道题干完全相同的题此前在位置映射里只留一条，另一道永远定位不到。现在用 `::N` 后缀区分，且每个位置只会被消费一次。
- **列表视图按题目容器限定**：一次渲染全部题目的页面上，选项/判断/填空定位器此前总命中第一题。现在先按题干定位题目容器再操作；选项标签改用 `starts-with(…, "A、")`，选项内容里提到「A、」也不会被误命中。
- **「已答」只认真实 `userAnswer`**：平台在仅打开题目时就会生成答案行（`hwAnswerId`）并置 `isAnswer`。旧口径把这些当成已答，审计会把空题报成已完成（「漏题」误判），答案包也不会包含它们。审计、巡检答案包与作答循环现在都要求 `userAnswer` 非空。
- **答案解析忽略说明文字**：`"AC because A and C are correct"` 这类答案此前会把说明里的每个拉丁字母都当成选项。现在只取开头的选项串，并与该题实际选项取交集；空的选择题答案直接报错，不再空点。
- **填空题写后回读**：富文本编辑器吞掉 `fill` 的情况现在会报错，而不是看起来成功。

## 0.7.5 版本更新（按题干定位，选项两种排版都能点）

作答不再按页面题号盲点，选项点击也覆盖了第二种排版。这两个问题是在真实随堂测验上撞出来的。

- **不再相信页面题序**：单题视图（有「答题卡」和「上一题/下一题」的页面）上，页面题序与接口 `order` 并不一致，按接口题号点会点到别的题上。现在作答前先把每个页面位置对应到题干，再按题干定位；找不到对应题目的直接跳过并报错，绝不冒险点错题。
- **没有「A、」的选项也能点**：部分测验把字母单独放在一个 span（`A` + `开闭原则`），文本定位器必然超时，那些题根本答不了；现在会退回 `label:has(input[value="A"])`，但仅在该字母在本页唯一时使用，避免在多题同屏时点错。
- **新增 9 个测试**：题干归一化、页面题序错位、最长前缀取舍、找不到题干返回 `None`。全量 179（178 通过 / 1 跳过）。

## 0.7.3 版本更新（发布可诊断）

Maven 发布现在自带诊断信息，凭据也改为显式写入，不再依赖生成的模板。

- **workflow 自己写 `settings.xml`**：GitHub Packages 读取 token 的那个 `<server>` 块，现在由工作流以确定的内容创建，凭据没写进文件这种情况不再可能发生。
- **发布前先探一次注册表**：用只读请求取一次 POM，先说清是凭据被拒（401）、版本不存在（404）还是已经发布过（200），发布失败时能直接归因。
- **不看原始日志也能读到 Maven 报错**：失败的关键行会以 error 注解的形式出现在运行页上，与日志构建产物并存。

## 0.7.4 版本更新（提交完整性核查）

新增「已提交作业的漏题核查」：不再需要撤回就能知道一份已交的作业有没有漏答，发现漏题才走「撤回 → 补齐 → 重新提交」，且整个核查对平台只读。

- **新增工具 `uplus_audit_homework(task_id, course_id)`**：逐题读学生端 detail，按 `userAnswer` 与 `hwAnswerId` 判定客观题是否作答——两者任一非空即算已答（与 `models._is_answered()` 同一口径）。*0.7.6 已修正该口径：只认非空 `userAnswer`，详见「0.7.6 版本更新」。* 全程不撤回、不提交、不改动任何答卷，因此对「待批改」和「已出分」的作业都可以安全运行。简答、编程、附件题单独统计，不会让作业被判成「不完整」。
- **巡检可带完整性核查**：`uplus_patrol(audit=True)` 与 `uplus-patrol --audit` 会在巡检每个已提交作业时顺带核查。漏题的作业会写进新增的报告章节，并进入待接管清单，附「可撤回后补齐」或「已出分无法撤回」的处置提示，题号超过 10 个时截断显示。
- **报告新增「提交完整性核查」表**：课程、作业、漏答题数、题号、可否撤回，一张表看清哪份已交作业还差题。
- **版本号终于对齐**：`__version__` 此前停在 0.7.0（0.7.1–0.7.3 的源码版本只写进了 `pyproject.toml`），本版统一到 0.7.4，`maven-package/pom.xml` 同步。
- **修掉一个「时间炸弹」测试**：`test_course_dates_from_the_study_feed_are_numeric` 把截止时间写死为 `2026-11-05`，日期一过 `is_expired()` 必然返回 `True`，测试必挂——它验的是「日期字符串能被解析成数字」，与时钟无关。现在按当前时间 +365 天构造。
- **`withdrawable` 以页面状态为准**：作业接口对已交作业仍可能返回「未提交」，所以核查会读一次作答页的真实按钮状态再判定能否撤回；页面读失败时才退回接口状态，并在结果里说明（`page_state_note`）。这一点很关键——只看接口会把「已交但有漏题」的作业整份跳过。
- **新增 9 个测试**（`tests/test_audit.py`）：覆盖漏题判定、主观题不计入、`hwAnswerId` 口径、报告渲染、已出分不可撤回、页面状态覆盖接口状态、页面读失败回退。测试总数 174。

## 0.7.2 版本更新（发布流水线修复）

发布流水线现已跑通。0.7.1 创建了 Release，却没能登记 Maven 制品：POM 把 wheel 与 sdist 作为附加件挂在自己身上，而 GitHub Packages 的 Maven 注册表不接受它不认识的扩展名——deploy 以 400 失败，Packages 面板始终是空的，而那正是这个条目存在的唯一目的。

- **Maven 制品只发布 POM 本身**：不再挂载 wheel 与 sdist，deploy 只上传注册表接受的那一个文件。可安装的发行物仍然是 Release 附件——pip 用户本来也是从那里安装。
- **发布失败会自己说明原因**：Maven 输出会存进 `mvn-deploy-log` 构建产物，其末尾写入运行摘要，下次失败时能直接看到原因，而不只是知道失败了。

## 0.7.1 版本更新（代码审查修复）

一次代码审查后的修复发布。对 0.7.0 代码的审计发现：截止时间换算依赖本机时区、主机名校验用的是子串匹配而非同源比较、发布流水线里的 Maven 推送根本无法认证。三者均已修复，既有工具契约不变。

- **截止时间按北京时间解析**：实验卡片与作业卡片此前用 naive `datetime` 解析页面上的墙钟时间，`.timestamp()` 会套用**本机**的 UTC 偏移。在非 UTC+8 的机器上，巡检报告里的每个截止时间都会偏移同样的量，「临近截止」警告也会提前或推迟触发。现在解析固定按 `Asia/Shanghai`（`utils.parse_cn_datetime`）。
- **非数字时间戳不再中断整轮巡检**：课程接口有时会返回日期字符串（`endDate` / `startDate`），此前与整数比较会抛 `TypeError` 直接终止巡检。现在统一做归一（`utils.to_epoch_ms`），无法识别时降级为「未知」。
- **接口读取只在平台同源下进行**：同源校验此前是子串判断（`host not in page.url`），`www.eduplus.net.evil.example` 这样的页面可以蒙混过关。现在改为比较解析后的主机名。
- **每个实验都有名字**：详情页标题此前只匹配 `实验一` / `实验二`（且存在运算符优先级问题，行内任意位置出现「实验一」都会命中），导致 `实验三` … `实验九` 全部取不到名字。
- **所有工具共用同一份配置**：报告类工具自己加载配置、引擎用的是另一份，两边的允许目录白名单可能不一致。现在配置只解析一次并共享；生成报告也移到工作线程，不再阻塞事件循环。
- **生成的报告用宋体**：`font.name` 只写拉丁字体，Word 取中文字形用的是 `w:eastAsia`，此前仍为主题默认字体。
- **发布流水线能认证且失败可见**：`mvn deploy` 此前没有 `settings.xml`，Maven 推送只可能 401，而 `continue-on-error` 把失败藏了起来。现在凭据由 `setup-java` 注入，构建前先跑测试，git tag 会与包版本校验，发布失败会让整轮变红并给出明确报错，同时 GitHub Release 照常创建。
- **安装 wheel 后不再往 Python 库目录里写文件**：`PROJECT_ROOT` 取自 `parents[2]`，在 wheel 安装下等于包含 `site-packages` 的目录：报告落在那里（系统级安装时不可写），白名单反而拒绝用户自己的项目。现在相对路径按工作目录解析，或由 `UPLUS_HOME` 指定。
- **示例配置随包发布**：`uplus_mcp/courses.example.json` 一起打进 wheel，wheel 安装的用户同样能拿到模板。
- **个人作业材料不再进仓库**：生成的报告里含姓名与学号，`*.doc`、`*.docx`、`*实验*.c`、`gen_exp*.py` 现已忽略，GitHub Desktop 的「Commit all」不会把它们推上去。
- **新增 42 个回归测试**（`tests/test_code_review_fixes.py`）。测试总数 165。

## 0.7.0 版本更新（安全加固）

一次安全加固发布。对 0.6.0 代码的审计发现：模型浏览过的网页有可能把一个导航步骤变成**本地文件读取**（`file:///…`），且若干文件工具接受磁盘上的任意路径。两者在本版均已修复，文档化的既有流程行为不变。

- **导航只允许 `http`/`https`**：批量动作（`browse_act_batch` 的 `op: "navigate"`）此前把 URL 直接交给浏览器，因此一步 `file:///C:/…` 就能打开本地文件，再用 `extract` 把内容读进模型上下文；配合 `browse_upload` 即构成「读取并外传」链路。`navigate` 与 `browse_open` 现在共用同一份 scheme 白名单（`utils.safe_navigation_url`），`file:`、`ftp:`、`data:`、`javascript:`、`blob:` 一律拒绝并给出说明，站内相对路径仍按平台域名解析。
- **文件工具限制在允许的目录内**：报告输出、截图、作业附件、实验上传现在都会先按 `allowed_io_paths`（配置项，或环境变量 `UPLUS_ALLOWED_PATHS`）解析路径，落在允许范围外即拒绝。`..`、`~`、符号链接都会在判定前展开，因此 `reports/../../.ssh/id_rsa` 与 `~/.ssh/id_rsa` 都会被拦下。默认允许目录为报告目录与系统临时目录，「生成报告 → 提交」这条常用流程无需任何配置即可照常工作。
- **`python-docx` 成为正式依赖**：`uplus_generate_experiment_report` 此前是懒加载它，导致 0.6.0 的主打功能在未预装该库的环境下直接 `ModuleNotFoundError`。
- **新增 32 个回归测试**（`tests/test_security_hardening.py`）：覆盖 scheme 白名单、路径穿越 / 符号链接 / 家目录展开逃逸、允许目录的配置链路、报告生成与密钥脱敏。测试总数 123。
- **CI Action 固定到 commit SHA**：`checkout`、`setup-python`、`setup-java`、`action-gh-release` 均改为固定到经过审阅的具体提交，不再使用可被移动的大版本 tag。
- 其他小修：接口错误响应体进入模型上下文前先过 `redact_secrets`；学校下拉在没有任何匹配项时不再退而点击第一项（改为交回手动登录，避免拿账号密码去试错误的学校）；`uplus_login` 文档补充了自动模式 30 秒等待上限的说明。

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
- **实验流水线**：`uplus_inspect_experiment` 读取实验提交物 / 实验手册 / 实验附件，找不到要求就去课程「资料」板块——包括递归进文件夹——找「实验N」和「实验报告模板」并下载；服务端把手册里能直接引用的目的 / 原理 / 内容 / 步骤 / 要求原样填好，模型只补实验实现、结果分析与总结；`uplus_answer_experiment` 填充模板后必须通过三道校验才提交（空报告、残留占位符、报告与实验不符），提交后回读页面确认生效。`uplus_audit_experiment` 对已提交实验做只读核查。
- 生成 Markdown 巡检报告：已提交 / 待批改 / 未完成作业，实验按已提交 / 未提交 / 状态未知三态归类；未提交实验给出待写章节清单，已提交实验附「实验提交完整性核查」；附件题、简答、编程进入人工接管清单。
- 提交完整性核查：`uplus_audit_homework` 只读判定每份已提交作业有没有漏答的客观题、多选题有没有漏选；巡检默认执行核查，发现缺口后用 `uplus_repair_homework` 走「撤回 → 补齐 → 重新提交」，且撤回前必须先备齐所有已知缺口答案。
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

**方式 A：从 GitHub Release 附件 wheel 安装（推荐，无需克隆源码）：**

1. 打开 [Releases 页面](https://github.com/jiangjunw15-web/uplus-mcp/releases)，
   下载最新版本附带的 `uplus_mcp-<版本>-py3-none-any.whl` 文件。
2. 安装刚下载的 wheel：

```bash
pip install <下载路径>/uplus_mcp-0.5.0-py3-none-any.whl
```

安装后会得到 `uplus-mcp` 与 `uplus-patrol` 两个命令；服务通过 CDP 附加到本机已装的 Edge 或 Chrome，正常流程不需要额外下载 Chromium。

**方式 B：从源码安装（开发或最新未发布代码）：**

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

看到 `165 passed`（或更多）即安装成功。

### 第 4 步：把 MCP 服务注册进你的 AI 客户端

在客户端的 MCP 配置里加一段（以 JSON 为例，DSH 的 `cordis.patch.yml` 同理）。

**Release wheel 安装（或 editable 源码安装）：直接注册控制台命令：**

```json
{
  "mcpServers": {
    "uplus": {
      "command": "uplus-mcp",
      "env": { "UPLUS_CONFIG": "config/courses.json" }
    }
  }
}
```

**源码安装 — Windows：**

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

**源码安装 — macOS / Linux：**

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
- 「列出 XX 课程的实验」→ `uplus_list_experiments`（返回 `exp_id` / `stu_exp_id`，可直接用于后续实验工具）
- 「帮我看看第 N 次作业的题目」→ `uplus_inspect_homework`，AI 审题后用 `uplus_answer_homework` 填写提交
- 「帮我做实验一」→ `uplus_inspect_experiment`（读手册、附件并在资料里找模板）→ 模型补写 implement/结果/总结 → `uplus_answer_experiment` 填充、校验并提交
- 「把实验用的资料和模板下载下来」→ `uplus_download_experiment_materials`
- 「检查已交实验是不是空报告 / 交错了」→ `uplus_audit_experiment`
- 「检查已交作业有没有漏题/多选漏选，有就撤回补交」→ `uplus_patrol`（默认核查）→ 按 `repair_briefs` 作答 → `uplus_repair_homework`
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

## GitHub Packages

仓库内的 GitHub Actions 工作流会在每次推送 `v*` 标签（也可在 Actions 页面手动触发并指定标签）时，把一份仅作元数据登记的 Apache Maven 制品发布到本仓库的 Packages 注册表。工作流使用内置 `GITHUB_TOKEN`，发布时不需要个人访问令牌。

工作流会先跑测试再构建，校验 git tag 与 `pyproject.toml`、`maven-package/pom.xml` 里的版本是否一致，先创建带 wheel / sdist 的 GitHub Release，再发布 Maven 制品。因此 Maven 失败不会连累 Release，但会让整轮变红并给出明确报错，不再静默通过。

Release 说明优先读取 `.github/release-notes/<tag>.md`（例如 `v0.8.0.md`）；没有对应文件时由 GitHub 按提交自动生成，不会因为缺少说明文件而失败。

```
maven-package/
  pom.xml              仅含元数据的 Maven POM（com.github.jiangjunw15-web:uplus-mcp）
  artifacts/           作为附属文件挂载的 wheel 与 sdist（已被 gitignore）
.github/workflows/publish-packages.yml   构建发行产物并执行 `mvn deploy`
```

**注意**：这个 Maven 条目只用于在仓库 Packages 面板登记本包，**不是** Python 发行包，**不能用 `pip` 安装**。Python 用户请从 Release 附件 wheel 或源码克隆安装：

- wheel / editable 安装后的控制台命令：`uplus-mcp`
- 定时巡检 CLI：`uplus-patrol`（或 `python -m uplus_mcp.daily_patrol`）

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
