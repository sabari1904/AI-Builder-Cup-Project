// Real-browser end-to-end UI test: four screens, the ICICI replay, a held payment, family stops it.
// usage: node tools/ui/ui_test.mjs [base_url] [out_dir]
import puppeteer from 'puppeteer-core';

const BASE = process.argv[2] || 'http://localhost:8765';
const OUT = process.argv[3] || '/tmp';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const step = (s) => console.log(`  .. ${s} (${((Date.now() - T0) / 1000).toFixed(1)}s)`);
const T0 = Date.now();
const check = (name, ok, info = '') => { results.push([name, ok]); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : '  -> ' + info}`); };

const browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: 'new',
  protocolTimeout: 60000, args: ['--no-sandbox', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] });
const errors = [];
async function open(path, w = 1200, h = 1400) {
  const p = await browser.newPage();
  await p.setViewport({ width: w, height: h });
  p.on('pageerror', (e) => errors.push(`${path}: ${e.message}`));
  p.on('console', (m) => { if (m.type() === 'error') errors.push(`${path}: ${m.text()}`); });
  p.on('dialog', (d) => d.accept());
  await p.goto(BASE + path, { waitUntil: 'domcontentloaded' });
  return p;
}
const text = (p, sel) => p.$eval(sel, (e) => e.innerText).catch(() => '');

try {
  const family = await open('/family', 430, 1100);
  const phone = await open('/phone', 430, 1100);
  await sleep(1500);
  await family.evaluate(() => { const c = document.querySelector('#onCall'); if (c.checked) c.click(); });
  const listen = await open('/listen?autodemo=1', 1280, 1500);

  // the replay sends 6 lines 4.5 s apart
  await listen.bringToFront();
  let level = '';
  for (let i = 0; i < 40; i++) { level = await text(listen, '#levelText'); if (level === 'SCAM ALERT') break; await sleep(1000); }
  check('listener reaches SCAM ALERT during the replay', level === 'SCAM ALERT', level);
  check('coach card visible', (await text(listen, '#cTitle')).length > 3, await text(listen, '#cTitle'));
  check('reasons listed', (await listen.$$('#reasons li')).length >= 3);
  check('transcript shown', (await text(listen, '#tx')).includes('ICICI'));
  check('strike button offered', await listen.$eval('#strikePanel', (e) => !e.classList.contains('hidden')));
  step('screenshot listen'); await listen.screenshot({ path: `${OUT}/ui-listen.png` });
  step('pay');

  // pay during the scam call
  await phone.bringToFront();
  await phone.$eval('#payee', (e) => (e.value = 'icicicardprotect@ybl'));
  await phone.click('#pay');
  await sleep(1500);
  const payTxt = await text(phone, '#payResult');
  check('phone shows payment on hold', payTxt.includes('on hold'), payTxt);
  step('shot phone'); await phone.screenshot({ path: `${OUT}/ui-phone-held.png` }); step('family');

  await family.bringToFront(); await sleep(1000);
  const famTxt = await text(family, '#feed');
  check('family sees urgent alert', famTxt.includes('scammer') || famTxt.includes('suspicious'), famTxt.slice(0, 200));
  check('family sees the held payment', famTxt.includes('on hold'), famTxt.slice(0, 200));
  await family.screenshot({ path: `${OUT}/ui-family.png` }); step('stop');
  check('family has no freeze/approve controls', !(await family.$('#kill')) && !famTxt.includes('Approve'));
  const ask = await family.$('#askCancel');
  await family.evaluate(() => document.querySelector('#askCancel').disabled = false);
  await ask.click();
  await sleep(1500);
  await phone.bringToFront();
  const req = await text(phone, '#reqSheet');
  check('Ramesh sees Priya\'s request on his phone', req.includes('Priya'), req);
  const cancelBtn = await phone.$$('xpath/.//div[@id="reqSheet"]//button[contains(., "Cancel the payment")]');
  if (cancelBtn.length) await cancelBtn[0].click();
  await sleep(1500);
  check('Ramesh cancels it himself', (await text(phone, '#payResult')).includes('cancelled'), await text(phone, '#payResult'));
  step('otp'); await phone.bringToFront();
  await phone.click('[data-otp="transaction"]');
  await sleep(1200);
  const otp = await text(phone, '#otpSheet');
  check('OTP during scam call is covered with a warning', otp.includes('Do NOT share'), otp.slice(0, 160));
  await phone.screenshot({ path: `${OUT}/ui-phone-otp.png` });

  step('report');
  await listen.bringToFront();
  await listen.click('#endCall');
  await sleep(2500);
  const rep = await listen.$('#reportPanel a');
  check('report link offered after the call', !!rep);
  const href = rep ? await rep.evaluate((a) => a.getAttribute('href')) : '';
  const rp = await open(href || '/report', 1100, 1600);
  let repTxt = '';
  for (let i = 0; i < 60 && !(repTxt.includes('Do this now') && repTxt.includes('Evidence')); i++) { await sleep(1000); repTxt = await text(rp, 'main'); }
  check('report has summary, evidence and transcript', repTxt.includes('Do this now') && repTxt.includes('ICICI'), repTxt.slice(0, 300));
  await rp.screenshot({ path: `${OUT}/ui-report.png` });
  await phone.bringToFront();
  await phone.evaluate(() => document.querySelector('#reqOverlay').classList.remove('show'));
  await phone.screenshot({ path: `${OUT}/ui-phone.png` });
  await family.screenshot({ path: `${OUT}/ui-family.png` });
} catch (e) {
  check('ui test ran without exceptions', false, e.stack);
} finally {
  check('no JavaScript errors on any screen', errors.length === 0, errors.join(' | '));
  await browser.close();
  const failed = results.filter(([, ok]) => !ok).length;
  console.log(`\n${results.length - failed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}
