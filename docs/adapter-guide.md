# 接入新平台适配器

MCP 工具层、编排引擎、报告与答案规范化不绑定青软 U+；接入新教学平台时只需替换两个适配层。

## 1. 抽象契约

### 1.1 `PlatformReader`（只读数据）

```python
class PlatformReader:
    def list_my_courses(self) -> list[Course]: ...
    def list_homeworks(self, course_id: str) -> list[HomeworkTask]: ...
    def list_questions(self, homework_id: str) -> list[Question]: ...
    def question_detail(self, question_id: str) -> Question: ...
```

返回值复用 `models.py` 中的 `Course` / `HomeworkTask` / `Question`，字段映射在适配器内完成；平台私有字段写入 `HomeworkTask.raw` 备查。

### 1.2 `PageOperator`（页面写入与状态复核）

```python
class PageOperator:
    def open_homework(self, task: HomeworkTask) -> Any: ...
    def read_page_state(self, page: Any) -> tuple[HomeworkStatus, str | None]: ...
    def fill_question(self, page: Any, question: Question, answer: str) -> str: ...
    def submit_homework(self, page: Any, task: HomeworkTask, outcome: dict) -> None: ...
    def upload_attachment(self, task: HomeworkTask, file_path: str) -> dict: ...
    def read_experiments(self, course: Course) -> list[Experiment]: ...
```

状态枚举固定为 `HomeworkStatus`；页面按钮 / 列表口径差异在适配器内归一。

## 2. 接入步骤

1. 新建 `src/uplus_mcp/adapters/<platform>/` 目录：
   - `reader.py`：实现 `PlatformReader`
   - `operator.py`：实现 `PageOperator`
   - `mapping.md`：接口、题型编码、状态字段对照表（参照 `docs/api-mapping.md`）
2. 在 `config.py` 增加 `platform` 字段（默认 `uplus`），`server.py` / `daily_patrol.py` 按平台选择适配器实例。
3. 复用现有 `engine.py` 编排、`report.py` 渲染、`solver.py` 答案规范，不修改 MCP 工具签名。
4. 补充 `tests/` 下该平台的模型映射单测。

## 3. 必须在适配器内处理的平台差异

| 差异点 | 处理要求 |
| --- | --- |
| 题型编码 | 统一映射到 `QuestionType`，未知题型归入 `UNKNOWN` 并进入人工接管 |
| 多选答案格式 | 规范化为排序去重大写字母串（`utils.normalize_letters`） |
| 判断题答案 | 规范化为 `true` / `false`（`utils.normalize_bool`） |
| 提交状态 | 以页面权威状态为准；接口字段只用于粗筛，避免待批改被误判为未提交 |
| 登录态 | 复用 `browser.py` 持久化会话；失效时抛出平台独立的 `AuthExpiredError` |
| 附件限制 | 在适配器记录大小、格式与命名要求，写入 takeover 清单明细 |

## 4. 不应进入适配器的内容

- 不内置题库与课程答案；答案推理始终由 MCP 客户端模型完成。
- 不自动提交主观题、编程题、附件题。
- 不绕过登录、验证码或设备校验。
