// Mobile viewport smoke check; not a physical iOS/Safari or Android device test.
import { chromium, devices } from '../../node_modules/playwright-core/index.mjs';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { createHash, X509Certificate } from 'node:crypto';

const config = join(homedir(), '.config/herdr-web-ui');
const deployment = JSON.parse(readFileSync(join(config, 'deployment.json'), 'utf8'));
const raw = readFileSync(join(config, 'lan.env'), 'utf8').split('\n').find(line => line.startsWith('HERDR_WEB_TOKEN=')).slice('HERDR_WEB_TOKEN='.length);
const token = raw.startsWith('"') ? JSON.parse(raw) : raw;
const cert = new X509Certificate(readFileSync(deployment.leaf_file));
const pin = createHash('sha256').update(cert.publicKey.export({type:'spki',format:'der'})).digest('base64');
const browser = await chromium.launch({headless:true,args:['--ignore-certificate-errors-spki-list=' + pin]});
try {
  for (const device of ['iPhone 13', 'Pixel 7']) {
    const context = await browser.newContext({...devices[device], locale:'en-US'});
    const page = await context.newPage();
    await page.goto('https://' + deployment.host + ':' + deployment.https_port + '/');
    await page.getByTestId('token-gate').waitFor();
    if (await page.evaluate(()=>fetch('/api/session').then(r=>r.status)) !== 401) throw new Error('匿名存取未遭拒絕');
    await page.locator('.access-gate-alt summary').click();
    await page.locator('#access-gate-token').fill(token);
    await page.locator('.access-gate-alt button[type=submit]').click();
    await page.getByTestId('token-gate').waitFor({state:'detached',timeout:20000});
    await page.waitForFunction(async()=>!!(await navigator.serviceWorker.getRegistration())?.active, null, {timeout:15000});
    const checks = await page.evaluate(async()=>({secure:window.isSecureContext, session:(await fetch('/api/session')).status, sw:!!(await navigator.serviceWorker.getRegistration())?.active, manifest:document.querySelector('link[rel=manifest]')!==null}));
    if(!checks.secure || checks.session!==200 || !checks.sw || !checks.manifest) throw new Error('手機版檢查未通過');
    console.log(device + '：通過密碼登入、安全環境、工作階段、Service Worker 與 Manifest 檢查');
    await context.close();
  }
} finally { await browser.close(); }
