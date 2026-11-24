# rename.md — AI 助手分步引导手册（接入本插件后必读）

> 本文件是给**接入本 MCP 插件的 AI 助手**看的操作剧本，新用户也可以照做。
>
> **这个插件是什么**：一个面向青软 U+ 学习平台的 MCP 服务，通过 CDP 附加到你本人已登录的
> Edge / Chrome（不另起无头内核，也不保存密码与 Cookie）。它在真实页面里完成四类工作：
>
> 1. **巡检**：扫描课程、作业与实验并生成 Markdown 报告；已提交作业默认核查漏答 / 多选漏选。
> 2. **客观题作答**：单选、多选、判断、填空整理成审题包交给模型；模型给出答案后，在真实作答页
>    点选、回读核对并提交。带图的题可以先截取题目图片给模型看。页面题干映射不到某道题时跳过该题，
>    不冒险点错题，也不再因此否决整份作业的提交。
> 3. **实验检索与查看**：读取实验提交物 / 实验手册 / 实验附件；详情页找不到要求时，去课程「资料」板块
>    （包括递归进入子文件夹）找实验材料与「实验报告模板」并下载。插件不填写、不上传、不提交实验报告，
>    报告内容由本人完成。
> 4. **页面兜底**：平台接口变化或某一步没有 API 时，用通用 `browse_*` 工具直接打开网页、
>    读快照、点击、填表、上传、截图。
>
> **目标**：在任意 AI agent（Claude Desktop、Cursor、VS Code MCP、DSH、Doubao 等）上，
> 让 AI 按下面的步骤把用户引导到「登录 → 巡检 → **先做完作业再统一复核** → 客观题作答 / 实验检索 → 人工接管清单」闭环，
> 全程不写死任何一台机器的路径、账号、学校或课程名。
>
> 约定：
> - `<安装目录>` = 用户存放本项目、里面有 `pyproject.toml` 的那个文件夹（下面用占位符表示）。
> - 命令分三种环境：**Windows PowerShell**、**macOS Terminal**、**Linux**；AI 先判断用户系统再给对应命令，不要混发。
> - 工具名以插件实际注册名为准：U+ 业务族 `uplus_*`，通用浏览器族 `browse_*`。
> - 客观题可自动作答；**简答、编程、附件、小组成果物一律交回用户本人**，AI 不得编造答案或伪造上传。实验部分只做检索与查看，报告由本人完成；插件不代写、不代交实验报告。
> - **0.8.12 起的作业主流程**：先把所有未提交作业做完，再统一检索复核是否还有未完成（见「第 6.5 步：两阶段作业流程」与「0.8.12 更新了什么」）。
> - 本手册基于 **0.8.16** 编写（0.8.4 起实验只保留检索与查看；下面 0.8.0 章节是历史记录）；0.7.0 起插件收紧了导航与文件路径边界（见「安全边界」），0.7.4 新增提交完整性核查（见「0.7.4 更新了什么」），0.7.5 修掉作答点错题（见「0.7.5 更新了什么」），0.7.6 修掉多选漏选与跳题（见「0.7.6 更新了什么」），0.7.7 修掉 0.7.6 引入的三处缺陷（见「0.7.7 更新了什么」），0.7.8 把已提交作业纳入默认巡检并支持撤回补交（见「0.7.8 更新了什么」），0.8.0 新增做实验流水线（见「0.8.0 更新了什么」）；0.8.5 填空题逐空作答、0.8.6 错标填空句、0.8.7 三窗口登录、0.8.8 真实作答页填空定位与写入加固（见「0.8.8 更新了什么」）、0.8.9 检索固定顺序与单课程扫描（见「0.8.9 更新了什么」）、0.8.10 下载/文件名安全加固与过期作业跳过（见「0.8.10 更新了什么」）、0.8.11 跨源凭据隔离、路由参数注入防护与安全深度加固（见「0.8.11 更新了什么」）、0.8.12 两阶段作业流程（先做完再统一复核）与作答页等待提速（见「0.8.12 更新了什么」）、0.8.13 页面/接口状态对账与漏选不再被答案簿放过（见「0.8.13 更新了什么」）。

## 0.8.16 更新了什么（页面纠偏 + 登录竞态 + 进度语义 + 显式退出）

| 变化 | AI 该怎么用 |
| --- | --- |
| **页面按钮纠正接口少报** | 列表/单课扫描传 `refresh_page_state=True`；巡检和两阶段流程也会核对页面。页面显示「撤回作业」时按已提交处理，不要因接口 `submitTask=0` 重复作答。已截止只读核对，绝不因此补交 |
| **登录检查等待会话就绪** | `uplus_login(stage=done)` 后可用 `uplus_check_login`；它会等待 token/跳转并验证受保护接口。仍失败才回到第 5 步，不要因瞬时 URL 误判 |
| **进度缺失不是未作答** | `progress=null` / `progress_status="unavailable"` 只说明未读到计数，不能推断 0 题或未作答；提交状态看页面按钮。`withdrawable_source="unknown"` 时先刷新页面再决定 |
| **新增退出工具** | 用户明确要退出时调用 `uplus_logout(confirm=True)`。它会走平台退出入口并验证，未确认不执行；没有入口或验证失败如实返回 |
| **多账号严格隔离** | 每个账号独立 profile、CDP 端口（如 9333/9340/9341）及工作目录/报告文件；不得共用答案簿或把同 `question_id` 当跨账号标准答案 |
| **学术与平台边界不变** | 已出分不可撤回；待批改无参考答案仍为 `unjudged`；主观题/编程/附件/实验由本人完成 |

