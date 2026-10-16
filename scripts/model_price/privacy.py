"""Keep credentials and personal data out of public diagnostics and model mirrors.

Capture validation rejects private data rather than rewriting official evidence.
Diagnostics redact it while retaining public URLs, status codes, and model IDs.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote_plus, urlsplit, urlunsplit

REDACTED = "[redacted]"
PRIVATE_FIELDS = frozenset({
    "authorization", "proxyauthorization", "apikey", "xapikey", "apikeyid",
    "accesskey", "accesskeyid", "secretkey", "secretid", "secretaccesskey",
    "clientsecret", "accesstoken", "refreshtoken", "idtoken", "token",
    "cookie", "setcookie", "session", "sessionid", "sessionkey", "sessiontoken",
    "csrf", "csrfcode", "csrftoken", "xcsrftoken", "xsrftoken", "sectoken",
    "password", "passwd", "privatekey", "owneruin", "uin", "userid", "uid",
    "accountid", "accountname", "username", "realname", "openid", "unionid",
    "email", "emailaddress", "phone", "phonenumber", "mobile", "mobilephone",
    "opendingtalkid", "openconversationid", "userquota", "accountbalance",
    "手机号", "手机号码", "电话号码", "电子邮箱", "身份证号", "用户id",
})
PRIVATE_QUERY_FIELDS = PRIVATE_FIELDS | {
    "key", "signature", "sig", "xamzsignature", "xgoogsignature", "auth",
}
URL = re.compile(r"https?://[^\s<>\"']+", re.I)
QUERY_VALUE = re.compile(r"(^|&)([^=&]+)=([^&]*)")
LOCAL_PATH = re.compile(
    r"(?:/(?:Users|home)/|/private/var/|/var/folders/|[A-Za-z]:[\\/]Users[\\/])"
    r"[^\r\n\"'<>;,)\]]+"
)
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
CONTACT_ID = re.compile(r"(?<![A-Za-z0-9])D[A-Za-z0-9]{33}(?![A-Za-z0-9])")
SECRET_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]{16,}|"
    r"gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{40,}|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|AKID[A-Za-z0-9]{16,}|"
    r"AIza[A-Za-z0-9_-]{35}|xox[baprs]-[A-Za-z0-9-]{20,}|"
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)
PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?"
    r"(?:-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----|$)", re.S,
)
COOKIE = re.compile(r"(?i)(\b(?:set[-_]?cookie|cookie)[\"']?\s*[:=]\s*)[^\r\n]+")
ASSIGNMENT = re.compile(
    r"(?i)(\b(?:authorization|proxy[-_]?authorization|(?:x[-_]?)?api[-_]?key|"
    r"secret[-_]?(?:access[-_]?key|key|id)|access[-_]?(?:token|key(?:[-_]?id)?)|"
    r"refresh[-_]?token|id[-_]?token|session[-_]?(?:id|key|token)|"
    r"password|passwd|csrf(?:code|[-_]?token)?|owner[-_]?uin|uin|token|"
    r"email|phone(?:[-_]?number)?|open[-_]?ding[-_]?talk[-_]?id|user[-_]?id)"
    r"\b[\"']?\s*[:=]\s*)"
    r"(\[redacted\]|\"[^\"\r\n]*\"|'[^'\r\n]*'|(?:Bearer|Basic)\s+[^\s,;]+|[^\s,;&)\]}]+)"
)


def _field_name(value: str) -> str:
    return re.sub(r"[\W_]", "", value.casefold())


def _placeholder(value: str) -> bool:
    value = re.sub(r"(?i)^(?:Bearer|Basic)\s+", "", value.strip("\"' "))
    return bool(re.fullmatch(
        r"(?:YOUR_[A-Z0-9_]+|API_KEY|TOKENHUB_API_KEY|TENCENT_CLOUD_API_KEY|"
        r"<[^<>]+>|\$\{?[A-Z0-9_]+\}?|\[redacted\])",
        value, re.I,
    ))


def _redact_text(text: str, *, preserve_placeholders: bool = False) -> str:
    def redact_url(match: re.Match[str]) -> str:
        value = match[0].rstrip(";,).")
        suffix = match[0][len(value):]
        try:
            parts = urlsplit(value)
        except ValueError:
            return REDACTED + suffix

        def redact_parameter(parameter: re.Match[str]) -> str:
            if _field_name(unquote_plus(parameter[2])) not in PRIVATE_QUERY_FIELDS:
                return parameter[0]
            if preserve_placeholders and _placeholder(unquote_plus(parameter[3])):
                return parameter[0]
            return parameter[1] + parameter[2] + "=" + REDACTED

        netloc = parts.netloc.rsplit("@", 1)[-1]
        query = QUERY_VALUE.sub(redact_parameter, parts.query)
        if netloc == parts.netloc and query == parts.query:
            return match[0]
        return urlunsplit(parts._replace(netloc=netloc, query=query)) + suffix

    def redact_assignment(match: re.Match[str]) -> str:
        if preserve_placeholders and _placeholder(match[2]):
            return match[0]
        return match[1] + REDACTED

    text = PRIVATE_KEY.sub(REDACTED, text)
    text = URL.sub(redact_url, text)
    text = COOKIE.sub(lambda match: match[1] + REDACTED, text)
    text = ASSIGNMENT.sub(redact_assignment, text)
    for pattern in (SECRET_TOKEN, LOCAL_PATH, EMAIL, CONTACT_ID):
        text = pattern.sub(REDACTED, text)
    return text


def safe_diagnostic(value: object) -> str:
    """Sanitize exception text before it can be saved or forwarded to a reader."""
    return _redact_text(str(value))


def redact_report_errors(value: Any) -> None:
    """Sanitize diagnostics in a loaded artifact without rewriting its source file."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (
                key == "error" or (key == "note" and value.get("status") == "source_error")
            ):
                value[key] = safe_diagnostic(item)
            elif key == "errors" and isinstance(item, list):
                value[key] = [safe_diagnostic(error) for error in item]
            else:
                redact_report_errors(item)
    elif isinstance(value, list):
        for item in value:
            redact_report_errors(item)


def contains_private_data(value: Any) -> bool:
    """Reject sensitive fields recursively, allowing only explicit API templates."""
    if isinstance(value, dict):
        return any(
            _field_name(str(key)) in PRIVATE_FIELDS or contains_private_data(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_private_data(item) for item in value)
    return isinstance(value, str) and _redact_text(value, preserve_placeholders=True) != value
