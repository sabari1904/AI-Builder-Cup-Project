// Shared helpers for all Riposte screens.
export const CIRCLE = new URLSearchParams(location.search).get('circle') || 'demo';
export const $ = (s, el = document) => el.querySelector(s);
export const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : String(kid));
  return el;
};

export async function api(path, body, method) {
  const res = await fetch(path, {
    method: method || (body ? 'POST' : 'GET'),
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.detail || res.statusText), { status: res.status, data });
  return data;
}

// Server-sent events with automatic reconnect (EventSource reconnects itself; we surface status).
export function stream(onEvent, onStatus = () => {}) {
  let es;
  const open = () => {
    es = new EventSource(`/api/stream?circle=${CIRCLE}`);
    es.onopen = () => onStatus('live');
    es.onerror = () => onStatus('reconnecting');
    es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (e) { console.error(e); } };
  };
  open();
  return () => es && es.close();
}

export const wsURL = (path) => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`;
export const rupees = (n) => '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
export const ago = (t) => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
};
export const LEVEL_TEXT = {
  calm: 'Calm', caution: 'Be careful', high: 'High risk', critical: 'SCAM ALERT',
};
export const SIGNAL_TEXT = {
  CLAIMS_BANK: 'Says they are from a bank', CLAIMS_GOVT_POLICE: 'Says they are police / government',
  CLAIMS_FAMILY: 'Says they are family', CLAIMS_COMPANY: 'Says they are from a company',
  ASKS_OTP: 'Asks for an OTP', ASKS_PIN_PASSWORD: 'Asks for PIN / password / CVV',
  ASKS_CARD_DETAILS: 'Asks for card details', ASKS_INSTALL_APP: 'Asks you to install an app',
  ASKS_SCREEN_SHARE: 'Asks you to share your screen', ASKS_OPEN_LINK: 'Asks you to open a link',
  ASKS_PAYMENT: 'Asks for money', ASKS_MOVE_MONEY_SAFE: "Asks to move money to a 'safe account'",
  URGENCY_FEAR: 'Pressure / fear', SECRECY_ISOLATION: "Says 'don't tell anyone'",
  REFUSES_VERIFICATION: 'Avoids normal ways to verify', TOO_GOOD_TO_BE_TRUE: 'Too good to be true',
  CALLER_UNKNOWN: 'Unknown number', CALLER_NOT_1600: "Bank call not from a 1600 number",
  LINK_LOOKALIKE: 'Fake look-alike website', LINK_MALICIOUS: 'Google flags the website',
  DOMAIN_NEW: 'Website created recently', PRESENCE_DENIED: "Family member's phone says it's not them",
  PRESENCE_CONFIRMED: 'Family member confirmed', OTP_RECEIVED: 'OTP arrived during the call',
  PAYMENT_STARTED: 'Payment started during the call',
};

// ---- theme: light by default, toggle persists per viewer ----------------------------------
(function theme() {
  let saved = null;
  try { saved = localStorage.getItem('riposte-theme'); } catch {}
  document.documentElement.dataset.theme = saved || 'light';
  const add = () => {
    const bar = document.querySelector('header.bar');
    if (!bar || bar.querySelector('.theme-toggle')) return;
    const b = document.createElement('button');
    b.className = 'theme-toggle no-print';
    const label = () => (b.textContent = document.documentElement.dataset.theme === 'dark' ? '☀️ Light' : '🌙 Dark');
    label();
    b.onclick = () => {
      const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = t;
      try { localStorage.setItem('riposte-theme', t); } catch {}
      label();
    };
    bar.append(b);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();

// ---- device-frame status bar clock (phone.html / family.html mockups only) ------------------
(function clock() {
  const els = document.querySelectorAll('.device-clock');
  if (!els.length) return;
  const tick = () => { const t = new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); els.forEach(e => e.textContent = t); };
  tick(); setInterval(tick, 15000);
})();
