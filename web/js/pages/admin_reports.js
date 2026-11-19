/* 管理：统计报表（用户活跃、模型用量、token 消耗） */
(function () {
  const NB = window.NB;
  const U = NB.ui;
  const I = NB.i18n;
  NB.pages = NB.pages || {};

  let pageRoot = null;
  let clickHandler = null;
  let inputHandler = null;
  let requestSeq = 0;

  function data() { return NB.mock.report; }
  function ui() { return NB.mock.ui; }

  function modelInfo(id) {
    const known = (NB.mock.models || []).find((item) => item.id === id);
    if (known) return { name: known.name || id, provider: known.provider || '' };
    return { name: id || '—', provider: String(id || '').split('/')[0] || '' };
  }

  function fmtTs(ts) {
    if (!ts) return '—';
    return U.fmtDateTime(new Date(Number(ts) * 1000).toISOString());
  }

  function dateLabel(value) {
    const text = String(value || '');
    return text.length >= 10 ? text.slice(5).replace('-', '/') : text;
  }

  function pageHead() {
    return '<div class="page-head">' +
      '<div><div class="kicker">' + U.esc(I.t('管理 / REPORT')) + '</div>' +
        '<h2 class="page-title">' + U.esc(I.t('报表')) + '</h2>' +
        '<p class="page-desc">' + U.esc(I.t('用户活跃、模型、技能、MCP 与搜索使用、token 消耗统计。只展示聚合数据，不包含消息正文。')) + '</p>' +
      '</div>' +
    '</div>';
  }

  function rangeButtons() {
    const current = Number(ui().reportDays) || 30;
    return '<div class="report-seg" role="group" aria-label="' + U.esc(I.t('时间范围')) + '">' +
      [7, 30, 90].map((days) =>
        '<button type="button" data-report-days="' + days + '" aria-pressed="' + (current === days) + '">' +
          U.esc(I.t('近 {n} 天', { n: days })) + '</button>').join('') +
    '</div>';
  }

  function bucketButtons() {
    const current = ui().reportBucket === 'week' ? 'week' : 'day';
    return '<div class="report-seg" role="group" aria-label="' + U.esc(I.t('时间粒度')) + '">' +
      '<button type="button" data-report-bucket="day" aria-pressed="' + (current === 'day') + '">' +
        U.esc(I.t('按天')) + '</button>' +
      '<button type="button" data-report-bucket="week" aria-pressed="' + (current === 'week') + '">' +
        U.esc(I.t('按周')) + '</button>' +
    '</div>';
  }

  function toolbar() {
    const updated = ui().reportUpdatedAt
      ? I.t('更新于 {time}', { time: U.fmtTime(ui().reportUpdatedAt) })
      : I.t('尚未加载');
    return '<div class="report-toolbar">' +
      '<div class="row" style="gap:10px;flex-wrap:wrap">' + rangeButtons() + bucketButtons() + '</div>' +
      '<div class="row" style="gap:10px">' +
        '<span class="mono-xs">' + U.esc(updated) + '</span>' +
        '<button type="button" class="btn ghost sm" data-report-refresh>' +
          U.icon('refresh') + U.esc(I.t('刷新')) + '</button>' +
      '</div>' +
    '</div>';
  }

  function kpi(label, value, sub, tone) {
    return '<div class="report-kpi ' + (tone || '') + '">' +
      '<div class="stat-label">' + U.esc(I.t(label)) + '</div>' +
      '<div class="stat-num">' + value + '</div>' +
      (sub ? '<div class="report-kpi-sub">' + sub + '</div>' : '') +
    '</div>';
  }

  function kpis(summary) {
    return '<div class="report-kpis">' +
      kpi('活跃用户', U.num(summary.active_users),
        I.t('今日 {dau} · 近 7 天 {wau} · 近 30 天 {mau}', {
          dau: U.num(summary.dau), wau: U.num(summary.wau), mau: U.num(summary.mau),
        })) +
      kpi('新增用户', U.num(summary.new_users), I.t('按注册时间统计')) +
      kpi('轮次', U.num(summary.turns),
        I.t('平均 {n} 轮 / 活跃用户', {
          n: summary.active_users ? (summary.turns / summary.active_users).toFixed(1) : '0',
        })) +
      kpi('会话数', U.num(summary.sessions),
        I.t('平均 {n} 轮 / 会话', {
          n: summary.sessions ? (summary.turns / summary.sessions).toFixed(1) : '0',
        })) +
      kpi('Token 总量', U.num(summary.tokens_total),
        I.t('输入 {input} · 输出 {output}', {
          input: U.num(summary.tokens_in), output: U.num(summary.tokens_out),
        })) +
      kpi('模型调用', U.num(summary.calls),
        summary.backfill_calls
          ? I.t('其中 {n} 次为历史回填，调用次数为近似值', { n: U.num(summary.backfill_calls) })
          : I.t('按 usage 事件逐次统计')) +
    '</div>';
  }

  function barChart(rows, valueFn, tipFn) {
    const values = rows.map(valueFn);
    const max = Math.max(1, ...values);
    const step = rows.length > 24 ? Math.ceil(rows.length / 12) : 1;
    return '<div class="report-chart">' + rows.map((row, index) => {
      const value = valueFn(row);
      const height = value ? Math.max(2, Math.round(value * 116 / max)) : 0;
      const label = index % step === 0 ? dateLabel(row.date) : '';
      return '<div class="report-chart-col" title="' + U.esc(tipFn(row)) + '">' +
        '<div class="report-chart-plot">' +
          '<span class="report-chart-bar" style="height:' + height + 'px"></span>' +
        '</div>' +
        '<span class="report-chart-label">' + U.esc(label) + '</span>' +
      '</div>';
    }).join('') + '</div>';
  }

  function tokenChart(rows) {
    const max = Math.max(1, ...rows.map((row) => row.total || 0));
    const step = rows.length > 24 ? Math.ceil(rows.length / 12) : 1;
    return '<div class="report-chart">' + rows.map((row, index) => {
      const total = row.total || 0;
      const height = total ? Math.max(2, Math.round(total * 116 / max)) : 0;
      const outShare = total ? Math.max(0, Math.min(100, Math.round((row.output || 0) * 100 / total))) : 0;
      const label = index % step === 0 ? dateLabel(row.date) : '';
      return '<div class="report-chart-col" title="' + U.esc(
        row.date + ' · ' + I.t('输入') + ' ' + U.num(row.input) + ' · ' + I.t('输出') + ' ' + U.num(row.output)
      ) + '">' +
        '<div class="report-chart-plot">' +
          '<span class="report-chart-bar report-stack" style="height:' + height + 'px">' +
            '<i class="out" style="height:' + outShare + '%"></i>' +
            '<i class="in" style="height:' + (100 - outShare) + '%"></i>' +
          '</span>' +
        '</div>' +
        '<span class="report-chart-label">' + U.esc(label) + '</span>' +
      '</div>';
    }).join('') + '</div>';
  }

  function activityPanel(report) {
    const summary = report.summary;
    return '<section class="panel col-7">' +
      '<div class="panel-head"><div class="panel-title">' + U.icon('pulse') + I.t('用户活跃趋势') + '</div>' +
        '<span class="kicker">ACTIVITY</span></div>' +
      '<div class="panel-body">' +
        barChart(report.activity, (row) => row.active_users || 0, (row) =>
          row.date + ' · ' + I.t('活跃用户') + ' ' + U.num(row.active_users) + ' · ' + I.t('轮次') + ' ' + U.num(row.turns)) +
        '<div class="report-legend"><span><i class="active"></i>' + U.esc(I.t('活跃用户')) + '</span>' +
          '<span>' + U.esc(I.t('共 {n} 个活跃用户 · {turns} 轮', {
            n: U.num(summary.active_users), turns: U.num(summary.turns),
          })) + '</span></div>' +
      '</div>' +
    '</section>';
  }

  function modelsPanel(report) {
    const models = report.models || [];
    const max = models.length ? Math.max(1, models[0].total || 0) : 1;
    return '<section class="panel col-5">' +
      '<div class="panel-head"><div class="panel-title">' + U.icon('cpu') + I.t('模型用量') + '</div>' +
        '<span class="kicker">MODELS</span></div>' +
      '<div class="panel-body">' +
        (models.length
          ? '<div class="report-model-list">' + models.slice(0, 6).map((row) =>
              '<div class="report-model-row">' +
                '<div class="report-model-main"><b title="' + U.esc(modelInfo(row.model).name) + '">' +
                  U.esc(modelInfo(row.model).name) + '</b>' +
                  '<span class="mono-xs">' + U.esc(modelInfo(row.model).provider) + '</span></div>' +
                '<div class="report-model-track"><span style="width:' +
                  Math.max(1, Math.round((row.total || 0) * 100 / max)) + '%"></span></div>' +
                '<span class="report-model-share">' + U.esc(String(row.token_share || 0)) + '%</span>' +
              '</div>').join('') + '</div>'
          : U.empty('pulse', I.t('暂无模型用量'), I.t('完成一次模型调用后，这里会显示模型占比。'))) +
      '</div>' +
    '</section>';
  }

  function tokenPanel(report) {
    const s = report.summary;
    return '<section class="panel col-12">' +
      '<div class="panel-head"><div class="panel-title">' + U.icon('layers') + I.t('Token 消耗趋势') + '</div>' +
        '<span class="kicker">TOKENS</span></div>' +
      '<div class="panel-body">' +
        tokenChart(report.tokens) +
        '<div class="report-legend"><span><i class="in"></i>' + U.esc(I.t('输入')) + '</span>' +
          '<span><i class="out"></i>' + U.esc(I.t('输出')) + '</span>' +
          '<span>' + U.esc(I.t('平均 {n} tok / 轮', { n: U.num(s.avg_tokens_per_turn) })) + '</span></div>' +
      '</div>' +
    '</section>';
  }

  /* MCP / 网络搜索共用的排行条：名字 + 副行 + 占比条，与模型用量同一套样式 */
  function usageRankList(rows, nameFn, subFn) {
    const max = Math.max(1, ...rows.map((row) => row.calls || 0));
    return '<div class="report-model-list">' + rows.slice(0, 6).map((row) => {
      const name = nameFn(row);
      return '<div class="report-model-row">' +
        '<div class="report-model-main"><b title="' + U.esc(name) + '">' + U.esc(name) + '</b>' +
          '<span class="mono-xs">' + subFn(row) + '</span></div>' +
        '<div class="report-model-track"><span style="width:' +
          Math.max(1, Math.round((row.calls || 0) * 100 / max)) + '%"></span></div>' +
        '<span class="report-model-share">' + U.esc(String(row.share || 0)) + '%</span>' +
      '</div>';
    }).join('') + '</div>';
  }

  function searchTierLabel(tier) {
    if (tier === 'user') return I.t('用户自备');
    if (tier === 'global') return I.t('全局');
    return I.t('内置');
  }

  function mcpPanel(report) {
    const rows = report.mcp || [];
    return '<section class="panel col-6">' +
      '<div class="panel-head"><div class="panel-title">' + U.icon('plug') + I.t('MCP 使用') + '</div>' +
        '<span class="kicker">' + U.num(report.summary.mcp_servers || 0) + ' SERVERS · ' +
          U.num(report.summary.mcp_calls || 0) + ' CALLS</span></div>' +
      '<div class="panel-body">' +
        (rows.length
          ? usageRankList(rows, (row) => row.server, (row) =>
              I.t('{tools} 个工具 · {users} 位用户', {
                tools: U.num(row.tools), users: U.num(row.users),
              }))
          : U.empty('plug', I.t('暂无 MCP 调用'), I.t('模型调用 MCP 工具后，这里会显示各 server 的调用占比。'))) +
      '</div>' +
    '</section>';
  }

  function searchPanel(report) {
    const rows = report.search || [];
    return '<section class="panel col-6">' +
      '<div class="panel-head"><div class="panel-title">' + U.icon('globe') + I.t('网络搜索') + '</div>' +
        '<span class="kicker">' + U.num(report.summary.search_calls || 0) + ' CALLS · ' +
          U.num(report.summary.search_users || 0) + ' USERS</span></div>' +
      '<div class="panel-body">' +
        (rows.length
          ? usageRankList(rows, (row) => row.provider, (row) =>
              searchTierLabel(row.tier) + ' · ' + I.t('{n} 次', { n: U.num(row.calls) }))
          : U.empty('globe', I.t('暂无网络搜索'), I.t('模型使用 websearch 后，这里会显示各 provider 的调用占比。'))) +
      '</div>' +
    '</section>';
  }

  function skillRows(report) {
    const rows = report.skills || [];
    if (!rows.length) {
      return '<tr><td colspan="7" class="report-empty">' +
        U.esc(I.t('暂无技能使用记录')) + '</td></tr>';
    }
    return rows.map((row) => {
      const name = row.skill || I.t('（未命名技能）');
      return '<tr>' +
        '<td><div class="report-model-name"><b title="' + U.esc(name) + '">' + U.esc(name) + '</b></div></td>' +
        '<td class="num">' + U.num(row.uses) + '</td>' +
        '<td class="num">' + U.num(row.users) + '</td>' +
        '<td class="num">' + U.num(row.sessions) + '</td>' +
        '<td class="num">' + U.num(row.script_uses) + '</td>' +
        '<td class="num">' + U.esc(String(row.share || 0)) + '%</td>' +
        '<td class="mono-xs">' + U.esc(fmtTs(row.last_used)) + '</td>' +
      '</tr>';
    }).join('');
  }

  function skillsPanel(report) {
    return '<section class="panel col-12">' +
      '<div class="panel-head"><div class="panel-title">' + U.icon('shield') + I.t('技能使用') + '</div>' +
        '<span class="kicker">' + U.num(report.summary.skills_used || 0) + ' SKILLS · ' +
          U.num(report.summary.skill_uses || 0) + ' USES</span></div>' +
      '<div class="table-wrap"><table class="table report-table report-skill-table"><thead><tr>' +
        '<th>' + I.t('技能') + '</th><th class="num">' + I.t('使用') + '</th>' +
        '<th class="num">' + I.t('用户') + '</th><th class="num">' + I.t('会话') + '</th>' +
        '<th class="num">' + I.t('脚本') + '</th><th class="num">' + I.t('占比') + '</th>' +
        '<th>' + I.t('最近使用') + '</th>' +
      '</tr></thead><tbody>' + skillRows(report) + '</tbody></table></div>' +
      '<div class="report-note">' + U.esc(I.t(
        '共 {uses} 次使用 · {users} 位用户。统计模型读取技能正文（SKILL.md）与执行技能脚本的次数；同一会话内重复读取会重复计数。',
        { uses: U.num(report.summary.skill_uses || 0), users: U.num(report.summary.skill_users || 0) },
      )) + '</div>' +
    '</section>';
  }

  function userRows(report) {
    const query = String(ui().reportUserQuery || '').trim().toLowerCase();
    const rows = (report.users || []).filter((row) =>
      !query || String(row.username || '').toLowerCase().includes(query) ||
      String(row.uid || '').toLowerCase().includes(query));
    if (!rows.length) {
      return '<tr><td colspan="7" class="report-empty">' +
        U.esc(I.t(query ? '没有匹配的用户' : '暂无用户数据')) + '</td></tr>';
    }
    return rows.map((row) => {
      const name = row.username || I.t('匿名');
      const role = row.is_admin ? I.t('管理员') : I.t('普通用户');
      const top = row.top_model ? modelInfo(row.top_model).name : '—';
      return '<tr>' +
        '<td class="report-user-cell"><div class="report-user"><b>' + U.esc(name) + '</b>' +
          '<span class="mono-xs">' + U.esc(row.uid || '') + '</span></div>' +
          '<div class="report-tags">' + U.badge(role, row.is_admin ? 'ink' : 'neutral') +
            (row.is_new ? U.badge(I.t('新用户'), 'warn') : '') +
            (row.is_anonymous ? U.badge(I.t('匿名'), 'neutral') : '') + '</div></td>' +
        '<td class="num">' + U.num(row.active_days) + '<span class="report-unit">' + I.t('天') + '</span></td>' +
        '<td class="num">' + U.num(row.sessions) + '</td>' +
        '<td class="num">' + U.num(row.turns) + '</td>' +
        '<td class="num" title="' + U.esc(I.t('输入 {input} · 输出 {output}', {
          input: U.num(row.input), output: U.num(row.output),
        })) + '">' + U.tokenCount(row.total) + '</td>' +
        '<td class="mono" style="font-size:12px">' + U.esc(top) + '</td>' +
        '<td class="mono-xs">' + U.esc(fmtTs(row.last_active)) + '</td>' +
      '</tr>';
    }).join('');
  }

  function usersPanel(report) {
    return '<section class="panel col-12">' +
      '<div class="panel-head report-table-head">' +
        '<div class="panel-title">' + U.icon('users') + I.t('用户明细') + '</div>' +
        '<div class="report-head-tools"><span class="kicker">USERS</span>' +
          '<div class="search">' + U.icon('search') +
            '<input class="input" id="report-user-search" autocomplete="off" placeholder="' +
              U.esc(I.t('搜索用户名')) + '" value="' + U.esc(ui().reportUserQuery || '') + '">' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="table-wrap"><table class="table report-table report-user-table"><thead><tr>' +
        '<th>' + I.t('用户') + '</th><th class="num">' + I.t('活跃') + '</th>' +
        '<th class="num">' + I.t('会话') + '</th><th class="num">' + I.t('轮次') + '</th>' +
        '<th class="num">' + I.t('Token') + '</th><th>' + I.t('常用模型') + '</th>' +
        '<th>' + I.t('最后活跃') + '</th>' +
      '</tr></thead><tbody id="report-user-tbody">' + userRows(report) + '</tbody></table></div>' +
    '</section>';
  }

  function modelRows(report) {
    const rows = report.models || [];
    if (!rows.length) {
      return '<tr><td colspan="7" class="report-empty">' + U.esc(I.t('暂无模型数据')) + '</td></tr>';
    }
    return rows.map((row) => {
      const info = modelInfo(row.model);
      return '<tr>' +
        '<td><div class="report-model-name"><b title="' + U.esc(info.name) + '">' + U.esc(info.name) + '</b>' +
          '<span class="mono-xs">' + U.esc(row.model) + '</span></div></td>' +
        '<td class="mono-sm">' + U.esc(info.provider || row.provider || '—') + '</td>' +
        '<td class="num">' + U.num(row.calls) + '</td>' +
        '<td class="num">' + U.num(row.users) + '</td>' +
        '<td class="num">' + U.num(row.input) + '</td>' +
        '<td class="num">' + U.num(row.output) + '</td>' +
        '<td class="num">' + U.esc(String(row.token_share || 0)) + '%' +
          (row.backfill_calls ? '<span class="report-unit">~</span>' : '') + '</td>' +
      '</tr>';
    }).join('');
  }

  function modelsTablePanel(report) {
    return '<section class="panel col-12">' +
      '<div class="panel-head"><div class="panel-title">' + U.icon('cpu') + I.t('模型明细') + '</div>' +
        '<span class="kicker">' + U.num(report.summary.models) + ' MODELS</span></div>' +
      '<div class="table-wrap"><table class="table report-table report-model-table"><thead><tr>' +
        '<th>' + I.t('模型') + '</th><th>' + I.t('Provider') + '</th>' +
        '<th class="num">' + I.t('调用') + '</th><th class="num">' + I.t('用户') + '</th>' +
        '<th class="num">' + I.t('输入') + '</th><th class="num">' + I.t('输出') + '</th>' +
        '<th class="num">' + I.t('占比') + '</th>' +
      '</tr></thead><tbody>' + modelRows(report) + '</tbody></table></div>' +
      (report.summary.backfill_calls
        ? '<div class="report-note">' + U.esc(I.t('历史回填 {n} 次；带 ~ 的调用次数为近似值。', {
            n: U.num(report.summary.backfill_calls),
          })) + '</div>'
        : '') +
    '</section>';
  }

  function content(report) {
    return kpis(report.summary) +
      '<div class="grid">' +
        activityPanel(report) +
        modelsPanel(report) +
        tokenPanel(report) +
        usersPanel(report) +
        modelsTablePanel(report) +
        skillsPanel(report) +
        mcpPanel(report) +
        searchPanel(report) +
      '</div>';
  }

  function emptyOrLoading() {
    if (ui().reportError) {
      return '<section class="panel"><div class="panel-body">' +
        '<div class="banner bad">' + U.icon('alert') +
          '<div><b>' + U.esc(I.t('报表加载失败')) + '</b>' +
          '<p class="hint">' + U.esc(ui().reportError) + '</p>' +
          '<button type="button" class="btn ghost sm" data-report-refresh>' +
            U.esc(I.t('重试')) + '</button></div>' +
        '</div></div></section>';
    }
    return '<section class="panel"><div class="panel-body">' +
      '<div class="empty"><div class="empty-title">' + U.esc(I.t('加载中…')) + '</div></div>' +
    '</div></section>';
  }

  function bodyHTML() {
    const report = data();
    return pageHead() + toolbar() + (report ? content(report) : emptyOrLoading());
  }

  function paint() {
    if (NB.app.route !== 'admin-reports') return;
    const box = document.querySelector('#report-page');
    if (box) box.innerHTML = bodyHTML();
  }

  function paintUserRows() {
    const report = data();
    const tbody = document.querySelector('#report-user-tbody');
    if (!report || !tbody) return;
    tbody.innerHTML = userRows(report);
  }

  function load() {
    const mock = NB.mock;
    mock.ui.reportLoading = true;
    mock.ui.reportError = '';
    paint();
    const seq = ++requestSeq;
    const days = Number(mock.ui.reportDays) || 30;
    const bucket = mock.ui.reportBucket === 'week' ? 'week' : 'day';
    NB.api.get('/admin/reports?days=' + days + '&bucket=' + bucket + '&limit=20')
      .then((result) => {
        if (seq !== requestSeq) return;
        mock.report = result;
        mock.ui.reportLoading = false;
        mock.ui.reportUpdatedAt = new Date().toISOString();
        paint();
      })
      .catch((error) => {
        if (seq !== requestSeq) return;
        mock.ui.reportLoading = false;
        mock.ui.reportError = error.message || I.t('报表加载失败');
        paint();
      });
  }

  NB.pages['admin-reports'] = {
    title: '报表', kicker: '管理 / REPORT', full: false,

    render() {
      return '<div class="report-page" id="report-page">' + bodyHTML() + '</div>';
    },

    mount(root) {
      pageRoot = root;
      if (!data() && !ui().reportLoading) load();

      if (clickHandler) root.removeEventListener('click', clickHandler);
      if (inputHandler) root.removeEventListener('input', inputHandler);

      clickHandler = (event) => {
        const target = event.target;
        if (!target || !target.closest) return;
        const dayBtn = target.closest('[data-report-days]');
        if (dayBtn) {
          const days = Number(dayBtn.dataset.reportDays) || 30;
          if (ui().reportDays === days) return;
          ui().reportDays = days;
          ui().reportBucket = days >= 90 ? 'week' : 'day';
          load();
          return;
        }
        const bucketBtn = target.closest('[data-report-bucket]');
        if (bucketBtn) {
          const bucket = bucketBtn.dataset.reportBucket === 'week' ? 'week' : 'day';
          if (ui().reportBucket === bucket) return;
          ui().reportBucket = bucket;
          load();
          return;
        }
        if (target.closest('[data-report-refresh]')) load();
      };
      inputHandler = (event) => {
        if (event.target && event.target.id === 'report-user-search') {
          ui().reportUserQuery = event.target.value;
          paintUserRows();
        }
      };
      root.addEventListener('click', clickHandler);
      root.addEventListener('input', inputHandler);
    },

    refresh() {
      load();
    },

    unmount() {
      if (pageRoot && clickHandler) pageRoot.removeEventListener('click', clickHandler);
      if (pageRoot && inputHandler) pageRoot.removeEventListener('input', inputHandler);
      pageRoot = null;
      clickHandler = null;
      inputHandler = null;
    },
  };
})();