**测试与验收边界**：513 项模拟回归测试通过，触及代码的 Ruff F/B 检查通过；未登录真实账号，也未实际点击平台退出按钮，真实页面退出流程需用户验收。

## 0.8.13 更新了什么（页面/接口状态对账 + 漏选不再被答案簿放过 + 再一轮提速）

平台的接口答案行会滞后于作答页：学生已在页面填好甚至点了提交，接口仍可能返回 `userAnswer` 为空、
`submitTask=0`。上一版把「哪些题有答案」交给接口、「是否已提交」交给页面却不对账，真实使用时会出现
「漏答 N 题，已出分无法撤回」这种两半都无法执行的结论。本版修掉这类误判，并修掉同一批审查发现的
其他真实缺陷；工具签名与既有功能不变。

| 变化 | 具体内容 | AI 该怎么用 |
| --- | --- | --- |
| **两个真相源都上报** | 核查结果含 `api_says_submitted` / `page_says_submitted`；不一致时 `state_conflicts` + `note` 说明「页面按钮权威、漏答/漏选取自接口、可能滞后」 | 看到 `state_conflicts` 非空时，先把差异如实告诉用户，别把「漏答」当成既成事实；先看作答页 |
| **页面全答不再误报漏答** | `finish_then_review` 阶段二当答题卡为 `N/N` 且仅「接口答案为空」时判为已完成（`api_only_missing` 仅作透明记录）；真漏选/错选仍照报 | `review[].note` 写了「页面答题卡显示已全部作答」就不用补答 |
| **`withdrawable` 区分未知** | 未读页面时是 `None`（`withdrawable_source="unknown"`），CLI/报告显示「需开页确认」 | 看到 `unknown` 时先 `uplus_audit_homework(refresh_page_state=True)` 再判断 |
| **漏选不再被答案簿放过** | `journal` 只作补交来源；作裁判只允许在提交是记录意图真子集时成立（证明漏选）；两者相等时该题 `unjudged`。平台标准答案优先 | `unjudged` 不是「没问题」，是「无可信参考答案」；要彻底排除漏选走 `uplus_repair_homework(verify_unjudged=True)` |
| **不再谎称已撤回** | 页面显示未提交时 `withdraw_homework` 返回 `not_submitted`；修复流程如实报「未发生撤回」 | 看 `repair_homework` 的 `withdrawn` 字段，不要只看备注 |
| **逗号分隔填空不再卡住** | `split_blanks` 多空时额外接受 `,` `，` `、`；单空不切 | 填空题多空答案用换行或逗号分隔都能识别 |
| **填空框先排除搜索框** | `_blank_boxes` 任何分支都排除「搜索/查找/关键字/筛选/检索/search/filter」提示的字段 | 无需操作 |
| **提速（判定不变）** | 答题卡走查改为等可见题干变化（单题视图 30 题原省 30–45 秒）；`open_homework` 不再重复 settling（每份作业页省约 2.3 秒，截图仍 settling）；详情读取统一走 `_homework_question_details` | 无需操作 |

**测试**：全量 464 项测试通过，原有测试零回归。

## 0.8.12 更新了什么（两阶段作业流程：先做完作业，再统一复核 + 作答页等待提速）

旧版 `uplus_patrol` 把「读页 → 核查 → 作答」按作业逐个串起来：一份作业读到一半就切到下一份，最后拿到的是一堆中间快照，看不出「我到底还有没有没写完的作业」。本版新增一个**两阶段编排** `uplus_finish_then_review`，把「做作业」和「查作业」彻底分开；`uplus_patrol` 的签名与行为完全不变。

