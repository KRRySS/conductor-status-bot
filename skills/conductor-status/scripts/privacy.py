"""Conservative best-effort redaction, not a secret/PII detection guarantee."""
import re

PATTERNS = [
    (re.compile(r'-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?(?:-----END [^-\r\n]*PRIVATE KEY-----|\Z)', re.S), '[REDACTED PRIVATE KEY]'),
    (re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|sk-[A-Za-z0-9_-]{12,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[A-Z0-9]{16})\b'), '[REDACTED TOKEN]'),
    (re.compile(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b'), '[REDACTED JWT]'),
    (re.compile(r'(?i)\bBearer\s+[^\s"\'<>]+'), 'Bearer [REDACTED]'),
    (re.compile(r'(?i)(["\']?\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|authorization)["\']?\s*[:=]\s*)(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s,;&]+)'), r'\1[REDACTED]'),
    (re.compile(r'(?i)(https?://)[^/\s:@]+:[^/\s@]+@'), r'\1[REDACTED]@'),
    (re.compile(r'(?<!\w)/(?:Users|home)/[^/\s]+'), '/[HOME]'),
    (re.compile(r'(?i)\b[A-Z]:\\Users\\[^\\\s]+'), r'[HOME]'),
]


def redact(text):
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def sanitize(value):
    # Redact BEFORE truncation (especially multiline private keys).
    if isinstance(value, str):
        clean = redact(value)
        return clean if len(clean) <= 6000 else clean[:6000] + '\n[TRUNCATED]'
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        return {redact(str(k)): sanitize(v) for k, v in value.items()}
    return value
