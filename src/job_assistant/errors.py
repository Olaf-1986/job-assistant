from __future__ import annotations

import re


def sanitize_error(error: object) -> str:
    """Remove credentials and response bodies from an error for display or persistence."""
    sanitized = "" if error is None else str(error)
    sanitized = re.sub(r"(?is)\bresponse_body=.*", "response_body=[omitted]", sanitized)
    sanitized = re.sub(r"(?i)(authorization:\s*bearer\s+)[^\s,;]+", r"\1REDACTED", sanitized)
    sanitized = re.sub(r"(?i)(\bbearer\s+)[^\s,;]+", r"\1REDACTED", sanitized)
    sanitized = re.sub(r"(?i)\b(access_token|token|password|secret)=([^\s&;,]+)", r"\1=REDACTED", sanitized)
    return sanitized if len(sanitized) <= 240 else f"{sanitized[:237]}..."