| 变化 | 具体内容 | AI 该怎么用 |
| --- | --- | --- |
| **新增 `uplus_finish_then_review`** | 阶段一（finish）按固定巡检顺序逐份完成**未提交且未截止**的作业；阶段二（review）在最后一份作业都尝试过之后，才回头逐份读真实页面状态并跑与 `uplus_patrol` 相同的完整性核查 | 「先把作业都做完，再看还有没有没写完的作业」就用它；见「第 6.5 步」 |
| **先做后查的顺序** | 阶段一答案以 `{task_id: {question_id: answer}}` 传入；已出分 / 已提交 / 已截止的作业不碰；主观 / 编程 / 附件题不代答，进 `takeover` | 对 finish 日志里 `skipped_questions` 的客观题补答案后重跑；主观题交用户本人 |
| **已答题的修正答案不丢弃** | 调用方对「平台已标记作答」的客观题给出的答案（如多选漏选补 AB）也会进入作答阶段，由 `answer_homework` 差异重填 | 多选漏选 / 半填空补全时直接带答案重跑，不要手工撤卷 |
| **阶段一只读 API 不开页** | 0.8.12–0.8.15 判断「哪些题还没答」用纯接口；0.8.16 起先只读核对页面按钮，已提交不再进入作答 | 无需操作；只读核对后再决定是否填写 |
| **进入页面后截屏审查** | `screenshot=True` 时，阶段二对每份复核仍不完整的作业截取**作答页整页**截图（新增 `operator.capture_answer_page`），路径随 `review[].screenshot` 返回 | 复核仍有未完成项时，先看截图确认还有哪些空没填，再决定补答或走 `uplus_repair_homework` |
| **复核报告有真实内容** | `report_path` 指向 `reports/review_YYYYmmdd_HHMMSS.md`，由真实 finish / review / takeover 数据渲染，不再是空快照；文件名带秒级时间戳 | 报告路径可直接打开看复核明细；不会覆盖同分钟的 patrol 报告 |
| **失败不中断** | 阶段一某一份作业作答失败不会中断其余作业；失败记入 `takeover`（`phase="finish"`）与 `finish` 日志，阶段二失败同理记入 `phase="review"` | 照常看 `takeover`，按 `phase` 区分是哪一段出的问题 |
| **没有答案就不开页** | 阶段一若某份作业的客观题还没答案，直接依据已读到的题目详情把待答题号写进 `finish[].needs_answers` 与 `takeover`，**不再打开作答页、不再逐题抓取题目详情** | 先看 `needs_answers` 里的题号，答好后再带答案重跑；不要反复空跑 |
| **提交闸门没有放松** | `submit=True` 只在客观题全部答出且无写入错误时提交；主观 / 编程 / 附件题依旧交回本人 | 与既有 `uplus_answer_homework` 同一套闸门，不得擅自 `force_submit` |
| **同页复用**（`browser.open`） | 目标 URL 与当前页相同（只忽略 `#hash`，协议/主机/路径/查询严格相等）时跳过 `goto` 直接返回；不同作业/不同课程照旧整页加载 | 无需操作；同一份作业的作答页不再被重复整页加载 |
| **翻页不再空等 networkidle**（`browser.open`） | `goto(domcontentloaded)` 后原先最多等 8 秒 `networkidle`；改为最多 2 秒短 settling，就绪判定交给各调用方自己的等待 | 无需操作；首次打开每一页也省下最多 6 秒 |
| **提交/撤回轮询提速**（`operator._probe_submit_button`） | 用一次小型 DOM 查询读「提交作业 / 撤回作业」按钮标签，替代每轮 `inner_text("body")` 整页序列化；探测不确定（`None`）时仍旧回退到完整读取，判定完全一致 | 无需操作 |
| **作答页等待提速** | `operator._await_page_state` 从「等 networkidle（最多 4 秒）+ 300 ms×10 轮询」改为「等 domcontentloaded + 150 ms×20 轮询，按钮出现即返回」；每次开作业页都走这条热路径，整轮巡检等待明显下降；判定口径与超时预算不变 | 无需操作；作答仍照旧 |
| **令牌探测只做一次**（`browser.api_get`） | 原先每次调用都探一次 `localStorage` 令牌，而 fetch 脚本本身每次都读令牌、401 也有恢复分支；改为会话级只探一次，离开平台域名后重新武装 | 无需操作；30 道题少 30 次接口往返 |
| **课程名解析缓存**（`engine.course_name`） | live 模式下原来每次调用都走整份课程 feed；改为每课程一次缓存；两阶段流程的两阶段也共用一次账号课程读取 | 无需操作；live 模式少拉整份课程 feed |
| **实验读取去重等待** | `read_experiments` 仅当首读为空或存在标题为空的卡片时才二次读取；`experiment.py` 删掉调用方在「实验手册」后 `sleep(1.0)`、「实验附件」后 `sleep(0.8)` 的重复等待（`_click_tab` 内部已等待内容加载） | 无需操作；每门课省约 0.8 秒，每个实验详情省约 1.8 秒 |
| **模板不再二次爬资料树** | `download_experiment_materials` 已 walk 并下载全部匹配资料，模板挑选改为复用已下载的 entries（`find_report_template(walk_materials=False)`） | 无需操作；少一次整棵资料树爬取 + 一次模板重复下载 |
| **实验标签页失败不再静默** | 「实验提交物 / 实验手册 / 实验附件」任一标签页读取失败时，原因记入 `ExperimentDetail.tab_errors`（已脱敏），并经 `uplus_read_experiment_detail` / `uplus_download_experiment_materials` 的 `tab_errors` 字段返回 | 返回 `tab_errors` 非空时，用 `browse_*` 工具自己看那一页，别把「没读到」当成「没有」 |
| **docx 同行正文不再被剪** | `report_generator._prune_empty_sections` 判定「空节」时先看标题行内联正文（`实验目的：掌握…`），不再把模板自带内容当空节删掉 | 无需操作；生成的实验报告 .docx 不会丢标题自带正文 |
| **多选回读变 None 不再当成功** | 多选题点选后回读若中途 DOM 脱离（`None`），先按目标重读一次再定论，仍无法确认则报「未能全部选中」 | 无需操作；不会再静默把未完成的选择当成成功 |
| **登录失败回显不再抛错** | `browser._body_hint` 对 `inner_text("body")` 在跳转中途抛异常的情况安全降级为空字符串 | 无需操作 |
| **重复题干截图可选第 N 份** | `operator.capture_question` / `engine.capture_question_image` 新增可选 `occurrence`（默认 0） | 列表页出现同文重复题时，可传 `occurrence` 截取指定那一份 |

**测试**：新增 8 个两阶段用例、9 个同页复用/令牌探测用例、5 个按钮探测用例、3 个实验标签页/模板用例、2 个登录回显用例等；全量 443 项测试通过，原有测试零回归。

## 0.8.11 更新了什么（跨源凭据隔离 + 路由参数/命令行注入防护 + 深度脱敏）

本版在不改变任何工具签名与原有功能的前提下，修复全量代码审计中发现的 8 类安全漏洞：

