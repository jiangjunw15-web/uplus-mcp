# U+ 平台接口与页面路由映射

> 实测日期：2026-10-25 ｜ 站点：`https://www.eduplus.net`（青软 U+ 产教融合云；部分院校使用自定义二级域名，接口路径同源）
> 本文档记录逆向得到的只读接口、作答页路由、题型编码与状态口径，供维护者对照页面实际行为校准。平台前端升级后字段可能变化，以页面真实行为为准。

## 1. 鉴权

- 登录态由浏览器 Cookie + `localStorage.AccessToken`（accessToken）共同维持；`localStorage.userid`、`role`、`majorName`、`shoolCode` 等为前端展示用。
- 只读接口在浏览器页面上下文内 `fetch(..., {credentials: 'include'})` 调用，并附带 `x-access-token: <AccessToken>` 头；两者缺一都会 401。
- 接口返回两种形态并存：顶层裸数组 / 裸对象，或包一层 `{ "data": ... }`；`platform.py` 的 `_unpack_list` / `_unpack_object` 对两种形态统一解包。
- 会话失效特征：接口返回 `401`（响应体 `{"code":4010000,"success":false,"message":"Unauthorized",...}`），或页面跳转登录路由。`browser.api_get` 先导航工作台重试一次，仍 401 才抛 `AuthExpiredError`，异常消息附带 HTTP 状态、当前页面 URL 与响应体前 200 字便于诊断。
- 课程教师名优先取 `teacherName`，缺失时兜底取 `creator` 字段。

## 2. 只读接口（GET）

| 用途 | 路径 | 关键返回字段 |
| --- | --- | --- |
| 我的课程列表 | `/api/course/athena/courses/study?types=Theory%2CTrain` | `data[].id`、`name`、`startTime`、`endTime`、`teacherName`、`creator` |
| 课程作业列表 | `/api/course/homeworks/published/student?courseId={courseId}` | `data[]`：`id`(taskId)、`homeworkId`、`endTime`、`submitTask`、`reviewTask`、`questionCount`（在 `homeworkDTO` 内）、`homeworkDTO.name` |
| 作业实例详情 | `/api/course/homeworks/published/{taskId}/student` | 作业实例全量字段、`course`、`teachClass`、`homeworkDTO` |
| 题目元数据列表 | `/api/course/homeworkQuestions/student?homeworkId={homeworkId}` | `data[]`：`id`(questionId)、`orderNumber`、`qsnType`、`isAnswer`、`everyScore` |
| 单题详情 | `/api/course/homeworkQuestions/{questionId}/student/detail` | `titleText`、`options[].id/optionContent`、`qsnType`、`userAnswer`、`hwAnswerId`、`knowledges`、`labels`、`everyScore`；**出分后**另有 `answer`（标准答案）、`isCorrect`（0/1）、`userScore` |
| 实验模式配置 | `/api/course/courseTestcases/experiment_modes?organId={organId}` | 实验环境与提交物配置 |
| 班级实验列表 | `/api/course/teach_classes/{teachClassId}/experiments?userId={userId}&details=report,...` | 实验名称、提交状态、截止时间、报告附件 |

> `homeworkDTO` 在不同课程下可能是 JSON 对象，也可能是 JSON 字符串；`models.HomeworkTask.from_api` 已做兼容解析。
>
> **已答判定**：只认 `userAnswer` 非空（`models._is_answered`）。`isAnswer` 与 `hwAnswerId` 会在学生**仅打开题目**时就生成，单独出现不能证明学生真的选了答案——0.7.6 起不再作为已答依据，否则审计会把空题报成已完成（漏题误判）。
>
> **漏选判定（0.7.8）**：
> - 单选题 / 判断题 / 填空题：把 `userAnswer` 与 `answer` 归一化后比较，不等即为错答；
> - 多选题：把两者解析成选项字母集合。实答是标准答案的**真子集**（如 `A` ⊂ `ABC`）即为「漏选」，`missing_letters` 给出漏掉的 `BC`；顺序不同不算错（`AB` = `BA`）；多出标准答案之外的字母记为错答；
> - `answer` 只在**出分后**返回；待批改时学生端 detail 没有该字段，作答页也只显示「我的答案」。此时本地答案簿 `reports/answer_journal.json`（工具成功提交时记录，已 gitignore）作为第二来源；两者都没有则题目进入 `unjudged`，不得当成已完成。
> - `options[].optionContent` 可能是 `<p><code>&lt;property&gt;</code></p>` 这类代码块；`strip_html` 会得到完整文本 `<property>`，页面选项在 `label.el-radio` 内、input 的 `value` 为字母，按 value 定位即可点击。

