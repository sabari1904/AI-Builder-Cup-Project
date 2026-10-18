// Real microphone path: Chrome's fake mic plays a WAV (a phone call on speaker) into the Listener.
// Tests AudioWorklet capture -> 16 kHz PCM -> WebSocket -> Gemini Transcribe Live -> risk, end to end.
// usage: node tools/ui/mic_test.mjs [base_url] [wav]
import puppeteer from 'puppeteer-core';

const BASE = process.argv[2] || 'http://localhost:8765';
const WAV = process.argv[3] || new URL('../../evals/audio/icici_card_protection.wav', import.meta.url).pathname;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: 'new', protocolTimeout: 30000,
  args: ['--no-sandbox', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
         `--use-file-for-fake-audio-capture=${WAV}%noloop`, '--autoplay-policy=no-user-gesture-required'] });
const errors = [];
const p = await browser.newPage();
p.on('pageerror', (e) => errors.push(e.message));
await p.goto(`${BASE}/listen`, { waitUntil: 'domcontentloaded' });
const t0 = Date.now();
await p.click('#startMic');
let level = '', seen = new Set();
for (let i = 0; i < 90; i++) {
  level = await p.$eval('#levelText', (e) => e.innerText).catch(() => '');
  const lines = await p.$$eval('#tx p:not(.interim)', (els) => els.map((e) => e.innerText)).catch(() => []);
  for (const l of lines) if (!seen.has(l)) { seen.add(l); console.log(`[${((Date.now() - t0) / 1000).toFixed(1)}s] ${l}`); }
  if (level === 'SCAM ALERT' && seen.size >= 4) break;
  await sleep(1000);
}
console.log(`level=${level} lines=${seen.size} errors=${errors.length ? errors.join(' | ') : 'none'}`);
await browser.close();
process.exit(level === 'SCAM ALERT' && !errors.length ? 0 : 1);
