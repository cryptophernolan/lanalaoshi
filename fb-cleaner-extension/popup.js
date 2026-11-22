const DEFAULTS = { dryRun: true, minDelaySec: 6, maxDelaySec: 14, batchSize: 20, batchPauseSec: 300,
  maxPerSession: 150, removeTags: false, removeReactions: false };
const FIELDS = Object.keys(DEFAULTS);
const $ = (id) => document.getElementById(id);

function notice(msg) { $('notice').hidden = !msg; $('notice').textContent = msg || ''; }

function render(state) {
  if (!state) return;
  $('stats').textContent = `Trạng thái: ${state.status} · xong: ${state.done} · bỏ qua: ${state.skipped}`;
  $('log').textContent = (state.log || []).join('\n');
  $('log').scrollTop = $('log').scrollHeight;
}

function readForm() {
  const s = {};
  for (const k of FIELDS) {
    const el = $(k);
    s[k] = el.type === 'checkbox' ? el.checked : Number(el.value);
  }
  return s;
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function send(cmd, extra = {}) {
  const tab = await activeTab();
  if (!tab || !/^https:\/\/www\.facebook\.com\//.test(tab.url || '')) {
    notice('Tab hiện tại không phải facebook.com.');
    return null;
  }
  try { return await chrome.tabs.sendMessage(tab.id, Object.assign({ cmd }, extra)); }
  catch (e) { notice('Không kết nối được trang. Hãy tải lại (F5) tab Facebook.'); return null; }
}

(async () => {
  const { fbcSettings, fbcState, fbcCooldownUntil } = await chrome.storage.local.get(['fbcSettings', 'fbcState', 'fbcCooldownUntil']);
  const s = Object.assign({}, DEFAULTS, fbcSettings);
  for (const k of FIELDS) { const el = $(k); if (el.type === 'checkbox') el.checked = !!s[k]; else el.value = s[k]; }
  render(fbcState);
  if (fbcCooldownUntil && Date.now() < fbcCooldownUntil) {
    notice('Facebook đã cảnh báo ở lần chạy trước. Chế độ xoá thật bị khoá đến ' + new Date(fbcCooldownUntil).toLocaleString());
  }
  const st = await send('status');
  if (st && st.state) render(st.state);
  if (st && !st.onActivityLog) notice('Hãy mở Nhật ký hoạt động trước khi Start.');
})();

$('openLog').onclick = (e) => { e.preventDefault(); chrome.tabs.create({ url: 'https://www.facebook.com/me/allactivity' }); };

$('start').onclick = async () => {
  const settings = readForm();
  await chrome.storage.local.set({ fbcSettings: settings });
  if (!settings.dryRun && !confirm(`Xoá THẬT tối đa ${settings.maxPerSession} mục? Không thể hoàn tác.`)) return;
  notice('');
  const r = await send('start', { settings });
  if (r && !r.ok) notice(r.error);
};
$('stop').onclick = () => send('stop');

chrome.runtime.onMessage.addListener((m) => { if (m.type === 'fbc-update') render(m.state); });
