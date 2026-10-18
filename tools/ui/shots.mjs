import puppeteer from 'puppeteer-core';
const OUT = process.argv[2], BASE = 'http://localhost:8765';
const b = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: 'new', protocolTimeout: 60000, args: ['--no-sandbox'] });
for (const [path, theme, w, hgt] of [['/', 'light', 1280, 900], ['/listen', 'light', 1280, 900], ['/phone', 'dark', 430, 900], ['/family', 'light', 430, 900]]) {
  const p = await b.newPage(); await p.setViewport({ width: w, height: hgt });
  await p.goto(BASE + path, { waitUntil: 'domcontentloaded' });
  await p.evaluate((t) => { localStorage.setItem('riposte-theme', t); }, theme);
  await p.reload({ waitUntil: 'domcontentloaded' }); await new Promise(r => setTimeout(r, 1500));
  await p.screenshot({ path: `${OUT}/d${path.replace('/', '_') || '_home'}-${theme}.png` }); await p.close();
}
await b.close();
