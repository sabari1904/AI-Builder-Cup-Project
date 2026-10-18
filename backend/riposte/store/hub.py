"""In-memory state + event bus for circles, calls, payments and bank state.

Behind a small interface so Firestore can replace it. For the hackathon the
Cloud Run service runs as a single instance (max-instances=1), which makes
in-memory state consistent; this is stated as a limitation in the docs.
"""
from __future__ import annotations

import asyncio
import random
import string
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field


def _id(n: int = 8) -> str:
    return uuid.uuid4().hex[:n]


class Member(BaseModel):
    id: str = Field(default_factory=_id)
    name: str
    relation: str = "family"
    on_call_with_protected: bool = False   # what the member's phone reports (demo toggle)
    online_at: float = 0.0                  # last heartbeat from the member's device
    removal_scheduled_at: float | None = None  # 24 h delay on removals (anti-coercion)


class HoneyTokens(BaseModel):
    """Planted fake details the decoy may give out. Any use is a tripwire."""
    card_number: str = "5244 9910 3378 4416"
    card_expiry: str = "09/29"
    card_cvv: str = "731"
    upi_id: str = "ramesh.k58@okaxis"
    account: str = "50100483920417"
    otp: str = "482913"
    name: str = "Ramesh Kumar"


class Contact(BaseModel):
    name: str
    number: str   # normalised, e.g. +919845012345
    source: str = "manual"       # manual | synced | on_call (what was true when it was saved)
    added_at: float = Field(default_factory=time.time)
    flagged: bool = False        # saved during, or right after, a risky call: show a warning


class Circle(BaseModel):
    id: str
    protected_name: str
    protected_number: str = ""
    members: list[Member] = Field(default_factory=list)
    invite_code: str = Field(default_factory=lambda: "".join(random.choices(string.digits, k=6)))
    # Saved contacts. In the real phone app these are read on-device (READ_CONTACTS) and only
    # normalised numbers are synced; here they are entered on the phone screen.
    contacts: list[Contact] = Field(default_factory=list)
    frozen: bool = False
    risk_context_until: float = 0.0                       # bank: extra checks until this time
    honey: HoneyTokens = Field(default_factory=HoneyTokens)


class Payment(BaseModel):
    id: str = Field(default_factory=lambda: "PAY" + _id(6).upper())
    circle_id: str
    amount: float
    payee: str
    status: str = "pending"   # completed | held | released | blocked | frozen
    reason: str = ""
    level: str = "calm"
    call_id: str | None = None
    created_at: float = Field(default_factory=time.time)
    release_after: float | None = None   # cooling-off: user may release after this time
    decided_by: str | None = None


class IntelItem(BaseModel):
    id: str = Field(default_factory=_id)
    circle_id: str
    call_id: str | None
    kind: str
    value: str
    valid: bool = True
    source: str = "call"     # call | decoy | tripwire
    note: str = ""
    at: float = Field(default_factory=time.time)