| 变化 | 具体内容 | AI 该怎么用 |
| --- | --- | --- |
| **跨源凭据隔离** | `BrowserSession.api_get` 拒绝非平台同源地址；`fetch_bytes` 仅对同源请求附带 `x-access-token` 与 `credentials:'include'`，跨域 OSS 链接直接走 `context.request`，避免平台会话令牌外泄 | 照旧调用；若报「拒绝向非平台域名发送携带凭据的 API 请求」，说明目标地址不属于当前平台域名 |
| **路由参数校验** | 新增 `utils.safe_url_segment`，对 `course_id`、`task_id`、`homework_id`、`question_id`、`exp_id`、`stu_exp_id` 拒绝 `..`、`/`、`\`、`?`、`#`、`@`、`%` 与控制字符，防止 API 路径穿越与参数注入 | 照旧传真实平台 ID |
| **启动器与配置加固** | `launch_browser_with_cdp` 对 `url` 走 `safe_navigation_url` 校验（防 Chromium `--flag` 参数注入与 `file://`），端口严格限定 `1..65535`；`_copy_profile` 跳过指向源目录外的符号链接 | 无需操作 |
| **敏感信息深度脱敏** | `redact_secrets` 新增覆盖 `Authorization: Bearer/Basic` 令牌、独立 JWT、多键值对 `Cookie`/`Set-Cookie` 与 `JSESSIONID`；同步覆盖 `browse.act_batch`、`browser` 登录失败回显与 `engine` 异常文本 | 无需操作 |
| **路径边界与导航加固** | `resolve_io_path` 拒绝空字节 `\x00` 与空路径，配置目录全部无法解析时改为 fail-closed；`safe_navigation_url` 拒绝控制字符 | 无需操作 |
| **资料爬取同源限制** | `materials.walk` 入口与子文件夹跳转校验同源，防止页面外链触发跨源爬取 | 无需操作 |
| **选择器与原子写加固** | `browse.snapshot` 对 `data-testid` / `name` 做 `CSS.escape`；`journal.record` 改用 `tempfile.mkstemp` 原子写入替代固定 `.tmp` 文件名 | 无需操作 |
| **CI 发布流水线加固** | `.github/workflows/publish-packages.yml` 改用 `env:` + XML 转义 + `0600` 权限生成 Maven `settings.xml`，消除工作流脚本注入风险 | 无需操作 |

## 0.8.10 更新了什么（下载与文件名安全 + 过期作业跳过）

这轮再次审查发现下载链路可以绕开导航白名单，平台文件名也可能落到 Windows 设备名或尾随点号。以下全部修正，工具签名不变：

| 变化 | 具体内容 | AI 该怎么用 |
| --- | --- | --- |
| **下载 URL 白名单** | 实验附件、手册、资料、模板统一经 `fetch_bytes`，先过 `safe_navigation_url`：只允许 http/https；`file://`、`javascript:`、`blob:`、`data:` 拒绝。资料文件夹 `page.goto` 同样校验 | 照旧下载；若返回「拒绝下载非 http/https 链接」，说明页面给了非网页链接，交用户处理 |
| **错误脱敏扩展** | `Signature`、`AccessKeyId`、`Credential`、`Cookie` 及 token/secret/password 等值会在 API/下载错误返回前替换为 `***` | 无需操作；错误里仍保留主机与路径 |
| **文件名净化** | `utils.safe_filename` 去掉路径分隔符/控制字符/首尾点号，Windows 设备名前置 `_`，截断 120 字符；附件、手册、资料、模板共用 | 无需操作 |
| **过期作业跳过页面** | 0.8.10–0.8.15 为提速只打开未截止且接口未提交的作业；**0.8.16 起为纠正接口少报，刷新状态时也只读打开已截止作业**，过期仍禁止作答/提交 | 已截止作业不要再尝试作答 |
| **启动器审计** | CDP 探测固定回环地址 + `int()` 端口；`Popen` 明确 `shell=False`、参数列表执行；Bandit 无 MEDIUM/HIGH | 无需操作 |

**测试与审计**：本地全量 **388 通过**（2 个真实 DOM 用例在无浏览器 CI 会跳过）；`pip-audit` 无已知漏洞；`ruff F/B` 通过。

## 0.8.9 更新了什么（检索固定顺序 + 单课程扫描）

平台接口不保证课程、作业、实验的返回顺序；混用「全部课程作业列表」和「逐门课实验列表」会让页面在课程之间来回跳，检索看起来没有规律。本版把顺序固定下来：

| 变化 | 具体内容 | AI 该怎么用 |
| --- | --- | --- |
| **课程固定顺序** | 配置课程按 `courses.json` 顺序；未配置时按实时课程列表的「名称 + id」排序；过期课程照旧过滤 | `uplus_list_courses` 返回的就是固定顺序 |
| **作业固定顺序** | 同一门课内按「截止时间 → 作业名 → task_id」排序；无截止时间排最后 | 直接按返回顺序处理 |
| **实验固定顺序** | 按标题里的 `实验N` 排序（中文/阿拉伯数字都识别：实验一 → 实验二 → 实验十）；无编号排最后 | 直接按返回顺序处理 |
| **新增 `uplus_scan_course`** | 一次只读一门课：作业接口一次 → 未提交作业作答页各一次 → 实验列表页一次；返回 `homeworks` / `experiments` / `summary` | 推荐用它替代「先列全部课程作业再回头列实验」 |
| **`verify_experiments`** | 默认 `False`：实验只读列表徽章，一门课只加载一次；`True`：在同一次扫描里逐条打开实验详情页复核已提交/未提交 | 要快速检索保持默认；要准确状态时传 `True` |
| **`uplus_list_experiments(verify=...)`** | 新增可选参数；`False` 只读列表徽章，不逐条开详情页 | 单门课快速列表时用 `verify=False` |
| **`uplus_patrol` 课程优先** | 一门课的作业全部读完 → 这门的实验 → 下一门课 | 需要全量报告/漏题核查时照旧使用 |

