# Theo Bauer · ChargeRelay
"""
secrets_filter.py – Keep configuration secrets out of the database and the logs.

``is_secret_key`` decides whether the value of an OCPP configuration key is a
secret (passwords, keys, tokens, PSK, PIN/PUK ...).  It is used to mask values
before they are stored, and by ``RedactSecretsFilter`` to mask them in log
records: the ocpp library logs every received and sent message at INFO, so a
GetConfiguration response would otherwise put every password, the OCPP key and
the SIM PUK of a charger into the container logs.
"""
import logging
import re

REDACTED = "*****"

# A secret-like word anywhere in the name hides the value.  Substring matching
# is used on purpose: word splitting cannot recognise run-together names such as
# "WPAPSK" or "authkey", and a leaked credential is worse than a hidden number.
# "Keys" (plural, e.g. GetConfigurationMaxKeys) is a count, not a secret.
_SECRET_PATTERN = re.compile(
    r"key|pass(?:word|wd|code|phrase)|secret|token|psk|credential"
    r"|(?:^|[._\-])(?:pin|puk)(?:$|[._\-])",
    re.IGNORECASE,
)
# CamelCase "SimPin" / "SimPuk": must stay case-sensitive, otherwise "Spinner",
# "Shipping" and "WebSocketPingInterval" would match.
_SECRET_CAMEL = re.compile(r"[a-z](?:Pin|Puk)(?:$|[A-Z])")
_NOT_SECRET = re.compile(r"keys", re.IGNORECASE)


def is_secret_key(key: str) -> bool:
    """True if the value of this configuration key must never be stored, served or logged."""
    # Drop the harmless plural before matching so "MaxKeys" is not flagged, while
    # "AuthKeys" style names still match via their other words.
    cleaned = _NOT_SECRET.sub("", key)
    return bool(_SECRET_PATTERN.search(cleaned) or _SECRET_CAMEL.search(cleaned))


# --------------------------------------------------------------------------- log redaction

# One configuration entry, in either of the two formats that reach the log:
#   JSON wire format:  {"key":"ecu.accu.puk","readonly":"false","value":"Eejo9Dol"}
#   Python repr:       {'key': 'ecu.accu.puk', 'readonly': 'false', 'value': 'Eejo9Dol'}
# The entry is matched as a whole so the value is paired with ITS key, whichever
# order the two fields come in.  Quoted strings may contain escaped quotes.
_Q = r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')"""
_KEY_THEN_VALUE = re.compile(
    r"""(?P<head>["']key["']\s*:\s*(?P<key>""" + _Q + r""")\s*(?:,\s*["']readonly["']\s*:\s*[^,}]+?)?\s*,\s*["']value["']\s*:\s*)(?P<value>""" + _Q + r")"
)
_VALUE_THEN_KEY = re.compile(
    r"""(?P<head>["']value["']\s*:\s*)(?P<value>""" + _Q + r""")(?P<mid>\s*,\s*(?:["']readonly["']\s*:\s*[^,}]+?\s*,\s*)?["']key["']\s*:\s*)(?P<key>""" + _Q + r")"
)
# ChangeConfiguration request:  {"key":"WifiPassword","value":"hunter2"}  – same shape as above,
# so _KEY_THEN_VALUE covers it.


def _unquote(token: str) -> str:
    return token[1:-1]


def redact(text: str) -> str:
    """Mask the value of every secret configuration key found in *text*."""

    def key_then_value(m: re.Match) -> str:
        if is_secret_key(_unquote(m.group("key"))):
            return m.group("head") + '"' + REDACTED + '"'
        return m.group(0)

    def value_then_key(m: re.Match) -> str:
        if is_secret_key(_unquote(m.group("key"))):
            return m.group("head") + '"' + REDACTED + '"' + m.group("mid") + m.group("key")
        return m.group(0)

    text = _KEY_THEN_VALUE.sub(key_then_value, text)
    return _VALUE_THEN_KEY.sub(value_then_key, text)


class RedactSecretsFilter(logging.Filter):
    """Logging filter that masks secret configuration values in the rendered message.

    Attach it to *handlers* (not loggers): a logger filter only sees records from
    that one logger, while a handler filter sees records from every logger,
    including the ocpp library's.  It never raises and never drops a record.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            redacted = redact(message)
            if redacted != message:
                record.msg = redacted
                record.args = None
            # Tracebacks quote the offending message too (validation errors do).
            if record.exc_info and record.exc_info[1] is not None and not record.exc_text:
                record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            elif record.exc_text:
                record.exc_text = redact(record.exc_text)
        except Exception:  # noqa: BLE001 – logging must never fail because of the filter
            pass
        return True
