// Run: node test/run-tests.js   (uses the pre-installed Playwright + Chromium)
// Simulation only: the mock page imitates Facebook's Activity Log UI and a rate limiter.
// It does NOT reproduce Facebook's real (secret, server-side) detection.
const path = require('path');
const { chromium } = require('playwright');

const PAGE = 'file://' + path.resolve(__dirname, 'mock-activity-log.html');
let failures = 0;
const check = (cond, msg) => { console.log((cond ? '  PASS ' : '  FAIL ') + msg); if (!cond) failures++; };

async function runScenario(browser, name, settings, rule) {
  const page = await browser.newPage();
  await page.goto(PAGE);
  const r = await page.evaluate(async ({ settings, rule }) => {
    if (rule) window.__rateRule = rule;
    const pauses = [];
    const c = new window.FBCleaner({
      // virtual clock: sleeping advances simulated time instantly
      sleep: (ms) => { window.__vclock += ms; pauses.push(ms); return new Promise((r) => setTimeout(r, 0)); }
    });
    const snap = await c.run(settings);
    return { snap, deleted: window.__deleted.length, blockedShown: !!window.__blockedShown,
      blockedReason: c.blockedReason, settings: c.settings, vclockSec: window.__vclock / 1000,
      remaining: document.querySelectorAll('.item').length, marked: document.querySelectorAll('[data-fbc-done]').length };
  }, { settings, rule });
  await page.close();
  console.log(`\n# ${name}\n  -> deleted=${r.deleted} skipped=${r.snap.skipped} status=${r.snap.status} ` +
    `simulated time=${Math.round(r.vclockSec)}s${r.blockedReason ? ' | ' + r.blockedReason : ''}`);
  return r;
}

(async () => {
  const browser = await chromium.launch();

  let r = await runScenario(browser, 'Dry-run (mặc định)', {});
  check(r.deleted === 0, 'dry-run không xoá gì');
  check(r.marked === 25, 'dry-run đánh dấu đủ 25 mục (kể cả sau khi cuộn tải thêm)');

  // Mock limiter: >5 deletes in 30s => block
  const rule = { windowMs: 30000, maxInWindow: 5 };
  r = await runScenario(browser, 'Xoá thật, tốc độ mặc định (6–14s, nghỉ 300s sau 20 mục)', { dryRun: false }, rule);
  check(!r.blockedShown, 'không kích hoạt cảnh báo của trang mô phỏng');
  check(r.deleted === 22, 'xoá 22 mục của mình');
  check(r.snap.skipped === 3, 'bỏ qua 3 mục không có nút Xoá (bài của người khác)');
  check(r.snap.log.some((l) => l.includes('Nghỉ dài')), 'có nghỉ dài sau mỗi batch');

  r = await runScenario(browser, 'Cố tình đặt quá nhanh (minDelay=0, batchPause=0, max=9999)',
    { dryRun: false, minDelaySec: 0, maxDelaySec: 0, batchPauseSec: 0, maxPerSession: 9999 }, rule);
  check(r.settings.minDelaySec === 3 && r.settings.batchPauseSec === 60 && r.settings.maxPerSession === 500,
    'cấu hình bị ép về mức sàn (3s / 60s / 500)');
  check(r.blockedShown, 'trang mô phỏng hiện "Temporarily Blocked" khi xoá 3s/mục');
  check(r.snap.status === 'stopped' && !!r.blockedReason, 'extension phát hiện cảnh báo và tự dừng');
  check(r.deleted <= rule.maxInWindow + 1, 'không xoá thêm mục nào sau khi bị cảnh báo');

  r = await runScenario(browser, 'Giới hạn mỗi phiên = 7', { dryRun: false, maxPerSession: 7 }, rule);
  check(r.deleted === 7, 'dừng đúng ở 7 mục');

  await browser.close();

  // Load the real extension to make sure the manifest/scripts are valid.
  console.log('\n# Nạp extension vào Chromium');
  const os = require('os'); const fs = require('fs');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fbc-'));
  const ext = path.resolve(__dirname, '..');
  const ctx = await chromium.launchPersistentContext(dir, {
    channel: 'chromium', args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`] });
  // Unpacked extension id = first 32 hex chars of sha256(abs path), mapped 0-f -> a-p
  const id = require('crypto').createHash('sha256').update(ext).digest('hex').slice(0, 32)
    .split('').map((c) => String.fromCharCode(97 + parseInt(c, 16))).join('');
  {
    const p = await ctx.newPage();
    const errs = []; p.on('pageerror', (e) => errs.push(e.message));
    const resp = await p.goto(`chrome-extension://${id}/popup.html`).catch((e) => null);
    check(!!resp, 'Chromium nạp extension thành công (id=' + id + ')');
    await p.waitForTimeout(500);
    check((await p.inputValue('#minDelaySec')) === '6', 'popup mở được và nạp cấu hình mặc định');
    check(errs.length === 0, 'popup không có lỗi JS ' + (errs.join('; ')));
  }
  await ctx.close();

  console.log(failures ? `\n${failures} FAILED` : '\nALL PASSED');
  process.exit(failures ? 1 : 0);
})();
