# U+ 平台接口与页面路由映射

> 实测日期：2026-10-25 ｜ 站点：`https://www.eduplus.net`（青软 U+ 产教融合云；部分院校使用自定义二级域名，接口路径同源）
> 本文档记录逆向得到的只读接口、作答页路由、题型编码与状态口径，供维护者对照页面实际行为校准。平台前端升级后字段可能变化，以页面真实行为为准。

## 1. 鉴权

- 登录态由浏览器 Cookie + `localStorage.AccessToken`（accessToken）共同维持；`localStorage.userid`、`frontend-token` 等为前端展示用。
- 只读接口在浏览器页面上下文内 `fetch(..., {credentials: 'include'})` 调用即可继承会话，无需手工拼接鉴权头。
- 会话失效特征：接口返回 `401`，或页面跳转登录路由。

## 2. 只读接口（GET）

| 用途 | 路径 | 关键返回字段 |
| --- | --- | --- |
| 我的课程列表 | `/api/course/athena/courses/study?types=Theory%2CTrain` | `data[].id`、`name`、`startTime`、`endTime`、`teacherName` |
| 课程作业列表 | `/api/course/homeworks/published/student?courseId={courseId}` | `data[]`：`id`(taskId)、`homeworkId`、`endTime`、`submitTask`、`reviewTask`、`questionCount`（在 `homeworkDTO` 内）、`homeworkDTO.name` |
| 作业实例详情 | `/api/course/homeworks/published/{taskId}/student` | 作业实例全量字段、`course`、`teachClass`、`homeworkDTO` |
| 题目元数据列表 | `/api/course/homeworkQuestions/student?homeworkId={homeworkId}` | `data[]`：`id`(questionId)、`orderNumber`、`qsnType`、`isAnswer`、`everyScore` |
| 单题详情 | `/api/course/homeworkQuestions/{questionId}/student/detail` | `titleText`、`options[].id/optionContent`、`qsnType`、`userAnswer`、`hwAnswerId`、`knowledges`、`labels`、`everyScore` |
| 实验模式配置 | `/api/course/courseTestcases/experiment_modes?organId={organId}` | 实验环境与提交物配置 |
| 班级实验列表 | `/api/course/teach_classes/{teachClassId}/experiments?userId={userId}&details=report,...` | 实验名称、提交状态、截止时间、报告附件 |

> `homeworkDTO` 在不同课程下可能是 JSON 对象，也可能是 JSON 字符串；`models.HomeworkTask.from_api` 已做兼容解析。

## 3. 页面路由

| 页面 | 路由 |
| --- | --- |
| 工作台 | `/workbench/index/school` |
| 课程主页 | `/course/course/{courseId}` |
| 作业栏目 | `/course/course/{courseId}/HomeworkPage?userRole=` |
| 实验栏目 | `/course/course/{courseId}/ExperimentPage?userRole=` |
| 作答页 | `/course/workAnswer/{courseId}/{taskId}/true?isPiYue=noPiYue` |
| AI 练习（自主练习，不计入必交作业） | `/uplus-ai-workbench/aiexercise/{courseId}/page` |

## 4. 题型编码（`qsnType`）

| 编码 | 题型 | `userAnswer` 格式 | 选项 | 自动作答 |
| --- | --- | --- | --- | --- |
| 1 | 单选题 | 单个字母，如 `D` | `A/B/C/D` | ✅ |
| 2 | 多选题 | 无分隔字母串，如 `ABCD`（平台侧已排序去重） | `A/B/C/D…` | ✅ |
| 3 | 判断题 | `true` / `false` | 页面渲染「正确 / 错误」按钮，接口 `options` 为空 | ✅ |
| 4 | 填空题 | 填空文本 | 文本输入框 | ✅（按题目文本作答） |
| 5 | 简答题 | 文本 / 附件 | 富文本或上传框 | ❌ 需本人 |
| 6 | 编程题 | 代码 / 附件 | 代码编辑器或上传框 | ❌ 需本人 |
| 其他 | 未知 | — | — | ❌ 标记待人工 |

## 5. 提交状态口径（重要）

接口字段与页面按钮语义并不一一对应，**以页面按钮为最终状态依据**：

| 页面按钮 | 含义 | 接口常见取值 |
| --- | --- | --- |
| 「撤回作业」 | 已提交（待批改或已出分） | `submitTask=1`；待批改 `subStatus=1`，已批改 `subStatus=3` |
| 「提交作业」 | 未提交 | `submitTask=0`；`subStatus=1` |
| 答题卡 `(n/N)` | 已答题目数 / 总题数 | 题目元数据 `isAnswer=1` 表示已答 |

> 实测发现：已提交待批改的作业在列表接口中 `submitTask` 仍可能为 `0`，因此 `operator.read_page_state()` 必须打开作答页读取按钮文案复核，不能仅凭接口字段判定未提交。

## 6. 提交交互流程（页面层）

1. 进入作答页，逐题选择 / 填写；切题通过答题卡题号点击。
2. 点击「提交作业」按钮（Element Plus `el-button--primary`）。
3. 弹出确认框「作业已经全部做完，是否提交?」，点击「确定」。
4. 成功校验：按钮文案由「提交作业」变为「撤回作业」，答题卡显示 `(N/N)`。

## 7. 附件上传

- 作答页存在 `input[type=file]`，`accept=".pdf,.doc,.docx,.csv,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg,.webp"`，支持多选；多文件可打包 ZIP，单附件 ≤50M。
- 上传后仍需点击「提交作业」才会完成提交。

## 8. 课程配置格式

个人课程清单保存在本地 gitignored 文件 `config/courses.json`，不进入公开仓库。字段格式示例：

| 字段 | 说明 |
| --- | --- |
| `id` | 课程接口返回的 `courseId` |
| `name` | 课程显示名（仅用于报告归类，可自定义） |
| `teacher` | 任课教师名（仅本地展示） |

未在配置中列出课程时，服务会登录后从 `/api/course/athena/courses/study` 动态拉取账号下全部课程。
