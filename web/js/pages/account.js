/* 账号：改密、注销 */
(function () {
  const NB = window.NB;
  const U = NB.ui;
  const I = NB.i18n;
  NB.pages = NB.pages || {};

  NB.pages.account = {
    title: '账号', kicker: '配置 / ACCOUNT', full: false,
    render() {
      const mock = NB.mock;
      const me = mock.me();
      const admins = mock.users.filter((u) => u.isAdmin);
      const lastAdmin = me.isAdmin && admins.length <= 1;
      const single = me.mode === 'single' || me.role === 'owner';
      // provider / MCP 是懒加载数据：对应页面没打开过时 mock 里还是空的。
      // 账号页单独记一份计数，刷新后直接进来也不显示成 0。
      const counts = mock.accountCounts || {};
      const providerLoaded = !!(mock.ui.providerLoaded && mock.ui.providerLoaded.user);
      const providerCount = providerLoaded
        ? (mock.providers || []).length
        : (counts.providers != null ? counts.providers : 0);
      const mcpLoaded = !!mock.ui.mcpLoaded;
      const mcpCount = mcpLoaded
        ? ((mock.mcp && mock.mcp.servers) || []).length
        : (counts.mcp != null ? counts.mcp : 0);
      // 会话/tokens 统计由后端聚合（不受列表 200 条上限影响）；拉取失败时退回本地会话求和
      const stats = mock.accountStats || {};
      const sessionCount = stats.sessions != null ? stats.sessions : (mock.sessions || []).length;
      const tokenTotal = stats.tokens != null
        ? stats.tokens
        : (mock.sessions || []).reduce((sum, s) => sum + (s.tokensIn || 0) + (s.tokensOut || 0), 0);

      return '<div class="account-wrap">' +
        '<input type="file" id="avatar-file" accept="image/*" hidden>' +
        '<div class="page-head">' +
          I.t('<div><div class="kicker">配置 / ACCOUNT</div><h2 class="page-title">账号设置</h2>') +
            I.t('<p class="page-desc">密码、登录状态与私有数据。注销会同时删除会话、provider、MCP server 与私有技能。</p></div>') +
        '</div>' +
        '<div class="account-page">' +
        '<div class="stack account-main" style="gap:18px">' +
          '<section class="panel">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('lock') + I.t('修改密码</div>') +
              '<span class="kicker">SECURITY</span></div>' +
            '<div class="panel-body">' +
              (single
                ? '<div class="banner steel">' + U.icon('user') + I.t('<div><b>单用户模式没有登录密码。</b>账号页只用于展示身份；切到多用户模式后才需要密码。</div></div>')
                : '<form id="pwd-form" class="form-narrow" novalidate>' +
                    '<div class="stack">' +
                      I.t('<div class="field"><label for="pw-old">当前密码</label>') +
                        I.t('<input class="input" id="pw-old" type="password" autocomplete="current-password" placeholder="输入当前密码"></div>') +
                      I.t('<div class="field"><label for="pw-new">新密码</label>') +
                        I.t('<input class="input" id="pw-new" type="password" autocomplete="new-password" placeholder="至少 8 位">') +
                        I.t('<span class="hint">修改后，其他设备上的登录会立即失效，本页面自动续登。</span></div>') +
                      I.t('<div class="field"><label for="pw-new2">确认新密码</label>') +
                        I.t('<input class="input" id="pw-new2" type="password" autocomplete="new-password" placeholder="再输入一次"></div>') +
                      I.t('<div class="row" style="gap:8px"><button class="btn signal" type="submit">保存新密码</button>') +
                        '<button class="btn ghost" type="button" id="logout">' + U.icon('logout') + I.t('退出登录</button></div>') +
                    '</div>' +
                  '</form>') +
            '</div>' +
          '</section>' +

          '<section class="panel danger-panel">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('alert') + I.t('危险区</div>') +
              '<span class="kicker">IRREVERSIBLE</span></div>' +
            '<div class="panel-body">' +
              I.t('<p class="muted" style="font-size:13.5px;line-height:1.7;max-width:560px">注销会删除账号、会话、消息、私有 provider、MCP server 与私有技能。') +
                (lastAdmin ? I.t('<strong style="color:var(--bad)"> 你是最后一个管理员，必须先指定另一位管理员。</strong>') : '') +
              '</p>' +
              '<div class="row" style="margin-top:14px">' +
                '<button type="button" class="btn danger" id="delete-account"' + (lastAdmin ? ' disabled' : '') + '>' + U.icon('trash') + I.t('注销账号</button>') +
                I.t('<span class="mono-xs">需要输入密码与用户名确认</span>') +
              '</div>' +
            '</div>' +
          '</section>' +
        '</div>' +

        '<aside class="account-side">' +
          '<section class="panel">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('user') + I.t('身份</div>') +
              U.badge(U.roleName(me.role), me.isAdmin ? 'ink' : 'neutral') + '</div>' +
            '<div class="panel-body"><div class="stack">' +
              '<div class="account-id"><button type="button" class="account-avatar" id="avatar-btn" aria-label="' + U.esc(I.t('更换头像')) + '" title="' + U.esc(I.t('更换头像')) + '">' +
                  '<span class="avatar ' + (me.isAdmin ? 'acid' : '') + '">' + U.avatarInner(me.name, me.avatar) + '</span>' +
                '</button>' +
                '<div><div style="font-size:16px;font-weight:600">' + U.esc(me.name) + '</div>' +
                '<div class="mono-xs">' + U.esc(me.uid || '—') + '</div></div></div>' +
              '<dl class="kv">' +
                I.t('<dt>执行天花板</dt><dd>') + U.esc(me.ceiling || 'T0') + '</dd>' +
                '<dt>MCP</dt><dd>' + (me.mcpGranted ? I.t('已授权') : I.t('未授权')) + '</dd>' +
                (me.createdAt ? I.t('<dt>加入时间</dt><dd>') + U.fmtDate(me.createdAt) + '</dd>' : '') +
                (me.lastSeen ? I.t('<dt>上次活跃</dt><dd>') + U.fmtTime(me.lastSeen) + '</dd>' : '') +
              '</dl>' +
            '</div></div>' +
          '</section>' +

          '<section class="panel">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('database') + I.t('我的数据</div></div>') +
            '<div class="panel-body"><dl class="kv">' +
              I.t('<dt>会话 / tokens</dt><dd data-stat="sessions">') + sessionCount + ' / ' + U.tokenCount(tokenTotal) + '</dd>' +
              I.t('<dt>搜索配额</dt><dd>') + (me.quota && me.quota.enabled ? U.num(me.quota.used) + ' / ' + U.num(me.quota.limit) : I.t('未启用')) + '</dd>' +
              I.t('<dt>私有 provider</dt><dd data-count="providers">') + providerCount + '</dd>' +
              '<dt>MCP server</dt><dd data-count="mcp">' + mcpCount + '</dd>' +
            '</dl></div>' +
          '</section>' +
        '</aside>' +
        '</div>' +
      '</div>';
    },

    mount(root) {
      const mock = NB.mock;
      const me = mock.me();
      const counts = mock.accountCounts || (mock.accountCounts = {});

      // 只读计数：对应页面没打开过时，懒加载的 mock 还是空的，这里单独补一次
      const paint = (key, value) => {
        counts[key] = value;
        const el = root.querySelector('[data-count="' + key + '"]');
        if (el) el.textContent = String(value);
      };
      const unknown = (key) => {
        const el = root.querySelector('[data-count="' + key + '"]');
        if (el) el.textContent = '—';
      };

      if (mock.ui.providerLoaded && mock.ui.providerLoaded.user) {
        paint('providers', (mock.providers || []).length);
      } else {
        NB.api.get('/web/providers')
          .then((data) => paint('providers', (data.providers || []).length))
          .catch(() => unknown('providers'));
      }

      if (mock.ui.mcpLoaded) {
        paint('mcp', ((mock.mcp && mock.mcp.servers) || []).length);
      } else {
        NB.api.get('/mcp/servers')
          .then((data) => paint('mcp', (data.servers || []).length))
          .catch(() => unknown('mcp'));
      }

      // 会话数 / 累计 token：后端聚合，避免只统计当前加载的 200 条会话
      NB.api.get('/sessions/stats').then((data) => {
        const sessions = Number(data.sessions || 0);
        const tokens = Number(data.tokens || 0);
        mock.accountStats = { sessions, tokens };
        const el = root.querySelector('[data-stat="sessions"]');
        if (el) el.textContent = sessions + ' / ' + U.tokenCount(tokens);
      }).catch(() => {
        // 失败时保留本地会话求和，不清零已有展示
      });

      const avatarBtn = root.querySelector('#avatar-btn');
      const avatarFile = root.querySelector('#avatar-file');
      if (avatarBtn && avatarFile) {
        avatarBtn.onclick = () => avatarFile.click();
        avatarFile.onchange = () => {
          const file = avatarFile.files && avatarFile.files[0];
          avatarFile.value = '';
          if (!file) return;
          U.pickAvatar({
            file,
            endpoint: '/auth/avatar',
            onSaved: (avatar) => {
              if (NB.app.user) NB.app.user.avatar = avatar;
              const mine = (NB.mock.users || []).find((u) => u.uid === me.uid);
              if (mine) mine.avatar = avatar;
              NB.app.render();
            },
          });
        };
      }

      const logout = root.querySelector('#logout');
      if (logout) {
        logout.onclick = async () => {
          try { await NB.api.logout(); } catch (e) { /* 已过期也照样本地退出 */ }
          NB.api.token.set('');
          U.toast(I.t('已退出登录'));
          location.hash = '#/login';
          await NB.app.bootstrap();
        };
      }

      const form = root.querySelector('#pwd-form');
      if (form) {
        form.addEventListener('submit', (e) => {
          e.preventDefault();
          const oldP = form.querySelector('#pw-old').value;
          const p1 = form.querySelector('#pw-new').value;
          const p2 = form.querySelector('#pw-new2').value;
          if (!oldP) { U.toast(I.t('请输入当前密码'), 'bad'); return; }
          if (p1.length < 8) { U.toast(I.t('新密码至少 8 位'), 'bad'); return; }
          if (p1 !== p2) { U.toast(I.t('两次新密码不一致'), 'bad'); return; }
          if (p1 === oldP) { U.toast(I.t('新密码不能与旧密码相同'), 'warn'); return; }
          NB.api.changePassword(oldP, p1).then((result) => {
            NB.api.token.set(result.token);
            form.reset();
            U.toast(I.t('密码已更新（其他设备需重新登录）'));
          }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
        });
      }

      const del = root.querySelector('#delete-account');
      if (del) {
        del.onclick = () => {
          U.modal({
            kicker: 'DANGER ZONE',
            title: I.t('注销账号'),
            body: '<div class="stack">' +
              '<div class="banner bad">' + U.icon('alert') + I.t('<div>此操作不可恢复。输入密码并键入用户名 <b>') + U.esc(me.name) + I.t('</b> 以确认。</div></div>') +
              I.t('<div class="field"><label for="del-pass">当前密码</label><input class="input" id="del-pass" type="password" autocomplete="current-password"></div>') +
              I.t('<div class="field"><label for="del-name">键入用户名</label><input class="input mono" id="del-name" placeholder="') + U.esc(me.name) + '" autocomplete="off"></div>' +
              I.t('<label class="check"><input type="checkbox" id="del-files"><span>同时删除我的工作区文件<span class="check-hint">会话、消息与 checkpoint 总会删除；勾选后连工作区文件一起清理。</span></span></label>') +
            '</div>',
            footer: I.t('<span class="foot-left">账号与私有数据会一并删除</span>') +
              I.t('<button type="button" class="btn ghost" data-cancel>取消</button>') +
              I.t('<button type="button" class="btn danger" data-confirm>永久注销</button>'),
            onMount: (wrap, close) => {
              wrap.querySelector('[data-cancel]').onclick = close;
              wrap.querySelector('[data-confirm]').onclick = () => {
                const pass = wrap.querySelector('#del-pass').value;
                const name = wrap.querySelector('#del-name').value.trim();
                if (!pass) { U.toast(I.t('请输入密码'), 'bad'); return; }
                if (name !== me.name) { U.toast(I.t('用户名不匹配'), 'bad'); return; }
                const purge = wrap.querySelector('#del-files').checked;
                NB.api.deleteAccount(pass, purge).then(async () => {
                  NB.api.token.set('');
                  close();
                  U.toast(I.t('账号已注销'));
                  location.hash = '#/login';
                  await NB.app.bootstrap();
                }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
              };
            },
          });
        };
      }
    },
  };
})();
