"""Mask account and card numbers in stored text, so no agent ever queries a full number.

PDFs and images are read by Claude once, before masking, to extract their Rows; the Rows are masked as they are stored.
"""

import re

# 8+ digits, optionally split by single spaces or dashes.
_LONG_NUMBER = re.compile(r"\b\d(?:[ -]?\d){7,}\b")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def mask(text: str) -> str:
    def repl(m: re.Match) -> str:
        if _ISO_DATE.fullmatch(m.group()):
            return m.group()
        digits = re.sub(r"\D", "", m.group())
        return "••••" + digits[-4:]

    return _LONG_NUMBER.sub(repl, text)