**AI 的检索动作（按这个顺序，不要跳）**：

1. `uplus_list_courses()` 拿到固定顺序的课程列表；
2. 取第一门课，调用 `uplus_scan_course(course_id)`；把这门课的 `homeworks` 与 `experiments` 都读完（需要答题就接着 `uplus_inspect_homework` / `uplus_answer_homework`），处理完再进入下一门；
3. 需要实验状态逐条复核时传 `verify_experiments=True`；只要快速列表时保持默认 `False`；
4. **不要**先对所有课程调用 `uplus_list_homeworks`，再回头逐门调用 `uplus_list_experiments`——这正是旧版跳来跳去的来源；
5. 需要全量巡检报告、漏题核查与 `repair_briefs` 时，再用 `uplus_patrol`（它本身也是课程优先）。

## 0.8.8 更新了什么（真实作答页填空题可填、可提交 + 运行时边界）

实际作答页的填空题：题干在普通 `div`，题干里的空可能渲染成空的样式 `span`（DOM 文本没有 `____`），每个空对应一个 `textarea`，标签是「答案一：」「答案二：」，占位符「请输入填空项答案」。旧版定位不到题目容器时会退回整页，或把页头搜索框当成答案框，于是填空题写不进去、整份作业卡在提交闸门前。本版逐项补齐：

| 变化 | 具体内容 | AI 该怎么用 |
| --- | --- | --- |
| **通用题干定位** | `.ck-content` 找不到题干时，扫描已知标题容器 → 文本节点 → 元素文本，找到最具体的题干，再向上定位真正包含答案框的题目卡片 | 照旧调用 `uplus_inspect_homework` / `uplus_answer_homework`，无需改参数 |
| **答案框过滤** | 只认可见、可编辑且非页头搜索/筛选的框；`placeholder` / `data-placeholder` / `aria-label` 含「填空」或「答案」的框优先，取够题干空数时排除无关输入 | 审题包 `blank_count` 是几个空就返回几行，每空一行；答案仍按 `question_id` 传给 `uplus_answer_homework` |
| **空下划线兼容** | 页面把空渲染成空 `span`、DOM 文本没有 `____` 时，按“去掉下划线串再比”的次级规则匹配题干 | 无需操作 |
| **写入兜底** | `fill` 与键盘输入都不生效时，用原生 value setter 写入并派发 `input` / `change`；写后仍逐格回读 | 返回「写入后页面内容不一致」时不要 `force_submit`，交用户核对页面 |
| **单题页识别** | 页面只渲染「上一题」或「下一题」其中一个按钮时也按单题视图处理，避免把每道接口题写进当前可见题 | 无需操作 |
| **运行时边界** | 字符串布尔配置（`"false"` / `"0"` 不再为真）、401 无重试预算时抛 `AuthExpiredError`、配置课程全过期不再越权列其它课程、实验详情状态无法识别时保留列表徽章、`networkidle` 超时继续解析、资料相对链接按所在目录解析、大写/带横线实验 id、`uplus_close` 失败也清缓存 | 无需操作；这些是内部修正 |

**提交闸门没有放松**：所有客观题（含填空）都有答案、且没有写入错误时才自动提交；简答、编程、附件题仍交回本人。答完仍建议跑一次 `uplus_audit_homework` 复核。

---

## 0.8.0 更新了什么（历史：当时的实验流水线，已在 0.8.4 移除填写/提交）

- **实验流水线**：`uplus_inspect_experiment(course_id, exp_id, stu_exp_id)` 读取实验提交物 / 实验手册 / 实验附件；详情页找不到要求时，自动去课程「资料」板块找，**包括递归进入子文件夹**，按「实验N / 实验报告模板」匹配并下载模板。返回的审题包区分 `auto_sections`（手册可推导、服务端原样引用）与 `needed_sections`（模型补写）。
- **`uplus_read_experiment_detail(course_id, exp_id, stu_exp_id)`**：只读实验详情（要求、提交物、截止时间、已提交文件）。
- **`uplus_download_experiment_materials`**：下载实验附件与资料模板，不提交。
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
| 让下载工具抓取 `file://` / `javascript:` 等附件或文件夹链接 | 报错「拒绝下载非 http/https 链接」 / 「资料文件夹链接被拒绝」 | 只下载 http/https 资源；本地文件让用户自己提供 |
| 读或写 `allowed_io_paths` 之外的路径（如 `~/.ssh/`、系统目录） | 报错「路径 … 不在允许的目录内」 | 报告、截图、附件一律放在报告目录或临时目录；确有需要时让用户把目录加进 `allowed_io_paths` |
| 用 `../` 穿越出允许目录 | 报错同上（`..` 会被展开后再判定） | 不要拼相对路径去猜文件位置 |

默认允许目录：**报告目录（`reports/`）与系统临时目录**。`browse_screenshot` 与 `uplus_capture_question_image` 会往这里写文件，无需额外配置。

---

## 总览：AI 要带用户走的 10 步

