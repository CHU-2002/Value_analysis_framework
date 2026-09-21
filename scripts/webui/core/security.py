"""安全中间件（AC-3.7）：路径 jail、环回校验、shell 元字符、token 脱敏。

都放在一处的原因：这些是**判定**，不是业务。散在各插件里迟早会有一个漏掉，
而漏掉的那一个就是漏洞。
"""

from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path

from .errors import NotLoopback, PathOutsideRoot, ShellMetachar

# 保守集合：这些字符在参数值里没有任何正当用途，一律拒绝（命令本来就不经过 shell）。
SHELL_METACHARS = (";", "|", "&", "`", "$(", "\n", "\r", ">", "<")
# 这些环境变量引用的值会被脱敏（日志、响应、存档）。
SECRET_ENV_VARS = ("TUSHARE_TOKEN", "TUSHARE_API_URL", "HTTP_PROXY", "HTTPS_PROXY")
_REDACT_PLACEHOLDER = "***"


def check_loopback(host: str, *, allow: frozenset) -> None:
    """非环回地址直接拒绝启动。"""
    if (host or "").strip().lower() not in allow:
        raise NotLoopback(
            f"host={host!r} 不是环回地址：本面板只允许监听本机。",
            hint="远程访问与鉴权属于另一个需求；这里不提供放开安全边界的开关。",
        )


def reject_shell_metachars(name: str, value) -> None:
    """参数取值里出现 shell 元字符就拒绝（AC-1.3）。"""
    text = "" if value is None else str(value)
    for metachar in SHELL_METACHARS:
        if metachar in text:
            raise ShellMetachar(
                f"参数 {name!r} 的取值含被拒绝的字符 {metachar!r}",
                hint="参数按列表传给子进程（shell=False），不接受 shell 语法。",
            )


def safe_join(root: Path, *parts: str) -> Path:
    """把 `parts` 拼到 `root` 下，并保证结果仍在 `root` 子树内。

    - 先 `resolve()` 再比较：`..`、绝对路径、**指向树外的符号链接**都会被识别（AC-3.7）；
    - 允许目标不存在（读操作会自己报 404），但绝不允许逃出 root。
    """
    root_resolved = Path(root).resolve()
    candidate = root_resolved
    for part in parts:
        if part in ("", "."):
            continue
        if part.startswith("/") or part.startswith("\\"):
            raise PathOutsideRoot(
                f"不接受绝对路径：{part!r}",
                hint="只允许 output/ 下的相对路径。",
            )
        candidate = candidate / part
    resolved = candidate.resolve()
    if not is_within(resolved, root_resolved):
        raise PathOutsideRoot(
            "路径越出允许的根目录",
            hint=f"只允许访问 {root_resolved} 之下的内容（含符号链接的真实路径判定）。",
        )
    return resolved


def is_within(path: Path, root: Path) -> bool:
    """`path` 是否就是 `root` 或在 `root` 之下。

    先逐级用 `os.path.samefile` **问操作系统**（大小写不敏感的文件系统、符号链接、
    硬链接都由内核判定，修 N5）；路径还不存在时 `samefile` 会失败，
    此时退回**词法比较**——注意两端都已经 `resolve()` 过，所以存在的符号链接
    仍然在词法比较里体现为它的真实路径。
    """
    current, root = Path(path), Path(root)
    while True:
        try:
            if os.path.samefile(current, root):
                return True
        except OSError:
            pass
        parent = current.parent
        if parent == current:
            break
        current = parent
    return current == root or path == root or path.is_relative_to(root)


def collect_secrets(env: dict) -> tuple:
    """从环境里取出要脱敏的值；过短的忽略（避免把常见字符串全替换掉）。"""
    secrets = []
    for name in SECRET_ENV_VARS:
        value = (env or {}).get(name, "")
        if isinstance(value, str) and len(value) >= 8:
            secrets.append(value)
    return tuple(secrets)


def _escaped_forms(secret: str) -> tuple:
    """一个凭据在「已序列化文本」里的几种形态。

    复验 P1：`redact` 作用在**已经序列化过的**文本上（JSON 字符串、HTML 片段），
    凭据里的 `"` `\\` `&` `<` 会被转义，裸 `replace` 就漏掉了。
    真实 token 是字母数字串、当前不受影响，但这里一并堵上——
    否则「脱敏」会被后来者误读成「任意凭据都安全」。
    """
    forms = {secret}
    try:
        forms.add(json.dumps(secret, ensure_ascii=False)[1:-1])
    except (TypeError, ValueError):  # pragma: no cover
        pass
    forms.add(html.escape(secret, quote=True))
    forms.add(html.escape(secret, quote=False))
    return tuple(form for form in forms if form)


def redact(text: str, secrets) -> str:
    """把已知凭据从文本里替换掉（日志、响应体、任务输出、存档写入前都要过这一道）。"""
    if not text:
        return text
    redacted = text
    for secret in secrets or ():
        if secret:
            for form in _escaped_forms(str(secret)):
                redacted = redacted.replace(form, _REDACT_PLACEHOLDER)
    return redacted


def token_fingerprint(token: str) -> str:
    """token 指纹：只存前 8 位，用来分辨「这批数据是哪个账号拉的」（AC-4.7）。

    **永不存 token 本身**——存档会被拷走、备份、上传。
    """
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
