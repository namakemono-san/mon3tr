import re

# Exception messages from FFmpeg, yt-dlp and S3 can contain local paths, object keys
# and request details, so only these known storage errors are shown verbatim.
STORAGE_ERRORS = {
    "AccessDenied": "Storage access denied (AccessDenied). Check upload permissions.",
    "InvalidAccessKeyId": "Storage credentials are invalid (InvalidAccessKeyId).",
    "SignatureDoesNotMatch": "Storage authentication failed (SignatureDoesNotMatch).",
    "ExpiredToken": "Storage credentials have expired (ExpiredToken).",
    "NoSuchBucket": "Storage configuration is invalid (NoSuchBucket).",
    "RequestTimeout": "The storage request timed out (RequestTimeout).",
    "SlowDown": "Storage is busy (SlowDown). Please try again later.",
}


def clean_error(e: Exception) -> str:
    message = str(e)
    for code, safe_message in STORAGE_ERRORS.items():
        if f"({code})" in message:
            return safe_message
    status = getattr(e, "status", None)
    if isinstance(status, int) and 400 <= status <= 599:
        return f"The request failed (HTTP {status})."
    if isinstance(e, TimeoutError):
        return "The operation timed out. Please try again later."
    return "The operation failed. Please try again later."


def parse_duration_ms(text: str) -> int | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d*)?|\.\d+)\s*(ms|s)?\s*", text.lower())
    if not match:
        return None
    value, unit = float(match.group(1)), match.group(2) or "ms"
    return round(value * 1000 if unit == "s" else value)


def fmt_duration(seconds: float | None) -> str:
    minutes, secs = divmod(int(seconds or 0), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def fmt_size(nbytes: int) -> str:
    if nbytes >= 1024 * 1024:
        return f"{nbytes / 1024 / 1024:.1f}MB"
    return f"{nbytes / 1024:.1f}KB"
