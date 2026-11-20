"""Sign-in dialog definitions for the U+ portal.

The platform offers three different sign-in methods and the tool is not allowed
to assume one of them. ``uplus_login`` therefore walks the caller through three
dialogs, in this order:

1. **owner** — is this the caller's own account, or someone else's?
2. **method** — 学号/工号登录, 短信验证码登录, or 手机号/账号密码登录?
3. **credentials** — the fields that method needs (school only for 学号/工号).

Every prompt is a plain dict so an MCP client can render it as a question /
option list, and so a CLI can print the same structure. The tool never defaults
the method: with nothing supplied it returns the first prompt and does nothing
else.
"""

from __future__ import annotations

from typing import Optional

#: ``self`` = the caller's own account, ``proxy`` = signing in for someone else.
LOGIN_OWNERS: dict[str, dict] = {
    "self": {
        "label": "我自己登录",
        "description": "登录你自己的 U+ 账号",
    },
    "proxy": {
        "label": "代登录",
        "description": "替别人登录他/她的 U+ 账号",
    },
}

#: The three sign-in methods the platform itself offers.
LOGIN_METHODS: dict[str, dict] = {
    "student_id": {
        "label": "学号/工号登录",
        "description": "需要先选学校，再填学号/工号和密码；工具自动填表登录",
    },
    "sms": {
        "label": "短信验证码登录",
        "description": "工具只把 U+ 登录页打开并停在短信登录，手机号和验证码由你本人填写，不代填",
    },
    "password": {
        "label": "手机号/账号密码登录",
        "description": "填写手机号（或账号）与密码；工具自动填表登录",
    },
}

#: Fields each auto-filled method needs. ``secret`` fields are never echoed.
LOGIN_FIELDS: dict[str, list[dict]] = {
    "student_id": [
        {"name": "school", "label": "学校", "placeholder": "学校全名，如 中北大学",
         "required": True, "secret": False},
        {"name": "username", "label": "学号/工号", "placeholder": "如 2413041221",
         "required": True, "secret": False},
        {"name": "password", "label": "密码", "placeholder": "登录密码",
         "required": True, "secret": True},
    ],
    "password": [
        {"name": "username", "label": "手机号或账号", "placeholder": "手机号 / 账号",
         "required": True, "secret": False},
        {"name": "password", "label": "密码", "placeholder": "登录密码",
         "required": True, "secret": True},
    ],
}

_OWNER_ALIASES = {
    "self": ("self", "自己", "本人", "我自己登录", "自己登录", "myself", "me"),
    "proxy": ("proxy", "代", "代登录", "他人", "别人", "别人登录", "代他人登录", "other"),
}

_METHOD_ALIASES = {
    "student_id": ("student_id", "studentid", "id", "学号", "学号登录", "学号/工号登录",
                   "学号/工号", "工号", "学号工号"),
    "sms": ("sms", "short_message", "短信", "短信登录", "短信验证码登录", "验证码",
            "手机验证码", "手机验证码登录"),
    "password": ("password", "pwd", "密码", "密码登录", "账号密码登录",
                 "手机号密码登录", "手机号/账号密码登录", "手机号登录"),
}


def _match(raw: str, aliases: dict[str, tuple[str, ...]]) -> Optional[str]:
    text = (raw or "").strip()
    if not text:
        return None
    low = text.lower().replace(" ", "")
    for key, names in aliases.items():
        for name in names:
            if low == name.lower() or low == name.lower().replace(" ", ""):
                return key
    return None


def normalize_owner(raw: str) -> Optional[str]:
    """Map free-text answers onto ``self`` / ``proxy``; None when unrecognised."""
    return _match(raw, _OWNER_ALIASES)


def normalize_method(raw: str) -> Optional[str]:
    """Map free-text answers onto ``student_id`` / ``sms`` / ``password``."""
    return _match(raw, _METHOD_ALIASES)


def _options(options: dict[str, dict]) -> list[dict]:
    return [{"value": key, **meta} for key, meta in options.items()]


def owner_prompt() -> dict:
    """Dialog 1: own account or someone else's."""
    return {
        "logged_in": False,
        "stage": "owner",
        "dialog": "login_owner",
        "question": "这次登录是谁的账号？",
        "options": _options(LOGIN_OWNERS),
        "note": ("先确认登录对象；选定后还要再选登录方式，最后才填登录信息。"
                 "三个问题都会依次弹给你确认。"),
        "next": "带着 owner 重新调用 uplus_login，例如 uplus_login(owner=\"self\")",
    }


def method_prompt(owner: str) -> dict:
    """Dialog 2: which of the platform's three sign-in methods to use."""
    return {
        "logged_in": False,
        "stage": "method",
        "dialog": "login_method",
        "owner": owner,
        "question": "选择登录方式（三种都由你定，我不默认）",
        "options": _options(LOGIN_METHODS),
        "note": ("学号/工号与手机号密码两种方式由工具自动填表登录；短信验证码方式只把页面"
                 "打开并停在短信登录，手机号与验证码请本人填写。"),
        "next": ("带着 method 重新调用 uplus_login，"
                 "例如 uplus_login(owner=\"self\", method=\"password\")"),
    }


def credentials_prompt(method: str, missing: Optional[list[str]] = None) -> dict:
    """Dialog 3: the fields that the chosen method needs.

    Every field of the method is returned so the caller can render the form in
    one go; ``missing`` only marks which ones are still empty.
    """
    fields = LOGIN_FIELDS.get(method, [])
    labels = "、".join(f["label"] for f in fields) or "登录信息"
    question = f"请填写{labels}（{LOGIN_METHODS[method]['label']}）"
    return {
        "logged_in": False,
        "stage": "credentials",
        "dialog": "login_credentials",
        "method": method,
        "owner": None,
        "question": question,
        "fields": fields,
        "missing": missing or [],
        "note": ("密码只在本次调用里使用，不落盘、不进报告、不回显。填写后再次调用 "
                 "uplus_login 就会自动登录。"),
        "next": ("带上 method 与这些字段重新调用 uplus_login，"
                 "例如 uplus_login(owner=\"self\", method=\"password\", username=\"…\", password=\"…\")"),
    }


def sms_manual_prompt(url: str = "", owner: str = "") -> dict:
    """The SMS path hands over: the page is open, the human finishes it."""
    return {
        "logged_in": False,
        "stage": "manual_sms",
        "dialog": "login_sms_manual",
        "owner": owner,
        "method": "sms",
        "manual": True,
        "url": url,
        "question": "页面已停在短信登录，请在浏览器里填手机号并点「获取验证码」完成登录",
        "note": ("这条登录方式不代填：手机号与验证码由本人在浏览器里输入。登录完成后调用 "
                 "uplus_check_login 复核即可。"),
        "next": "登录完成后调用 uplus_check_login",
    }
