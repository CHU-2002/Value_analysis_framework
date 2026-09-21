"""异常 → **稳定错误码**（AC-3.6）。

错误码是一份封闭枚举，前端据此分支；**绝不把异常文本当错误码**——那样一改文案就断契约。
堆栈与路径细节只进服务端日志，响应里只给 code + message + hint。
"""

from __future__ import annotations

# 封闭枚举：新增错误码要同时改这里与测试里的「全集」断言。
CODES = (
    "BAD_REQUEST",
    "BAD_JSON",
    "NOT_FOUND",
    "UNKNOWN_ROUTE",
    "INVALID_PARAM",
    "UNKNOWN_COMMAND",
    "SHELL_METACHAR",
    "PATH_OUTSIDE_ROOT",
    "ARTIFACT_MISSING",
    "PARSE_FAILED",
    "PARSE_COLUMNS_MISMATCH",
    "TOO_MANY_JOBS",
    "JOB_NOT_FOUND",
    "NOT_LOOPBACK",
    "PORT_IN_USE",
    "BIND_FAILED",
    "INTERNAL",
    "NO_TOKEN",
    "QUOTA_CONFIRM_REQUIRED",
    "BATCH_RUNNING",
    "BATCH_NOT_FOUND",
    "ARCHIVE_UNWRITABLE",
)
CODE_SET = frozenset(CODES)


class WebUIError(Exception):
    """面板自己的异常基类：带错误码与 HTTP 状态码。"""

    code = "INTERNAL"
    status = 500

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_error(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint}


class BadRequest(WebUIError):
    code = "BAD_REQUEST"
    status = 400


class BadJson(WebUIError):
    code = "BAD_JSON"
    status = 400


class NotFound(WebUIError):
    code = "NOT_FOUND"
    status = 404


class UnknownRoute(WebUIError):
    code = "UNKNOWN_ROUTE"
    status = 404


class InvalidParam(WebUIError):
    code = "INVALID_PARAM"
    status = 422


class UnknownCommand(WebUIError):
    code = "UNKNOWN_COMMAND"
    status = 404


class ShellMetachar(WebUIError):
    code = "SHELL_METACHAR"
    status = 400


class PathOutsideRoot(WebUIError):
    code = "PATH_OUTSIDE_ROOT"
    status = 403


class ArtifactMissing(WebUIError):
    code = "ARTIFACT_MISSING"
    status = 404


class ParseFailed(WebUIError):
    code = "PARSE_FAILED"
    status = 422


class ParseColumnsMismatch(ParseFailed):
    code = "PARSE_COLUMNS_MISMATCH"


class TooManyJobs(WebUIError):
    code = "TOO_MANY_JOBS"
    status = 429


class JobNotFound(WebUIError):
    code = "JOB_NOT_FOUND"
    status = 404


class NotLoopback(WebUIError):
    code = "NOT_LOOPBACK"
    status = 400


class BindFailed(WebUIError):
    """绑定失败但**不是**端口占用（例如地址不可用）：别让用户去换端口，换了也没用。"""

    code = "BIND_FAILED"
    status = 500


class RegistrationConflict(ValueError):
    """注册冲突：同一个 id 被注册两次。

    刻意**不继承** `WebUIError`：它不是 HTTP 错误，而是**启动期的配置错误**。
    `from_registry=True` 标记「这是注册表自己抛的」——插件代码里恰好抛出的同名异常
    不该被当成真冲突（复验 N9）。
    """

    def __init__(self, message: str, *, from_registry: bool = False):
        super().__init__(message)
        self.from_registry = from_registry


class PortInUse(WebUIError):
    code = "PORT_IN_USE"
    status = 500


class NoToken(WebUIError):
    code = "NO_TOKEN"
    status = 409


class QuotaConfirmRequired(WebUIError):
    code = "QUOTA_CONFIRM_REQUIRED"
    status = 409


class BatchRunning(WebUIError):
    code = "BATCH_RUNNING"
    status = 409


class BatchNotFound(WebUIError):
    code = "BATCH_NOT_FOUND"
    status = 404


class ArchiveUnwritable(WebUIError):
    code = "ARCHIVE_UNWRITABLE"
    status = 500
