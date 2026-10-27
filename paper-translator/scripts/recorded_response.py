"""Adapt recorded @index@ examples to link-label protection; not an AI translator."""
import re

from paper_translator.common import PipelineError
from paper_translator.protect import PROTECTED

# Recorded examples indexed whole links as one unit. Rebuild those units using
# current tokens so the example prose and scientific content stay unchanged.
RECORDED_LINK = re.compile(r"!?\[[^\]\n]*\]\([^\)\n]+\)")
RECORDED_UNITS = re.compile(RECORDED_LINK.pattern + "|" + PROTECTED.pattern)


def recorded_text(block, template):
    tokens = list(block["placeholders"])
    matches = list(PROTECTED.finditer(block["text"]))
    if [m[0] for m in matches] != list(block["placeholders"].values()):
        if any(RECORDED_LINK.fullmatch(v) for v in block["placeholders"].values()):
            # Legacy packets retain their old token order.
            return re.sub(r"@(\d+)@", lambda m: tokens[int(m[1])], template)
        raise PipelineError("Recorded example does not match this protection format")
    units = []
    for unit in RECORDED_UNITS.finditer(block["text"]):
        value = block["text"][unit.start():unit.end()]
        for match, token in reversed(list(zip(matches, tokens, strict=True))):
            if unit.start() <= match.start() and match.end() <= unit.end():
                start, end = match.start() - unit.start(), match.end() - unit.start()
                value = value[:start] + token + value[end:]
        units.append(value)
    return re.sub(r"@(\d+)@", lambda m: units[int(m[1])], template)