## 3. 页面路由

| 页面 | 路由 |
| --- | --- |
| 门户（未登录跳转登录域） | `/home/index` |
| 工作台 | `/workbench/index/school` |
| 登录跳转域 | `uc.eduplus.net/login?theme=athena&layout=e30&redirect_uri=…` |
| 课程主页 | `/course/course/{courseId}` |
| 作业栏目 | `/course/course/{courseId}/HomeworkPage?userRole=` |
| 实验栏目 | `/course/course/{courseId}/ExperimentPage?userRole=` |
| 作答页 | `/course/workAnswer/{courseId}/{taskId}/true?isPiYue=noPiYue` |
| AI 练习（自主练习，不计入必交作业） | `/uplus-ai-workbench/aiexercise/{courseId}/page` |

### 双模式登录

登录跳转域实测为 `https://uc.eduplus.net/login?theme=athena&layout=e30&redirect_uri=…`；门户 `/home/index` 本身只有 `.login-button`（`div.blue-button.login-button`）入口，需先点击该入口才会跳转到登录表单。

真实登录表单（Element Plus）结构：

| 字段 | 选择器 / 特征 |
| --- | --- |
| 学校（必填，远程搜索） | 容器 `.el-select`，框内 `input.el-select__input`，placeholder「请选择或输入学校名称」；输入关键词后在 `.el-select-dropdown__item` 中点击匹配项 |
| 账号 | `input[placeholder='请输入学号/工号']`（`input.el-input__inner`，`type=text`） |
| 密码 | `input[type='password']`，placeholder「请输入密码」 |
| 登录按钮 | `button.el-button--primary.el-button--large`，未填完时带 `is-disabled`；填写完成后再点击 |
| 登录方式页签 | 「学号/工号登录」「短信登录」「密码登录」；默认密码登录 |

- `uplus_login(mode="manual")`：打开门户、点击登录入口进入表单，轮询等待 `localStorage.AccessToken` 出现且 URL 离开登录域；由用户在附加浏览器窗口自行选学校、填账号密码并提交。
- `uplus_login(mode="auto", username, password, school)`：模型先询问账号、密码与学校，点击登录入口进入表单；先填学校远程搜索并下拉选中，再填账号 / 密码，等待主按钮解除 `is-disabled` 后点击；提交后等待会话 token 校验。凭据仅当次内存使用，不落盘、不进报告、不回显；找不到输入框、学校下拉匹配失败、出现验证码 / 短信 / 密码错误时回退手动模式并把窗口交还用户。
- 登录成功判定：`AccessToken` 非空且当前 URL 不含 `login`。

## 4. 题型编码（`qsnType`）

| 编码 | 题型 | `userAnswer` 格式 | 选项 | 自动作答 |
| --- | --- | --- | --- | --- |
| 1 | 单选题 | 单个字母，如 `D` | `A/B/C/D` | ✅ |
| 2 | 多选题 | 无分隔字母串，如 `ABCD`（平台侧已排序去重） | `A/B/C/D…` | ✅ |
| 3 | 判断题 | `true` / `false` | 页面渲染「正确 / 错误」按钮，接口 `options` 为空 | ✅ |
| 4 | 填空题 | 填空文本 | 文本输入框 | ✅（按题目文本作答） |
| 5 | 简答题 | 文本 / 附件 | 富文本或上传框 | ❌ 需本人 |
| 6 | 编程题 | 代码 / 附件 | 代码编辑器或上传框 | ❌ 需本人 |
| 7 | 附件题 | 附件 | 文件上传框 | ❌ 需本人上传文件 |
| 其他 | 未知 | — | — | ❌ 标记待人工 |

