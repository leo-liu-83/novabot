/* 侧边栏收缩状态：expanded / collapsed
   在 <head> 同步执行，首次绘制前把 data-rail 写到 <html>，避免先按 238px 画一遍再跳窄。
   这里只管状态与属性；按钮绑定、悬浮提示在 app.js（要等 DOM 与 i18n 就绪）。 */
(function () {
  'use strict';

  var root = typeof window !== 'undefined' ? window : globalThis;
  root.NB = root.NB || {};
  var NB = root.NB;

  var KEY = 'nb.rail';
  var STATES = ['expanded', 'collapsed'];

  var state = 'expanded';

  function storage() {
    try {
      return typeof localStorage !== 'undefined' ? localStorage : null;
    } catch (e) {
      return null;
    }
  }

  function normalize(value) {
    return STATES.indexOf(value) === -1 ? 'expanded' : value;
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

  function apply() {
    try {
      document.documentElement.dataset.rail = state;
    } catch (e) {
      /* DOM 不完整时忽略 */
    }
  }

  function set(value) {
    state = normalize(value);
    write(state);
    apply();
    return state;
  }

  function toggle() {
    return set(state === 'collapsed' ? 'expanded' : 'collapsed');
  }

  NB.rail = {
    normalize: normalize,
    set: set,
    toggle: toggle,
    apply: apply,
    get collapsed() { return state === 'collapsed'; },
    get state() { return state; },
  };

  /* 启动：先解析并应用，再交给样式表按宽度绘制 */
  state = normalize(read());
  apply();
})();