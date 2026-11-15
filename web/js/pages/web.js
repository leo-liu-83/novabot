/* 网络搜索：用户自备 provider / 管理员全局 provider */
(function () {
  const NB = window.NB;
  const U = NB.ui;
  const I = NB.i18n;
  NB.pages = NB.pages || {};

  const KINDS = ['exa', 'tavily', 'brave', 'parallel', 'serpapi', 'searxng', 'custom_mcp'];
  const NEEDS_ENDPOINT = ['searxng', 'custom_mcp'];
  const NO_KEY = ['searxng', 'custom_mcp'];

  function mapProvider(row) {
    return {
      kind: row.provider,
      name: U.kindName(row.provider),
      endpoint: row.endpoint || '',
      enabled: !!row.enabled,
      hasKey: !!row.has_key,
      note: '',
      tool: 'search',
    };
  }

  function makePage(scope) {
    const global = scope === 'global';
    const apiBase = global ? '/web/global-providers' : '/web/providers';
    const list = () => (global ? NB.mock.globalProviders : NB.mock.providers);

    function reload(force) {
      const flags = NB.mock.ui.providerLoaded || (NB.mock.ui.providerLoaded = {});
      if (flags[scope] && !force) return;
      flags[scope] = true;
      NB.api.get(apiBase).then((res) => {
        const rows = (res.providers || []).map(mapProvider);
        if (global) NB.mock.globalProviders = rows;
        else NB.mock.providers = rows;
        NB.app.render();
      }).catch((error) => {
        flags[scope] = false;
        U.toast(error.message || NB.i18n.t('error.network'), 'bad');
      });
    }

    function providerHTML(p, i) {
      return '<tr>' +
        '<td class="cell-user"><div class="row" style="gap:8px"><span class="provider-kind">' + U.esc(U.kindName(p.kind)) + '</span>' +
          (NEEDS_ENDPOINT.includes(p.kind) ? U.badge('SELF-HOST', 'steel') : '') + '</div>' +
          '<span class="sub">' + U.esc(p.name) + (p.note ? ' · ' + U.esc(p.note) : '') + '</span></td>' +
        '<td class="hide-sm"><span class="mono" style="font-size:11.5px;color:var(--muted)">' + U.esc(p.endpoint || I.t('官方端点')) + '</span></td>' +
        I.t('<td data-label="密钥">') + (NO_KEY.includes(p.kind)
          ? '<span class="key-state off">' + U.icon('globe') + I.t('无需 key</span>')
          : p.hasKey
            ? '<span class="key-state ok">' + U.icon('key') + I.t('已保存</span>')
            : '<span class="key-state off">' + U.icon('key') + I.t('未配置</span>')) + '</td>' +
        I.t('<td data-label="启用">') + U.switchHTML(p.enabled, 'data-toggle="' + i + '"') + '</td>' +
        '<td class="right cell-actions"><div class="actions">' +
          '<button type="button" class="icon-btn" data-edit="' + i + I.t('" title="编辑">') + U.icon('pencil') + '</button>' +
          '<button type="button" class="icon-btn danger" data-remove="' + i + I.t('" title="删除">') + U.icon('trash') + '</button>' +
        '</div></td>' +
      '</tr>';
    }

    function formBody(p) {
      const kind = p ? p.kind : 'exa';
      return '<div class="stack">' +
        I.t('<div class="field"><label for="pv-kind">服务类型</label>') +
          '<select class="select" id="pv-kind">' + KINDS.map((k) =>
            '<option value="' + k + '"' + (k === kind ? ' selected' : '') + '>' + U.kindName(k) + '</option>').join('') +
          '</select>' +
          I.t('<span class="hint">每种类型只保留一条配置；再次保存就是更新。</span></div>') +
        I.t('<div class="field" data-when="endpoint"><label for="pv-endpoint">端点 / URL</label>') +
          '<input class="input mono" id="pv-endpoint" autocomplete="off" value="' + U.esc(p ? p.endpoint : '') + I.t('" placeholder="https://… 或 http://127.0.0.1:8080/search">') +
          I.t('<span class="hint">自托管 SearXNG 填完整搜索 URL；custom_mcp 填 MCP server 地址。</span></div>') +
        '<div class="field" data-when="key"><label for="pv-key">API Key</label>' +
          '<input class="input mono" id="pv-key" type="password" autocomplete="new-password" placeholder="' + (p && p.hasKey ? I.t('已保存，留空表示不修改') : I.t('粘贴 provider 的 key')) + '">' +
          I.t('<span class="hint">只写入本机数据库；界面上不回显明文。</span></div>') +
        I.t('<div class="field" data-when="tool"><label for="pv-tool">MCP 工具名</label>') +
          '<input class="input mono" id="pv-tool" value="' + U.esc(p && p.tool ? p.tool : 'search') + '" placeholder="search"></div>' +
      '</div>';
    }

    function openEditor(index) {
      const arr = list();
      const editing = index != null ? arr[index] : null;
      let currentKind = editing ? editing.kind : 'exa';
      const m = U.modal({
        kicker: scope === 'global' ? 'GLOBAL PROVIDER' : 'PROVIDER',
        title: editing ? I.t('编辑 provider') : I.t('添加 provider'),
        wide: true,
        body: formBody(editing),
        footer: I.t('<button type="button" class="btn ghost" data-cancel>取消</button>') +
          '<button type="button" class="btn signal" data-save>' + (editing ? I.t('保存修改') : I.t('添加')) + '</button>',
        onMount: (wrap, close) => {
          const kindSel = wrap.querySelector('#pv-kind');
          const sync = () => {
            currentKind = kindSel.value;
            wrap.querySelector('[data-when="endpoint"]').style.display = NEEDS_ENDPOINT.includes(currentKind) ? '' : 'none';
            wrap.querySelector('[data-when="key"]').style.display = NO_KEY.includes(currentKind) ? 'none' : '';
            wrap.querySelector('[data-when="tool"]').style.display = currentKind === 'custom_mcp' ? '' : 'none';
          };
          kindSel.addEventListener('change', sync);
          sync();
          const save = () => {
            // 端点只对自托管类型有意义。内置 provider 的输入框是隐藏的，
            // 但里面可能残留浏览器自动填充或切换类型前的文本，不能读也不能发。
            const needsEndpoint = NEEDS_ENDPOINT.includes(currentKind);
            const endpoint = needsEndpoint ? wrap.querySelector('#pv-endpoint').value.trim() : '';
            const key = wrap.querySelector('#pv-key').value.trim();
            if (needsEndpoint && !endpoint) { U.toast(I.t('这个类型需要端点'), 'bad'); return; }
            const payload = {};
            if (key) payload.api_key = key;
            if (endpoint) payload.endpoint = endpoint;
            if (currentKind === 'custom_mcp') {
              payload.extra = { tool: wrap.querySelector('#pv-tool').value.trim() || 'search' };
            }
            NB.api.put(apiBase + '/' + encodeURIComponent(currentKind), payload).then(() => {
              close();
              U.toast(editing ? I.t('provider 已更新') : I.t('provider 已添加'));
              reload(true);
            }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
          };
          wrap.querySelector('[data-cancel]').onclick = close;
          wrap.querySelector('[data-save]').onclick = save;
        },
      });
    }

    function render() {
      const mock = NB.mock;
      const me = mock.me();
      const arr = list();
      const isGlobal = scope === 'global';
      const quota = mock.quota || me.quota || { enabled: false, limit: 0, used: 0, resetAt: null };
      const quotaOff = !quota.enabled;
      const unlimited = !quotaOff && quota.limit < 0;
      const pct = quotaOff || unlimited ? 0 : U.pct(quota.used, quota.limit);
      const enabledCount = arr.filter((p) => p.enabled).length;
      const mcpCount = mock.mcp.servers.length;

      const quotaPanel = !isGlobal ? '' +
        '<section class="panel"><div class="quota-panel">' +
          '<div class="quota-main">' +
            I.t('<div class="stat-label">SEARCH QUOTA / 本期</div>') +
            '<div class="quota-big"><span class="stat-num">' + U.num(quota.used) + '</span><span class="of">/ ' + (quotaOff ? '—' : unlimited ? I.t('不限') : U.num(quota.limit)) + '</span></div>' +
            (quotaOff || unlimited ? '' : '<div class="meter"><span class="' + (pct >= 90 ? 'bad' : pct >= 70 ? 'warn' : '') + '" style="width:' + pct + '%"></span></div>') +
            (quota.resetAt
              ? I.t('<div class="quota-when"><span>重置 ') + U.fmtDateTime(quota.resetAt) + '</span><span>' + (quotaOff || unlimited ? '' : pct + '%') + '</span></div>'
              : '') +
          '</div>' +
          '<div class="quota-aside">' +
            I.t('<div class="stat-label">降级顺序</div>') +
            I.t('<div class="mono-sm" style="color:var(--ink-2);font-size:12px">用户自备 → 全局配置 → 免费兜底</div>') +
            I.t('<div class="mono-xs">当前有 ') + enabledCount + I.t(' 个可用 provider。失败请求不计入配额。</div>') +
          '</div>' +
          '<div class="quota-action"><a class="btn ghost" href="#/mcp">' + U.icon('plug') + I.t('MCP 调用（') + mcpCount + '）</a></div>' +
        '</div></section>' : '' +
        '<section class="panel"><div class="quota-panel">' +
          '<div class="quota-main">' +
            '<div class="stat-label">GLOBAL PROVIDERS</div>' +
            '<div class="quota-big"><span class="stat-num">' + arr.length + I.t('</span><span class="of">个配置 · ') + enabledCount + I.t(' 启用</span></div>') +
            I.t('<div class="mono-xs" style="margin-top:10px">全局配置只在用户没有自备 provider 时兜底；用户自己的优先。</div>') +
          '</div>' +
          '<div class="quota-aside">' +
            I.t('<div class="stat-label">权限</div>') +
            I.t('<div class="mono-sm" style="color:var(--ink-2);font-size:12px">仅管理员可改</div>') +
            I.t('<div class="mono-xs">密钥写库后不回显，替换需要重新输入。</div>') +
          '</div>' +
          '<div class="quota-action"><span class="badge ink">ADMIN ONLY</span></div>' +
        '</div></section>';

      return '<div class="web-page">' +
        '<div class="page-head">' +
          '<div><div class="kicker">' + (isGlobal ? I.t('管理 / GLOBAL WEB') : I.t('配置 / WEB ACCESS')) + '</div>' +
            '<h2 class="page-title">' + (isGlobal ? I.t('全局搜索 provider') : I.t('网络搜索')) + '</h2>' +
            '<p class="page-desc">' + (isGlobal
              ? I.t('所有账号共享的兜底 provider。用户自备配置优先，其次才轮到全局，最后才是免费兜底。')
              : I.t('搜索与抓取分离计费。抓取正文属于读操作，但仍受域名 allowlist 与 ask 策略约束。')) + '</p></div>' +
          '<div class="page-actions"><button type="button" class="btn signal" id="add-provider">' + U.icon('plus') + I.t('添加 provider</button></div>') +
        '</div>' +
        quotaPanel +
        '<section class="panel">' +
          '<div class="panel-head"><div class="panel-title">' + U.icon('globe') + (isGlobal ? I.t('全局 provider') : I.t('我的 provider')) + '</div>' +
            '<span class="kicker">' + arr.length + ' CONFIGURED</span></div>' +
          (arr.length
            ? I.t('<div class="table-wrap"><table class="table responsive"><thead><tr><th>服务</th><th class="hide-sm">端点</th><th>密钥</th><th>启用</th><th class="right">操作</th></tr></thead><tbody id="provider-rows">') +
              arr.map(providerHTML).join('') + '</tbody></table></div>'
            : U.empty('globe', I.t('还没有 provider'), I.t('添加一个 Exa、Tavily 或本地 SearXNG。'))) +
        '</section>' +
        '<div class="banner steel">' + U.icon('alert') +
          I.t('<div><b>抓取边界：</b>网页抓取只能访问 allowlist 内的域名（') + mock.system.allowlist.slice(0, 3).map(U.esc).join('、') + I.t(' 等 ') + mock.system.allowlist.length + I.t(' 个）。MCP 工具调用不计入搜索配额，但逐工具权限仍然生效。</div>') +
        '</div>' +
      '</div>';
    }

    function mount(root) {
      const mock = NB.mock;
      const arr = list();

      // 后端接管：进入页面拉一次真实配置；首次渲染可能先空一帧。
      reload();
      if (!global && !NB.mock.ui.quotaLoaded) {
        NB.mock.ui.quotaLoaded = true;
        NB.api.get('/web/quota').then((quota) => {
          mock.quota = {
            enabled: !!quota.enabled, limit: quota.daily_limit, used: quota.used,
            resetAt: quota.reset_at ? new Date(quota.reset_at * 1000).toISOString() : null,
          };
          NB.app.render();
        }).catch(() => { NB.mock.ui.quotaLoaded = false; });
      }

      const addBtn = root.querySelector('#add-provider');
      if (addBtn) addBtn.onclick = () => openEditor(null);

      root.querySelectorAll('[data-toggle]').forEach((el) => {
        el.onclick = () => {
          const i = Number(el.dataset.toggle);
          const p = arr[i];
          if (!p) return;
          NB.api.patch(apiBase + '/' + encodeURIComponent(p.kind), { enabled: !p.enabled }).then(() => {
            U.toast(p.name + (!p.enabled ? I.t(' 已启用') : I.t(' 已停用')), !p.enabled ? 'ok' : 'warn');
            reload(true);
          }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
        };
      });
      root.querySelectorAll('[data-edit]').forEach((el) => {
        el.onclick = () => openEditor(Number(el.dataset.edit));
      });
      root.querySelectorAll('[data-remove]').forEach((el) => {
        el.onclick = () => {
          const i = Number(el.dataset.remove);
          const p = arr[i];
          if (!p) return;
          U.confirm({
            kicker: 'DELETE', title: I.t('删除 provider'),
            message: I.t('删除 ') + p.name + I.t(' 后，回落顺序会立即改变。已保存的密钥会一并删除。'),
            okLabel: I.t('删除'), danger: true,
          }).then((ok) => {
            if (!ok) return;
            NB.api.delete(apiBase + '/' + encodeURIComponent(p.kind)).then(() => {
              U.toast(I.t('provider 已删除'));
              reload(true);
            }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
          });
        };
      });
    }

    return {
      title: scope === 'global' ? '全局搜索' : '网络搜索',
      kicker: scope === 'global' ? '管理 / GLOBAL WEB' : '配置 / WEB ACCESS',
      full: false,
      render,
      mount,
    };
  }

  NB.pages.web = makePage('user');
  NB.pages['admin-web'] = makePage('global');
})();
