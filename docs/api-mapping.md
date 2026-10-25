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
| 单题详情 | `/api/course/homeworkQuestions/{questionId}/student/detail` | `titleText`、`options[].id/optionContent`、`qsnType`、`userAnswer`、`hwAnswerId`、`knowledges`、`labels`、`everyScore` |
| 实验模式配置 | `/api/course/courseTestcases/experiment_modes?organId={organId}` | 实验环境与提交物配置 |
| 班级实验列表 | `/api/course/teach_classes/{teachClassId}/experiments?userId={userId}&details=report,...` | 实验名称、提交状态、截止时间、报告附件 |

> `homeworkDTO` 在不同课程下可能是 JSON 对象，也可能是 JSON 字符串；`models.HomeworkTask.from_api` 已做兼容解析。
>
> **已答判定**：`isAnswer` 字段可能缺失或为 null；`models._is_answered` 采用联合判定——`userAnswer` 非空、`hwAnswerId` 非空、`isAnswer` 为 `1/"1"/true/"true"`，任一命中即视为已答。

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
