/* 三态主题：light / dark / system
   在 <head> 同步执行，第一次绘制前把 data-theme 写到 <html>，避免暗色下闪白。
   组件样式只依赖 CSS 变量与 [data-theme] 属性，切换不需要重载页面。 */
(function () {
  'use strict';

  var root = typeof window !== 'undefined' ? window : globalThis;
  root.NB = root.NB || {};
  var NB = root.NB;

  var KEY = 'nb.theme';
  var MODES = ['light', 'dark', 'system'];
  var ICONS = { light: 'sun', dark: 'moon', system: 'monitor' };
  var FALLBACK_PAPER = { light: '#f2eee4', dark: '#1d1a16' };

  var mode = 'system';
  var resolved = 'light';
  var listeners = [];

  function storage() {
    try {
      return typeof localStorage !== 'undefined' ? localStorage : null;
    } catch (e) {
      return null;
    }
  }

  function normalize(value) {
    return MODES.indexOf(value) === -1 ? 'system' : value;
  }

  function systemDark() {
    try {
      return !!(root.matchMedia && root.matchMedia('(prefers-color-scheme: dark)').matches);
    } catch (e) {
      return false;
    }
  }

  function resolve(value, dark) {
    var m = normalize(value);
    if (m === 'system') return dark ? 'dark' : 'light';
    return m;
  }

  function read() {
    try {
      var s = storage();
      return s ? s.getItem(KEY) : null;
    } catch (e) {
      return null;
    }
  }

  function write(value) {
    try {
      var s = storage();
      if (s) s.setItem(KEY, value);
    } catch (e) {
      /* 隐私模式或 file:// 下可能不可写，忽略 */
    }
  }

  function paperColor() {
    try {
      var css = root.getComputedStyle
        ? root.getComputedStyle(document.documentElement).getPropertyValue('--paper').trim()
        : '';
      return css || FALLBACK_PAPER[resolved];
    } catch (e) {
      return FALLBACK_PAPER[resolved];
    }
  }

  function apply(next) {
    resolved = next;
    var el = document.documentElement;
    el.dataset.theme = next;
    el.style.colorScheme = next;
    try {
      var meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute('content', paperColor());
    } catch (e) {
      /* DOM 不完整时忽略 */
    }
  }

  function label(value) {
    try {
      return NB.i18n && NB.i18n.t ? NB.i18n.t('theme.' + value) : value;
    } catch (e) {
      return value;
    }
  }

  function title() {
    try {
      return NB.i18n && NB.i18n.t ? NB.i18n.t('theme.title') : 'Theme';
    } catch (e) {
      return 'Theme';
    }
  }

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function chipInner() {
    var icon = NB.ui && NB.ui.icon ? NB.ui.icon(ICONS[mode]) : '';
    return icon + '<b>' + esc(label(mode)) + '</b>';
  }

  function chipHTML() {
    return '<button type="button" class="chip theme-chip" data-theme-toggle aria-haspopup="menu" aria-label="' +
      esc(title() + ': ' + label(mode)) + '" title="' +
      esc(title() + ': ' + label(mode)) + '">' + chipInner() + '</button>';
  }

  function syncChip() {
    try {
      var nodes = document.querySelectorAll('[data-theme-toggle]');
      if (!nodes) return;
      for (var i = 0; i < nodes.length; i++) {
        nodes[i].innerHTML = chipInner();
        nodes[i].title = title() + ': ' + label(mode);
        nodes[i].setAttribute('aria-label', title() + ': ' + label(mode));
      }
    } catch (e) {
      /* 非浏览器环境跳过 */
    }
  }

  function notify() {
    listeners.forEach(function (fn) {
      try { fn(mode, resolved); } catch (e) { /* 单个监听器出错不影响其他 */ }
    });
    try {
      if (typeof root.dispatchEvent === 'function' && typeof root.CustomEvent === 'function') {
        root.dispatchEvent(new root.CustomEvent('nb:theme', {
          detail: { mode: mode, resolved: resolved },
        }));
      }
    } catch (e) { /* 忽略 */ }
    syncChip();
  }

  function set(value) {
    mode = normalize(value);
    write(mode);
    apply(resolve(mode, systemDark()));
    notify();
  }

  function bindChip(el) {
    if (!el) return;
    el.onclick = function () {
      if (!NB.ui || !NB.ui.menu) return;
      NB.ui.menu(el, MODES.map(function (m) {
        return {
          label: label(m),
          active: m === mode,
          onPick: function () { set(m); },
        };
      }), { align: 'right', head: title() });
    };
  }

  function onChange(fn) {
    if (typeof fn === 'function') listeners.push(fn);
  }

  /* 系统偏好变化：只有 mode 为 system 时才跟随 */
  var mq = null;
  try {
    mq = root.matchMedia ? root.matchMedia('(prefers-color-scheme: dark)') : null;
  } catch (e) {
    mq = null;
  }
  if (mq) {
    var onSystem = function () {
      if (mode !== 'system') return;
      apply(resolve('system', mq.matches));
      notify();
    };
    if (mq.addEventListener) mq.addEventListener('change', onSystem);
    else if (mq.addListener) mq.addListener(onSystem);
  }

  NB.theme = {
    normalize: normalize,
    resolve: resolve,
    set: set,
    chipHTML: chipHTML,
    bindChip: bindChip,
    syncChip: syncChip,
    onChange: onChange,
    get mode() { return mode; },
    get resolved() { return resolved; },
  };

  /* 启动：先解析并应用，再交给样式表绘制 */
  mode = normalize(read());
  apply(resolve(mode, systemDark()));
})();
