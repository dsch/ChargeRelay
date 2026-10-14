# Theo Bauer · ChargeRelay
"""
secrets_filter.py – Which OCPP configuration keys hold secrets.

COPY of the rule in backend/secrets_filter.py.  The API and the OCPP server are
built from separate directories, so the module cannot be shared.  Keep the
pattern identical to the backend copy: a key hidden there must be hidden here.
"""
import re

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
