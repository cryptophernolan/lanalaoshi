/*
 * FB Activity Cleaner — core logic (no chrome.* APIs here, so it can be tested on a mock page).
 *
 * Works ONLY on the Activity Log of the account that is already logged in.
 * It clicks the same "..." -> Delete/Move to trash -> Confirm buttons a human would,
 * at a human pace, and stops immediately if Facebook shows any rate-limit / block / checkpoint.
 */
(function (global) {
  'use strict';

  const DEFAULTS = {
    dryRun: true,          // only highlight items, do not delete
    minDelaySec: 6,        // random pause between two deletions
    maxDelaySec: 14,
    batchSize: 20,         // after this many deletions...
    batchPauseSec: 300,    // ...take a long break (5 min)
    maxPerSession: 150,    // hard cap per run
    removeTags: false,     // also "Remove tag" on posts you were tagged in
    removeReactions: false // also "Unlike" / "Remove reaction"
  };

  // Hard floors: UI cannot go faster than this.
  const FLOOR = { minDelaySec: 3, batchPauseSec: 60, maxPerSession: 500 };

  const norm = (s) => (s || '').toLowerCase().normalize('NFC').replace(/\s+/g, ' ').trim();

  const L = {
    actionButton: ['action options', 'tùy chọn hành động', 'tuỳ chọn hành động', 'more options', 'xem thêm tùy chọn'],
    delete: ['delete', 'xóa', 'xoá', 'move to trash', 'chuyển vào thùng rác', 'move to recycle bin', 'delete post', 'xóa bài viết'],
    removeTag: ['remove tag', 'gỡ thẻ'],
    removeReaction: ['unlike', 'bỏ thích', 'remove reaction', 'gỡ cảm xúc'],
    confirm: ['delete', 'xóa', 'xoá', 'move', 'chuyển', 'remove', 'gỡ', 'confirm', 'xác nhận', 'ok'],
    blocked: [
      "you're temporarily blocked", 'you’re temporarily blocked', 'temporarily blocked',
      'bạn tạm thời bị chặn', 'tạm thời bị chặn',
      'going too fast', 'quá nhanh',
      'we limit how often', 'chúng tôi giới hạn tần suất',
      'misusing this feature', 'lạm dụng tính năng',
      'confirm your identity', 'xác nhận danh tính',
      'suspicious activity', 'hoạt động đáng ngờ'
    ]
  };

  function matchesAny(text, list, exact) {
    const t = norm(text);
    if (!t) return false;
    return list.some((w) => (exact ? t === w : t.includes(w)));
  }

  function labelOf(el) {
    return el.getAttribute('aria-label') || el.innerText || el.textContent || '';
  }

  function visible(el) {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
  }

  class Cleaner {
    constructor(opts = {}) {
      this.doc = opts.document || global.document;
      this.win = opts.window || global;
      this.sleepImpl = opts.sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
      this.random = opts.random || Math.random;
      this.onUpdate = opts.onUpdate || (() => {});
      this.settings = Cleaner.sanitize({});
      this.reset();
    }

    static sanitize(s) {
      const o = Object.assign({}, DEFAULTS, s || {});
      const num = (v, d) => (v === '' || v === null || !Number.isFinite(Number(v)) ? d : Number(v));
      o.minDelaySec = Math.max(FLOOR.minDelaySec, num(o.minDelaySec, DEFAULTS.minDelaySec));
      o.maxDelaySec = Math.max(o.minDelaySec, num(o.maxDelaySec, DEFAULTS.maxDelaySec));
      o.batchSize = Math.max(1, Math.floor(num(o.batchSize, DEFAULTS.batchSize)));
      o.batchPauseSec = Math.max(FLOOR.batchPauseSec, num(o.batchPauseSec, DEFAULTS.batchPauseSec));
      o.maxPerSession = Math.min(FLOOR.maxPerSession, Math.max(1, Math.floor(num(o.maxPerSession, DEFAULTS.maxPerSession))));
      o.dryRun = o.dryRun !== false;
      o.removeTags = !!o.removeTags;
      o.removeReactions = !!o.removeReactions;
      return o;
    }

    reset() {
      this.running = false;
      this.state = { status: 'idle', done: 0, skipped: 0, scanned: 0, message: '', log: [] };
    }

    log(msg) {
      const line = `[${new Date().toLocaleTimeString()}] ${msg}`;
      this.state.log.push(line);
      if (this.state.log.length > 200) this.state.log.shift();
      this.state.message = msg;
      this.onUpdate(this.snapshot());
    }

    snapshot() {
      return JSON.parse(JSON.stringify(Object.assign({ running: this.running, settings: this.settings }, this.state)));
    }

    stop(reason) {
      if (!this.running) return;
      this.running = false;
      this.state.status = 'stopped';
      this.log('Đã dừng: ' + (reason || 'người dùng bấm Stop'));
    }

    // Any sign that Facebook is throttling / suspicious -> must stop.
    detectBlock() {
      const href = (this.win.location && this.win.location.href) || '';
      if (/\/checkpoint|\/login/.test(href)) return 'Facebook chuyển sang trang checkpoint/login';
      const dialogs = this.doc.querySelectorAll('[role="dialog"], [role="alertdialog"], [role="alert"]');
      for (const d of dialogs) {
        if (matchesAny(d.innerText || d.textContent, L.blocked)) {
          return 'Facebook hiện cảnh báo: "' + norm(d.innerText || d.textContent).slice(0, 120) + '"';
        }
      }
      return null;
    }

    async sleep(ms) {
      // sleep in slices so Stop / block detection react quickly
      let left = ms;
      while (this.running && left > 0) {
        const slice = Math.min(500, left);
        await this.sleepImpl(slice);
        left -= slice;
        const b = this.detectBlock();
        if (b) { this.blockedReason = b; this.stop(b); }
      }
    }

    randDelay() {
      const { minDelaySec, maxDelaySec } = this.settings;
      return (minDelaySec + this.random() * (maxDelaySec - minDelaySec)) * 1000;
    }

    async waitFor(fn, timeoutMs = 4000) {
      for (let t = 0; t <= timeoutMs; t += 150) {
        const v = fn();
        if (v) return v;
        await this.sleepImpl(150);
      }
      return null;
    }

    nextActionButton() {
      const cands = this.doc.querySelectorAll('[role="button"][aria-label], button[aria-label]');
      for (const b of cands) {
        if (b.dataset.fbcDone) continue;
        if (b.closest('[role="dialog"], [role="menu"], [role="banner"], [role="navigation"]')) continue;
        if (!matchesAny(b.getAttribute('aria-label'), L.actionButton)) continue;
        if (!visible(b)) continue;
        return b;
      }
      return null;
    }

    wantedMenuLabels() {
      let l = L.delete.slice();
      if (this.settings.removeTags) l = l.concat(L.removeTag);
      if (this.settings.removeReactions) l = l.concat(L.removeReaction);
      return l;
    }

    findMenuItem() {
      const items = this.doc.querySelectorAll('[role="menu"] [role="menuitem"], [role="menuitem"]');
      const wanted = this.wantedMenuLabels();
      for (const it of items) {
        if (visible(it) && matchesAny(labelOf(it), wanted)) return it;
      }
      return null;
    }

    findConfirmButton() {
      const dialogs = [...this.doc.querySelectorAll('[role="dialog"]')].filter(visible);
      for (const d of dialogs.reverse()) {
        const btns = d.querySelectorAll('[role="button"], button');
        for (const b of btns) {
          const t = labelOf(b);
          if (matchesAny(t, ['cancel', 'hủy', 'huỷ', 'close', 'đóng'])) continue;
          if (visible(b) && matchesAny(t, L.confirm)) return b;
        }
      }
      return null;
    }

    dismissMenu() {
      this.doc.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      const esc = this.doc.activeElement;
      if (esc) esc.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    }

    async processOne(btn) {
      btn.dataset.fbcDone = '1';
      this.state.scanned++;
      btn.scrollIntoView({ block: 'center' });

      if (this.settings.dryRun) {
        btn.style.outline = '3px solid #f59e0b';
        this.state.done++;
        this.log(`[DRY-RUN] Đánh dấu mục #${this.state.done} (không xoá)`);
        return true;
      }

      btn.click();
      const item = await this.waitFor(() => this.findMenuItem(), 3000);
      if (!item) {
        this.dismissMenu();
        this.state.skipped++;
        this.log('Bỏ qua: mục này không có lựa chọn Xoá/Gỡ (có thể không phải nội dung của bạn)');
        return false;
      }
      const what = norm(labelOf(item));
      item.click();

      // Some actions need confirmation, some don't.
      const confirm = await this.waitFor(() => this.findConfirmButton(), 2500);
      if (confirm) {
        confirm.click();
        await this.waitFor(() => !confirm.isConnected || !visible(confirm), 5000);
      }

      const b = this.detectBlock();
      if (b) { this.blockedReason = b; this.stop(b); return false; }

      this.state.done++;
      this.log(`Đã "${what}" mục #${this.state.done}`);
      return true;
    }

    async run(settings) {
      if (this.running) return;
      this.reset();
      this.settings = Cleaner.sanitize(settings);
      this.blockedReason = null;
      this.running = true;
      this.state.status = 'running';
      this.log(`Bắt đầu (${this.settings.dryRun ? 'DRY-RUN' : 'XOÁ THẬT'}, tối đa ${this.settings.maxPerSession} mục, ` +
        `${this.settings.minDelaySec}-${this.settings.maxDelaySec}s/mục)`);

      let emptyScrolls = 0;
      while (this.running) {
        const b = this.detectBlock();
        if (b) { this.blockedReason = b; this.stop(b); break; }

        if (this.state.done >= this.settings.maxPerSession) {
          this.log('Đạt giới hạn mỗi phiên. Hãy nghỉ vài giờ trước khi chạy tiếp.');
          break;
        }

        const btn = this.nextActionButton();
        if (!btn) {
          if (emptyScrolls >= 3) { this.log('Không còn mục nào trên trang.'); break; }
          emptyScrolls++;
          this.win.scrollTo(0, this.doc.body.scrollHeight);
          await this.sleep(2500);
          continue;
        }
        emptyScrolls = 0;

        const ok = await this.processOne(btn);
        if (!this.running) break;

        if (ok && !this.settings.dryRun && this.state.done % this.settings.batchSize === 0) {
          this.log(`Nghỉ dài ${this.settings.batchPauseSec}s sau ${this.settings.batchSize} mục…`);
          await this.sleep(this.settings.batchPauseSec * 1000);
        } else {
          await this.sleep(this.settings.dryRun ? 300 : this.randDelay());
        }
      }

      if (this.state.status === 'running') this.state.status = 'finished';
      this.running = false;
      this.log(`Kết thúc. Thành công: ${this.state.done}, bỏ qua: ${this.state.skipped}`);
      return this.snapshot();
    }
  }

  Cleaner.DEFAULTS = DEFAULTS;
  Cleaner.FLOOR = FLOOR;
  Cleaner.LABELS = L;
  global.FBCleaner = Cleaner;
})(typeof window !== 'undefined' ? window : globalThis);
