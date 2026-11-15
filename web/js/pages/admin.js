/* 管理：用户与授权、系统信息 */
(function () {
  const NB = window.NB;
  const U = NB.ui;
  const I = NB.i18n;
  NB.pages = NB.pages || {};

  // 与概览页「执行档位」保持同一套说明，避免两处对档位的描述不一致
  const LEVEL_NOTES = [
    'T0 不执行命令 · 无风险',
    'T1 仅技能 .py 脚本 · 作者自定',
    'T2 工作区只读 · 不可修改',
    'T3 任意命令 · 逐条确认 · 可能误操作',
    'T4 任意命令 · 免确认 · 等同交出账号',
  ];

  // 模型目录分页：聚合平台可能有几百个模型，不能一次全部渲染
  const MODEL_PAGE_SIZE = 10;

  function modelPageState(total) {
    const pages = Math.max(1, Math.ceil(total / MODEL_PAGE_SIZE));
    let page = Number(NB.mock.ui.systemModelPage) || 0;
    page = Math.min(Math.max(page, 0), pages - 1);
    NB.mock.ui.systemModelPage = page;
    return { page, pages, start: page * MODEL_PAGE_SIZE };
  }

  function filteredModels(models) {
    const query = String(NB.mock.ui.systemModelQuery || '').trim().toLowerCase();
    if (!query) return models;
    return models.filter((m) =>
      [m.provider, m.name, m.id].some((value) =>
        String(value || '').toLowerCase().includes(query)));
  }

  function modelRowsHTML(models, defaultModel) {
    if (!models.length) {
      return '<tr><td colspan="5" class="model-empty">' +
        U.esc(I.t('没有匹配的模型')) + '</td></tr>';
    }
    return models.map((m) => {
      const isDefault = m.id === defaultModel;
      // 非默认行悬停/聚焦时才露出切换按钮；默认行只挂徽标
      const action = isDefault
        ? U.badge(I.t('默认'), 'ink')
        : '<button type="button" class="model-set-default" data-set-default="' +
            U.esc(m.id) + '">' + I.t('设为默认 →') + '</button>';
      return '<tr class="model-row">' +
        '<td class="mono" style="font-size:12.5px">' + U.esc(m.id) + '</td>' +
        '<td>' + U.esc(m.provider) + '</td>' +
        '<td>' + (m.thinking ? U.badge(I.t('支持'), 'ok') : U.badge('—', 'neutral')) + '</td>' +
        '<td class="num">' + U.num(m.ctx) + '</td>' +
        '<td class="model-default-cell">' + action + '</td></tr>';
    }).join('');
  }

  function modelPagerInnerHTML(page, pages, total) {
    return '<span class="pager-info mono-xs">' +
        I.t('第 {page} / {total} 页 · 共 {count} 个模型', {
          page: page + 1, total: pages, count: total,
        }) + '</span>' +
      '<div class="row" style="gap:6px">' +
        '<button type="button" class="btn ghost sm" data-model-page="prev"' +
          (page <= 0 ? ' disabled' : '') + '>' + I.t('上一页') + '</button>' +
        '<button type="button" class="btn ghost sm" data-model-page="next"' +
          (page >= pages - 1 ? ' disabled' : '') + '>' + I.t('下一页') + '</button>' +
      '</div>';
  }

  function paintModelCatalog(root) {
    const tbody = root.querySelector('#system-model-tbody');
    const pager = root.querySelector('#system-model-pager');
    const count = root.querySelector('#system-model-count');
    if (!tbody || !pager) return;

    const models = NB.mock.models || [];
    const filtered = filteredModels(models);
    const catalog = modelPageState(filtered.length);
    const defaultModel = (NB.app && NB.app.defaultModel)
      || (NB.mock.ui && NB.mock.ui.defaultModel) || '';

    tbody.innerHTML = modelRowsHTML(
      filtered.slice(catalog.start, catalog.start + MODEL_PAGE_SIZE), defaultModel);
    pager.innerHTML = modelPagerInnerHTML(catalog.page, catalog.pages, filtered.length);
    if (count) {
      count.textContent = filtered.length === models.length
        ? models.length + ' MODELS'
        : filtered.length + ' / ' + models.length + ' MODELS';
    }
  }

  function chips(items, empty) {
    if (!items.length) return '<span class="muted">' + U.esc(empty) + '</span>';
    return '<div class="system-chip-row">' + items.join('') + '</div>';
  }

  function mapUser(u) {
    return {
      uid: u.uid,
      username: u.username,
      isAdmin: !!u.is_admin,
      avatar: u.avatar || '',
      createdAt: new Date((u.created_at || 0) * 1000).toISOString(),
      // null 表示未单独授权；非 admin 的默认天花板由服务端策略决定（T1）
      ceiling: u.exec_ceiling || null,
      quota: {
        enabled: !!(u.web_quota || {}).enabled,
        limit: (u.web_quota || {}).daily_limit || 0,
        used: (u.web_quota || {}).used || 0,
      },
      mcpGranted: !!u.mcp_granted,
      projectsEnabled: !!u.projects_enabled,
      canProjects: !!u.can_projects,
    };
  }

  function loadUsers(mock, force) {
    if (mock.ui.adminLoading) return;
    if (!force && mock.ui.adminLoaded) return;
    mock.ui.adminLoading = true;
    NB.api.adminUsers().then((res) => {
      // 列表接口只带账号/授权字段；当前账号的 hasSoul、agentAvatar 等派生字段
      // 只存在于内存里，整表替换会把它们冲掉（侧栏「人格」随即消失）。
      const previous = new Map((mock.users || []).map((u) => [u.uid, u]));
      mock.users = (res.users || []).map((row) =>
        Object.assign({}, previous.get(row.uid) || {}, mapUser(row)));
      mock.ui.adminLoading = false;
      mock.ui.adminLoaded = true;
      NB.app.render();
    }).catch((error) => {
      mock.ui.adminLoading = false;
      U.toast(error.message || NB.i18n.t('error.network'), 'bad');
    });
  }

  function patchUser(mock, uid, patch, done) {
    NB.api.adminUpdateUser(uid, patch).then((row) => {
      const mapped = mapUser(row);
      const index = mock.users.findIndex((u) => u.uid === uid);
      if (index >= 0) mock.users[index] = Object.assign({}, mock.users[index], mapped);
      if (done) done(mapped);
      NB.app.render();
    }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
  }

  function projectsCell(u) {
    if (NB.mock.mode !== 'multi') return '';
    const control = u.isAdmin
      ? U.switchHTML(true, 'disabled title="' + U.esc(I.t('admin.projectsAdminAlways')) + '"')
      : U.switchHTML(u.projectsEnabled, 'data-projects-grant="' + U.esc(u.uid) + '"');
    return I.t('<td data-label="项目">') + control + '</td>';
  }

  function userRow(u, me, admins) {
    const isSelf = u.uid === me.uid;
    const last = u.isAdmin && admins.length <= 1;
    return '<tr>' +
      '<td class="cell-user"><div class="user-cell"><span class="avatar ' + (u.isAdmin ? 'acid' : '') + '">' + U.avatarInner(u.username, u.avatar) + '</span>' +
        '<div><div class="u-name">' + U.esc(u.username) + (isSelf ? I.t(' <span class="mono-xs">(你)</span>') : '') + '</div>' +
        '<span class="u-id">' + U.esc(u.uid) + '</span></div></div></td>' +
      I.t('<td data-label="角色">') + (u.isAdmin ? U.badge(I.t('管理员'), 'ink') : U.badge(I.t('普通用户'), 'neutral')) + '</td>' +
      '<td data-label="MCP">' + (u.mcpGranted ? '<span class="key-state ok">' + U.icon('check') + I.t('已授权</span>') : '<span class="key-state off">' + U.icon('lock') + I.t('未授权</span>')) + '</td>' +
      projectsCell(u) +
      I.t('<td class="num" data-label="搜索配额">') + (u.quota.enabled ? U.num(u.quota.used) + ' / ' + (u.quota.limit < 0 ? '∞' : U.num(u.quota.limit)) : I.t('未启用')) + '</td>' +
      '<td class="num hide-sm">' + U.fmtDate(u.createdAt) + '</td>' +
      '<td class="right cell-actions"><div class="actions">' +
        '<button type="button" class="btn ghost sm" data-admin="' + U.esc(u.uid) + '"' +
          ((isSelf && u.isAdmin) || last ? I.t(' disabled title="不能撤销自己或最后一个管理员"') : '') + '>' +
          (u.isAdmin ? I.t('撤销管理员') : I.t('设为管理员')) + '</button>' +
        (isSelf ? '' :
          '<button type="button" class="icon-btn" data-reset-pass="' + U.esc(u.uid) +
            '" title="' + U.esc(I.t('重置密码')) + '">' + U.icon('key') + '</button>') +
        '<button type="button" class="icon-btn danger" data-del-user="' + U.esc(u.uid) + '"' +
          (isSelf ? I.t(' disabled title="不能删除自己"') : '') + I.t(' title="删除账号">') + U.icon('trash') + '</button>' +
      '</div></td>' +
    '</tr>';
  }

  function openCeilingMenu(anchor, user) {
    const items = LEVEL_NOTES.map((note) => {
      const text = I.t(note);
      const id = text.slice(0, 2);
      const index = parseInt(id.replace(/\D/g, ''), 10);
      return {
        label: id + ' · ' + text.slice(2).trim(),
        disabled: index > 4,
        active: user.ceiling === id,
        onPick: () => {
          if (user.ceiling === id) return;
          patchUser(NB.mock, user.uid, { exec_ceiling: id }, () => {
            U.toast(user.username + I.t(' 的天花板 → ') + id);
          });
        },
      };
    });
    U.menu(anchor, items, { head: I.t('执行天花板') });
  }

  function grantsRow(u, me) {
    return '<tr>' +
      '<td class="cell-user"><div class="user-cell"><span class="avatar ' + (u.isAdmin ? 'acid' : '') + '">' + U.avatarInner(u.username, u.avatar) + '</span>' +
        '<div><div class="u-name">' + U.esc(u.username) + '</div><span class="u-id">' + (u.isAdmin ? I.t('管理员') : I.t('普通用户')) + '</span></div></div></td>' +
      I.t('<td data-label="执行天花板"><div class="ceiling-cell">') +
        '<button type="button" class="select" data-ceiling="' + U.esc(u.uid) + '"' +
          ' aria-haspopup="menu">' + U.esc(u.ceiling || I.t('默认')) + '</button>' +
        (u.ceiling ? U.levelLadder(u.ceiling) : I.t('<span class="mono-xs">按身份默认（非管理员 T1）</span>')) +
      '</div></td>' +
      I.t('<td data-label="搜索配额 / 日"><div class="quota-cell">') +
        U.switchHTML(u.quota.enabled, 'data-quota-on="' + U.esc(u.uid) + '"') +
        '<input class="input mono" type="number" min="0" max="9999" value="' + (u.quota.limit < 0 ? 0 : u.quota.limit) + '" data-quota-limit="' + U.esc(u.uid) + '"' + (u.quota.enabled ? '' : ' disabled') + '>' +
        I.t('<span class="mono-xs">/ 日</span>') +
      '</div></td>' +
      '<td data-label="MCP">' + U.switchHTML(u.mcpGranted, 'data-mcp-grant="' + U.esc(u.uid) + '"') + '</td>' +
      projectsCell(u) +
      I.t('<td data-label="角色">') + (u.isAdmin ? U.badge(I.t('管理员'), 'ink') : U.badge(I.t('普通'), 'neutral')) + '</td>' +
    '</tr>';
  }

  function addUserModal() {
    U.modal({
      kicker: 'NEW USER',
      title: I.t('添加用户'),
      body: '<div class="stack">' +
        I.t('<div class="field"><label for="nu-name">用户名</label><input class="input" id="nu-name" placeholder="2–32 位" autocomplete="off"></div>') +
        I.t('<div class="field"><label for="nu-pass">初始密码</label><input class="input" id="nu-pass" type="password" placeholder="至少 8 位" autocomplete="new-password"></div>') +
        I.t('<div class="field"><label for="nu-pass2">确认密码</label><input class="input" id="nu-pass2" type="password" placeholder="再输入一次" autocomplete="new-password"></div>') +
        I.t('<label class="check"><input type="checkbox" id="nu-admin"><span>设为管理员<span class="check-hint">管理员可以管理其他账号、全局 provider 与系统层配置。</span></span></label>') +
        I.t('<div class="banner steel" style="margin-top:2px">新账号默认 T0、MCP 未授权、搜索配额启用（每天 50 次）。创建后可在「授权」页调整。</div>') +
      '</div>',
      footer: I.t('<button type="button" class="btn ghost" data-cancel>取消</button><button type="button" class="btn signal" data-save>创建账号</button>'),
      onMount: (wrap, close) => {
        wrap.querySelector('[data-cancel]').onclick = close;
        wrap.querySelector('[data-save]').onclick = () => {
          const name = wrap.querySelector('#nu-name').value.trim();
          const p1 = wrap.querySelector('#nu-pass').value;
          const p2 = wrap.querySelector('#nu-pass2').value;
          if (!/^[a-zA-Z0-9_.-]{2,32}$/.test(name)) { U.toast(I.t('用户名格式不正确'), 'bad'); return; }
          if (p1.length < 8) { U.toast(I.t('密码至少 8 位'), 'bad'); return; }
          if (p1 !== p2) { U.toast(I.t('两次密码不一致'), 'bad'); return; }
          const isAdmin = wrap.querySelector('#nu-admin').checked;
          NB.api.adminCreateUser({ username: name, password: p1, is_admin: isAdmin }).then(() => {
            close();
            loadUsers(NB.mock, true);
            U.toast(I.t('已创建用户 ') + name);
          }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
        };
      },
    });
  }

  function resetPasswordModal(u) {
    U.modal({
      kicker: 'RESET PASSWORD',
      title: I.t('重置密码') + ' · ' + u.username,
      body: '<div class="stack">' +
        '<div class="banner steel">' + U.icon('key') +
          I.t('<div>保存后该账号的所有登录会立即失效。</div></div>') +
        I.t('<div class="field"><label for="rp-pass">新密码</label><input class="input" id="rp-pass" type="password" autocomplete="new-password" placeholder="至少 8 位"></div>') +
        I.t('<div class="field"><label for="rp-pass2">确认新密码</label><input class="input" id="rp-pass2" type="password" autocomplete="new-password" placeholder="再输入一次"></div>') +
      '</div>',
      footer: I.t('<button type="button" class="btn ghost" data-cancel>取消</button>') +
        '<button type="button" class="btn signal" data-confirm>' + I.t('重置密码') + '</button>',
      onMount: (wrap, close) => {
        wrap.querySelector('[data-cancel]').onclick = close;
        wrap.querySelector('[data-confirm]').onclick = () => {
          const p1 = wrap.querySelector('#rp-pass').value;
          const p2 = wrap.querySelector('#rp-pass2').value;
          if (p1.length < 8) { U.toast(I.t('密码至少 8 位'), 'bad'); return; }
          if (p1 !== p2) { U.toast(I.t('两次密码不一致'), 'bad'); return; }
          NB.api.adminResetPassword(u.uid, p1).then(() => {
            close();
            U.toast(I.t('密码已重置'));
          }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
        };
      },
    });
  }

  function deleteUserModal(u, me) {
    if (u.uid === me.uid) { U.toast(I.t('不能删除自己'), 'bad'); return; }
    const admins = NB.mock.users.filter((x) => x.isAdmin);
    if (u.isAdmin && admins.length <= 1) { U.toast(I.t('不能删除最后一个管理员'), 'bad'); return; }
    U.modal({
      kicker: 'DELETE USER',
      title: I.t('删除账号'),
      body: '<div class="stack">' +
        '<div class="banner bad">' + U.icon('alert') + I.t('<div>删除 <b>') + U.esc(u.username) + I.t('</b> 会一并移除其会话、消息、私有 provider、MCP server 与私有技能。不可恢复。</div></div>') +
        I.t('<div class="field"><label for="du-name">键入目标用户名</label><input class="input mono" id="du-name" placeholder="') + U.esc(u.username) + '" autocomplete="off"></div>' +
      '</div>',
      footer: I.t('<button type="button" class="btn ghost" data-cancel>取消</button><button type="button" class="btn danger" data-confirm>删除账号</button>'),
      onMount: (wrap, close) => {
        wrap.querySelector('[data-cancel]').onclick = close;
        wrap.querySelector('[data-confirm]').onclick = () => {
          const name = wrap.querySelector('#du-name').value.trim();
          if (name !== u.username) { U.toast(I.t('用户名不匹配'), 'bad'); return; }
          NB.api.adminDeleteUser(u.uid).then(() => {
            close();
            loadUsers(NB.mock, true);
            U.toast(I.t('已删除 ') + u.username);
          }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
        };
      },
    });
  }

  NB.pages['admin-users'] = {
    userRowForTest: userRow,
    title: '用户与授权', kicker: '管理 / USERS & GRANTS', full: false,
    render() {
      const mock = NB.mock;
      const me = mock.me();
      const admins = mock.users.filter((u) => u.isAdmin);
      const tab = mock.ui.adminTab || 'users';
      const mcpOn = mock.users.filter((u) => u.mcpGranted).length;

      return '<div class="admin-page">' +
        '<div class="page-head">' +
          I.t('<div><div class="kicker">管理 / USERS & GRANTS</div><h2 class="page-title">用户与授权</h2>') +
            I.t('<p class="page-desc">执行天花板决定账号“最多能做到哪一档”，会话可以更低。撤销管理员与删除账号都受保护：不能操作自己，也不能移除最后一个管理员。</p></div>') +
          '<div class="page-actions">' +
            '<span class="badge neutral">' + mock.users.length + I.t(' 账号</span>') +
            '<span class="badge steel">' + mcpOn + I.t(' MCP 授权</span>') +
            '<button type="button" class="btn signal" id="add-user">' + U.icon('plus') + I.t('添加用户</button>') +
          '</div>' +
        '</div>' +

        '<div class="tabs" role="tablist">' +
          '<button type="button" class="tab" role="tab" data-tab="users" aria-selected="' + (tab === 'users') + I.t('">账号<span class="tab-count">') + mock.users.length + '</span></button>' +
          '<button type="button" class="tab" role="tab" data-tab="grants" aria-selected="' + (tab === 'grants') + I.t('">授权<span class="tab-count">') + mock.users.length + '</span></button>' +
        '</div>' +

        (tab === 'users'
          ? '<section class="panel"><div class="table-wrap"><table class="table responsive"><thead><tr>' +
              I.t('<th>用户</th><th>角色</th><th>MCP</th>') +
              (NB.mock.mode === 'multi' ? I.t('<th>项目</th>') : '') +
              I.t('<th>搜索配额</th><th class="hide-sm">创建时间</th><th class="right">操作</th>') +
            '</tr></thead><tbody>' + mock.users.map((u) => userRow(u, me, admins)).join('') + '</tbody></table></div></section>'
          : '<section class="panel">' +
              '<div class="panel-head"><div class="panel-title">' + U.icon('sliders') + I.t('执行与资源授权</div>') +
                '<span class="kicker">CHANGES SAVE IMMEDIATELY</span></div>' +
              '<div class="table-wrap"><table class="table responsive"><thead><tr>' +
                I.t('<th>用户</th><th>执行天花板</th><th>搜索配额 / 日</th><th>MCP</th>') +
                (NB.mock.mode === 'multi' ? I.t('<th>项目</th>') : '') +
                I.t('<th>角色</th>') +
              '</tr></thead><tbody>' + mock.users.map((u) => grantsRow(u, me)).join('') + '</tbody></table></div>' +
              I.t('<div class="panel-foot"><span>服务上限 ') + U.esc(mock.system.execCap) + I.t(' · 改天花板不会自动提升当前会话档位</span>') +
                '<span class="mono-xs">T0 → T4</span></div>' +
            '</section>') +

        '<div class="banner steel">' + U.icon('shield') +
          I.t('<div><b>授权边界：</b>账号天花板只限制“会话能开到哪一档”；实际执行仍由当前会话档位、工具级权限与确认门共同决定。MCP 授权只决定能否添加自己的 server。</div>') +
        '</div>' +
      '</div>';
    },

    mount(root) {
      const mock = NB.mock;
      loadUsers(mock);
      root.querySelectorAll('[data-tab]').forEach((b) => {
        b.onclick = () => { mock.ui.adminTab = b.dataset.tab; NB.app.render(); };
      });
      const add = root.querySelector('#add-user');
      if (add) add.onclick = addUserModal;

      root.querySelectorAll('[data-admin]').forEach((b) => {
        b.onclick = () => {
          const u = mock.users.find((x) => x.uid === b.dataset.admin);
          if (!u) return;
          const me = mock.me();
          const admins = mock.users.filter((x) => x.isAdmin);
          if (u.uid === me.uid && u.isAdmin) { U.toast(I.t('不能撤销自己的管理员'), 'bad'); return; }
          if (u.isAdmin && admins.length <= 1) { U.toast(I.t('不能撤销最后一个管理员'), 'bad'); return; }
          patchUser(mock, u.uid, { is_admin: !u.isAdmin }, (updated) => {
            U.toast(updated.username + (updated.isAdmin ? I.t(' 已设为管理员') : I.t(' 已撤销管理员')));
            if (updated.uid === me.uid) NB.app.bootstrap();
          });
        };
      });

      root.querySelectorAll('[data-reset-pass]').forEach((b) => {
        b.onclick = () => {
          const u = mock.users.find((x) => x.uid === b.dataset.resetPass);
          if (u) resetPasswordModal(u);
        };
      });

      root.querySelectorAll('[data-del-user]').forEach((b) => {
        b.onclick = () => {
          const u = mock.users.find((x) => x.uid === b.dataset.delUser);
          if (!u) return;
          deleteUserModal(u, mock.me());
        };
      });

      root.querySelectorAll('[data-ceiling]').forEach((btn) => {
        btn.onclick = () => {
          const u = mock.users.find((x) => x.uid === btn.dataset.ceiling);
          if (u) openCeilingMenu(btn, u);
        };
      });

      root.querySelectorAll('[data-quota-on]').forEach((sw) => {
        sw.onclick = () => {
          const u = mock.users.find((x) => x.uid === sw.dataset.quotaOn);
          if (!u) return;
          const enabled = !u.quota.enabled;
          // 不改用户已设的次数；默认值由后端给（50/天），0 是用户有意“全禁”
          const limit = u.quota.limit;
          patchUser(mock, u.uid, { quota_enabled: enabled, quota_limit: limit }, () => {
            U.toast(u.username + I.t(' 的搜索配额') + (enabled ? I.t('已启用') : I.t('已关闭')));
          });
        };
      });

      root.querySelectorAll('[data-quota-limit]').forEach((inp) => {
        inp.onchange = () => {
          const u = mock.users.find((x) => x.uid === inp.dataset.quotaLimit);
          if (!u) return;
          const v = Math.max(0, Math.min(9999, parseInt(inp.value, 10) || 0));
          inp.value = v;
          patchUser(mock, u.uid, { quota_limit: v, quota_enabled: u.quota.enabled }, () => {
            U.toast(u.username + I.t(' 的配额上限 → ') + v + I.t(' / 日'));
          });
        };
      });

      root.querySelectorAll('[data-mcp-grant]').forEach((sw) => {
        sw.onclick = () => {
          const u = mock.users.find((x) => x.uid === sw.dataset.mcpGrant);
          if (!u) return;
          const granted = !u.mcpGranted;
          patchUser(mock, u.uid, { mcp_granted: granted }, () => {
            U.toast(u.username + I.t(' 的 MCP ') + (granted ? I.t('已授权') : I.t('已收回')), granted ? 'ok' : 'warn');
          });
        };
      });

      root.querySelectorAll('[data-projects-grant]').forEach((sw) => {
        sw.onclick = () => {
          const u = mock.users.find((x) => x.uid === sw.dataset.projectsGrant);
          if (!u) return;
          const enabled = !u.projectsEnabled;
          patchUser(mock, u.uid, { projects_enabled: enabled }, () => {
            U.toast(u.username + (enabled ? I.t(' 已开通项目功能') : I.t(' 的项目功能已收回')),
              enabled ? 'ok' : 'warn');
            if (u.uid === mock.me().uid) NB.app.bootstrap();
          });
        };
      });
    },
  };

  NB.pages['admin-system'] = {
    title: '系统信息', kicker: '管理 / SYSTEM', full: false,
    render() {
      const s = NB.mock.system;
      const w = s.web || {};
      const defaultModel = (NB.app && NB.app.defaultModel) || (NB.mock.ui && NB.mock.ui.defaultModel) || '';
      const models = NB.mock.models || [];
      const filtered = filteredModels(models);
      const catalog = modelPageState(filtered.length);
      const visibleModels = filtered.slice(catalog.start, catalog.start + MODEL_PAGE_SIZE);
      const countLabel = filtered.length === models.length
        ? models.length + ' MODELS'
        : filtered.length + ' / ' + models.length + ' MODELS';
      return '<div class="system-page">' +
        '<div class="page-head">' +
          I.t('<div><div class="kicker">管理 / SYSTEM</div><h2 class="page-title">系统信息</h2>') +
            I.t('<p class="page-desc">诊断视图。默认模型可在「模型目录」直接切换并写入 novabot.json；其余值只读。</p></div>') +
        '</div>' +

        '<div class="system-grid">' +
          '<section class="panel system-section">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('terminal') + I.t('运行环境</div><span class="kicker">RUNTIME</span></div>') +
            '<div class="panel-body"><dl class="kv">' +
              I.t('<dt>版本</dt><dd>') + U.esc(s.version) + '</dd>' +
              I.t('<dt>构建</dt><dd>') + U.esc(s.build) + '</dd>' +
              I.t('<dt>API 监听</dt><dd>') + U.esc(s.entry) + '</dd>' +
              I.t('<dt>用户模式</dt><dd>') + U.esc(I.t(s.modeLabel)) + '</dd>' +
              I.t('<dt>数据目录</dt><dd>') + U.esc(s.dataDir) + '</dd>' +
              I.t('<dt>数据库</dt><dd>') + U.esc(s.dbPath) + '</dd>' +
              I.t('<dt>配置文件</dt><dd>') + U.esc(s.configPath) + '</dd>' +
            '</dl></div>' +
          '</section>' +

          '<section class="panel system-section">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('shield') + I.t('执行权限</div><span class="kicker">EXEC</span></div>') +
            '<div class="panel-body"><dl class="kv">' +
              I.t('<dt>服务上限</dt><dd>') + U.esc(s.execCap) + '</dd>' +
              I.t('<dt>单用户默认</dt><dd>') + U.esc(s.defaults.single) + '</dd>' +
              I.t('<dt>管理员默认</dt><dd>') + U.esc(s.defaults.admin) + '</dd>' +
              I.t('<dt>普通用户默认</dt><dd>') + U.esc(s.defaults.user) + '</dd>' +
            '</dl>' +
            '<div class="level-desc-grid">' +
              LEVEL_NOTES.map((note) => {
                const text = I.t(note);
                return '<div class="level-desc"><span class="badge mono ink">' + U.esc(text.slice(0, 2)) + '</span>' +
                  '<span>' + U.esc(text.slice(2).trim()) + '</span></div>';
              }).join('') +
            '</div></div>' +
          '</section>' +

          '<section class="panel system-section">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('globe') + I.t('网络能力</div><span class="kicker">WEB</span></div>') +
            '<div class="panel-body"><dl class="kv">' +
              I.t('<dt>兜底 provider</dt><dd>') +
                chips(w.builtin.map((name) => U.badge(name, 'neutral')), I.t('未配置')) + '</dd>' +
              I.t('<dt>全局 provider</dt><dd>') +
                chips(w.global.map((row) => U.badge(row.provider + (row.enabled ? '' : I.t(' · 停用')),
                  row.enabled ? 'steel' : 'neutral')), I.t('未配置')) + '</dd>' +
              I.t('<dt>用户配置</dt><dd>') + U.esc(String(w.userCount || 0)) + I.t(' 个</dd>') +
            '</dl>' +
            '<div class="banner steel" style="margin-top:12px">' + U.icon('clock') +
              I.t('<div>兜底 provider 每日 00:00 重置；下次重置 ') +
              U.fmtDateTime(w.resetAt) + '</div></div>' +
            '</div>' +
          '</section>' +

          '<section class="panel system-section">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('plug') + I.t('MCP 限制</div><span class="kicker">MCP</span></div>') +
            '<div class="panel-body"><dl class="kv">' +
              I.t('<dt>用户层上限</dt><dd>') + s.mcp.userMax + I.t(' 个 / 用户</dd>') +
              I.t('<dt>单 server 工具</dt><dd>') + s.mcp.toolMax + I.t(' 个</dd>') +
              I.t('<dt>系统层</dt><dd>') + (s.mcp.system.length ? s.mcp.system.length + I.t(' 个') : I.t('未配置')) + '</dd>' +
            '</dl>' +
            '<div class="banner steel" style="margin-top:12px">' + U.icon('lock') +
              I.t('<div>系统层 server 在配置文件维护。用户层 server 由各自账号添加，逐工具权限只作用于本人。</div></div>') +
            '</div>' +
          '</section>' +
        '</div>' +

        '<section class="panel">' +
          '<div class="panel-head system-model-head">' +
            '<div class="panel-title">' + U.icon('cpu') + I.t('模型目录</div>') +
            '<div class="system-model-tools">' +
              '<span class="kicker" id="system-model-count">' + countLabel + '</span>' +
              '<div class="search">' + U.icon('search') +
                '<input class="input" id="system-model-search" autocomplete="off" placeholder="' +
                  U.esc(I.t('搜索 provider 或模型…')) + '" value="' +
                  U.esc(NB.mock.ui.systemModelQuery || '') + '">' +
              '</div>' +
            '</div>' +
          '</div>' +
          '<div class="table-wrap"><table class="table model-table"><thead><tr>' +
            I.t('<th>模型</th><th>Provider</th><th>思考</th><th>上下文</th><th>默认</th>') +
          '</tr></thead><tbody id="system-model-tbody">' +
            modelRowsHTML(visibleModels, defaultModel) +
          '</tbody></table></div>' +
          '<div class="table-pager" id="system-model-pager">' +
            modelPagerInnerHTML(catalog.page, catalog.pages, filtered.length) +
          '</div>' +
        '</section>' +
      '</div>';
    },
    mount(root) {
      const mock = NB.mock;
      const search = root.querySelector('#system-model-search');
      if (search) {
        search.addEventListener('input', (e) => {
          mock.ui.systemModelQuery = e.target.value;
          mock.ui.systemModelPage = 0;
          paintModelCatalog(root);
        });
      }
      const pager = root.querySelector('#system-model-pager');
      if (pager) {
        pager.addEventListener('click', (e) => {
          const btn = e.target.closest && e.target.closest('[data-model-page]');
          if (!btn) return;
          const filtered = filteredModels(mock.models || []);
          const pages = Math.max(1, Math.ceil(filtered.length / MODEL_PAGE_SIZE));
          let page = Number(mock.ui.systemModelPage) || 0;
          page += btn.dataset.modelPage === 'prev' ? -1 : 1;
          if (page < 0 || page >= pages) return;
          mock.ui.systemModelPage = page;
          paintModelCatalog(root);
        });
      }
      // 设为默认：委托挂在 tbody 上，重绘只换 innerHTML，监听不会丢
      const tbody = root.querySelector('#system-model-tbody');
      if (tbody) {
        tbody.addEventListener('click', (event) => {
          const btn = event.target.closest && event.target.closest('[data-set-default]');
          if (!btn || btn.disabled) return;
          const model = btn.dataset.setDefault;
          btn.disabled = true;
          btn.textContent = I.t('设置中…');
          NB.api.setDefaultModel(model).then((result) => {
            const next = (result && result.default_model) || model;
            NB.app.defaultModel = next;
            mock.ui.defaultModel = next;
            const chosen = (mock.models || []).find((m) => m.id === next);
            if (chosen) mock.ui.defaultThinking = !!chosen.thinking;
            paintModelCatalog(root);
            U.toast(I.t('已将 {model} 设为默认模型', { model: next }), 'ok');
          }).catch((error) => {
            btn.disabled = false;
            btn.textContent = I.t('设为默认 →');
            U.toast(error.message || I.t('error.network'), 'bad');
          });
        });
      }
      if (!mock.ui.systemLoaded) {
        NB.api.get('/system').then((s) => {
          Object.assign(mock.system, {
            version: s.version,
            build: s.platform + ' · Python ' + s.python,
            entry: I.t('FastAPI · 本机 API'),
            modeLabel: s.mode_label,
            dataDir: s.data_dir,
            dbPath: s.db_path,
            configPath: s.config_file,
            execCap: s.exec_cap,
            defaults: { single: s.defaults.single, admin: s.defaults.admin, user: s.defaults.user },
            web: {
              builtin: (s.web && s.web.builtin) || [],
              global: (s.web && s.web.global) || [],
              userCount: (s.web && s.web.user_provider_count) || 0,
              resetAt: s.web && s.web.reset_at
                ? new Date(s.web.reset_at * 1000).toISOString() : null,
            },
          });
          mock.ui.systemLoaded = true;
          NB.app.render();
        }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
      }
    },
  };
})();
