"""Link guard: turns a URL (typed or *spoken*) into hard, verifiable signals.

Three independent checks, any of which can fail without breaking the others:
  1. Lookalike: brand name in a domain that is not the brand's official domain.
  2. Domain age via RDAP (new domains are a strong scam signal).
  3. Google Web Risk lookup (known phishing/malware).
"""
from __future__ import annotations

import datetime as dt
import logging
import re

import httpx

from riposte.config import settings
from riposte.signals import Signal, SignalType, Source

log = logging.getLogger(__name__)

# brand token -> official registrable domains
BRANDS: dict[str, set[str]] = {
    "icici": {"icicibank.com", "icicidirect.com", "icicilombard.com", "iciciprulife.com"},
    "hdfc": {"hdfcbank.com", "hdfc.com", "hdfclife.com", "hdfcergo.com"},
    "sbi": {"sbi.co.in", "onlinesbi.sbi", "sbicard.com", "sbilife.co.in", "onlinesbi.com"},
    "axis": {"axisbank.com"},
    "kotak": {"kotak.com", "kotak811.com"},
    "paytm": {"paytm.com"},
    "phonepe": {"phonepe.com"},
    "npci": {"npci.org.in"},
    "rbi": {"rbi.org.in"},
    "indiapost": {"indiapost.gov.in"},
    "incometax": {"incometax.gov.in"},
    "uidai": {"uidai.gov.in"},
    "dbs": {"dbs.com", "dbs.com.sg"},
    "ocbc": {"ocbc.com"},
    "uob": {"uob.com.sg"},
}
TLDS = ["com", "in", "co.in", "org", "net", "services", "online", "xyz", "top", "site", "info",
        "app", "live", "support", "help", "co", "io", "gov.in", "sbi", "com.sg", "sg"]

_STOPWORDS = {"this", "that", "the", "a", "an", "open", "website", "site", "link", "go", "to", "on", "your",
              "visit", "type", "enter", "please", "sir", "madam", "is", "our", "and", "in", "at", "www", "click"}
_SPOKEN = [(r"\s+dot\s+", "."), (r"\s+point\s+", "."), (r"\s+slash\s+", "/"),
           (r"\s+(hyphen|dash)\s+", "-"), (r"\s+at the rate\s+", "@")]
_DOMAIN_RX = re.compile(r"\b((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:%s))\b" %
                        "|".join(re.escape(t) for t in sorted(TLDS, key=len, reverse=True)), re.I)


def domains_in(text: str) -> list[str]:
    """Extract candidate domains from typed or spoken text ('icici cards dot services')."""
    t = " " + text.lower() + " "
    for rx, rep in _SPOKEN:
        t = re.sub(rx, rep, t)
    found: list[str] = []
    for m in _DOMAIN_RX.finditer(t):
        d = m.group(1).strip(".")
        found.append(d)
    # spoken domains often arrive with spaces inside the label: "icici cards.services".
    # Try joining the last 2-3 words before the TLD, skipping ordinary words.
    for m in re.finditer(r"((?:[a-z0-9]+ ){0,3}[a-z0-9]+)\.(%s)\b" % "|".join(re.escape(x) for x in TLDS), t):
        words = m.group(1).split()
        for k in (2, 3):
            if len(words) >= k and not (set(words[-k:]) & _STOPWORDS):
                found.append("".join(words[-k:]) + "." + m.group(2))
    seen, out = set(), []
    for d in found:
        d = d.removeprefix("www.")
        if d not in seen:
            seen.add(d)
            out.append(d)
    # "icicicards.services" and "cards.services" come from the same spoken phrase: keep the longest.
    # A real subdomain ("m.icicibank.com" vs "icicibank.com") is separated by a dot and is kept.
    def swallowed(d: str) -> bool:
        return any(o != d and o.endswith(d) and not o.endswith("." + d) for o in out)
    return [d for d in out if not swallowed(d)]


