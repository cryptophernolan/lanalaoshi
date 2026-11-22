/* Glue between popup and FBCleaner. Only acts on the Activity Log of the logged-in account. */
(function () {
  'use strict';
  if (window.__fbcContentLoaded) return;
  window.__fbcContentLoaded = true;

  const COOLDOWN_MS = 24 * 60 * 60 * 1000; // after any FB warning: refuse to run for 24h

  const isActivityLog = () => /\/allactivity/.test(location.pathname + location.search);

  const cleaner = new window.FBCleaner({
    onUpdate: (snap) => {
      chrome.storage.local.set({ fbcState: snap });
      chrome.runtime.sendMessage({ type: 'fbc-update', state: snap }).catch(() => {});
    }
  });

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    (async () => {
      if (msg.cmd === 'status') {
        return sendResponse({ ok: true, onActivityLog: isActivityLog(), state: cleaner.snapshot() });
      }
      if (msg.cmd === 'stop') {
        cleaner.stop('người dùng bấm Stop');
        return sendResponse({ ok: true });
      }
      if (msg.cmd === 'start') {
        if (!isActivityLog()) {
          return sendResponse({ ok: false, error: 'Hãy mở Nhật ký hoạt động (facebook.com/me/allactivity) trước.' });
        }
        const { fbcCooldownUntil = 0 } = await chrome.storage.local.get('fbcCooldownUntil');
        if (!msg.settings.dryRun && Date.now() < fbcCooldownUntil) {
          return sendResponse({ ok: false, error: 'Facebook đã cảnh báo trước đó. Tạm khoá đến ' +
            new Date(fbcCooldownUntil).toLocaleString() });
        }
        sendResponse({ ok: true });
        await cleaner.run(msg.settings);
        if (cleaner.blockedReason) {
          await chrome.storage.local.set({ fbcCooldownUntil: Date.now() + COOLDOWN_MS });
        }
        return;
      }
      sendResponse({ ok: false, error: 'unknown cmd' });
    })();
    return true;
  });
})();