class Hub:
    def __init__(self) -> None:
        self.circles: dict[str, Circle] = {}
        self.payments: dict[str, Payment] = {}
        self.intel: list[IntelItem] = []
        self.calls: dict[str, dict[str, Any]] = {}   # call_id -> summary (risk, circle, caller...)
        self.ping_waiters: dict[str, asyncio.Future] = {}
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self.seed_demo()

    # ---- events -----------------------------------------------------------------
    def subscribe(self, circle_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.setdefault(circle_id, set()).add(q)
        return q

    def unsubscribe(self, circle_id: str, q: asyncio.Queue) -> None:
        self._subs.get(circle_id, set()).discard(q)

    def publish(self, circle_id: str, event: dict) -> None:
        event = {"at": time.time(), **event}
        for q in list(self._subs.get(circle_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:   # slow consumer: drop it rather than stall everyone
                self._subs[circle_id].discard(q)

    # ---- demo data ----------------------------------------------------------------
    MAX_CIRCLES = 300

    def seed_demo(self, cid: str = "demo", protected_name: str = "Ramesh", protected_number: str = "") -> Circle:
        c = Circle(id=cid, protected_name=protected_name, protected_number=protected_number, invite_code="482190",
                   members=[Member(name="Priya", relation="daughter")],
                   contacts=[Contact(name="Priya (daughter)", number="+919845012345", source="manual"),
                             Contact(name="Sunita (wife)", number="+919845098765", source="manual")])
        self.circles[c.id] = c
        return c

    def new_private_demo(self, protected_name: str = "", protected_number: str = "", blank: bool = False) -> Circle:
        """Each visitor of the public URL gets an isolated circle so judges never see each other's calls.

        `blank=True` is for the setup wizard: a circle with no seeded members/contacts, so onboarding
        input is the only thing in it. The one-click "Create my demo" path keeps the seeded data.
        """
        if len(self.circles) >= self.MAX_CIRCLES:   # evict the oldest visitor circles (never 'demo')
            for old in [k for k in self.circles if k != "demo"][: len(self.circles) - self.MAX_CIRCLES + 1]:
                self.drop_circle(old)
        cid = "d" + _id(7)
        if blank:
            c = Circle(id=cid, protected_name=protected_name or "Ramesh", protected_number=protected_number)
            self.circles[c.id] = c
            return c
        c = self.seed_demo(cid, protected_name or "Ramesh", protected_number)
        return c

    def add_contact(self, cid: str, name: str, number: str, source: str = "manual") -> Contact:
        """A scammer mid-call sometimes asks the victim to save a number ('save this as Bank Helpline').
        Flag any contact saved during, or just after, a risky call so it gets a second look."""
        risky_now = self.active_call(cid) is not None or self.recent_risky_call(cid) is not None
        contact = Contact(name=name, number=number, source=source, flagged=risky_now)
        c = self.circle(cid)
        c.contacts = [x for x in c.contacts if x.number != number] + [contact]
        return contact

    def drop_circle(self, cid: str) -> None:
        self.circles.pop(cid, None)
        self.payments = {k: p for k, p in self.payments.items() if p.circle_id != cid}
        self.intel = [i for i in self.intel if i.circle_id != cid]
        self.calls = {k: c for k, c in self.calls.items() if c["circle_id"] != cid}
        self._subs.pop(cid, None)

    def contact_for(self, cid: str, number: str) -> Contact | None:
        return next((c for c in self.circle(cid).contacts if c.number == number), None)

    def circle(self, cid: str) -> Circle:
        if cid not in self.circles:
            raise KeyError(cid)
        return self.circles[cid]

    def active_call(self, cid: str) -> dict | None:
        live = [c for c in self.calls.values() if c["circle_id"] == cid and c.get("ended_at") is None]
        return max(live, key=lambda c: c["started_at"]) if live else None

    def recent_risky_call(self, cid: str, within_s: float = 1800) -> dict | None:
        """The riskiest call that is live or ended within `within_s` seconds."""
        now = time.time()
        order = ["calm", "caution", "high", "critical"]
        cands = [c for c in self.calls.values() if c["circle_id"] == cid and
                 (c.get("ended_at") is None or now - c["ended_at"] < within_s)]
        cands = [c for c in cands if order.index(c.get("peak", "calm")) >= 2]
        return max(cands, key=lambda c: order.index(c["peak"])) if cands else None

    def snapshot(self, cid: str) -> dict:
        c = self.circle(cid)
        return {
            "circle": c.model_dump(exclude={"honey"}),
            "honey": c.honey.model_dump(),
            "payments": [p.model_dump() for p in sorted(self.payments.values(), key=lambda p: -p.created_at)
                         if p.circle_id == cid],
            "intel": [i.model_dump() for i in self.intel if i.circle_id == cid][-100:],
            "calls": [v for v in self.calls.values() if v["circle_id"] == cid][-20:],
            "active_call": self.active_call(cid),
        }


hub = Hub()
