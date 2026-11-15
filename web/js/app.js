/* 路由、外壳、导航与顶栏 */
(function () {
  const NB = window.NB;
  const U = NB.ui;
  const mock = NB.mock;
  const I = NB.i18n;

  const NAV = [
    {
      section: 'nav.workbench',
      items: [
        { route: 'chat', label: 'nav.chat', icon: 'chat', href: '#/chat', roles: ['owner', 'admin', 'user'] },
        { route: 'overview', label: 'nav.overview', icon: 'grid', href: '#/overview', roles: ['owner', 'admin', 'user'] },
        { route: 'projects', label: 'nav.projects', icon: 'layers', href: '#/projects', roles: ['owner', 'admin', 'user'] },
        { route: 'persona', label: 'nav.persona', icon: 'brain', href: '#/persona', roles: ['owner', 'admin', 'user'], soul: true },
      ],
    },
    {
      section: 'nav.settings',
      items: [
        { route: 'web', label: 'nav.web', icon: 'globe', href: '#/web', roles: ['owner', 'admin', 'user'] },
        { route: 'mcp', label: 'nav.mcp', icon: 'plug', href: '#/mcp', roles: ['owner', 'admin', 'user'] },
        { route: 'skills', label: 'nav.skills', icon: 'shield', href: '#/skills', roles: ['owner', 'admin', 'user'] },
        { route: 'account', label: 'nav.account', icon: 'user', href: '#/account', roles: ['admin', 'user'] },
      ],
    },
    {
      section: 'nav.admin',
      items: [
        // 单用户没有账号体系：用户管理与全局 provider 只对多用户 admin 有意义
        { route: 'admin-users', label: 'nav.users', icon: 'users', href: '#/admin/users', roles: ['admin'] },
        { route: 'admin-web', label: 'nav.globalSearch', icon: 'globe', href: '#/admin/web', roles: ['admin'] },
        { route: 'admin-reports', label: 'nav.reports', icon: 'pulse', href: '#/admin/reports', roles: ['owner', 'admin'] },
        { route: 'admin-system', label: 'nav.system', icon: 'cpu', href: '#/admin/system', roles: ['owner', 'admin'] },
      ],
    },
  ];

  const AUTH_ROUTES = { login: '#/login', setup: '#/setup' };

  function role() {
    return mock.me().role;
  }

  function allowed(route, r) {
    if (route === 'login') return r === 'anon';
    if (route === 'setup') return r === 'setup';
    if (route === 'persona') return !!mock.me().hasSoul;
    if (route === 'projects') return !!mock.me().canProjects;
    for (const sec of NAV) {
      for (const it of sec.items) {
        if (it.route === route) return it.roles.includes(r);
      }
    }
    return false;
  }

  function hashFor(route) {
    if (route === 'admin-users') return '#/admin/users';
    if (route === 'admin-web') return '#/admin/web';
    if (route === 'admin-reports') return '#/admin/reports';
    if (route === 'admin-system') return '#/admin/system';
    if (route === 'login') return '#/login';
    if (route === 'setup') return '#/setup';
    return '#/' + route;
  }

  function parseHash() {
    const raw = (location.hash || '').replace(/^#\/?/, '').replace(/\/+$/, '');
    if (!raw) return null;
    if (raw === 'admin/users') return 'admin-users';
    if (raw === 'admin/web') return 'admin-web';
    if (raw === 'admin/reports') return 'admin-reports';
    if (raw === 'admin/system') return 'admin-system';
    return raw;
  }

  function go(route, replace) {
    const h = hashFor(route);
    if (location.hash === h) { render(); return; }
    if (replace) history.replaceState(null, '', h);
    else location.hash = h;
    if (replace) render();
  }

  /* ── 导航 ─────────────────────────────────────────────────────────── */
  function navHTML(r) {
    /* 导航数字口径：
       - MCP：当前账号配置的用户层 server 总数
       - 技能：当前身份可见的技能总数（待审查数在技能页里单独展示）
       - 用户与授权：管理员可见的账号总数
       对应页面还没加载时先用启动阶段预取的计数，避免刷新后数字变 0。 */
    const cached = mock.navCounts || {};
    const canProjects = !!mock.me().canProjects;
    const counts = {
      projects: mock.ui.projectsLoaded ? (mock.ui.projects || []).length : (cached.projects || 0),
      skills: mock.ui.skillsLoaded ? (mock.skills || []).length : (cached.skills || 0),
      mcp: mock.ui.mcpLoaded
        ? ((mock.mcp && mock.mcp.servers) || []).length
        : (cached.mcp || 0),
      'admin-users': mock.ui.adminLoaded ? (mock.users || []).length : (cached.users || 0),
    };
    return NAV.map((sec) => {
      const items = sec.items.filter((it) => it.roles.includes(r)
        && (!it.soul || !!mock.me().hasSoul)
        && (it.route !== 'projects' || canProjects));
      if (!items.length) return '';
      return '<div class="nav-section"><span>' + U.esc(I.t(sec.section)) + '</span></div>' +
        items.map((it) => {
          const active = NB.app.route === it.route;
          const count = counts[it.route];
          const label = I.t(it.label);
          /* 收起后文字被隐藏，提示只保留名称；数字只在展开态显示，不做红点 */
          const tip = label;
          return '<a class="nav-item" href="' + it.href + '" data-tip="' + U.esc(tip) + '"' +
            ' aria-label="' + U.esc(tip) + '"' +
            (active ? ' aria-current="page"' : '') + '>' +
            U.icon(it.icon) + '<span class="nav-label">' + U.esc(label) + '</span>' +
            (count ? '<span class="nav-count">' + count + '</span>' : '') +
          '</a>';
        }).join('');
    }).join('');
  }

  function bindNav() {
    document.querySelectorAll('.nav-item').forEach((a) => {
      a.addEventListener('click', () => closeRail());
    });
  }

  function closeRail() {
    const rail = document.getElementById('rail');
    const scrim = document.getElementById('scrim');
    if (rail) rail.classList.remove('open');
    if (scrim) scrim.hidden = true;
  }

  /* ── 侧栏收缩 ─────────────────────────────────────────────────────── */
  let railTip = null;

  function hideRailTip() {
    if (railTip) { railTip.remove(); railTip = null; }
  }

  function showRailTip(target) {
    hideRailTip();
    if (!NB.rail || !NB.rail.collapsed || window.innerWidth <= 860) return;
    const text = target.getAttribute('data-tip');
    if (!text) return;
    const el = document.createElement('div');
    el.className = 'rail-tip';
    el.setAttribute('role', 'tooltip');
    el.textContent = text;
    document.body.appendChild(el);
    const box = target.getBoundingClientRect();
    const size = el.getBoundingClientRect();
    let left = box.right + 12;
    if (left + size.width > window.innerWidth - 8) left = Math.max(8, box.left - size.width - 12);
    let top = box.top + box.height / 2 - size.height / 2;
    top = Math.max(8, Math.min(window.innerHeight - size.height - 8, top));
    el.style.left = left + 'px';
    el.style.top = top + 'px';
    railTip = el;
  }

  function bindRailTips() {
    /* 导航重绘会换掉元素，旧的浮层留不住；先清掉再绑 */
    hideRailTip();
    document.querySelectorAll('.rail [data-tip]').forEach((el) => {
      el.onmouseenter = () => showRailTip(el);
      el.onmouseleave = hideRailTip;
      el.onfocus = () => showRailTip(el);
      el.onblur = hideRailTip;
    });
  }

  /* 按钮的文案与 aria 要跟着状态、语言一起变 */
  function syncRailChrome() {
    const btn = document.getElementById('rail-collapse');
    if (!btn || !NB.rail) return;
    const collapsed = NB.rail.collapsed;
    const label = I.t(collapsed ? '展开侧栏' : '收起侧栏');
    btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    btn.setAttribute('aria-label', label);
    btn.setAttribute('data-tip', label);
    /* 收起态用自定义浮层（能跟主题），别再叠一个浏览器原生 title */
    if (collapsed) btn.removeAttribute('title');
    else btn.title = label;
    hideRailTip();
  }

  /* ── 顶栏 ─────────────────────────────────────────────────────────── */
  function topbarHTML() {
    const r = mock.me().role;
    if (r === 'anon' || r === 'setup') return '';
    return NB.theme.chipHTML() +
      '<button type="button" class="chip locale-chip" id="locale-toggle" title="' + U.esc(I.t('common.language')) + '">' +
        (I.locale() === 'en' ? '中文' : 'EN') + '</button>';
  }

  function bindTopbar() {
    const r = mock.me().role;
    if (r === 'anon' || r === 'setup') return;
    const localeBtn = document.getElementById('locale-toggle');
    if (localeBtn) localeBtn.onclick = () => {
      I.setLocale(I.locale() === 'en' ? 'zh' : 'en');
      render();
    };
    NB.theme.bindChip(document.querySelector('.topbar-tools [data-theme-toggle]'));
  }

  /* ── 身份切换 ─────────────────────────────────────────────────────── */
  function identityHTML(me) {
    const r = me.role;
    const name = r === 'anon' ? I.t('auth.login') : r === 'setup' ? I.t('首次启动') : me.name;
    const desc = r === 'anon' ? I.t('auth.login') : r === 'setup' ? I.t('还没有管理员') : U.roleName(r) + ' · ' + U.modeLabel(me.mode);
    return '<span class="identity-avatar">' + U.avatarInner(name, me.avatar) + '</span>' +
      '<span class="identity-meta"><span class="identity-name">' + U.esc(name) + '</span>' +
        '<span class="identity-role">' + U.esc(desc) + '</span></span>' +
      '<span class="identity-caret">▾</span>';
  }

  /* 收起态只露头像，悬停提示补上被藏起来的身份信息 */
  function syncIdentityTip() {
    const btn = document.getElementById('identity');
    if (!btn) return;
    const name = (btn.querySelector('.identity-name') || {}).textContent || '';
    const desc = (btn.querySelector('.identity-role') || {}).textContent || '';
    const tip = [name, desc].filter(Boolean).join(' · ');
    if (tip) btn.setAttribute('data-tip', tip);
    else btn.removeAttribute('data-tip');
  }

  function bindIdentity() {
    const btn = document.getElementById('identity');
    if (!btn) return;
    btn.onclick = () => {
      if (NB.app.user && NB.app.user.role !== 'anon' && NB.app.user.role !== 'setup') {
        U.menu(btn, [
          { label: I.t('auth.logout'), onPick: async () => {
            try { await NB.api.logout(); } catch (e) { /* 已过期的 token 也应本地退出 */ }
            NB.api.token.set('');
            await bootstrap();
          } },
        ], { head: U.esc(NB.app.user.username || I.t('nav.account')), align: 'left' });
        return;
      }
      U.menu(btn, [
        { label: I.t('auth.login'), hint: 'API', onPick: () => { location.hash = '#/login'; } },
      ], { head: I.t('nav.account'), align: 'left' });
    };
  }

  async function reloadSessions() {
    if (!NB.app.user || NB.app.user.role === 'anon' || NB.app.user.role === 'setup') {
      mock.sessions = [];
      mock.ui.chatSession = null;
      return;
    }
    const rows = await NB.api.sessions(200);
    const previous = new Map(mock.sessions.map((s) => [s.id, s]));
    mock.sessions = rows.map((row) => {
      const existing = previous.get(row.id);
      return Object.assign(existing || { messages: [], uploads: [], _loaded: false, _hasMore: false, _beforeId: null }, {
        id: row.id, title: row.title || I.t('chat.new'), model: row.model || null,
        thinking: row.thinking == null ? null : row.thinking,
        projectId: row.project_id || null, projectName: row.project_name || null,
        projectPath: row.project_path || null,
        level: row.exec_level || (existing && existing.level) || mock.ui.defaultLevel,
        updatedAt: new Date((row.updated_at || 0) * 1000).toISOString(),
        tokensIn: row.tokens_in || 0, tokensOut: row.tokens_out || 0,
        speed: row.speed || 0,
        contextTokens: row.context_tokens || 0, contextLimit: row.context_limit || 0,
      });
    });
    if (!mock.sessions.some((s) => s.id === mock.ui.chatSession)) {
      mock.ui.chatSession = mock.sessions[0] ? mock.sessions[0].id : null;
    }
  }

  async function loadModels() {
    if (!NB.app.user || NB.app.user.role === 'anon' || NB.app.user.role === 'setup') return;
    const result = await NB.api.models();
    const models = (result.providers || []).flatMap((p) => (p.models || []).map((m) => ({
      id: m.id, name: m.name, provider: p.name || p.code, providerCode: p.code,
      thinking: m.enable_thinking, ctx: m.num_ctx, configured: m.configured,
    })));
    if (models.length) mock.models = models;
    NB.app.defaultModel = result.default_model || (models[0] && models[0].id) || '';
    mock.ui.defaultModel = NB.app.defaultModel;
    const defaultModel = models.find((m) => m.id === NB.app.defaultModel);
    if (defaultModel) mock.ui.defaultThinking = !!defaultModel.thinking;
  }

  async function loadQuota() {
    if (!NB.app.user || NB.app.user.role === 'anon' || NB.app.user.role === 'setup') {
      mock.quota = null;
      return;
    }
    const quota = await NB.api.get('/web/quota');
    mock.quota = {
      enabled: !!quota.enabled,
      limit: quota.daily_limit,
      used: quota.used,
      resetAt: quota.reset_at ? new Date(quota.reset_at * 1000).toISOString() : null,
    };
  }

  async function loadUsage() {
    if (!NB.app.user || NB.app.user.role === 'anon' || NB.app.user.role === 'setup') {
      mock.usage = null;
      return;
    }
    try {
      mock.usage = await NB.api.get('/sessions/usage?days=7');
    } catch (error) {
      mock.usage = null;
    }
  }

  async function createSession(model, thinking, projectId) {
    const selectedThinking = thinking == null ? mock.ui.defaultThinking : thinking;
    const created = await NB.api.createSession(model || NB.app.defaultModel || undefined, selectedThinking, projectId);
    const session = {
      id: created.id, title: created.title || I.t('chat.new'),
      model: created.model || model || null,
      projectId: created.project_id || projectId || null,
      projectName: created.project_name || null,
      projectPath: created.project_path || null,
      thinking: created.thinking == null ? selectedThinking : created.thinking,
      level: created.exec_level || mock.ui.defaultLevel, updatedAt: new Date((created.updated_at || 0) * 1000).toISOString(),
      tokensIn: created.tokens_in || 0, tokensOut: created.tokens_out || 0,
      speed: created.speed || 0,
      contextTokens: created.context_tokens || 0, contextLimit: created.context_limit || 0,
      messages: [], uploads: [], _loaded: true, _hasMore: false, _beforeId: null,
    };
    mock.sessions.unshift(session);
    mock.ui.chatSession = session.id;
    return session;
  }

  function applyUser(user, authStatus) {
    // 身份变化后旧草稿不属于新身份；没有会话时下次进入会话页会重新生成空草稿
    if (NB.pages && NB.pages.chat && NB.pages.chat.discardDraft) NB.pages.chat.discardDraft();
    NB.app.user = user || null;
    NB.app.authStatus = authStatus || { multi_user: false, setup_required: false };
    NB.app.attachmentLimits = (user && user.attachment_limits) || {
      max_per_message: 5,
      max_file_bytes: 10 * 1024 * 1024,
    };
    const key = (!authStatus || !authStatus.multi_user || !!authStatus.setup_required)
      ? 'setup' : (user ? user.role : 'anon');
    mock.identity = ['owner', 'admin', 'user', 'anon', 'setup'].includes(key) ? key : 'anon';
    mock.mode = authStatus && authStatus.multi_user ? 'multi' : 'single';
    mock.hasAdmin = !(authStatus && authStatus.setup_required);
    mock.activeUserId = user ? user.uid : null;
    // 后端接管后不再展示演示账号；管理员页会再拉一次完整列表。
    mock.users = [];
    mock.ui.adminLoaded = false;
    mock.ui.adminLoading = false;
    mock.ui.providerLoaded = {};
    mock.ui.quotaLoaded = false;
    mock.quota = null;
    mock.usage = null;
    mock.ui.mcpLoaded = false;
    mock.ui.mcpLoading = false;
    mock.ui.skillsLoaded = false;
    mock.ui.systemLoaded = false;
    mock.skills = [];
    mock.providers = [];
    mock.globalProviders = [];
    mock.accountCounts = {};
    mock.accountStats = null;
    mock.navCounts = { mcp: 0, skills: 0, users: 0, projects: 0 };
    mock.ui.projects = [];
    mock.ui.projectsLoaded = false;
    mock.ui.projectId = null;
    mock.persona = null;
    mock.personaLoading = false;
    if (mock.mcp) mock.mcp.servers = [];
    if (user) {
      const levelName = (value, fallback) => {
        const names = { OFF: 'T0', SKILL_ONLY: 'T1', READ_ONLY: 'T2', CONFIRM_ALL: 'T3', ALLOW_ALL: 'T4' };
        return names[value] || (String(value || '').startsWith('T') ? value : fallback);
      };
      const item = {
        uid: user.uid, username: user.username || '',
        isAdmin: !!user.is_admin || user.role === 'admin' || user.role === 'owner',
        // 加入时间只用后端给的真实注册时间；没有账号行（单用户机主）时留空，
        // 由页面隐藏该行，不要拿“现在”冒充。
        createdAt: user.created_at ? new Date(user.created_at * 1000).toISOString() : null,
        // 后端没有 last_seen 字段，同样不编造“刚才”。
        lastSeen: null,
        ceiling: levelName(user.ceiling, 'T1'),
        defaultLevel: levelName(user.default_level, user.role === 'admin' ? 'T3' : 'T0'),
        // 是否授权 MCP 由 admin 按账号控制，用 /auth/me 的真实值；
        // 写死 false 会让概览/账号页在刷新后误报“未授权”。
        avatar: user.avatar || '',
        agentAvatar: user.agent_avatar || '',
        hasSoul: !!user.has_soul,
        quota: user.web_quota ? {
          enabled: !!user.web_quota.enabled,
          limit: user.web_quota.daily_limit,
          used: user.web_quota.used,
          resetAt: user.web_quota.reset_at ? new Date(user.web_quota.reset_at * 1000).toISOString() : null,
        } : { enabled: false, limit: 0, used: 0, resetAt: null },
        mcpGranted: !!user.mcp_granted,
        canProjects: !!user.can_projects,
      };
      const index = mock.users.findIndex((x) => x.uid === item.uid);
      if (index >= 0) mock.users[index] = item;
      else mock.users.unshift(item);
      mock.ui.defaultLevel = item.defaultLevel;
    }
  }

  /* 侧栏数字的启动预取：对应页面是懒加载的，不预取的话刷新后 MCP/技能
     没有数字、用户与授权只剩当前账号一个。这里只取计数，不占用页面缓存。 */
  async function loadNavCounts() {
    const counts = mock.navCounts || (mock.navCounts = {});
    const me = mock.me();
    if (!NB.app.user || me.role === 'anon' || me.role === 'setup') return;

    const tasks = [
      NB.api.get('/mcp/servers').then((data) => {
        const rows = data.servers || [];
        counts.mcp = rows.length;
        // 概览页直接用 mcp.servers；不预填的话刷新后列表是空的，
        // 必须进一次 MCP 页才会出现。页面未加载时先用同一份映射预热。
        if (!mock.ui.mcpLoaded && NB.pages.mcp && NB.pages.mcp.mapServer) {
          mock.mcp = mock.mcp || { servers: [], limit: 3, granted: false };
          if (data.limit != null) mock.mcp.limit = data.limit;
          if (data.granted != null) mock.mcp.granted = !!data.granted;
          mock.mcp.servers = rows.map(NB.pages.mcp.mapServer);
        }
      }),
      NB.api.get('/skills').then((data) => {
        const rows = data.skills || [];
        counts.skills = rows.length;
        // 概览页的技能统计和最近技能都读 mock.skills；同理预热，避免刷新后全是 0。
        if (!mock.ui.skillsLoaded && NB.pages.skills && NB.pages.skills.mapRow) {
          mock.skills = rows.map(NB.pages.skills.mapRow);
        }
      }),
    ];
    if (me.canProjects) {
      tasks.push(NB.api.projects().then((data) => {
        const rows = data || [];
        counts.projects = rows.length;
        // 项目页是懒加载的，先预热一份映射后的行，刷新后导航数字与页面都不会空
        if (!mock.ui.projectsLoaded && NB.pages.projects) {
          mock.ui.projects = rows.map(NB.pages.projects.mapProject);
        }
      }));
    }
    if (me.isAdmin || me.role === 'admin' || me.role === 'owner') {
      tasks.push(NB.api.adminUsers().then((data) => { counts.users = (data.users || []).length; }));
    }

    await Promise.all(tasks.map((task) => task.catch(() => {})));
  }

  async function bootstrap() {
    try {
      const status = await NB.api.authStatus();
      let user = null;
      if (status.multi_user && !status.setup_required && NB.api.token.get()) {
        try { user = await NB.api.me(); }
        catch (e) { NB.api.token.set(''); }
      }
      applyUser(user, status);
      try { await loadNavCounts(); } catch (error) { console.warn('Nav counts unavailable', error); }
      try { await loadModels(); } catch (error) { console.warn('Model list unavailable', error); }
      try { await loadQuota(); } catch (error) { console.warn('Quota unavailable', error); }
      try { await loadUsage(); } catch (error) { console.warn('Usage unavailable', error); }
      try { await reloadSessions(); } catch (error) { console.warn('Session list unavailable', error); mock.sessions = []; }
    } catch (error) {
      console.error('NovaBot bootstrap failed', error);
      applyUser(null, { multi_user: true, setup_required: false });
      mock.sessions = [];
    }
    render();
  }

  function switchIdentity(key) {
    const info = mock.identities[key];
    if (!info) return;
    if (NB.pages.chat && NB.pages.chat.discardDraft) NB.pages.chat.discardDraft();
    mock.identity = key;
    mock.mode = info.mode;
    mock.activeUserId = info.userId || null;
    mock.hasAdmin = key !== 'setup';
    mock.ui.chatFilter = '';
    if (key === 'setup') { go('setup', true); return; }
    if (key === 'anon') { go('login', true); return; }
    const cur = NB.app.route;
    if (!cur || cur === 'login' || cur === 'setup' || !allowed(cur, info.role)) go('chat', true);
    else render();
  }

  /* ── 渲染 ─────────────────────────────────────────────────────────── */
  let currentRoute = null;
  let mountedPage = null;

  function render() {
    const me = mock.me();
    const r = me.role;
    const parsed = parseHash();
    let route = parsed;

    if (r === 'setup') route = 'setup';
    else if (r === 'anon') {
      if (route !== 'login') route = 'login';
    } else {
      if (!route || route === 'login' || route === 'setup') route = 'chat';
      if (!allowed(route, r)) route = 'chat';
    }
    if (r === 'setup' && parsed !== 'setup') history.replaceState(null, '', '#/setup');
    if (r === 'anon' && parsed !== 'login') history.replaceState(null, '', '#/login');

    /* 如果 hash 与解析后的 route 不同，规范化，避免刷新后丢失 */
    if (parsed && parsed !== route && (r !== 'anon' && r !== 'setup')) {
      const h = hashFor(route);
      if (location.hash !== h) history.replaceState(null, '', h);
    }

    /* 管理员下调天花板后，已有会话不能高于新上限 */
    if (route === 'chat' && NB.pages.chat) {
      const sess = NB.pages.chat.current();
      if (sess) {
        const cap = parseInt(String(me.ceiling || 'T4').replace(/\D/g, ''), 10) || 0;
        const cur = parseInt(String(sess.level || 'T0').replace(/\D/g, ''), 10) || 0;
        if (cur > cap) sess.level = 'T' + cap;
      }
    }

    const page = NB.pages[route] || NB.pages.chat;
    const isAuth = route === 'login' || route === 'setup';
    const routeChanged = currentRoute !== route;
    const hadRoute = currentRoute !== null;
    NB.app.route = route;

    if (mountedPage && mountedPage.unmount) {
      try { mountedPage.unmount(); } catch (e) { console.warn(e); }
    }

    document.body.classList.toggle('is-auth', isAuth);
    const view = document.getElementById('view');
    view.className = 'view' + (!isAuth && page.full ? ' full' : '');
    view.innerHTML = page.render();

    /* 外壳 */
    if (!isAuth) {
      const nav = document.getElementById('nav');
      nav.innerHTML = navHTML(r);
      bindNav();
      const idBtn = document.getElementById('identity');
      idBtn.innerHTML = identityHTML(me);
      syncIdentityTip();
      bindIdentity();
      /* 先刷新按钮文案（会补 data-tip），再统一绑定提示 */
      syncRailChrome();
      bindRailTips();
      const brandSub = document.getElementById('brand-sub');
      brandSub.textContent = mock.mode === 'single' ? 'LOCAL CONSOLE · SINGLE' : 'CONTROL CONSOLE · MULTI';
      document.getElementById('topbar-tools').innerHTML = topbarHTML(me);
      bindTopbar(me);
      document.getElementById('rail').setAttribute('aria-label', I.t('主导航'));
      document.getElementById('rail-toggle').setAttribute('aria-label', I.t('打开导航'));
      document.getElementById('page-kicker').textContent = I.t(page.kicker || '');
      document.getElementById('page-title').textContent = I.t(page.title || '');
    } else {
      document.getElementById('topbar-tools').innerHTML = '';
    }

    document.title = (page.title ? I.t(page.title) + ' · ' : '') + 'NovaBot';

    if (page.mount) page.mount(view);
    mountedPage = page;

    if (routeChanged && route !== 'chat') window.scrollTo(0, 0);
    currentRoute = route;

    /* 首次渲染由 bootstrap 预热；之后切页时让页面刷新自己的后端数据 */
    if (routeChanged && hadRoute && page.refresh) {
      try { page.refresh(); } catch (e) { console.warn('Page refresh failed', e); }
    }

    if (window.innerWidth <= 860) closeRail();
  }

  /* ── 启动 ─────────────────────────────────────────────────────────── */
  function boot() {
    const railToggle = document.getElementById('rail-toggle');
    const rail = document.getElementById('rail');
    const scrim = document.getElementById('scrim');
    railToggle.onclick = () => {
      const open = !rail.classList.contains('open');
      rail.classList.toggle('open', open);
      scrim.hidden = !open;
    };
    scrim.onclick = () => closeRail();

    /* 桌面收缩：只改 <html data-rail>，宽度由 CSS 变量接力，页面不重绘 */
    const collapse = document.getElementById('rail-collapse');
    if (collapse) {
      collapse.onclick = () => {
        if (!NB.rail) return;
        NB.rail.toggle();
        syncRailChrome();
        hideRailTip();
      };
    }
    /* 窗口拉过断点时清掉可能残留的浮层 */
    window.addEventListener('resize', () => hideRailTip());
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideRailTip(); });

    window.addEventListener('hashchange', render);
    window.addEventListener('nb:locale', render);
    bootstrap();
  }

  function canProjects() {
    return !!mock.me().canProjects;
  }

  NB.app = { render, go, route: null, allowed, switchIdentity, bootstrap, reloadSessions,
    createSession, loadModels, loadQuota, loadUsage, canProjects, user: null, authStatus: null,
    defaultModel: '', attachmentLimits: null };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
