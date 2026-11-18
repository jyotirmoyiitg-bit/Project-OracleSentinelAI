"""Verifier: a written conclusion must be supported by the evidence. Plain code; no model."""

import re

_ORA = re.compile(r"(?i)\bORA-\d{5}\b")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_STANDALONE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w]|\.\d)")
_STATEMENT = re.compile(
    r"(?is)\b(?:select\b[\s\S]{0,200}\bfrom\b|insert\s+into\b|delete\s+from\b|update\s+\w+\s+set\b|"
    r"alter\s+(?:system|database|session|tablespace|user|table|index)\b|drop\s+(?:table|user|tablespace|index)\b|"
    r"truncate\s+table\b|grant\s+\w+[\s\S]{0,40}\bto\b|shutdown\s+(?:immediate|abort|transactional)\b|"
    r"startup\s+(?:mount|nomount|force)\b|create\s+(?:table|user|tablespace|index)\b|execute\s+immediate\b|"
    r"dbms_\w+\.\w+\s*\()",
)


def _numbers(text: str) -> set[float]:
    return {float(match) for match in _NUMBER.findall(text.replace(",", ""))}


def _supported(value: float, known: set[float]) -> bool:
    if value in known:
        return True
    if value == int(value):
        return any(abs(item - value) < 0.5 for item in known)  # a figure rounded to a whole number
    return any(abs(item - value) <= 0.05 for item in known)  # a figure rounded to one decimal


def verify(
    parts: list[str], evidence: list[str], target_name: str, other_names: list[str], goal: str = "",
) -> tuple[bool, str]:
    """Check a conclusion against the evidence. Returns (passed, note for the operator).

    Every ORA code and every figure of two or more digits must appear in the evidence or the goal.
    Another database must not be named. A statement or command must not appear.
    """
    text = " ".join(part for part in parts if part).strip()
    if not text:
        return False, "The conclusion was empty."
    if _STATEMENT.search(text):
        return False, "The conclusion contained a statement or command."
    source = " ".join(evidence) + " " + goal + " " + target_name
    known_codes = {code.upper() for code in _ORA.findall(source)}
    for code in _ORA.findall(text):
        if code.upper() not in known_codes:
            return False, f"The conclusion named {code.upper()}, which is not in the evidence."
    known = _numbers(source)
    plain = _ORA.sub(" ", text).replace(",", "")
    for match in _STANDALONE.findall(plain):
        value = float(match)
        if value < 10 and "." not in match:
            continue  # small counts such as "two or 3 days" are not treated as measured figures
        if not _supported(value, known):
            return False, f"The conclusion used the figure {match}, which is not in the evidence."
    folded, folded_source = text.lower(), source.lower()
    for name in other_names:
        clean = name.strip().lower()
        if len(clean) < 3 or clean == target_name.strip().lower():
            continue
        if re.search(rf"(?<![\w-]){re.escape(clean)}(?![\w-])", folded) and clean not in folded_source:
            return False, "The conclusion named another database."
    return True, "Every code, figure, and database name in the conclusion is present in the evidence."
