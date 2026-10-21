// Run: node tests/browser.cjs (playwright required; server listening on FNSVN_TEST_URL).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
async function main() {
  const browser = await chromium.launch({ headless: true });
  const outer = await browser.newPage({ viewport: { width: 1440, height: 1000 }, ignoreHTTPSErrors: process.env.FNSVN_TEST_IGNORE_HTTPS === '1' });
  let page = outer;
  let desktop;
  const errors = [];
  outer.on('pageerror', e => errors.push(e.message));
  outer.on('console', e => { if (e.type() === 'error') errors.push(e.text()); });
  const target = process.env.FNSVN_TEST_URL || 'http://127.0.0.1:18880';
  if (process.env.FNSVN_TEST_DESKTOP_URL) {
    await outer.goto(process.env.FNSVN_TEST_DESKTOP_URL);
    page = outer.frameLocator('#fnos-app');
  } else if (process.env.FNSVN_TEST_IFRAME === '1') {
    // Embed from another port, as the HTTP fnOS desktop does.
    desktop = http.createServer((req, res) => {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end(`<html><body style="margin:0"><iframe id="fnos-app" src="${target}" style="border:0;width:100%;height:100dvh"></iframe></body></html>`);
    });
    await new Promise(resolve => desktop.listen(0, '127.0.0.1', resolve));
    await outer.goto(`http://127.0.0.1:${desktop.address().port}`);
    page = outer.frameLocator('#fnos-app');
  } else await outer.goto(target);
  await page.locator('#auth-screen').waitFor({ state: 'visible' });
  await page.locator('#auth-form input[name=username]').fill('previewadmin');
  await page.locator('#auth-form input[name=password]').fill('preview-admin-password');
  await page.locator('#auth-submit').click();
  await page.locator('#app').waitFor({ state: 'visible' });
  const queryPath = await page.locator('body').evaluate(() => appUrl('/api/repositories/GameProject?path=a%2Fb'));
  assert.ok(queryPath.endsWith('/api/repositories/GameProject?path=a%2Fb'), 'repository subdirectory query preserved');
  const overview = await page.locator('body').evaluate(async () => (await fetch(new URL('api/overview', document.baseURI))).json());
  assert.equal(await page.locator('#repository-storage-path').textContent(), overview.repositoriesPath);
  assert.ok((await page.locator('#active-svn-port').textContent()).includes(String(overview.svnPort)));
  await page.locator('#page-action').click();
  await page.locator('#modal input[name=name]').fill('GameProject');
  await page.locator('#modal-form button[type=submit]').click();
  await page.getByText('先创建 SVN 账户', { exact: true }).waitFor();
  await page.locator('[data-view=users]').click();
  await page.locator('#page-action').click();
  await page.locator('#modal input[name=svnAccountName]').fill('developer');
  await page.locator('#modal input[name=svnAccountSecret]').fill('developer-password');
  await page.locator('#modal-form [data-modal-submit]').click();
  await page.locator('[data-reset-password=developer]').waitFor();
  await page.locator('[data-view=repositories]').click();
  const displayedUrl = await page.locator('.repo-url code').first().textContent();
  assert.ok(overview.svnPort === 3690 ? !displayedUrl.includes(':3690/') : displayedUrl.includes(`:${overview.svnPort}/`));
  await page.locator('[data-permissions=GameProject]').click();
  await page.locator('[data-permission-user=developer]').selectOption('rw');
  await page.getByRole('button', { name: '保存权限设置' }).click();
  await page.getByText('仓库权限已保存', { exact: true }).waitFor();
  await page.locator('[data-tab=files]').click();
  await page.getByText('此目录暂无文件。通过 SVN 客户端提交后，即可在此浏览。').waitFor();
  await page.locator('[data-backup=GameProject]').click();
  await page.getByText(/备份已创建：/).waitFor();
  await page.locator('[data-view=backups]').click();
  const backup = page.getByRole('link', { name: '下载备份', exact: true });
  await backup.waitFor();
  const downloadPath = await backup.getAttribute('href');
  const expectedBase = new URL(target).pathname.replace(/\/$/, '');
  assert.ok(downloadPath.startsWith(expectedBase + '/api/backups/'), 'backup stays under application path');
  const [download] = await Promise.all([outer.waitForEvent('download'), backup.click()]);
  assert.equal(await download.failure(), null, 'backup download succeeds with session cookie');
  await page.locator('[data-view=repositories]').click();
  await outer.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.locator('body').evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'mobile horizontal overflow');
  await page.locator('#logout').click();
  await page.locator('#auth-screen').waitFor({ state: 'visible' });
  assert.deepEqual(errors, [], 'browser console errors');
  await browser.close();
  if (desktop) await new Promise(resolve => desktop.close(resolve));
  console.log('Browser workflow passed: setup, repository, user, permissions, browsing, backup, mobile, logout, actual storage/port' + (desktop ? ', cross-port iframe' : ''));
}
main().catch(e => { console.error(e); process.exit(1); });
