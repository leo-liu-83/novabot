/* 首次初始化 / 登录 */
(function () {
  const NB = window.NB;
  const U = NB.ui;
  const I = NB.i18n;
  NB.pages = NB.pages || {};

  const aside = (kind) => {
    const setup = kind === 'setup';
    return '' +
      '<aside class="auth-aside">' +
        '<div class="brand-row" style="align-items:center">' +
          '<span class="brand-mark">NOVA<em>BOT</em></span>' +
        '</div>' +
        '<div class="auth-copy">' +
          '<div class="kicker" style="color:var(--signal);margin-bottom:18px">' +
            (setup ? I.t('setup.firstRunKicker') : I.t('auth.accessKicker')) +
          '</div>' +
          '<h2>' + (setup ? I.t('setup.asideTitle') : I.t('auth.asideTitle')) + '</h2>' +
          '<p>' + (setup ? I.t('setup.asideBody') : I.t('auth.asideBody')) + '</p>' +
        '</div>' +
        '<div class="auth-points">' +
          '<div class="auth-point"><b>01</b><span>' + I.t('auth.point1') + '</span></div>' +
          '<div class="auth-point"><b>02</b><span>' + I.t('auth.point2') + '</span></div>' +
          '<div class="auth-point"><b>03</b><span>' + I.t('auth.point3') + '</span></div>' +
        '</div>' +
        '<div class="auth-foot"><span>NOVABOT / LOCAL AGENT</span><span>LOCAL CONSOLE</span></div>' +
      '</aside>';
  };

  const shell = (kind, body) =>
    '<div class="auth">' + aside(kind) + '<main class="auth-main">' +
      '<div class="auth-tools">' + NB.theme.chipHTML() +
        '<button type="button" class="chip auth-locale" data-language>' + (I.locale() === 'en' ? '中文' : 'English') + '</button>' +
      '</div>' +
      body + '</main></div>';

  /* 首次初始化：欢迎 → 创建管理员 → 完成；单用户模式只显示提示 */
  let step = 1;
  let timer = null;

  const setupWelcome = () => '' +
    '<div class="auth-card">' +
      '<div class="kicker" style="color:var(--signal-deep)">' + I.t('setup.step1.kicker') + '</div>' +
      '<h1 class="auth-title">' + I.t('setup.welcomeTitle') + '</h1>' +
      '<p class="auth-sub">' + I.t('setup.welcomeBody') + '</p>' +
      '<div class="auth-note">' + U.icon('database') + I.t('setup.point1') + '</div>' +
      '<div class="auth-note">' + U.icon('shield') + I.t('setup.point2') + '</div>' +
      '<div class="auth-note">' + U.icon('clock') + I.t('setup.point3') + '</div>' +
      '<div class="auth-note">' + U.icon('key') + I.t('setup.welcomeNote') + '</div>' +
      '<button type="button" class="btn signal lg block" data-start style="margin-top:22px">' +
        U.icon('arrow') + I.t('setup.start') + '</button>' +
    '</div>';

  const setupForm = () => '' +
    '<div class="auth-card">' +
      '<div class="kicker" style="color:var(--signal-deep)">' + I.t('setup.step2.kicker') + '</div>' +
      '<h1 class="auth-title">' + I.t('setup.createTitle') + '</h1>' +
      '<p class="auth-sub">' + I.t('setup.createBody') + '</p>' +
      '<div class="banner warn" id="setup-error" hidden></div>' +
      '<form id="setup-form" novalidate style="margin-top:18px">' +
        '<div class="field"><label for="su-name">' + I.t('auth.username') + '</label>' +
          '<input class="input" id="su-name" name="username" autocomplete="username" placeholder="' + I.t('auth.usernameHint') + '" maxlength="32"></div>' +
        '<div class="field"><label for="su-pass">' + I.t('auth.password') + '</label>' +
          '<input class="input" id="su-pass" name="password" type="password" autocomplete="new-password" placeholder="' + I.t('auth.passwordHint') + '"></div>' +
        '<div class="field"><label for="su-pass2">' + I.t('auth.confirmPassword') + '</label>' +
          '<input class="input" id="su-pass2" name="password2" type="password" autocomplete="new-password" placeholder="' + I.t('auth.passwordAgain') + '"></div>' +
        '<button class="btn signal lg block" type="submit" data-submit>' + I.t('setup.create') + '</button>' +
      '</form>' +
      '<div class="auth-switch"><button type="button" data-back>' + I.t('setup.back') + '</button></div>' +
    '</div>';

  const setupDone = () => '' +
    '<div class="auth-card" style="text-align:center">' +
      '<div class="kicker" style="color:var(--signal-deep)">' + I.t('setup.step3.kicker') + '</div>' +
      '<div style="color:var(--ok);margin:6px 0 2px">' + U.icon('check', 'lg') + '</div>' +
      '<h1 class="auth-title">' + I.t('setup.doneTitle') + '</h1>' +
      '<p class="auth-sub">' + I.t('setup.doneBody') + '</p>' +
    '</div>';

  const setupSingle = () => '' +
    '<div class="auth-card">' +
      '<div class="kicker" style="color:var(--signal-deep)">' + I.t('setup.single.kicker') + '</div>' +
      '<h1 class="auth-title">' + I.t('setup.singleTitle') + '</h1>' +
      '<p class="auth-sub">' + I.t('setup.singleBody') + '</p>' +
      '<div class="auth-note"><b>01</b> ' + I.t('setup.singleStep1') + '</div>' +
      '<div class="auth-note"><b>02</b> ' + I.t('setup.singleStep2') + '</div>' +
      '<div class="auth-note"><b>03</b> ' + I.t('setup.singleStep3') + '</div>' +
      '<div class="auth-note"><b>04</b> ' + I.t('setup.singleStep4') + '</div>' +
      '<div class="auth-note">' + U.icon('lock') + I.t('setup.singleNote') + '</div>' +
      '<button type="button" class="btn signal lg block" data-recheck style="margin-top:22px">' +
        U.icon('refresh') + I.t('setup.recheck') + '</button>' +
    '</div>';

  NB.pages.setup = {
    title: 'setup.title', kicker: 'SETUP', auth: true, full: true,
    render() {
      const status = NB.app.authStatus || {};
      if (!status.multi_user) return shell('setup', setupSingle());
      if (step === 2) return shell('setup', setupForm());
      if (step === 3) return shell('setup', setupDone());
      return shell('setup', setupWelcome());
    },
    mount(root) {
      root.querySelectorAll('[data-language]').forEach((el) => el.onclick = () => {
        I.setLocale(I.locale() === 'en' ? 'zh' : 'en');
      });
      NB.theme.bindChip(root.querySelector('[data-theme-toggle]'));

      const start = root.querySelector('[data-start]');
      if (start) start.onclick = () => { step = 2; NB.app.render(); };

      const back = root.querySelector('[data-back]');
      if (back) back.onclick = () => { step = 1; NB.app.render(); };

      const recheck = root.querySelector('[data-recheck]');
      if (recheck) recheck.onclick = () => NB.app.bootstrap();

      const form = root.querySelector('#setup-form');
      if (form) {
        const error = root.querySelector('#setup-error');
        const button = form.querySelector('[data-submit]');
        const fail = (text) => {
          error.textContent = text;
          error.hidden = false;
        };
        form.querySelectorAll('input').forEach((el) => {
          el.oninput = () => { error.hidden = true; };
        });
        form.addEventListener('submit', (e) => {
          e.preventDefault();
          const data = new FormData(form);
          const name = String(data.get('username') || '').trim();
          const p1 = String(data.get('password') || '');
          const p2 = String(data.get('password2') || '');
          if (!/^[a-zA-Z0-9_.-]{2,32}$/.test(name)) { fail(I.t('auth.errUsername')); return; }
          if (p1.length < 8) { fail(I.t('auth.errPasswordMin')); return; }
          if (p1 !== p2) { fail(I.t('auth.errPasswordMismatch')); return; }

          error.hidden = true;
          button.disabled = true;
          button.textContent = I.t('setup.creating');
          NB.api.setupAdmin({ username: name, password: p1, password2: p2 }).then((result) => {
            NB.api.token.set(result.token);
            step = 3;
            NB.app.render();
          }).catch((err) => {
            // 输掉初始化竞态或后端已切到单用户：页面状态过期了，重新取一次身份，
            // 而不是把用户困在一个永远提交不成功的表单里。
            if (err.code === 'auth.admin_exists' || err.code === 'auth.multi_user_required') {
              fail(err.message || I.t('error.network'));
              step = 1;
              clearTimeout(timer);
              timer = setTimeout(() => { timer = null; NB.app.bootstrap(); }, 1200);
              return;
            }
            fail(err.message || I.t('error.network'));
            button.disabled = false;
            button.textContent = I.t('setup.create');
          });
        });
      }

      if (step === 3 && (!NB.app.authStatus || NB.app.authStatus.multi_user)) {
        clearTimeout(timer);
        timer = setTimeout(() => {
          timer = null;
          NB.app.bootstrap();
        }, 800);
      }
    },
    unmount() {
      clearTimeout(timer);
      timer = null;
    },
  };

  /* 登录 / 注册 */
  let tab = 'login';

  const loginForm = () =>
    '<form id="login-form" novalidate>' +
      '<div class="field"><label for="li-name">' + I.t('auth.username') + '</label>' +
        '<input class="input" id="li-name" name="username" autocomplete="username" placeholder="leo" maxlength="32"></div>' +
      '<div class="field"><label for="li-pass">' + I.t('auth.password') + '</label>' +
        '<input class="input" id="li-pass" name="password" type="password" autocomplete="current-password"></div>' +
      '<button class="btn signal lg block" type="submit">' + U.icon('logout') + I.t('auth.submitLogin') + '</button>' +
      '' +
    '</form>';

  const registerForm = () => NB.mock.hasAdmin
    ? '<form id="register-form" novalidate>' +
        '<div class="field"><label for="rg-name">' + I.t('auth.username') + '</label>' +
          '<input class="input" id="rg-name" name="username" autocomplete="username" placeholder="' + I.t('auth.usernameHint') + '" maxlength="32"></div>' +
        '<div class="field"><label for="rg-pass">' + I.t('auth.password') + '</label>' +
          '<input class="input" id="rg-pass" name="password" type="password" autocomplete="new-password" placeholder="' + I.t('auth.passwordHint') + '"></div>' +
        '<div class="field"><label for="rg-pass2">' + I.t('auth.confirmPassword') + '</label>' +
          '<input class="input" id="rg-pass2" name="password2" type="password" autocomplete="new-password" placeholder="' + I.t('auth.passwordAgain') + '"></div>' +
        '<button class="btn signal lg block" type="submit">' + U.icon('user') + I.t('auth.createAccount') + '</button>' +
        '<div class="auth-note" style="margin-top:2px">' + I.t('auth.registerNote') + '</div>' +
      '</form>'
    : '<div class="banner warn">' + U.icon('alert') + '<div>' + I.t('setup.welcomeBody') + '</div></div>' +
      '<button class="btn signal lg block" style="margin-top:16px" data-go="setup">' + I.t('setup.start') + '</button>';

  NB.pages.login = {
    title: '登录', kicker: 'AUTH', auth: true, full: true,
    render() {
      return shell('login',
        '<div class="auth-card">' +
          '<div class="kicker" style="color:var(--signal-deep)">ACCESS / 02</div>' +
          '<h1 class="auth-title">' + (tab === 'login' ? I.t('auth.loginTitle') : I.t('auth.registerTitle')) + '</h1>' +
          '<p class="auth-sub">' + (tab === 'login' ? I.t('auth.loginHelp') : I.t('auth.registerHelp')) + '</p>' +
          '<div class="auth-tabs" role="tablist">' +
            '<button type="button" class="auth-tab" role="tab" data-tab="login" aria-selected="' + (tab === 'login') + '">' + I.t('auth.login') + '</button>' +
            '<button type="button" class="auth-tab" role="tab" data-tab="register" aria-selected="' + (tab === 'register') + '">' + I.t('auth.register') + '</button>' +
          '</div>' +
          '<div style="margin-top:22px">' + (tab === 'login' ? loginForm() : registerForm()) + '</div>' +
          (tab === 'login'
            ? '<div class="auth-switch">' + I.t('auth.noAccount') + '<button type="button" data-tab="register">' + I.t('auth.goRegister') + '</button></div>'
            : '<div class="auth-switch">' + I.t('auth.hasAccount') + '<button type="button" data-tab="login">' + I.t('auth.goLogin') + '</button></div>') +
          (!NB.mock.hasAdmin && tab === 'login'
            ? '<div class="auth-note">' + I.t('auth.firstRun') + '<button type="button" class="linklike" data-go="setup" style="border:0;background:none;color:var(--signal);padding:0">' + I.t('auth.goSetup') + '</button></div>'
            : '') +
        '</div>');
    },
    mount(root) {
      root.querySelectorAll('[data-language]').forEach((el) => el.onclick = () => {
        I.setLocale(I.locale() === 'en' ? 'zh' : 'en');
      });
      NB.theme.bindChip(root.querySelector('[data-theme-toggle]'));
      root.querySelectorAll('[data-tab]').forEach((el) => {
        el.onclick = () => { tab = el.dataset.tab; NB.app.render(); };
      });
      root.querySelectorAll('[data-go]').forEach((el) => {
        el.onclick = () => { location.hash = el.dataset.go === 'setup' ? '#/setup' : '#/login'; };
      });

      const login = root.querySelector('#login-form');
      if (login) {
        login.addEventListener('submit', (e) => {
          e.preventDefault();
          const data = new FormData(login);
          const name = String(data.get('username') || '').trim();
          NB.api.login(name, String(data.get('password') || '')).then((result) => {
            NB.api.token.set(result.token);
            U.toast(I.t('auth.welcomeBack') + '，' + (result.user.username || name));
            return NB.app.bootstrap();
          }).catch((error) => U.toast(error.message || I.t('error.network'), 'bad'));
        });
      }

      const reg = root.querySelector('#register-form');
      if (reg) {
        reg.addEventListener('submit', (e) => {
          e.preventDefault();
          const data = new FormData(reg);
          const name = String(data.get('username') || '').trim();
          const p1 = String(data.get('password') || '');
          const p2 = String(data.get('password2') || '');
          if (!/^[a-zA-Z0-9_.-]{2,32}$/.test(name)) { U.toast(I.t('auth.errUsername'), 'bad'); return; }
          if (p1.length < 8) { U.toast(I.t('auth.errPasswordMin'), 'bad'); return; }
          if (p1 !== p2) { U.toast(I.t('auth.errPasswordMismatch'), 'bad'); return; }
          NB.api.register(name, p1).then((result) => {
            NB.api.token.set(result.token);
            U.toast(I.t('auth.accountCreated'));
            return NB.app.bootstrap();
          }).catch((error) => U.toast(error.message || I.t('error.network'), 'bad'));
        });
      }
    },
  };
})();
