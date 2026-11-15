/* DOM / 交互小工具。所有页面共用。 */
(function () {
  const NB = (window.NB = window.NB || {});
  const U = {};
  U.t = (key, vars) => NB.i18n.t(key, vars);

  const ENT = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  U.esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ENT[c]);
  U.icon = (name, cls) => '<svg class="icon ' + (cls || '') + '" aria-hidden="true"><use href="#i-' + name + '"></use></svg>';

  const pad = (n) => String(n).padStart(2, '0');

  U.fmtTime = function (iso) {
    if (!iso) return '—';
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return '—';
    const diff = Date.now() - t;
    const m = Math.round(diff / 60000);
    if (m < 1) return U.t('time.justNow');
    if (m < 60) return U.t('time.minutesAgo', { n: m });
    const h = Math.round(m / 60);
    if (h < 24) return U.t('time.hoursAgo', { n: h });
    const d = Math.round(h / 24);
    if (d < 7) return U.t('time.daysAgo', { n: d });
    const dt = new Date(t);
    return pad(dt.getMonth() + 1) + '-' + pad(dt.getDate());
  };

  U.fmtClock = function (iso) {
    if (!iso) return '';
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return '';
    return pad(dt.getHours()) + ':' + pad(dt.getMinutes());
  };

  U.fmtDate = function (iso) {
    if (!iso) return '—';
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return '—';
    return dt.toLocaleDateString(NB.i18n.dateLocale(), { year: 'numeric', month: '2-digit', day: '2-digit' });
  };

  U.fmtDateTime = function (iso) {
    if (!iso) return '—';
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return '—';
    return dt.toLocaleString(NB.i18n.dateLocale(), { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  };

  U.initials = function (name) {
    const s = String(name || '?').trim();
    return (s[0] || '?').toUpperCase();
  };

  /* 头像内容：有图就显示图，没有就退回首字母。外层尺寸与形状由现有 .avatar 样式决定。 */
  U.avatarInner = function (name, avatar) {
    return avatar
      ? '<img src="' + U.esc(avatar) + '" alt="">'
      : U.esc(U.initials(name));
  };

  U.num = function (n) {
    return Number(n || 0).toLocaleString(NB.i18n.dateLocale());
  };

  /* 累计 token：小于 1 万给精确数字，之后用 K / M 紧凑显示（最大单位 M）。 */
  U.tokenCount = function (n) {
    const value = Math.max(0, Math.round(Number(n) || 0));
    if (value < 10000) return U.num(value);
    if (value >= 1000000) {
      const m = Math.round(value / 100000) / 10;
      return (m >= 1000 ? Math.round(m) : m) + 'M';
    }
    const k = Math.round(value / 100) / 10;
    // 999,950 以上四舍五入会得到 1000K，进位成 1M 更自然
    return k >= 1000 ? '1M' : k + 'K';
  };

  U.pct = function (used, limit) {
    if (!limit) return 0;
    return Math.max(0, Math.min(100, Math.round((used / limit) * 100)));
  };

  U.badge = function (text, tone) {
    return '<span class="badge ' + (tone || '') + '">' + U.esc(text) + '</span>';
  };

  U.dot = function (tone) {
    return '<span class="dot ' + (tone || '') + '"></span>';
  };

  U.roleName = function (role) {
    return U.t(({ owner: 'role.owner', admin: 'role.admin', user: 'role.user', anon: 'role.anon', setup: 'role.setup' })[role]) || role;
  };

  U.modeLabel = function (mode) {
    return U.t(mode === 'single' ? 'mode.single' : 'mode.multi');
  };

  U.kindName = function (kind) {
    return ({
      exa: 'Exa', tavily: 'Tavily', brave: 'Brave', parallel: 'Parallel',
      serpapi: 'SerpAPI', searxng: 'SearXNG', custom_mcp: 'Custom MCP',
    })[kind] || kind;
  };

  U.severityTone = function (s) {
    return s === 'high' ? 'bad' : s === 'medium' ? 'warn' : s === 'low' ? 'steel' : 'neutral';
  };

  U.severityName = function (s) {
    return ({ high: U.t('高'), medium: U.t('中'), low: U.t('低') })[s] || s;
  };

  U.levelLadder = function (level, opts) {
    const cur = Math.max(0, Math.min(4, parseInt(String(level || 'T0').replace(/\D/g, ''), 10) || 0));
    let html = '<div class="ladder"' + (opts && opts.title ? ' title="' + U.esc(opts.title) + '"' : '') + '>';
    for (let i = 0; i <= 4; i++) {
      // 只高亮传进来的那一档：当前会话档或账号天花板。
      // 低于它是已过的档（橙色），高于它是不可达的档（灰色）。
      const st = i < cur ? 'on' : i === cur ? 'cur' : 'off';
      html += '<div class="step" data-state="' + st + '"><span class="bar"></span><span>T' + i + '</span></div>';
    }
    return html + '</div>';
  };

  U.levelBadge = function (level) {
    return '<span class="badge mono ink">' + U.esc(level || 'T0') + '</span>';
  };

  U.switchHTML = function (on, attrs) {
    return '<button type="button" class="switch" role="switch" aria-checked="' + (on ? 'true' : 'false') + '" ' + (attrs || '') + '></button>';
  };

  U.empty = function (iconName, title, hint) {
    return '<div class="empty">' +
      '<span class="empty-icon">' + U.icon(iconName || 'layers', 'lg') + '</span>' +
      '<div class="empty-title">' + U.esc(title) + '</div>' +
      (hint ? '<div class="empty-hint">' + U.esc(hint) + '</div>' : '') +
      '</div>';
  };

  /* Toast */
  U.toast = function (text, tone) {
    const root = document.getElementById('toast-root');
    if (!root) return;
    const el = document.createElement('div');
    el.className = 'toast ' + (tone || '');
    el.innerHTML = U.icon(tone === 'bad' || tone === 'warn' ? 'alert' : 'check') + '<span>' + U.esc(text) + '</span>';
    root.appendChild(el);
    requestAnimationFrame(() => el.classList.add('in'));
    setTimeout(() => {
      el.classList.remove('in');
      setTimeout(() => el.remove(), 200);
    }, 3200);
  };

  /* Modal */
  U.modal = function (opts) {
    const root = document.getElementById('modal-root');
    const wrap = document.createElement('div');
    wrap.className = 'modal-wrap';
    const footer = opts.footer
      ? '<footer class="modal-foot">' + opts.footer + '</footer>'
      : '';
    wrap.innerHTML =
      '<div class="modal-scrim" data-close></div>' +
      '<section class="modal ' + (opts.wide ? 'wide' : '') + '" role="dialog" aria-modal="true" aria-label="' + U.esc(opts.title || U.t('对话框')) + '">' +
        '<header class="modal-head">' +
          '<div>' + (opts.kicker ? '<div class="kicker">' + U.esc(opts.kicker) + '</div>' : '') +
            '<h2>' + U.esc(opts.title || '') + '</h2></div>' +
          U.t('<button type="button" class="icon-btn" data-close aria-label="关闭">') + U.icon('x') + '</button>' +
        '</header>' +
        '<div class="modal-body">' + (opts.body || '') + '</div>' +
        footer +
      '</section>';
    root.appendChild(wrap);

    let closed = false;
    // 弹窗可以叠加（如新建项目 → 选目录）：只有最上层响应键盘，否则 Esc 会连开它的那层一起关掉
    const isTopmost = () => root.lastElementChild === wrap;
    function close() {
      if (closed) return;
      closed = true;
      wrap.classList.remove('in');
      document.removeEventListener('keydown', onKey, true);
      setTimeout(() => wrap.remove(), 180);
      if (opts.onClose) opts.onClose();
    }
    function onKey(e) {
      if (!isTopmost()) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        close();
      }
      if (e.key === 'Tab') {
        const focusables = wrap.querySelectorAll('button, input, select, textarea, a[href]');
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }
    wrap.querySelectorAll('[data-close]').forEach((el) => { el.onclick = close; });
    document.addEventListener('keydown', onKey, true);
    requestAnimationFrame(() => wrap.classList.add('in'));
    setTimeout(() => {
      const f = wrap.querySelector('input:not([type="hidden"]), select, textarea, button');
      if (f) f.focus();
    }, 40);
    if (opts.onMount) opts.onMount(wrap, close);
    return { el: wrap, close };
  };

  /* 通用头像裁切：选图后框内看到什么，保存的就是什么。
     opts: { file, endpoint, onSaved, kicker, title, saveLabel } */
  U.pickAvatar = function (opts) {
    const o = opts || {};
    const file = o.file;
    if (!file || !/^image\//.test(file.type || '')) {
      U.toast(U.t('avatar.pickImage'), 'bad');
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      U.toast(U.t('avatar.tooLarge'), 'bad');
      return;
    }

    const url = URL.createObjectURL(file);
    let cleanup = null;
    U.modal({
      kicker: o.kicker || 'AVATAR',
      title: o.title || U.t('avatar.adjust'),
      wide: true,
      body: '<div class="avatar-crop">' +
          '<div class="avatar-crop-frame" id="avatar-crop-frame">' +
            '<img id="avatar-crop-img" alt="" draggable="false">' +
          '</div>' +
          '<div class="avatar-crop-side">' +
            '<p class="hint">' + U.esc(U.t('avatar.drag')) + '</p>' +
            '<label class="avatar-zoom"><span>' + U.esc(U.t('avatar.zoom')) + '</span>' +
              '<input type="range" id="avatar-zoom" min="1" max="3" step="0.01" value="1"></label>' +
          '</div>' +
        '</div>',
      footer: '<button type="button" class="btn ghost" data-cancel>' + U.t('common.cancel') + '</button>' +
        '<button type="button" class="btn signal" data-save>' + (o.saveLabel || U.t('avatar.save')) + '</button>',
      onMount: (wrap, close) => {
        const frame = wrap.querySelector('#avatar-crop-frame');
        const img = wrap.querySelector('#avatar-crop-img');
        const zoom = wrap.querySelector('#avatar-zoom');
        let frameSize = 0;
        let base = 1;
        let scale = 1;
        let x = 0;
        let y = 0;
        let drag = null;

        const clamp = () => {
          const width = img.naturalWidth * scale;
          const height = img.naturalHeight * scale;
          x = Math.min(0, Math.max(frameSize - width, x));
          y = Math.min(0, Math.max(frameSize - height, y));
          img.style.width = width + 'px';
          img.style.height = height + 'px';
          img.style.left = x + 'px';
          img.style.top = y + 'px';
        };

        const reset = () => {
          frameSize = frame.clientWidth || 320;
          base = Math.max(frameSize / img.naturalWidth, frameSize / img.naturalHeight);
          scale = base;
          x = (frameSize - img.naturalWidth * scale) / 2;
          y = (frameSize - img.naturalHeight * scale) / 2;
          zoom.value = '1';
          clamp();
        };

        img.onload = reset;
        img.onerror = () => {
          U.toast(U.t('avatar.pickImage'), 'bad');
          close();
        };
        img.src = url;

        frame.addEventListener('pointerdown', (e) => {
          if (e.button !== 0) return;
          drag = { sx: e.clientX, sy: e.clientY, x, y };
          frame.setPointerCapture(e.pointerId);
        });
        frame.addEventListener('pointermove', (e) => {
          if (!drag) return;
          x = drag.x + (e.clientX - drag.sx);
          y = drag.y + (e.clientY - drag.sy);
          clamp();
        });
        const stopDrag = () => { drag = null; };
        frame.addEventListener('pointerup', stopDrag);
        frame.addEventListener('pointercancel', stopDrag);

        zoom.addEventListener('input', () => {
          const next = base * Number(zoom.value || 1);
          const cx = frameSize / 2;
          const cy = frameSize / 2;
          const px = (cx - x) / scale;
          const py = (cy - y) / scale;
          scale = next;
          x = cx - px * scale;
          y = cy - py * scale;
          clamp();
        });

        wrap.querySelector('[data-cancel]').onclick = close;
        wrap.querySelector('[data-save]').onclick = () => {
          if (!img.naturalWidth || !frameSize) return;
          const size = 256;
          const canvas = document.createElement('canvas');
          canvas.width = canvas.height = size;
          const ctx = canvas.getContext('2d');
          const sx = (0 - x) / scale;
          const sy = (0 - y) / scale;
          const side = frameSize / scale;
          ctx.drawImage(img, sx, sy, side, side, 0, 0, size, size);
          const avatar = canvas.toDataURL('image/jpeg', 0.86);
          NB.api.put(o.endpoint, { avatar }).then((data) => {
            close();
            U.toast(U.t('avatar.updated'));
            if (o.onSaved) o.onSaved(data.avatar || avatar);
          }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
        };

        cleanup = () => URL.revokeObjectURL(url);
      },
      onClose: () => { if (cleanup) cleanup(); },
    });
  };

  U.confirm = function (opts) {
    return new Promise((resolve) => {
      let settled = false;
      const done = (v) => {
        if (settled) return;
        settled = true;
        resolve(v);
      };
      const m = U.modal({
        kicker: opts.kicker || U.t('请确认'),
        title: opts.title || U.t('确认操作'),
        body: '<p class="muted" style="font-size:14px;line-height:1.7">' + U.esc(opts.message || '') + '</p>' +
          (opts.body || ''),
        footer: U.t('<button type="button" class="btn ghost" data-cancel>取消</button>') +
          '<button type="button" class="btn ' + (opts.danger ? 'danger' : 'signal') + '" data-ok>' + U.esc(opts.okLabel || U.t('确认')) + '</button>',
        onClose: () => done(false),
        onMount: (wrap, close) => {
          wrap.querySelector('[data-cancel]').onclick = () => { done(false); close(); };
          wrap.querySelector('[data-ok]').onclick = () => {
            if (opts.onConfirm) {
              const r = opts.onConfirm(wrap);
              if (r === false) return;
            }
            done(true);
            close();
          };
        },
      });
    });
  };

  /* 下拉菜单 */
  let activeMenu = null;
  function closeMenu() {
    if (!activeMenu) return;
    const m = activeMenu;
    activeMenu = null;
    document.removeEventListener('click', m.outside, true);
    document.removeEventListener('keydown', m.key, true);
    m.el.remove();
  }
  U.closeMenu = closeMenu;
  U.menu = function (anchor, items, opts) {
    opts = opts || {};
    closeMenu();
    const el = document.createElement('div');
    el.className = 'menu' + (opts.className ? ' ' + opts.className : '');
    el.setAttribute('role', 'menu');
    let html = '';
    if (opts.head) html += '<div class="menu-head">' + U.esc(opts.head) + '</div>';
    items.forEach((it, i) => {
      if (it.group) { html += '<div class="menu-group">' + U.esc(it.group) + '</div>'; return; }
      if (it.sep) { html += '<div class="menu-sep"></div>'; return; }
      html += '<button type="button" class="menu-item ' + (it.active ? 'active' : '') + '" role="menuitem" data-i="' + i + '"' + (it.disabled ? ' disabled' : '') + '>' +
        (it.active ? U.icon('check') : '') +
        '<span class="menu-label">' + U.esc(it.label) + '</span>' +
        (it.hint ? '<span class="menu-hint">' + U.esc(it.hint) + '</span>' : '') +
        '</button>';
    });
    el.innerHTML = html;
    document.body.appendChild(el);

    const r = anchor.getBoundingClientRect();
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    let left = opts.align === 'left' ? r.left : r.right - w;
    left = Math.max(10, Math.min(window.innerWidth - w - 10, left));
    let top = r.bottom + 6;
    if (top + h > window.innerHeight - 10) top = Math.max(10, r.top - h - 6);
    el.style.left = left + 'px';
    el.style.top = top + 'px';

    const outside = (e) => {
      if (!el.contains(e.target)) closeMenu();
    };
    const key = (e) => { if (e.key === 'Escape') closeMenu(); };
    setTimeout(() => {
      document.addEventListener('click', outside, true);
      document.addEventListener('keydown', key, true);
    }, 0);

    el.querySelectorAll('[data-i]').forEach((btn) => {
      btn.onclick = () => {
        const it = items[Number(btn.dataset.i)];
        closeMenu();
        if (it && it.onPick) it.onPick();
      };
    });

    activeMenu = { el, outside, key };
    return el;
  };

  /* 小工具 */
  U.debounce = function (fn, ms) {
    let t;
    return function () {
      const args = arguments;
      clearTimeout(t);
      t = setTimeout(() => fn.apply(null, args), ms || 200);
    };
  };

  U.sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  U.rand = function (arr) { return arr[Math.floor(Math.random() * arr.length)]; };

  U.copy = async function (text) {
    // 非安全上下文（局域网 http）没有 navigator.clipboard，退回 textarea + execCommand
    const fallback = function () {
      const area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.top = '-1000px';
      document.body.appendChild(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand && document.execCommand('copy'); } catch (e) { ok = false; }
      area.remove();
      return ok;
    };

    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        U.toast(U.t('已复制'));
        return;
      }
      if (!fallback()) throw new Error('copy unavailable');
      U.toast(U.t('已复制'));
    } catch (e) {
      if (fallback()) {
        U.toast(U.t('已复制'));
        return;
      }
      U.toast(U.t('复制失败，请手动选择'), 'warn');
    }
  };

  /* 代码块复制：按钮由 markdown.js 渲染，事件委托绑一次，页面重绘后不用重新挂。 */
  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('click', function (event) {
      const btn = event.target && event.target.closest && event.target.closest('[data-copy-code]');
      if (!btn) return;
      const block = btn.closest('.md-code');
      const code = block && block.querySelector('code');
      if (code) U.copy(code.textContent || '');
    });
  }

  NB.ui = U;
})();