| 步骤 | AI 动作（调用工具 / 执行命令） | 成功信号 |
| --- | --- | --- |
| 0 环境检查 | 确认 Python 版本、项目目录、浏览器 | 三条检查都通过 |
| 1 安装依赖 | 创建虚拟环境并 `pip install -e .` | 无报错，`uplus-mcp` 入口可导入 |
| 2 个人配置（可选） | 复制 `courses.example.json` 为 `courses.json` | 文件存在；留空也能用，自动拉在线课程 |
| 3 注册 MCP 客户端 | 跑 `python scripts\register_mcp.py` 写入客户端配置，再重启客户端 | 客户端工具列表出现 `uplus_*` / `browse_*` |
| 4 启动浏览器会话 | 调用 `browse_launch_edge` | 返回端口号，`uplus_check_login` 可连接 |
| 5 登录 | **按三窗口逐项问**：自己/代登录 → 登录方式 → 登录信息，再带参数调 `uplus_login` | `stage="done"` 且 `logged_in: true`，能看到工作台 |
| 6 巡检/逐门检索 | `uplus_list_courses` → 对每门课 `uplus_scan_course`；需要全量报告时 `uplus_patrol` | 一门课的作业+实验读完再进入下一门；返回每门课的作业/实验与统计 |
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

### 3.1 首选：一条命令完成注册

**「MCP 未注册、只能用本地 CLI」几乎都是这一步没做完**，不是服务端坏了。
先直接跑注册脚本，它会把正确的 `uplus` 条目写进本机已装的客户端：

```powershell
# Windows
python scripts\register_mcp.py
```

```bash
# macOS / Linux
python3 scripts/register_mcp.py
```

- `python scripts\register_mcp.py --dry-run` —— 只看将要写入什么，不改文件。
- `python scripts\register_mcp.py --client claude-desktop` —— 只注册一个客户端
  （可选值：`claude-desktop` / `cursor` / `claude-code`）。

脚本自动按**绝对路径**定位虚拟环境解释器（向上查找项目根目录的 `.venv`，git
worktree 场景下也能找到真正的 venv），写好 `cwd` 与 `UPLUS_CONFIG`，保留用户已有
的每一个配置项，遇到解析不了的 JSON 只跳过不覆盖，可重复执行。

### 3.2 手工注册内容（核心命令，脚本不可用时的备选）

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

### 3.3 安装过 console script 的简化写法

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

### 3.4 各客户端配置文件位置（详见 `docs/mcp-clients.md`）

