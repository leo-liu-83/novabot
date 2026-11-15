/* 概览 */
(function () {
  const NB = window.NB;
  const U = NB.ui;
  const I = NB.i18n;
  NB.pages = NB.pages || {};

  function greeting() {
    const h = new Date().getHours();
    if (h < 6) return I.t('夜深了');
    if (h < 11) return I.t('早上好');
    if (h < 14) return I.t('中午好');
    if (h < 19) return I.t('下午好');
    return I.t('晚上好');
  }

  NB.pages.overview = {
    title: '概览', kicker: '工作台 / OVERVIEW', full: false,
    render() {
      const m = NB.mock;
      const me = m.me();
      const admin = m.isAdmin();
      const session = m.session(m.ui.chatSession) || m.sessions[0];
      const curLevel = session ? session.level : me.ceiling;
      // 配额是“当前账号每天”的真实状态，不是演示用的全局常量
      const quota = m.quota || me.quota || { enabled: false, limit: 0, used: 0, resetAt: null };
      const quotaOff = !quota.enabled;
      const quotaUnlimited = !quotaOff && quota.limit < 0;
      const quotaPct = quotaOff || quotaUnlimited ? 0 : U.pct(quota.used, quota.limit);
      const mcpServers = m.mcp.servers;
      // 与技能页统计同口径：系统层只读，不参与审查状态/结论/过期计数
      const reviewable = m.skills.filter((s) => s.layer !== 'system');
      const released = reviewable.filter((s) => s.status === 'released').length;
      const pending = reviewable.filter((s) => s.status === 'undecided').length;
      const dangerous = reviewable.filter((s) => s.verdict === 'dangerous').length;
      const stale = reviewable.filter((s) => s.stale).length;

      const recent = m.sessions.slice().sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt)).slice(0, 4);
      const lastActive = recent.length ? recent[0].updatedAt : null;
      // 最近审查的三条；没有审查时间的排后面
      const skillTime = (s) => {
        const value = s.updatedAt || s.scannedAt;
        const time = value ? Date.parse(value) : 0;
        return Number.isFinite(time) ? time : 0;
      };
      const latestSkills = m.skills.slice()
        .sort((a, b) => skillTime(b) - skillTime(a))
        .slice(0, 3);

      const usage = m.usage || null;
      const series = usage && Array.isArray(usage.series) ? usage.series : [];
      const maxTotal = series.reduce((max, day) => Math.max(max, day.total || 0), 0);
      const topModel = usage && usage.models && usage.models[0] ? usage.models[0] : null;
      const knownModel = topModel ? (m.models || []).find((item) => item.id === topModel.model) : null;
      const topName = knownModel ? knownModel.name : (topModel ? topModel.model || '—' : '');
      const usageTotal = usage && usage.totals ? usage.totals.total || 0 : 0;
      const topShare = topModel && usageTotal
        ? Math.max(0, Math.min(100, Math.round(topModel.total * 100 / usageTotal)))
        : 0;

      return '' +
      '<div class="overview">' +
        '<section class="hero">' +
          '<div class="hero-main">' +
            (lastActive ? '<div class="hero-eyebrow">' +
              I.t('<span class="mono-xs">最近活动 ') + U.fmtTime(lastActive) + '</span></div>' : '') +
            '<h2>' + greeting() + I.t('，') + U.esc(me.name) + I.t('。<br>今天要让 NovaBot <em>做点什么</em>？</h2>') +
            I.t('<p>所有执行都发生在这台机器上。当前身份是 <strong>') + U.esc(U.roleName(me.role)) + I.t('</strong>，执行天花板 <strong>') + U.esc(me.ceiling) + I.t('</strong>。') +
              (admin ? I.t('你可以分配账号、调整配额与全局 provider。') : I.t('需要更高权限时，请联系管理员调整天花板。')) + '</p>' +
            '<div class="hero-actions">' +
              '<button type="button" class="btn signal" data-action="new-chat">' + U.icon('plus') + I.t('新会话</button>') +
              '<a class="btn ghost" href="#/skills">' + U.icon('shield') + I.t('审查技能</a>') +
              (admin ? '<a class="btn ghost" href="#/admin/users">' + U.icon('users') + I.t('用户与授权</a>') : '') +
            '</div>' +
          '</div>' +
          '<div class="hero-side">' +
            '<div class="hero-clock">' + U.esc(new Date().toLocaleDateString(NB.i18n.dateLocale(), { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' })) +
              '<b>' + new Date().toLocaleTimeString(NB.i18n.dateLocale(), { hour: '2-digit', minute: '2-digit' }) + '</b></div>' +
            '<div class="hero-signal">' +
              I.t('<div><div class="stat-label">AGENT STATUS</div><div class="hero-status"><b>● 就绪</b>等待指令</div></div>') +
              '<span class="pulse-bars" aria-hidden="true"><i></i><i></i><i></i><i></i></span>' +
            '</div>' +
          '</div>' +
        '</section>' +

        '<div class="grid">' +
          /* 身份 */
          '<section class="panel col-5">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('user') + I.t('当前身份</div><span class="kicker">IDENTITY</span></div>') +
            '<div class="panel-body"><div class="identity-card">' +
              '<div class="identity-card-top">' +
                '<span class="avatar ' + (admin ? 'acid' : '') + '">' + U.avatarInner(me.name, me.avatar) + '</span>' +
                '<div class="who"><b>' + U.esc(me.name) + '</b><span>' + U.esc(I.t(me.desc || '')) + '</span></div>' +
              '</div>' +
              '<div class="identity-tags">' +
                U.badge(U.roleName(me.role), admin ? 'ink' : 'neutral') +
                (me.mcpGranted ? U.badge(I.t('MCP 已授权'), 'ok') : U.badge(I.t('MCP 未授权'), 'bad')) +
              '</div>' +
              '<dl class="kv">' +
                '<dt>UID</dt><dd>' + U.esc(me.uid || '—') + '</dd>' +
                (me.createdAt ? I.t('<dt>加入时间</dt><dd>') + U.fmtDate(me.createdAt) + '</dd>' : '') +
                I.t('<dt>执行天花板</dt><dd>') + U.esc(me.ceiling || 'T0') + '</dd>' +
              '</dl>' +
            '</div></div>' +
          '</section>' +

          /* 执行档位 */
          '<section class="panel col-3 level-panel">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('terminal') + I.t('执行档位</div>') +
              U.badge(curLevel, 'signal') + '</div>' +
            '<div class="panel-body"><div class="stack">' +
              I.t('<div><div class="stat-label">当前会话</div>') +
                '<div style="display:flex;align-items:baseline;gap:6px;margin-top:5px"><span class="stat-num">' + U.esc(curLevel) + '</span>' +
                I.t('<span class="mono-sm">/ 天花板 ') + U.esc(me.ceiling) + '</span></div></div>' +
              U.levelLadder(curLevel) +
              '<div class="mono-xs level-notes">' +
                I.t('T0 不执行命令 · 无风险') + '<br>' +
                I.t('T1 仅技能 .py 脚本 · 作者自定') + '<br>' +
                I.t('T2 工作区只读 · 不可修改') + '<br>' +
                I.t('T3 任意命令 · 逐条确认 · 可能误操作') + '<br>' +
                I.t('T4 任意命令 · 免确认 · 等同交出账号') +
              '</div>' +
            '</div></div>' +
          '</section>' +

          /* 配额 */
          '<section class="panel col-4">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('globe') + I.t('我的搜索配额') + '</div>' +
              U.badge(quotaOff ? I.t('未启用') : quotaUnlimited ? I.t('不限') : I.t('启用'),
                quotaOff || quotaUnlimited ? 'neutral' : 'ok') + '</div>' +
            '<div class="panel-body"><div class="stack">' +
              I.t('<div class="quota-line"><span class="mono-sm">本期已用</span><b>') + U.num(quota.used) + ' / ' +
                (quotaOff ? '—' : quotaUnlimited ? I.t('不限') : U.num(quota.limit)) + '</b></div>' +
              (quotaOff || quotaUnlimited ? '' : '<div class="meter"><span class="' + (quotaPct >= 90 ? 'bad' : quotaPct >= 70 ? 'warn' : '') + '" style="width:' + quotaPct + '%"></span></div>') +
              (quota.resetAt
                ? I.t('<div class="quota-reset"><span>重置 ') + U.fmtDateTime(quota.resetAt) + '</span><span>' + (quotaOff || quotaUnlimited ? '' : quotaPct + '%') + '</span></div>'
                : '') +
              I.t('<div class="mono-xs">自定义端点不计入搜索配额。</div>') +
            '</div></div>' +
          '</section>' +

          /* MCP */
          '<section class="panel col-7">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('plug') + I.t('MCP 连接</div>') +
              I.t('<a class="mono-xs" href="#/mcp">管理 →</a></div>') +
            '<div class="panel-body">' +
              (me.mcpGranted
                ? '<div class="stack">' + mcpServers.map((s) =>
                    '<div class="server-mini"><span class="dot ' + (s.enabled ? '' : 'off') + '"></span>' +
                    '<span class="name">' + U.esc(s.name) + '</span>' +
                    U.badge(s.mode, s.mode === 'deny' ? 'bad' : s.mode === 'allow' ? 'steel' : 'warn') +
                    '<span class="mode">' + (s.enabled ? s.tools.length + ' tools' : I.t('停用')) + '</span></div>'
                  ).join('') + '</div>'
                : '<div class="banner bad">' + U.icon('lock') + I.t('<div>当前账号没有 MCP 授权。管理员在「用户与授权」里打开后，才能添加自己的 server。</div></div>')) +
              I.t('<div class="mono-xs" style="margin-top:12px">用户层上限 ') + m.mcp.limit + I.t(' 个 · 系统层由管理员统一配置</div>') +
            '</div>' +
          '</section>' +

          /* 技能 */
          '<section class="panel col-5">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('shield') + I.t('技能审查</div>') +
              I.t('<a class="mono-xs" href="#/skills">全部 →</a></div>') +
            '<div class="panel-body">' +
              '<div class="skill-counts" style="margin-bottom:12px">' +
                I.t('<span class="skill-count">已放行 <b>') + released + '</b></span>' +
                I.t('<span class="skill-count warn">待审查 <b>') + pending + '</b></span>' +
                I.t('<span class="skill-count bad">高危 <b>') + dangerous + '</b></span>' +
              '</div>' +
              latestSkills.map((s) =>
                '<div class="skill-mini">' +
                  '<span class="dot" style="width:6px;height:6px;border-radius:50%;background:' + (s.verdict === 'dangerous' ? 'var(--bad)' : 'var(--ok)') + '"></span>' +
                  '<span class="s-name">' + U.esc(s.name) + '</span>' +
                  '<span class="s-layer">' + U.esc(s.layer === 'system' ? I.t('系统') : s.layer === 'shared' ? I.t('共享') : I.t('私有')) + '</span>' +
                  U.badge(
                    s.layer === 'system' || s.status === 'released' ? I.t('已放行') : s.status === 'rejected' ? I.t('已拒绝') : I.t('待定'),
                    s.layer === 'system' || s.status === 'released' ? 'ok' : s.status === 'rejected' ? 'bad' : 'warn'
                  ) +
                '</div>'
              ).join('') +
              (stale ? '<div class="mono-xs" style="margin-top:10px;color:var(--warn)">' + stale + I.t(' 个技能内容已变化，建议重新扫描。</div>') : '') +
            '</div>' +
          '</section>' +

          /* 最近会话 */
          '<section class="panel col-8">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('chat') + I.t('最近会话</div>') +
              I.t('<a class="mono-xs" href="#/chat">进入会话 →</a></div>') +
            '<div class="session-list">' +
              (recent.length ? recent.map((s) =>
                '<a class="session-row" href="#/chat" data-session="' + U.esc(s.id) + '">' +
                  '<div class="s-main"><div class="s-title">' + U.esc(s.title) + '</div>' +
                    '<div class="s-meta">' + U.esc(m.model(s.model).name) + ' · ' + U.esc(s.level) + ' · ' +
                      // 这是会话全历史累计，与右边「近 7 天」的用量卡不是同一口径；
                      // 写明"全历史"才不会被当成同一段时间的合计
                      I.t('全历史 ') + U.num((s.tokensIn || 0) + (s.tokensOut || 0)) + ' tokens</div></div>' +
                  '<span class="s-time">' + U.fmtTime(s.updatedAt) + '</span>' +
                  U.icon('chevron', 'sm') +
                '</a>'
              ).join('') : U.empty('chat', I.t('还没有会话'), I.t('在会话页提出第一个问题。'))) +
            '</div>' +
          '</section>' +

          /* 用量 */
          '<section class="panel col-4">' +
            '<div class="panel-head"><div class="panel-title">' + U.icon('pulse') + I.t('用量趋势</div>') +
              U.badge(I.t('近 7 天'), 'neutral') + '</div>' +
            '<div class="panel-body"><div class="stack">' +
              (series.length
                ? '<div class="usage-chart">' + series.map((day) => {
                    const bar = maxTotal ? Math.max(2, Math.round((day.total || 0) * 88 / maxTotal)) : 2;
                    const outShare = day.total
                      ? Math.max(0, Math.min(100, Math.round((day.output || 0) * 100 / day.total)))
                      : 0;
                    const tip = day.date + ' · ' + I.t('输入') + ' ' + U.num(day.input) +
                      ' · ' + I.t('输出') + ' ' + U.num(day.output);
                    return '<div class="usage-col" title="' + U.esc(tip) + '">' +
                      '<div class="usage-plot"><div class="usage-bar" style="height:' + bar + 'px">' +
                        '<span class="usage-out" style="height:' + outShare + '%"></span>' +
                        '<span class="usage-in" style="height:' + (100 - outShare) + '%"></span>' +
                      '</div></div>' +
                      '<span class="usage-day">' + U.esc(String(day.date).slice(5).replace('-', '/')) + '</span>' +
                    '</div>';
                  }).join('') + '</div>'
                : '') +
              '<div class="usage-legend"><span><i class="in"></i>' + I.t('输入') + '</span>' +
                '<span><i class="out"></i>' + I.t('输出') + '</span></div>' +
              (topModel
                ? '<div class="usage-model"><span class="stat-label">' + I.t('最常用模型') + '</span>' +
                  '<b>' + U.esc(topName) + '</b><span class="mono-xs">' + topShare + '%</span></div>'
                : '<div class="mono-xs">' + I.t('暂无用量记录') + '</div>') +
            '</div></div>' +
          '</section>' +
        '</div>' +
      '</div>';
    },
    refresh() {
      /* 切回概览时重拉账号级数据；失败也重绘，保持旧值而不是卡住 */
      Promise.allSettled([
        NB.app.loadUsage(),
        NB.app.loadQuota(),
        NB.app.reloadSessions(),
      ]).then(() => {
        if (NB.app.route === 'overview') NB.app.render();
      });
    },
    mount(root) {
      root.querySelector('[data-action="new-chat"]').onclick = () => {
        NB.pages.chat.create().then(() => { location.hash = '#/chat'; })
          .catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
      };
      root.querySelectorAll('[data-session]').forEach((el) => {
        el.addEventListener('click', () => {
          if (NB.pages.chat.discardDraft) NB.pages.chat.discardDraft();
          NB.mock.ui.chatSession = el.dataset.session;
        });
      });
    },
  };
})();