## 5. 提交状态口径（重要）

接口字段与页面按钮语义并不一一对应，**以页面按钮为最终状态依据**；页面关键词都不命中时返回 `None`，保留 REST 接口判定结果，不得兜底判为未提交。

| 页面关键词 | 含义 | 接口常见取值 |
| --- | --- | --- |
| 「撤回作业」 | 已提交（待批改或已出分） | `submitTask=1`；待批改 `subStatus=1`，已批改 `subStatus=3` |
| 「提交作业」 | 未提交 | `submitTask=0`；`subStatus=1` |
| 「参考答案」/「总分：」/「得分：」 | 已出分（已批改） | `subStatus=3`，`userScore` 有分值 |
| 答题卡 `(n/N)` | 已答题目数 / 总题数 | 题目元数据 `isAnswer=1` 表示已答 |

> 判定优先级：「撤回作业」优先于「提交作业」；「参考答案 / 总分 / 得分」= 已出分；都不命中返回 `None`，由调用方保留接口状态。
>
> 实测发现：已提交待批改的作业在列表接口中 `submitTask` 仍可能为 `0`，因此 `operator.read_page_state()` 必须打开作答页读取按钮文案复核，不能仅凭接口字段判定未提交。

## 6. 提交交互流程（页面层）

1. 进入作答页，逐题选择 / 填写；切题通过答题卡题号点击。
2. 点击「提交作业」按钮（Element Plus `el-button--primary`）。
3. 弹出确认框「作业已经全部做完，是否提交?」，点击「确定」。
4. 成功校验：按钮文案由「提交作业」变为「撤回作业」，答题卡显示 `(N/N)`。

## 7. 附件上传

- 作答页存在 `input[type=file]`，`accept=".pdf,.doc,.docx,.csv,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg,.webp"`，支持多选；多文件可打包 ZIP，单附件 ≤50M。
- 上传后仍需点击「提交作业」才会完成提交。

## 7.1 实验卡片三态判定

实验栏目行容器为 `.exp-item.border.shaddow`；标题 `.exp-item-center-name`；徽章首列 `.flag.gray`（灰底 `rgb(153,153,153)`，文案「已提交」）= 已提交；`.flag.red`（红底 `rgb(252,115,115)`）= 未提交；CSS 仅 gray / red 两类。

- **文本优先于 class**：SPA 刚插入的底部卡片仍带默认 `gray` class、接口状态尚未刷成 red，因此可见文本含「已提交」→ `True`；含「未提交 / 待提交 / 未开始」→ `False`；文本缺失才退而看 gray / red class；都识别不出保持 `None`（状态未知，列入人工复核）。
- **时序**：打开实验页先等待 `.exp-item` 出现、短超时 `networkidle`；滚动到底再回顶触发懒加载；首读约 0.8 秒后二次快照，第二次卡片数不少于第一次时以第二次为准。
- 材料正则 `提交物[：:]\s*([^\n]+)`；截止时间正则 `(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})`。

## 7.2 课程「资料」板块与文件下载（实验材料 / 报告模板）

`资料` 板块的路由没有固定契约，随课程而异，因此 `materials.py` **不硬编码路由**：