- Claude Desktop：`%APPDATA%\Claude\claude_desktop_config.json`（Windows）；`~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）。
- Cursor：`~/.cursor/mcp.json`（全局）或项目内 `.cursor/mcp.json`。
- Claude Code：`~/.claude/settings.json` 的 `mcpServers` 段。
- VS Code：MCP 插件的 `mcp.json`。
- DSH 等其它客户端：按 stdio 协议注册同一 `command` / `args`。

### AI 动作

1. **先跑 `python scripts\register_mcp.py`**（第 3.1 节），这是最不容易出错的方式。
2. 先问用户使用哪个 AI 客户端；脚本会覆盖 Claude Desktop / Cursor / Claude Code，
   其它客户端再按第 3.2 节的格式手工写入。
3. 手工填路径时，把 `<安装目录>` 替换成真实绝对路径（JSON 里 Windows 路径反斜杠要写成 `\\`）。
4. 提示用户**保存后必须重启客户端**，新工具才会加载。

### 成功信号

- 重启后客户端工具列表出现 `uplus_login`、`uplus_patrol`、`browse_snapshot` 等 `uplus_*` / `browse_*` 工具。

### 失败回退

- 工具没出现：先跑 `python scripts\register_mcp.py --dry-run` 看它写到了哪个文件；
  核对解释器路径是否存在、`cwd` 是否正确、JSON 是否合法；**重启客户端**。
- 启动报错 `ModuleNotFoundError: uplus_mcp`：`command` 指向的解释器里没装这个包。
  必须用虚拟环境的解释器（`<安装目录>\.venv\Scripts\python.exe`），
  回到第 1 步确认 `pip install -e .` 成功。

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

## 第 5 步：三窗口问清登录（AI 必须逐个问，禁止替用户默认）

### AI 动作（顺序不能乱）

1. **第一窗口：自己登录还是代登录** —— 调 `uplus_login()`，工具返回 `stage="owner"`，原话把两个选项讲给用户：自己登录（`self`）/ 代别人登录（`proxy`）。
2. **第二窗口：用哪种方式登** —— 拿 owner 再调 `uplus_login(owner=...)`，返回 `stage="method"`，把三个选项讲清楚：
   - **学号·工号登录（`student_id`）**：需要学校、账号、密码；学校必须选，选错登不上。
   - **短信验证码登录（`sms`）**：工具只把浏览器停在短信页签，手机号和验证码由用户自己在窗口里填，AI 不填也不问。
   - **手机号·账号密码登录（`password`）**：只要账号（手机号）和密码，不需要学校。
3. **第三窗口：填登录信息** —— 调 `uplus_login(owner=..., method=...)`，返回 `stage="credentials"` 并列出 `fields` / `missing`；把缺的字段问齐后带上参数再调一次：学号·工号 `school` + `username` + `password`；手机号密码 `username` + `password`。
4. 字段齐全时工具自己填表、点登录并校验会话，不需要用户再动手；短信方式则返回 `stage="manual_sms"` 和 `url`，提示用户在该窗口自行填写。

### 成功信号

- 自动填写的两种方式返回 `stage="done"`、`logged_in: true`、当前 URL 已离开登录域、`token_len > 0`；再调 `uplus_check_login` 复核一次返回登录有效。
- 短信方式返回 `stage="manual_sms"`、`manual: true`；用户填完后再调 `uplus_check_login` 确认。

### 隐私红线（AI 必须遵守）

- 账号、密码、学校名**仅用于当次调用**：不写进 `courses.json`、报告、日志、聊天记录，不在工具结果里回显，不保存到任何文件。
- 短信方式的手机号与验证码完全不经手：不要索要、不要代填、不要读取输入框。

### 失败回退

- 工具返回 `stage="login_failed"`，先读 `reason` / `body_hint`：多为账号密码错误、学校未选对、需要图形验证码。
- 学校下拉匹配不到时不要反复试，改让用户确认学校全称；仍不行就改用短信验证码方式，让用户自己在窗口里登。

---

## 第 6 步：巡检课程、作业与实验

### AI 动作（按顺序调用）

1. `uplus_check_login`：确认登录态有效；失效则回到第 5 步重新登录。
2. `uplus_list_courses`：列出账号下当前学期课程（已截止课程默认跳过），顺序固定。
3. **逐门检索（推荐）**：按课程顺序，对每门课调用 `uplus_scan_course(course_id)`；先读完这门课的 `homeworks`，再读完 `experiments`，处理完再进入下一门。要快速检索时实验保持默认 `verify_experiments=False`，要逐条复核实验状态时传 `True`。不要先对所有课程列作业再回头列实验。
4. `uplus_patrol`（需要全量报告时）：全量巡检作业与实验，生成 Markdown 报告，返回统计摘要、待答审题包、`repair_briefs` 与待接管清单；遍历同样是课程优先。
   - 本工具**默认不答题、不提交**；不要在未经用户确认时开启自动提交。
   - **已提交作业默认纳入核查**（`audit=true`，CLI 默认核查、`--no-audit` 才跳过）：报告会列出漏答与多选漏选。
   - 对 `repair_briefs` 中可撤回的作业：先回答非 optional 且 `needs_model_answer` 的题目，再调 `uplus_repair_homework` 完成撤回、补齐、重新提交；已出分作业不得撤回。
5. **要「先把作业都做完，再看还有没有没写完的」**：走第 6.5 步的 `uplus_finish_then_review`（两阶段：先做完，再统一复核）。

### 成功信号

- 返回课程清单；巡检摘要包含「作业已提交 / 待批改 / 未完成」「实验已提交 / 未完成 / 状态未知」「待人工接管」计数；报告路径可打开。

### AI 对用户说什么

> “巡检完成：已提交 X 项、未完成 Y 项；实验 Z 项待处理；另有 N 项需要你本人完成。我把明细列给你。”

### 失败回退

- 接口 401：会话过期，回到第 5 步重新登录后重试。
- 课程列表为空：先确认用户账号下当前学期确实有课程；「AI 练习」属于自主练习，不计入必交作业。

---

## 第 6.5 步：两阶段作业流程（先做完作业，再统一复核）

**什么时候用这一步**：用户想「把作业都写完，然后一次性看还有没有没写完的」时。0.8.12 起推荐用
`uplus_finish_then_review`；`uplus_patrol`（第 6 步）行为完全不变，需要全量报告 / 漏题核查时照旧使用。

### 为什么是两阶段

旧流程把「读页 → 核查 → 作答」按作业逐个串起来：一份作业读到一半就切到下一份，最后拿到的是一堆
中间快照，看不出「我到底还有没有没写完的作业」。两阶段把「做作业」和「查作业」彻底分开——
**先把所有未提交作业做完，再回头统一复核**，因此「还有没有未完成的作业」反映的是全部作答完成之后
的账号整体状态。

### AI 动作

1. `uplus_list_courses()` 拿到固定顺序的课程列表（顺序与第 6 步一致）。
2. 对每门课要做的作业，先用 `uplus_inspect_homework(task_id, course_id)` 取审题包；
   图形题按第 7 步的规则先 `uplus_capture_question_image` 看图再答。
3. AI 逐题作答，按题型规范化（单选字母、多选字母串、判断 `true`/`false`、填空每空一行）。
4. 把所有答案按 `{task_id: {question_id: answer}}` 汇总，调用：

   ```
   uplus_finish_then_review(answers, submit=False, audit=True, screenshot=False)
   ```

   - `submit=False`：只落页不提交，先看复核结果；
   - `submit=True`：所有客观题都答出的作业自动提交（闸门与 `uplus_answer_homework` 相同）；
   - `screenshot=True`：阶段二对每份复核仍不完整的作业截一张作答页整页图。
5. 看返回：
   - `summary.review_incomplete`：复核后仍不完整的作业份数——这就是「还有没有未完成的作业」的答案；
   - `finish[].needs_answers`：**还没给答案**的客观题号（接口记录为空；接口答案行可能滞后于作答页，阶段二会开页复核，页面其实填好了就不用补）；
   - `review[].complete` / `missing` / `partial` / `wrong` / `unjudged`：每份作业的复核明细；
   - `review[].filled_on_page` / `api_only_missing`：页面答题卡是否已 `N/N`、以及仅因接口滞后而列出的空行（这类作业 `complete=true`，无需补答）；
   - `review[].api_says_submitted` / `page_says_submitted` / `state_conflicts`：页面与接口对提交状态是否一致；`state_conflicts` 非空时**先把差异如实告诉用户**，不要把「漏答」当既成事实；
   - `review[].screenshot`：`screenshot=True` 时不完整作业的截图路径；
   - `takeover`：按 `phase`（`finish` / `review`）区分是哪一段报的问题。
6. 对复核仍不完整的作业：先看截图或 `missing` 明细确认还有哪些空没填，补答后再跑一次；
   已提交但不完整的（漏答 / 多选漏选）走 `uplus_repair_homework` 撤回、补齐、重新提交。
   **`unjudged` 不是「没问题」**：它表示平台尚未公布参考答案、本地也没有能证明缺口的记录，要彻底排除
   漏选时用 `uplus_repair_homework(verify_unjudged=True)`。
7. 主观题、编程题、附件题始终进 `takeover`，交回用户本人；AI 不得代答、不得伪造上传。

### 成功信号

- `summary.finish_attempted` 等于你给了答案的未提交作业数；`finish_submitted` 为其中成功提交的份数；
- `summary.review_total` 覆盖账号内所有作业；`summary.review_incomplete` 为 0 表示没有未完成作业。

### 失败回退

- 某份作业 `takeover` 里 `phase="finish"` 且写了「作答失败」：其余作业不受影响，单独重试那一份；
- `needs_answers` 非空：先按提示作答再重跑；若阶段二显示该作业页面已 `N/N`，说明接口滞后，不必补；
- `state_conflicts` 非空：页面与接口对提交状态不一致，先看作答页（或截图）再决定，别直接报「漏答」；
- 401：回到第 5 步重新登录。

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
   - 填空：题目对应的文本。题干有**多个空**时（审题包里该题带 `blank_count`，如 2），**每个空一个答案**，按题干顺序用换行分隔成 `N` 行返回，例如 `"@GetMapping\n@PostMapping"`；答案数少于空数会被工具判为无效，不会半填着提交。
4. 调用 `uplus_answer_homework(task_id, course_id, answers, submit=False)` **先落页不提交**；工具回读核对已填题目。
5. 核对无误、用户确认后，再以 `submit=True` 提交；提交确认弹窗由工具自动点击，提交后回读按钮状态校验。

### 成功信号

- 落页后未答题数为 0；提交成功后页面按钮由「提交作业」变为「撤回作业」，答题卡显示 `(N/N)`。

### 强制边界

- 题目未答完、部分题目回读失败时，工具会自动暂缓提交；不得擅自 `force_submit=True`，除非用户明确要求并知晓风险。
- **简答、编程、附件题、小组 PPT 不进入本步**；AI 不得为这些题编造答案。
- 实验报告不由插件填写或提交：服务端只做检索与查看，报告内容由本人完成。

### 失败回退

- 选项乱序 / 题号错位：重新 `uplus_inspect_homework` 取最新审题包；页面被导航打断时用 `browse_open` 回到作答页再续做。

---

## 第 7.5 步：实验检索与查看

插件对实验**只做检索与查看**，不填写、不上传、不提交实验报告。

### AI 动作

1. `uplus_list_experiments(course_id)` 取实验，记下 `exp_id` / `stu_exp_id` 与截止时间。
2. `uplus_read_experiment_detail(course_id, exp_id, stu_exp_id)` 读取要求、提交物、截止时间与已提交文件；读手册/附件内容也可直接用 `browse_*` 工具打开详情页。
3. `uplus_download_experiment_materials(course_id, exp_id, stu_exp_id)` 把实验附件、实验手册文件链接和课程「资料」里递归找到的模板下载到 `reports/experiment_materials/`，供本人离线查阅。
4. 把要求、截止时间、下载路径如实告诉用户；实验报告的撰写与提交由本人完成。

### 成功信号

- 列表返回实验条目及 `exp_id` / `stu_exp_id`；详情返回要求与截止时间；下载工具返回 `downloaded` 清单和 `folder`。

### 强制边界

- 不代写、不代交实验报告；不调用不存在的 `uplus_answer_experiment` / `uplus_audit_experiment` / `uplus_generate_experiment_report` / `uplus_submit_experiment_report`。
- 找不到模板或资料入口时如实告知用户，可用 `browse_snapshot` 查看课程页面手动定位；不要声称已生成或已提交。

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
| 实验报告 | 不代写、不代交；插件只检索要求并下载手册/附件/模板 | 列出材料要求、提交入口与截止时间；模板或资料缺失时请用户确认 |
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
- 用户明确要求退出当前 U+ 账号时，调用 `uplus_logout(confirm=True)`。未传确认只返回提示，不会退出；
  工具走平台退出入口并验证受保护接口已不可读，成功后才清理账号缓存。找不到入口或验证失败时不得声称已退出。
- **多账号**：每个账号使用独立浏览器 profile、CDP 端口（例如 9333 / 9340 / 9341）和独立工作目录/报告文件；
  退出一个 profile 不等于切换另一个 profile。相同 `question_id` 不是标准答案证明，不得自动跨账号复用答案或答案簿。

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
3. 工具是否在客户端工具列表里？否 → 回到第 3 步：跑 `python scripts\register_mcp.py --dry-run`
   确认写到了哪个文件，核对解释器路径与 `cwd`，然后**重启客户端**（新工具只在启动时加载）。
4. 依赖是否装好？回到第 1 步重装并重启客户端。若报错 `ModuleNotFoundError: uplus_mcp`，
   说明配置里的 `command` 指向的解释器里没装包——必须用虚拟环境的解释器。
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
- 实验报告不代写、不代交：工具只读取要求、下载手册/附件/模板供本人核对，用户须自行完成内容并遵守学校诚信规定。小组作品、编程作业、开放简答必须由本人完成；考试、闭卷测验及任何禁止辅助的考核场景不得使用。
- 服务端不内置题库；客观题答案由接入的 AI 模型生成，提交前用户应自行复核。
- 密码、Cookie、token、学号、姓名、课程 id 等敏感信息不写入仓库、巡检报告或公开配置。姓名 / 学号只写入 gitignored 的 `config/courses.json` 与本地生成的报告 `.docx`（`*.docx` 已 gitignore）。
