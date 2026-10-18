"""Extract and validate the scammer's payment rails from transcript text.

Output entities are *leads* for a bank to verify, never automatic grounds to freeze.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from riposte.linkguard.guard import domains_in

UPI_HANDLES = {
    "ybl", "ibl", "axl", "okaxis", "okhdfcbank", "okicici", "oksbi", "paytm", "ptyes", "ptaxis",
    "pthdfc", "ptsbi", "apl", "yapl", "rapl", "upi", "sbi", "icici", "hdfcbank", "axisbank",
    "kotak", "yesbank", "fbl", "idfcbank", "pnb", "boi", "barodampay", "federal", "jupiteraxis",
    "fam", "slice", "superyes", "naviaxis", "waaxis", "wahdfcbank", "wasbi", "waicici", "airtel",
    "jio", "freecharge", "mbk", "ikwik", "axisb", "kmbl", "aubank", "indus", "cnrb", "unionbank",
}
_NUM_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
              "six": "6", "seven": "7", "eight": "8", "nine": "9", "double": "", "triple": ""}


class Entity(BaseModel):
    kind: str           # upi | account | ifsc | phone | domain
    value: str
    valid: bool
    note: str = ""


def _spoken_to_text(t: str) -> str:
    t = re.sub(r"\s+at the rate( of)?\s+", "@", t, flags=re.I)
    t = re.sub(r"\s+at\s+(?=[a-z]{2,12}\b)", "@", t, flags=re.I)

    def digits(m: re.Match) -> str:
        words = m.group(0).lower().split()
        out, i = [], 0
        while i < len(words):
            w = words[i]
            if w in ("double", "triple") and i + 1 < len(words):
                out.append(_NUM_WORDS.get(words[i + 1], "") * (2 if w == "double" else 3))
                i += 2
                continue
            out.append(_NUM_WORDS.get(w, w))
            i += 1
        return "".join(out)

    num = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|double|triple|\d)"
    return re.sub(rf"\b{num}(?:[\s-]+{num}){{3,}}\b", digits, t, flags=re.I)


def extract(text: str) -> list[Entity]:
    t = _spoken_to_text(text)
    out: dict[tuple[str, str], Entity] = {}
    for m in re.finditer(r"\b([a-zA-Z0-9.\-_]{2,64})@([a-zA-Z]{2,20})\b", t):
        handle = m.group(2).lower()
        out[("upi", m.group(0).lower())] = Entity(kind="upi", value=m.group(0).lower(),
                                                  valid=handle in UPI_HANDLES,
                                                  note="" if handle in UPI_HANDLES else "unknown UPI handle")
    for m in re.finditer(r"\b[A-Z]{4}0[A-Z0-9]{6}\b", t.upper()):
        out[("ifsc", m.group(0))] = Entity(kind="ifsc", value=m.group(0), valid=True)
    for m in re.finditer(r"(?<!\d)(\d{9,18})(?!\d)", re.sub(r"(?<=\d)[\s-](?=\d)", "", t)):
        v = m.group(1)
        if len(v) == 10 and v[0] in "6789":
            out[("phone", v)] = Entity(kind="phone", value=v, valid=True)
        elif len(v) == 12 and re.search(r"aadha+r", t, re.I):
            continue  # likely an Aadhaar number, not an account
        else:
            out[("account", v)] = Entity(kind="account", value=v, valid=9 <= len(v) <= 18)
    for d in domains_in(text):
        out[("domain", d)] = Entity(kind="domain", value=d, valid=True)
    return list(out.values())
