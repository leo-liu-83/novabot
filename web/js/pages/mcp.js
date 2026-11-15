/* MCP：用户层 server + 逐工具权限 */
(function () {
  const NB = window.NB;
  const U = NB.ui;
  const I = NB.i18n;
  NB.pages = NB.pages || {};

  function effective(server, tool) {
    return tool.mode || server.mode;
  }

  function modeTone(mode) {
    return mode === 'allow' ? 'steel' : mode === 'deny' ? 'bad' : 'warn';
  }

  function toolRow(server, tool) {
    const eff = effective(server, tool);
    return '<div class="mcp-tool-row">' +
      '<div class="mt-name" title="' + U.esc(tool.name) + '">' + U.esc(tool.name) + '</div>' +
      '<div class="mt-desc" title="' + U.esc(tool.desc || '') + '">' + U.esc(tool.desc || '—') + '</div>' +
      '<div class="row" style="gap:6px">' +
        '<select class="select" data-tool="' + U.esc(server.name) + '|' + U.esc(tool.name) + '">' +
          '<option value="inherit"' + (!tool.mode ? ' selected' : '') + I.t('>继承 · ') + U.esc(server.mode) + '</option>' +
          '<option value="ask"' + (tool.mode === 'ask' ? ' selected' : '') + '>ask</option>' +
          '<option value="allow"' + (tool.mode === 'allow' ? ' selected' : '') + '>allow</option>' +
          '<option value="deny"' + (tool.mode === 'deny' ? ' selected' : '') + '>deny</option>' +
        '</select>' +
        U.badge(eff, modeTone(eff)) +
      '</div>' +
    '</div>';
  }

  function card(server) {
    const open = NB.mock.ui.mcpOpen === server.name;
    const overrides = server.tools.filter((t) => t.mode).length;
    return '<article class="mcp-card" data-server="' + U.esc(server.name) + '">' +
      '<header class="mcp-card-head">' +
        '<div class="mcp-ident">' +
          '<div class="mcp-name">' + U.esc(server.name) +
            (server.enabled ? U.badge('connected', 'ok') : U.badge('disabled', 'neutral')) +
            U.badge(server.mode, modeTone(server.mode)) + '</div>' +
          '<div class="mcp-url" title="' + U.esc(server.url) + '">' + U.esc(server.url) + '</div>' +
        '</div>' +
        '<div class="mcp-controls">' +
          I.t('<div class="seg" role="group" aria-label="server 模式">') +
            ['ask', 'allow', 'deny'].map((mode) =>
              '<button type="button" data-server-mode="' + U.esc(server.name) + '|' + mode + '" data-tone="' + mode + '" aria-pressed="' + (server.mode === mode) + '">' + mode + '</button>'
            ).join('') +
          '</div>' +
          U.switchHTML(server.enabled, 'data-server-toggle="' + U.esc(server.name) + '"') +
          '<button type="button" class="icon-btn" data-server-edit="' + U.esc(server.name) + I.t('" title="编辑">') + U.icon('pencil') + '</button>' +
          '<button type="button" class="icon-btn danger" data-server-del="' + U.esc(server.name) + I.t('" title="删除">') + U.icon('trash') + '</button>' +
        '</div>' +
      '</header>' +
      (open
        ? '<div class="mcp-card-body">' +
            '<div class="mcp-tools-head"><span class="kicker">TOOLS / ' + server.tools.length + I.t(' · 逐工具覆盖 ') + overrides + '</span>' +
              (overrides ? '<button type="button" class="btn ghost sm" data-inherit-all="' + U.esc(server.name) + I.t('">全部继承</button>') : '') +
            '</div>' +
            (server.tools.length
              ? server.tools.map((t) => toolRow(server, t)).join('')
              : U.empty('plug', I.t('还没有工具列表'), I.t('连接成功后会拉取工具；deny 工具不会注册给模型。'))) +
          '</div>'
        : '') +
      '<footer class="mcp-card-foot">' +
        '<span>' + server.tools.length + I.t(' tools · 添加于 ') + U.fmtDate(server.addedAt) + '</span>' +
        '<span class="row" style="gap:6px">' +
          '<button type="button" class="btn ghost sm" data-refresh="' + U.esc(server.name) + '">' + U.icon('refresh') + I.t('刷新工具</button>') +
          '<button type="button" class="btn ghost sm" data-expand="' + U.esc(server.name) + '">' + (open ? I.t('收起') : I.t('工具权限')) + '</button>' +
        '</span>' +
      '</footer>' +
    '</article>';
  }

  function mapServer(row) {
    return {
      name: row.name,
      url: row.url,
      enabled: !!row.enabled,
      mode: row.mode || 'ask',
      addedAt: row.created_at ? new Date(row.created_at * 1000).toISOString() : null,
      // 后端返回上次探测到的工具缓存；刷新页面不必重新连接就能看到
      tools: (row.tools || []).map((t) => ({
        name: typeof t === 'string' ? t : t.name,
        desc: '',
        mode: typeof t === 'string' ? null : (t.mode || null),
      })),
    };
  }

  function loadServers(force) {
    const m = NB.mock;
    if (m.ui.mcpLoading || (m.ui.mcpLoaded && !force)) return Promise.resolve();
    m.ui.mcpLoading = true;
    return NB.api.get('/mcp/servers').then((data) => {
      m.mcp = m.mcp || {};
      m.mcp.granted = !!data.granted;
      m.mcp.limit = data.limit;
      m.mcp.servers = (data.servers || []).map(mapServer);
      m.ui.mcpLoading = false;
      m.ui.mcpLoaded = true;
      NB.app.render();
    }).catch((error) => {
      m.ui.mcpLoading = false;
      U.toast(error.message || NB.i18n.t('error.network'), 'bad');
    });
  }

  /* 只重绘 server 卡片列表，不动整页。
     刷新工具、展开/收起、改模式都只影响一张卡片；整页 render 会把页面滚回顶部，
     看起来像刷新了浏览器。 */
  function syncList() {
    if (NB.app.route !== 'mcp') return;
    const page = document.querySelector('.mcp-page');
    const box = page && page.querySelector('.mcp-list');
    const servers = (NB.mock.mcp && NB.mock.mcp.servers) || [];
    if (!box) {
      // 列表还没有渲染（例如初次加载）或已被删空：退回整页渲染
      if (page) NB.app.render();
      return;
    }
    box.innerHTML = servers.map(card).join('');
  }

  function refreshTools(server, quiet) {
    return NB.api.get('/mcp/servers/' + encodeURIComponent(server.name) + '/tools').then((data) => {
      server.tools = (data.tools || []).map((t) => ({ name: t.name, desc: '', mode: t.mode || null }));
      syncList();
      if (!quiet) U.toast(I.t('已同步 ') + server.tools.length + I.t(' 个工具'));
    }).catch((error) => {
      if (!quiet) U.toast(error.message || NB.i18n.t('error.network'), 'bad');
    });
  }

  function openEditor(name) {
    const editing = name ? NB.mock.mcp.servers.find((s) => s.name === name) : null;
    U.modal({
      kicker: 'MCP SERVER',
      title: editing ? I.t('编辑 server') : I.t('添加 MCP server'),
      body: '<div class="stack">' +
        I.t('<div class="field"><label for="mcp-name">名称</label>') +
          '<input class="input mono" id="mcp-name" value="' + U.esc(editing ? editing.name : '') + I.t('" placeholder="例如 docs" ') + (editing ? 'disabled' : '') + '>' +
          I.t('<span class="hint">名称用于工具前缀，建议短且唯一。</span></div>') +
        '<div class="field"><label for="mcp-url">Server URL</label>' +
          '<input class="input mono" id="mcp-url" value="' + U.esc(editing ? editing.url : '') + '" placeholder="https://mcp.example.com/mcp">' +
          I.t('<span class="hint">只允许 http/https；回环与内网地址会被拒绝（SSRF 防护）。mock 不校验。</span></div>') +
        I.t('<div class="field"><label for="mcp-mode">初始模式</label>') +
          '<select class="select" id="mcp-mode">' +
            ['ask', 'allow', 'deny'].map((m) => '<option value="' + m + '"' + (editing && editing.mode === m ? ' selected' : '') + '>' + m + (m === 'ask' ? I.t('（默认）') : '') + '</option>').join('') +
          '</select></div>' +
      '</div>',
      footer: I.t('<button type="button" class="btn ghost" data-cancel>取消</button><button type="button" class="btn signal" data-save>') + (editing ? I.t('保存') : I.t('添加并连接')) + '</button>',
      onMount: (wrap, close) => {
        wrap.querySelector('[data-cancel]').onclick = close;
        wrap.querySelector('[data-save]').onclick = () => {
          const nm = wrap.querySelector('#mcp-name').value.trim();
          const url = wrap.querySelector('#mcp-url').value.trim();
          const mode = wrap.querySelector('#mcp-mode').value;
          if (!/^[a-zA-Z0-9_-]{2,32}$/.test(nm)) { U.toast(I.t('名称需为 2–32 位字母、数字、下划线或短横线'), 'bad'); return; }
          if (!/^https?:\/\//.test(url)) { U.toast(I.t('URL 需要以 http:// 或 https:// 开头'), 'bad'); return; }
          if (!editing && NB.mock.mcp.servers.some((s) => s.name === nm)) { U.toast(I.t('名称已存在'), 'bad'); return; }
          NB.api.put('/mcp/servers/' + encodeURIComponent(nm), { url, mode }).then(() => {
            close();
            NB.mock.ui.mcpOpen = nm;
            loadServers(true).then(() => {
              const added = NB.mock.mcp.servers.find((s) => s.name === nm);
              if (added && !added.tools.length) refreshTools(added, true);
            });
            U.toast(editing ? I.t('server 已更新') : I.t('server 已添加；授权后才会连接'));
          }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
        };
      },
    });
  }

  let pageRoot = null;
  let cardClickListener = null;
  let cardChangeListener = null;

  NB.pages.mcp = {
    title: 'MCP', kicker: '配置 / MCP', full: false,
    mapServer: mapServer,
    render() {
      const mock = NB.mock;
      const me = mock.me();
      mock.mcp = mock.mcp || { servers: [], limit: 3, granted: false };
      const granted = mock.mcp.granted != null ? !!mock.mcp.granted : !!me.mcpGranted;
      const servers = mock.mcp.servers || [];
      const limit = mock.mcp.limit || 3;
      const full = servers.length >= limit;

      return '<div class="mcp-page">' +
        '<div class="page-head">' +
          I.t('<div><div class="kicker">配置 / MCP CONNECTIONS</div><h2 class="page-title">MCP 连接</h2>') +
            I.t('<p class="page-desc">用户层 server 由你自己维护，最多 ') + limit + I.t(' 个。server 模式是默认值，逐工具覆盖优先；deny 的工具不会注册给模型。</p></div>') +
          '<div class="page-actions">' +
            '<span class="badge ' + (granted ? 'ok' : 'bad') + '">' + (granted ? I.t('已授权') : I.t('未授权')) + '</span>' +
            '<button type="button" class="btn signal" id="add-server"' + (!granted || full ? ' disabled' : '') + '>' + U.icon('plus') + I.t('添加 server</button>') +
          '</div>' +
        '</div>' +

        (!granted
          ? '<div class="banner bad">' + U.icon('lock') + I.t('<div><b>MCP 未授权。</b>管理员在「用户与授权」里打开 MCP 开关后，你才能添加自己的 server。当前页面只读。</div></div>')
          : full
            ? '<div class="banner warn">' + U.icon('alert') + I.t('<div>已达到用户层上限（') + servers.length + '/' + limit + I.t('）。删除一个 server 后才能继续添加。</div></div>')
            : '') +

        '<section class="panel"><div class="panel-head"><div class="panel-title">' + U.icon('plug') + I.t('我的 server</div>') +
          '<span class="kicker">' + servers.length + ' / ' + limit + '</span></div>' +
          '<div class="panel-body">' +
            (servers.length
              ? '<div class="mcp-list">' + servers.map(card).join('') + '</div>'
              : U.empty('plug', I.t('还没有 MCP server'), granted ? I.t('添加一个 server，连接后即可按工具授权。') : I.t('先请管理员打开 MCP 授权。'))) +
          '</div>' +
        '</section>' +

        '<div class="banner steel">' + U.icon('shield') +
          I.t('<div><b>调用点确认：</b>当 server 为 allow、但工具为 ask 时，调用仍会暂停并请求确认。拒绝会把「未执行」回给模型，不会中断整个会话。</div>') +
        '</div>' +
      '</div>';
    },

    mount(root) {
      const mock = NB.mock;
      const me = mock.me();
      const granted = mock.mcp && mock.mcp.granted != null ? !!mock.mcp.granted : !!me.mcpGranted;

      pageRoot = root;
      loadServers();

      const add = root.querySelector('#add-server');
      if (add) add.onclick = () => { if (granted) openEditor(null); };

      const findServer = (nm) =>
        ((mock.mcp && mock.mcp.servers) || []).find((x) => x.name === nm);

      /* 卡片上的按钮走事件委托：syncList() 只替换列表 innerHTML，
         委托挂在页面根节点上，替换后不用重新绑一遍。 */
      if (cardClickListener) root.removeEventListener('click', cardClickListener);
      if (cardChangeListener) root.removeEventListener('change', cardChangeListener);

      cardClickListener = (e) => {
        const t = e.target;
        if (!t || !t.closest) return;

        const expand = t.closest('[data-expand]');
        if (expand) {
          const nm = expand.dataset.expand;
          const opening = mock.ui.mcpOpen !== nm;
          mock.ui.mcpOpen = opening ? nm : null;
          syncList();
          // 第一次展开且还没有缓存工具：顺手探测一次，免得用户还要再点“刷新工具”
          const s = findServer(nm);
          if (opening && s && !s.tools.length && granted) refreshTools(s, true);
          return;
        }

        const refresh = t.closest('[data-refresh]');
        if (refresh) {
          if (!granted) return;
          const s = findServer(refresh.dataset.refresh);
          if (!s) return;
          U.toast(I.t('正在连接 ') + s.name + ' …');
          refreshTools(s);
          return;
        }

        const toggle = t.closest('[data-server-toggle]');
        if (toggle) {
          if (!granted) return;
          const s = findServer(toggle.dataset.serverToggle);
          if (!s) return;
          NB.api.patch('/mcp/servers/' + encodeURIComponent(s.name), { enabled: !s.enabled }).then((row) => {
            s.enabled = !!row.enabled;
            U.toast(s.name + (s.enabled ? I.t(' 已连接') : I.t(' 已停用')), s.enabled ? 'ok' : 'warn');
            syncList();
          }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
          return;
        }

        const modeBtn = t.closest('[data-server-mode]');
        if (modeBtn) {
          if (!granted) return;
          const [nm, mode] = modeBtn.dataset.serverMode.split('|');
          NB.api.patch('/mcp/servers/' + encodeURIComponent(nm), { mode }).then((row) => {
            const s = findServer(nm);
            if (s) s.mode = row.mode || mode;
            U.toast(nm + I.t(' 的默认模式已改为 ') + mode);
            syncList();
          }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
          return;
        }

        const inherit = t.closest('[data-inherit-all]');
        if (inherit) {
          if (!granted) return;
          const s = findServer(inherit.dataset.inheritAll);
          if (!s) return;
          Promise.all((s.tools || []).filter((x) => x.mode).map((x) =>
            NB.api.delete('/mcp/servers/' + encodeURIComponent(s.name) + '/tools/' + encodeURIComponent(x.name))
          )).then(() => {
            (s.tools || []).forEach((x) => { x.mode = null; });
            U.toast(I.t('已清除 ') + s.name + I.t(' 的工具级覆盖'));
            syncList();
          }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
          return;
        }

        const edit = t.closest('[data-server-edit]');
        if (edit) {
          if (granted) openEditor(edit.dataset.serverEdit);
          return;
        }

        const del = t.closest('[data-server-del]');
        if (del) {
          if (!granted) return;
          const nm = del.dataset.serverDel;
          U.confirm({
            kicker: 'DELETE', title: I.t('删除 MCP server'),
            message: I.t('删除 ') + nm + I.t(' 后，它的工具会从模型上下文中移除，相关授权记录一并清除。'),
            okLabel: I.t('删除'), danger: true,
          }).then((ok) => {
            if (!ok) return;
            NB.api.delete('/mcp/servers/' + encodeURIComponent(nm)).then(() => {
              if (mock.ui.mcpOpen === nm) mock.ui.mcpOpen = null;
              U.toast(I.t('server 已删除'));
              loadServers(true);
            }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
          });
        }
      };

      cardChangeListener = (e) => {
        const sel = e.target && e.target.closest && e.target.closest('[data-tool]');
        if (!sel || !granted) return;
        const [nm, tn] = sel.dataset.tool.split('|');
        const next = sel.value;
        const base = '/mcp/servers/' + encodeURIComponent(nm) + '/tools/' + encodeURIComponent(tn);
        const request = next === 'inherit' ? NB.api.delete(base) : NB.api.put(base, { mode: next });
        request.then(() => {
          const s = findServer(nm);
          const tool = s && (s.tools || []).find((x) => x.name === tn);
          if (tool) tool.mode = next === 'inherit' ? null : next;
          U.toast(tn + ' → ' + (next === 'inherit' ? I.t('继承') : next));
          syncList();
        }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
      };

      root.addEventListener('click', cardClickListener);
      root.addEventListener('change', cardChangeListener);
    },

    unmount() {
      if (pageRoot && cardClickListener) pageRoot.removeEventListener('click', cardClickListener);
      if (pageRoot && cardChangeListener) pageRoot.removeEventListener('change', cardChangeListener);
      pageRoot = null;
      cardClickListener = null;
      cardChangeListener = null;
    },
  };
})();