1. 打开课程主页 `/course/course/{courseId}`，在左侧栏按文案匹配「资料 / 资源 / 素材 / 文件 / 课程资料 / 课程资源 / 教学资料 / 学习资料」找到入口并点击；选择器依次尝试 `.el-menu-item`、`.el-sub-menu__title`、`.nav-item`、`.sidebar-item`、`.left-nav a`、`.course-nav a`、`#leftNav a`、`.el-menu a`、`.el-aside a`，最后回退到全局 `<a>` 文本扫描。
2. 读取当前层条目：优先 `.el-table__row` / `.file-row` / `.folder-row` / `.resource-row` / `.material-row`，回退为全局 `<a>` 扫描；名称含 `文件夹 / 目录 / folder` 或没有 href 的判为文件夹。大小列正则 `\d+\s*(KB|MB|GB|B)$`。
3. 递归进入子文件夹：有 href 直接 `goto`，没有 href 点击该行文本；条目按 `(name, href)` 去重，深度上限默认 6。
4. 模板匹配与优先级：名称含「实验报告模板」>「报告模板」>「模板 + 报告」>「模板」，然后才是「实验N」材料。

文件下载（`BrowserSession.fetch_bytes`）：

| 顺序 | 方式 | 适用 |
| --- | --- | --- |
| 1 | 页内 `fetch` + `x-access-token` + `credentials:'include'`，`arrayBuffer` 转 base64 回传 | 同源附件 |
| 2 | `context.request.get(url)`（共享 Cookie、不受 CORS 限制） | 跨域 OSS 签名链接 |

无 href 的条目退回 `page.expect_download()` 点击捕获。所有落盘路径都经 `utils.resolve_io_path` 限制在 `allowed_io_paths` 内；下载目录为 `reports/experiment_materials/`，已提交报告回读目录为 `reports/experiment_audit/`，生成报告目录为 `reports/experiment_reports/`（`*.docx` 已 gitignore）。

## 7.3 实验报告模板填充口径

- `TEMPLATE_SECTIONS` 定义规范章节与别名：实验目的 / 实验原理 / 实验内容 / 实验步骤 / 实验实现 / 实验结果 / 实验总结 / 实验要求。
- `canonical_section` 只在整行等于别名，或别名后紧跟 `：/:/、/（/，` 等分隔符时认定标题；`要求掌握进程同步`、`结果是生产者` 这类正文句不会被误判。
- `derive_sections` 从实验手册逐字抽取「目的 / 原理 / 内容 / 步骤 / 要求」，不改写。
- `fill_template` 打开下载的模板，按标题定位其后第一个空段落或空表格单元格填入；无空位时在标题后插入新段落；表格中首格为章节名的行填首个空格。填充后回报 `filled / still_empty / still_placeholders`。
- `verify_report` 的提交闸门：文件 <1KB、正文 <30 字、残留占位符（`（此处填写…）/ 待填写 / TODO / ____`）、报告主题与实验名零重合，任一命中即拒绝提交。

## 8. 课程配置格式

个人课程清单保存在本地 gitignored 文件 `config/courses.json`，不进入公开仓库。`courses` 数组内每项字段格式：

| 字段 | 说明 |
| --- | --- |
| `id` | 课程接口返回的 `courseId` |
| `name` | 课程显示名（仅用于报告归类，可自定义） |
| `teacher` | 任课教师名（仅本地展示） |

顶层（与 `courses` 同级）另有全局开关：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `include_expired_courses` | `false` | 默认 `false`：巡检跳过已截止课程；`true` 保留历史归档视图 |
| `auto_launch_browser` | `true` | 调试端口未监听时自动启动浏览器；旧字段 `auto_launch_edge` 兼容读取 |
| `browser` | `auto` | `auto` / `edge` / `chrome` / `chromium`；`auto` 自动探测已安装浏览器 |
| `cdp_port` | `9333` | 首选调试端口，备选 9222；环境变量 `UPLUS_CDP_PORT` 覆盖 |
| `profile_dir` | 用户家目录专用目录 | 专用浏览器 profile 目录；环境变量 `UPLUS_PROFILE_DIR` 覆盖 |
| `report_dir` | `reports` | Markdown 报告输出目录 |

未在配置中列出课程时，服务会登录后从 `/api/course/athena/courses/study` 动态拉取账号下当前学期课程；已截止课程按上述开关过滤。