def registrable(domain: str) -> str:
    parts = domain.lower().split(".")
    for multi in ("co.in", "gov.in", "com.sg", "org.in", "net.in"):
        if domain.endswith("." + multi) or domain == multi:
            return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _skeleton(s: str) -> str:
    # cheap confusable folding: 0->o, 1/l->i, rn->m, strip hyphens/digits
    s = s.lower().replace("rn", "m").replace("0", "o").replace("1", "i").replace("l", "i")
    return re.sub(r"[^a-z]", "", s)


def lookalike_brand(domain: str) -> str | None:
    """Return the impersonated brand if the domain uses a brand name but isn't official."""
    reg = registrable(domain)
    label_skel = _skeleton(reg.split(".")[0] + domain.split(reg)[0])
    for brand, official in BRANDS.items():
        if reg in official or any(domain.endswith("." + o) for o in official):
            return None
        if _skeleton(brand) in label_skel:
            return brand
    return None


async def domain_age_days(domain: str, client: httpx.AsyncClient) -> int | None:
    try:
        r = await client.get(f"https://rdap.org/domain/{registrable(domain)}", follow_redirects=True, timeout=4)
        if r.status_code != 200:
            return None
        for ev in r.json().get("events", []):
            if ev.get("eventAction") == "registration":
                created = dt.datetime.fromisoformat(ev["eventDate"].replace("Z", "+00:00"))
                return (dt.datetime.now(dt.timezone.utc) - created).days
    except Exception as e:  # network failures must never break the call pipeline
        log.info("rdap failed for %s: %s", domain, e)
    return None


async def web_risk_threats(url: str, client: httpx.AsyncClient) -> list[str]:
    if not settings.web_risk_api_key:
        return []
    try:
        r = await client.get(
            "https://webrisk.googleapis.com/v1/uris:search",
            params=[("uri", url), ("threatTypes", "SOCIAL_ENGINEERING"), ("threatTypes", "MALWARE"),
                    ("threatTypes", "UNWANTED_SOFTWARE"), ("key", settings.web_risk_api_key)],
            timeout=4)
        return r.json().get("threat", {}).get("threatTypes", []) if r.status_code == 200 else []
    except Exception as e:
        log.info("web risk failed for %s: %s", url, e)
        return []


class LinkGuard:
    NEW_DOMAIN_DAYS = 90

    def __init__(self, client: httpx.AsyncClient | None = None, online: bool = True) -> None:
        self.client = client or httpx.AsyncClient()
        self.online = online
        self._done: set[str] = set()

    async def check_text(self, text: str) -> list[Signal]:
        out: list[Signal] = []
        for d in domains_in(text):
            if d in self._done:
                continue
            self._done.add(d)
            out += await self.check_domain(d)
        return out

    async def check_domain(self, domain: str) -> list[Signal]:
        out: list[Signal] = []
        brand = lookalike_brand(domain)
        if brand:
            out.append(Signal(type=SignalType.LINK_LOOKALIKE, source=Source.LINKGUARD,
                              evidence=f"{domain} uses '{brand}' but is not {brand.upper()}'s official website",
                              detail={"domain": domain, "brand": brand}))
        if self.online:
            threats = await web_risk_threats(f"http://{domain}/", self.client)
            if threats:
                out.append(Signal(type=SignalType.LINK_MALICIOUS, source=Source.LINKGUARD,
                                  evidence=f"Google Web Risk flags {domain}: {', '.join(threats)}",
                                  detail={"domain": domain, "threats": threats}))
            age = await domain_age_days(domain, self.client)
            if age is not None and age < self.NEW_DOMAIN_DAYS:
                out.append(Signal(type=SignalType.DOMAIN_NEW, source=Source.LINKGUARD,
                                  evidence=f"{registrable(domain)} was registered only {age} days ago",
                                  detail={"domain": domain, "age_days": age}))
        return out
