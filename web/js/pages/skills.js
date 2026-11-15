/* 技能审查：主从布局 */
(function () {
  const NB = window.NB;
  const U = NB.ui;
  const I = NB.i18n;
  NB.pages = NB.pages || {};

  /* 选中技能 / 切 tab / 选文件都只重绘对应的局部区域，
     不能走 NB.app.render()，否则整个导航、页头和列表都会被重建。 */
  let pageRoot = null;
  let filterOutside = null;
  let filterEscape = null;

  const layerName = (s) => (
    s.layer === 'system' ? I.t('系统层') : s.layer === 'shared' ? I.t('共享层') : I.t('私有层'));
  const statusLabel = (s) => (s.layer === 'system' ? I.t('只读')
    : s.status === 'released' ? I.t('已放行') : s.status === 'rejected' ? I.t('已拒绝') : I.t('待审查'));
  const statusTone = (s) => (s.layer === 'system' ? 'steel'
    : s.status === 'released' ? 'ok' : s.status === 'rejected' ? 'bad' : 'warn');
  // 私有技能所属账号：展示时去掉后端 label 的前缀 @
  const ownerName = (s) => String(s.owner || '').replace(/^@+/, '');

  function mapSkill(row) {
    const findings = (row.findings || []).map((f) => ({
      severity: f.severity, rule: f.rule_id || f.rule || '', file: f.file,
      line: f.line, message: f.message, snippet: f.snippet || '',
    }));
    const reviewed = row.reviewed_at ? new Date(row.reviewed_at * 1000).toISOString() : null;
    return {
      // 同名技能可能存在于不同层；私有层还可能分属不同账号，key 必须带层 + 归属
      id: row.layer + ':' + (row.layer === 'private' && row.owner_uid ? row.owner_uid + ':' : '') + row.name,
      name: row.name,
      layer: row.layer,
      owner: row.owner,
      ownerUid: row.owner_uid || '',
      hasScripts: !!row.has_scripts,
      verdict: row.scan_verdict || 'unscanned',
      // 系统层没有审查状态（后端返回 null），不能默认成“待审查”
      status: row.layer === 'system' ? 'readonly' : (row.status || 'undecided'),
      stale: !!row.stale,
      updatedAt: reviewed,
      scannedAt: reviewed,
      summary: row.summary || '',
      findings,
      caps: row.caps || {},
    };
  }

  function loadSkills(force) {
    const m = NB.mock;
    if (m.ui.skillsLoading || (m.ui.skillsLoaded && !force)) return;
    m.ui.skillsLoading = true;
    NB.api.get('/skills').then((data) => {
      m.skills = (data.skills || []).map(mapSkill);
      m.ui.skillsLoading = false;
      m.ui.skillsLoaded = true;
      m.ui.skillFiles = {};
      m.ui.skillFileContent = {};
      m.ui.skillFile = null;
      paintAll();
    }).catch((error) => {
      m.ui.skillsLoading = false;
      U.toast(error.message || NB.i18n.t('error.network'), 'bad');
    });
  }

  function act(skill, action) {
    return NB.api.post('/skills/' + encodeURIComponent(skill.name) + '/' + action +
      '?' + scopeQuery(skill), {}).then(() => {
      loadSkills(true);
    }).catch((error) => {
      paintDetail();
      U.toast(error.message || NB.i18n.t('error.network'), 'bad');
    });
  }

  /* 私有层必须带归属账号；否则管理员面对多个同名私有技能时会改错目标 */
  function scopeQuery(skill) {
    let query = 'layer=' + encodeURIComponent(skill.layer);
    if (skill.layer === 'private' && skill.ownerUid) {
      query += '&owner_uid=' + encodeURIComponent(skill.ownerUid);
    }
    return query;
  }

  function fmtVerdict(s) {
    if (s.layer === 'system') return U.badge(I.t('只读'), 'steel');
    return s.verdict === 'dangerous' ? U.badge(I.t('危险'), 'bad') : s.verdict === 'safe' ? U.badge(I.t('安全'), 'ok') : U.badge(I.t('未扫描'), 'neutral');
  }

  const PAGE_SIZE = 20;
  const LAYER_RANK = { private: 0, shared: 1, system: 2 };
  const TAG_GROUPS = ['status', 'layer', 'verdict'];

  // 三行标签：组内多选为 OR，组间为 AND；某组为空表示不限
  const TAG_ROWS = [
    { group: 'status', label: '状态', values: [['released', '已放行'], ['rejected', '已拒绝'], ['undecided', '待审查']] },
    { group: 'layer', label: '层级', values: [['system', '系统'], ['shared', '共享'], ['private', '私有']] },
    { group: 'verdict', label: '结论', values: [['dangerous', '高危'], ['safe', '安全']] },
  ];

  function skillTags() {
    const ui = NB.mock.ui;
    if (!ui.skillTags || typeof ui.skillTags !== 'object') ui.skillTags = {};
    TAG_GROUPS.forEach((group) => {
      if (!Array.isArray(ui.skillTags[group])) ui.skillTags[group] = [];
    });
    return ui.skillTags;
  }

  function tagSelected(group, value) {
    return skillTags()[group].indexOf(value) >= 0;
  }

  function activeTagCount() {
    const tags = skillTags();
    return TAG_GROUPS.reduce((total, group) => total + tags[group].length, 0);
  }

  function hasActiveTags() {
    return activeTagCount() > 0;
  }

  function toggleTag(group, value) {
    const tags = skillTags();
    const list = tags[group];
    const index = list.indexOf(value);
    if (index >= 0) list.splice(index, 1);
    else list.push(value);
  }

  function clearTags() {
    const tags = skillTags();
    TAG_GROUPS.forEach((group) => { tags[group] = []; });
  }

  function filterPopHTML() {
    const count = activeTagCount();
    return '<div class="sf-head">' +
        '<span class="sf-title">' + I.t('筛选条件') + '</span>' +
        '<span class="sf-count"' + (count ? '' : ' hidden') + '>' + I.t('已选') + ' ' + count + '</span>' +
      '</div>' +
      '<div class="sf-body">' + TAG_ROWS.map((row) =>
        '<div class="sf-group">' +
          '<div class="sf-label">' + I.t(row.label) + '</div>' +
          '<div class="sf-tags">' + row.values.map(([value, label]) => {
            const on = tagSelected(row.group, value);
            return '<button type="button" class="skill-tag' + (on ? ' active' : '') + '"' +
              ' data-tag-group="' + row.group + '" data-tag-value="' + value + '"' +
              ' aria-pressed="' + on + '">' + I.t(label) + '</button>';
          }).join('') + '</div>' +
        '</div>').join('') +
      '</div>' +
      '<div class="sf-foot">' +
        '<button type="button" class="sf-clear" data-clear-tags' +
          (hasActiveTags() ? '' : ' disabled') + '>' +
          I.t('清空条件') + '</button>' +
      '</div>';
  }

  function filteredSkills() {
    const all = NB.mock.skills || [];
    const tags = skillTags();
    const q = (NB.mock.ui.skillQuery || '').trim().toLowerCase();
    return all.filter((s) => {
      // 系统层仓库内置、只读，等价于「已放行 + 安全」，因此这三个标签都能搜到它；
      // 它不进入已拒绝/待审查/高危的筛选结果。
      const system = s.layer === 'system';
      const status = system ? 'released' : (s.status || 'undecided');
      const verdict = system ? 'safe' : (s.verdict || 'unscanned');
      if (tags.status.length && tags.status.indexOf(status) < 0) return false;
      if (tags.layer.length && tags.layer.indexOf(s.layer) < 0) return false;
      if (tags.verdict.length && tags.verdict.indexOf(verdict) < 0) return false;
      if (q && !String(s.name).toLowerCase().includes(q)) return false;
      return true;
    }).sort((a, b) => {
      const ra = LAYER_RANK[a.layer] == null ? 9 : LAYER_RANK[a.layer];
      const rb = LAYER_RANK[b.layer] == null ? 9 : LAYER_RANK[b.layer];
      return ra - rb;
    });
  }

  function skillItemHTML(s, selected) {
    const review = s.layer !== 'system';
    const meta = [layerName(s)];
    // 私有技能展示归属账号：管理员会同时看到多个用户上传的同名技能
    if (s.layer === 'private' && s.owner) meta.push(ownerName(s));
    meta.push(s.hasScripts ? I.t('含脚本') : I.t('纯文档'));
    // 系统层没有审查时间，末尾不要再挂一个 “· —”
    if (s.updatedAt) meta.push(U.fmtTime(s.updatedAt));
    return '<button type="button" class="skill-item" data-skill="' + U.esc(s.id) + '" aria-current="' + (s.id === selected) + '">' +
      '<div class="si-top">' +
        (review ? '<span style="width:6px;height:6px;border-radius:50%;flex:0 0 auto;background:' + (s.verdict === 'dangerous' ? 'var(--bad)' : s.verdict === 'safe' ? 'var(--ok)' : 'var(--faint)') + '"></span>' : '') +
        '<span class="si-name">' + U.esc(s.name) + '</span>' +
        (review && s.stale ? U.badge('STALE', 'warn') : '') +
        (review ? U.badge(statusLabel(s), statusTone(s)) : '') +
      '</div>' +
      '<div class="si-meta">' + meta.map((part, index) =>
        (index ? '<span class="dot-sep">·</span>' : '') + '<span>' + U.esc(part) + '</span>').join('') + '</div>' +
    '</button>';
  }

  function itemsHTML(items, selected, limit) {
    if (!items.length) return U.empty('search', I.t('没有匹配的技能'), I.t('换个筛选条件。'));
    return items.slice(0, limit).map((s) => skillItemHTML(s, selected)).join('') +
      (items.length > limit ? '<div class="skill-more">' + I.t('滚动加载更多…') + '</div>' : '');
  }

  function listHTML(selected) {
    const limit = NB.mock.ui.skillLimit || PAGE_SIZE;
    return '<div class="skill-items">' + itemsHTML(filteredSkills(), selected, limit) + '</div>';
  }

  function fileState(s) {
    const m = NB.mock;
    m.ui.skillFiles = m.ui.skillFiles || {};
    if (!m.ui.skillFiles[s.id]) {
      m.ui.skillFiles[s.id] = { loading: false, loaded: false, files: [], truncated: false, error: '' };
    }
    return m.ui.skillFiles[s.id];
  }

  function contentState(s, path) {
    const m = NB.mock;
    m.ui.skillFileContent = m.ui.skillFileContent || {};
    const key = s.id + ':' + path;
    if (!m.ui.skillFileContent[key]) {
      m.ui.skillFileContent[key] = { loading: false, loaded: false, data: null, error: '' };
    }
    return m.ui.skillFileContent[key];
  }

  function loadSkillFiles(s) {
    const state = fileState(s);
    if (state.loading || state.loaded || state.error) return;
    state.loading = true;
    NB.api.get('/skills/' + encodeURIComponent(s.name) + '/files?' + scopeQuery(s))
      .then((data) => {
        state.files = data.files || [];
        state.truncated = !!data.truncated;
        state.loading = false;
        state.loaded = true;
        const m = NB.mock;
        // 请求返回时用户可能已经切到别的技能，不能把文件列表写到新选中的技能上
        if (m.ui.skillSelected !== s.id) return;
        const current = m.ui.skillFile;
        if (!current || !state.files.some((f) => f.path === current)) {
          const skillMd = state.files.find((f) => f.path === 'SKILL.md');
          m.ui.skillFile = skillMd ? skillMd.path : (state.files[0] ? state.files[0].path : null);
        }
        if (m.ui.skillFile) loadSkillFile(s, m.ui.skillFile);
        paintFilesPane();
      })
      .catch((error) => {
        state.loading = false;
        state.error = error.message || NB.i18n.t('error.network');
        if (NB.mock.ui.skillSelected === s.id) paintFilesPane();
      });
  }

  function loadSkillFile(s, path) {
    const state = contentState(s, path);
    if (state.loading || state.loaded || state.error) return;
    state.loading = true;
    NB.api.get('/skills/' + encodeURIComponent(s.name) + '/file?' + scopeQuery(s) +
      '&path=' + encodeURIComponent(path))
      .then((data) => {
        state.data = data;
        state.loading = false;
        state.loaded = true;
        const m = NB.mock;
        if (m.ui.skillSelected !== s.id || m.ui.skillFile !== path) return;
        paintFilePreview();
      })
      .catch((error) => {
        state.loading = false;
        state.error = error.message || NB.i18n.t('error.network');
        const m = NB.mock;
        if (m.ui.skillSelected === s.id && m.ui.skillFile === path) paintFilePreview();
      });
  }

  function scanHTML(s) {
    const system = s.layer === 'system';
    const shared = s.layer === 'shared';
    const review = !system;

    return '<div class="skill-meta-grid">' +
        I.t('<div class="skill-meta-cell"><div class="stat-label">来源</div><div class="v">') +
          U.esc(system ? I.t('系统 · 仓库内置')
            : shared ? I.t('共享 · 所有账号')
            : I.t('私有 · ') + ownerName(s)) + '</div></div>' +
        I.t('<div class="skill-meta-cell"><div class="stat-label">内容版本</div><div class="v">') + U.fmtDate(s.updatedAt) + '</div></div>' +
        (review
          ? I.t('<div class="skill-meta-cell"><div class="stat-label">最近扫描</div><div class="v">') + U.fmtTime(s.scannedAt) + '</div></div>' +
            '<div class="skill-meta-cell"><div class="stat-label">Findings</div><div class="v">' + s.findings.length + I.t(' 项</div></div>')
          : '') +
      '</div>' +

      (review && s.stale
        ? '<div style="padding:14px 18px 0"><div class="banner warn">' + U.icon('alert') + I.t('<div>技能内容在上次扫描后发生了变化，当前结论可能已经过时。建议重新扫描。</div></div></div>')
        : '') +

      (review
        ? '<div class="skill-section">' +
            '<div class="skill-section-title"><span class="kicker">SCAN RESULT</span>' +
              '<span class="mono-xs">' + U.esc(s.findings.length ? I.t('静态扫描 + AST 规则') : I.t('未发现问题')) + '</span></div>' +
            (s.findings.length
              ? s.findings.map((f) =>
                  '<div class="finding">' +
                    '<div>' + U.badge(U.severityName(f.severity), U.severityTone(f.severity)) + '<div class="f-rule">' + U.esc(f.rule) + '</div></div>' +
                    '<div><div class="f-msg">' + U.esc(f.message) + '</div>' +
                      '<div class="f-loc">' + U.esc(f.file) + ':' + f.line + '</div>' +
                      (f.snippet ? '<div class="code">' + U.esc(f.snippet) + '</div>' : '') +
                    '</div>' +
                  '</div>').join('')
              : '<div class="skill-empty-verdict">' + U.icon('check') + I.t('没有发现命令注入、密钥读取或外发行为。</div>')) +
          '</div>'
        : '') +

      '<div class="skill-section">' +
        '<div class="skill-section-title"><span class="kicker">POLICY</span></div>' +
        '<div class="mono-xs" style="font-family:var(--font-body);font-size:12.5px;line-height:1.8;color:var(--muted)">' +
          (system
            ? I.t('系统层技能由仓库维护，所有账号只读；不能扫描、放行或拒绝。')
            : shared
              ? I.t('共享层技能对所有账号可见。普通用户只能查看结论；重新扫描、放行或拒绝需要管理员。')
              : I.t('私有层技能只有 ') + U.esc(ownerName(s)) + I.t(' 和管理员可见。含高危 finding 的技能只能由管理员放行。')) +
          (review && s.verdict === 'dangerous' ? I.t('<br><span style="color:var(--bad)">当前结论为危险：放行按钮仅管理员可用。</span>') : '') +
        '</div>' +
      '</div>';
  }

  function previewHTML(s) {
    const current = NB.mock.ui.skillFile;
    const content = current ? contentState(s, current) : null;

    if (!content) return I.t('<div class="skill-file-empty">从左侧选择一个文件。</div>');
    if (content.loading) return I.t('<div class="skill-file-empty">加载中…</div>');
    if (content.error) return '<div class="skill-file-empty">' + U.esc(content.error) + '</div>';
    if (!content.data) return I.t('<div class="skill-file-empty">从左侧选择一个文件。</div>');
    if (content.data.binary) return I.t('<div class="skill-file-empty">二进制文件，暂不预览。</div>');
    if (content.data.too_large) return I.t('<div class="skill-file-empty">文件超过 512 KB，暂不预览。</div>');
    return '<pre class="skill-file-code">' + U.esc(content.data.content || '') + '</pre>';
  }

  function filesHTML(s) {
    const state = fileState(s);
    const files = state.files || [];
    const current = NB.mock.ui.skillFile;

    const preview = previewHTML(s);

    const list = files.length
      ? files.map((f) =>
          '<button type="button" class="skill-file-row" data-skill-file="' + U.esc(f.path) + '"' +
            ' aria-current="' + (f.path === current) + '">' +
            '<span class="skill-file-path">' + U.esc(f.path) + '</span>' +
            '<span class="skill-file-size">' + U.num(f.size) + ' B</span>' +
          '</button>').join('')
      : I.t('<div class="skill-file-empty">') + (state.loading ? I.t('加载中…') : U.esc(state.error || I.t('没有文件'))) + '</div>';

    return '<div class="skill-files">' +
      '<div class="skill-file-side">' +
        (state.truncated ? I.t('<div class="skill-file-more">文件过多，仅显示前 500 个。</div>') : '') +
        list +
      '</div>' +
      '<div class="skill-file-view">' + preview + '</div>' +
    '</div>';
  }

  function detailHTML(s, admin) {
    if (!s) return U.empty('shield', I.t('没有选中的技能'), I.t('从左侧选一个。'));
    const caps = s.caps || {};
    const canScan = !!caps.scan;
    const canRelease = !!caps.approve && !(caps.approve_needs_safe && s.verdict === 'dangerous');
    const canReject = !!caps.reject;
    const system = s.layer === 'system';
    const shared = s.layer === 'shared';
    const review = !system;
    const layerTone = system || shared ? 'steel' : 'neutral';
    const readonlyTip = I.t('共享层只读');
    const tab = NB.mock.ui.skillTab || 'scan';

    return '<div class="skill-detail">' +
      '<div class="skill-detail-head">' +
        '<div class="sd-title"><h2>' + U.esc(s.name) + '</h2>' +
          '<div class="sd-sub">' + U.badge(layerName(s), layerTone) +
            (s.layer === 'private' && s.owner ? U.badge(ownerName(s), 'neutral') : '') +
            (review ? U.badge(statusLabel(s), statusTone(s)) + fmtVerdict(s) : '') +
            (review && s.stale ? U.badge(I.t('内容已变化'), 'warn') : '') +
            (s.hasScripts ? U.badge('scripts', 'neutral') : U.badge('docs only', 'neutral')) + '</div>' +
          '<p class="muted" style="margin-top:9px;font-size:13px;line-height:1.65;max-width:615px">' + U.esc(s.summary) + '</p>' +
        '</div>' +
        (review
          ? '<div class="sd-actions">' +
              '<button type="button" class="btn ghost sm" data-scan="' + U.esc(s.id) + '"' + (canScan ? '' : I.t(' disabled title="') + U.esc(readonlyTip) + '"') + '>' + U.icon('refresh') + I.t('扫描</button>') +
              '<button type="button" class="btn acid sm" data-release="' + U.esc(s.id) + '"' + (canRelease && s.status !== 'released' ? '' : ' disabled') + '>' + U.icon('check') + I.t('放行</button>') +
              '<button type="button" class="btn danger sm" data-reject="' + U.esc(s.id) + '"' + (canReject && s.status !== 'rejected' ? '' : ' disabled') + '>' + U.icon('x') + I.t('拒绝</button>') +
            '</div>'
          : '') +
      '</div>' +
      '<div class="skill-tabs" role="tablist">' +
        '<button type="button" class="skill-tab" role="tab" data-skill-tab="scan" aria-selected="' + (tab === 'scan') + '">' + I.t('扫描信息') + '</button>' +
        '<button type="button" class="skill-tab" role="tab" data-skill-tab="files" aria-selected="' + (tab === 'files') + '">' + I.t('文件') + '</button>' +
      '</div>' +
      '<div class="skill-detail-body">' + (tab === 'files' ? filesHTML(s) : scanHTML(s)) + '</div>' +
    '</div>';
  }

  function currentSkill() {
    const m = NB.mock;
    return (m.skills || []).find((s) => s.id === m.ui.skillSelected) || null;
  }

  function countsHTML(skills) {
    const released = skills.filter((s) => s.layer !== 'system' && s.status === 'released').length;
    const pending = skills.filter((s) => s.layer !== 'system' && s.status === 'undecided').length;
    const dangerous = skills.filter((s) => s.layer !== 'system' && s.verdict === 'dangerous').length;
    const scanned = skills.filter((s) => s.layer !== 'system' && s.verdict !== 'unscanned').length;
    const stale = skills.filter((s) => s.layer !== 'system' && s.stale).length;

    return I.t('<span class="skill-count">已放行 <b>') + released + '</b></span>' +
      I.t('<span class="skill-count warn">待审查 <b>') + pending + '</b></span>' +
      I.t('<span class="skill-count bad">高危 <b>') + dangerous + '</b></span>' +
      '<span class="skill-count">' + I.t('已扫描') + ' <b>' + scanned + '</b></span>' +
      (stale ? I.t('<span class="skill-count warn">过期 <b>') + stale + '</b></span>' : '');
  }

  function paintCounts() {
    const el = pageRoot && pageRoot.querySelector('#skill-counts');
    if (el) el.innerHTML = countsHTML(NB.mock.skills || []);
  }

  function paintList(resetScroll) {
    const box = pageRoot && pageRoot.querySelector('.skill-items');
    if (!box) return;
    const top = resetScroll ? 0 : box.scrollTop;
    box.innerHTML = itemsHTML(filteredSkills(), NB.mock.ui.skillSelected, NB.mock.ui.skillLimit || PAGE_SIZE);
    box.scrollTop = top;
  }

  /* 只改选中态，不重建列表：保持滚动位置和滚动加载的条目 */
  function paintListSelection() {
    if (!pageRoot) return;
    const selected = NB.mock.ui.skillSelected;
    pageRoot.querySelectorAll('.skill-item').forEach((b) => {
      b.setAttribute('aria-current', String(b.dataset.skill === selected));
    });
  }

  function paintDetail() {
    const slot = pageRoot && pageRoot.querySelector('#skill-detail-slot');
    if (!slot) return;
    slot.innerHTML = detailHTML(currentSkill(), NB.mock.isAdmin());
  }

  function paintDetailBody() {
    const s = currentSkill();
    const body = pageRoot && pageRoot.querySelector('.skill-detail-body');
    if (!s || !body) return;
    const tab = NB.mock.ui.skillTab || 'scan';
    body.innerHTML = tab === 'files' ? filesHTML(s) : scanHTML(s);
    pageRoot.querySelectorAll('[data-skill-tab]').forEach((b) => {
      b.setAttribute('aria-selected', String(b.dataset.skillTab === tab));
    });
  }

  function paintFilesPane() {
    const s = currentSkill();
    if (!s || !pageRoot || (NB.mock.ui.skillTab || 'scan') !== 'files') return;
    const body = pageRoot.querySelector('.skill-detail-body');
    if (body) body.innerHTML = filesHTML(s);
  }

  /* 选文件只换预览和行高亮，文件列表的滚动位置保持不变 */
  function paintFilePreview() {
    const s = currentSkill();
    const view = pageRoot && pageRoot.querySelector('.skill-file-view');
    if (!s || !view) return;
    view.innerHTML = previewHTML(s);
    pageRoot.querySelectorAll('[data-skill-file]').forEach((b) => {
      b.setAttribute('aria-current', String(b.dataset.skillFile === NB.mock.ui.skillFile));
    });
  }

  function setFilterPopOpen(open) {
    const pop = pageRoot && pageRoot.querySelector('#skill-filter-pop');
    const btn = pageRoot && pageRoot.querySelector('#skill-filter-btn');
    if (pop) pop.hidden = !open;
    if (btn) btn.setAttribute('aria-expanded', String(!!open));
  }

  /* 有任一标签选中时漏斗按钮高亮；弹层里的标签同步选中态 */
  function paintFilterState() {
    const btn = pageRoot && pageRoot.querySelector('#skill-filter-btn');
    if (!btn) return;
    const active = hasActiveTags();
    if (btn.classList) btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));

    const pop = pageRoot.querySelector('#skill-filter-pop');
    if (!pop) return;
    pop.querySelectorAll('[data-tag-group]').forEach((chip) => {
      const on = tagSelected(chip.dataset.tagGroup, chip.dataset.tagValue);
      if (chip.classList) chip.classList.toggle('active', on);
      chip.setAttribute('aria-pressed', String(on));
    });
    const count = activeTagCount();
    const counter = pop.querySelector('.sf-count');
    if (counter) {
      counter.textContent = I.t('已选') + ' ' + count;
      counter.hidden = !count;
    }
    const clear = pop.querySelector('[data-clear-tags]');
    if (clear) clear.disabled = !active;
  }

  function paintScanAll() {
    const btn = pageRoot && pageRoot.querySelector('#scan-all');
    if (!btn) return;
    const scannable = (NB.mock.skills || []).some(
      (s) => s.layer !== 'system' && s.caps && s.caps.scan);
    btn.disabled = !scannable;
  }

  function paintAll() {
    const m = NB.mock;
    if (!m.skills.some((s) => s.id === m.ui.skillSelected)) {
      m.ui.skillSelected = m.skills[0] ? m.skills[0].id : null;
    }
    paintCounts();
    paintScanAll();
    paintList();
    const s = currentSkill();
    if (s && (m.ui.skillTab || 'scan') === 'files') loadSkillFiles(s);
    paintDetail();
  }

  function addSkillModal() {
    const mock = NB.mock;
    const admin = mock.isAdmin();
    const owner = mock.identity === 'owner';
    const options = admin
      ? [['private', I.t('私有层')], ['shared', I.t('共享层')]]
      : owner ? [['shared', I.t('共享层')]] : [['private', I.t('私有层')]];

    U.modal({
      kicker: 'UPLOAD',
      title: I.t('添加技能'),
      body: '<div class="stack">' +
        I.t('<div class="field"><label for="sk-file">选择技能包</label><input class="input" id="sk-file" type="file" accept=".zip,.skill"></div>') +
        I.t('<div class="field"><label for="sk-target">上传到</label><select class="select" id="sk-target">') +
          options.map(([v, l]) => '<option value="' + v + '">' + l + '</option>').join('') +
        '</select></div>' +
        I.t('<div class="mono-xs" style="line-height:1.7">支持 .zip / .skill；包内需有 SKILL.md，单层包装目录会自动去掉。</div>') +
      '</div>',
      footer: I.t('<button type="button" class="btn ghost" data-cancel>取消</button><button type="button" class="btn signal" data-upload>上传</button>'),
      onMount: (wrap, close) => {
        wrap.querySelector('[data-cancel]').onclick = close;
        wrap.querySelector('[data-upload]').onclick = () => {
          const input = wrap.querySelector('#sk-file');
          const file = input.files && input.files[0];
          if (!file) { U.toast(I.t('请选择技能包'), 'bad'); return; }
          if (!/\.(zip|skill)$/i.test(file.name)) { U.toast(I.t('只支持 .zip 或 .skill 文件'), 'bad'); return; }

          const btn = wrap.querySelector('[data-upload]');
          const target = wrap.querySelector('#sk-target').value;
          btn.disabled = true;
          btn.innerHTML = I.t('<span class="spinner"></span>上传中…');
          NB.api.uploadSkill(file, target).then((result) => {
            close();
            mock.ui.skillFiles = {};
            mock.ui.skillFileContent = {};
            mock.ui.skillFile = null;
            const me = mock.me();
            mock.ui.skillSelected = result.target === 'private' && me.uid
              ? 'private:' + me.uid + ':' + result.name
              : result.target + ':' + result.name;
            loadSkills(true);
            U.toast(I.t('技能已上传：') + result.name, 'ok');
          }).catch((error) => {
            btn.disabled = false;
            btn.textContent = I.t('上传');
            U.toast(error.message || NB.i18n.t('error.network'), 'bad');
          });
        };
      },
    });
  }

  NB.pages.skills = {
    title: '技能', kicker: '配置 / SKILLS', full: false,
    mapRow: mapSkill,
    render() {
      const mock = NB.mock;
      const admin = mock.isAdmin();
      const skills = mock.skills;
      let sel = mock.ui.skillSelected;
      if (!skills.some((s) => s.id === sel)) sel = skills[0] ? skills[0].id : null;
      mock.ui.skillSelected = sel;
      const selected = skills.find((s) => s.id === sel) || null;

      const scannable = skills.filter((s) => s.layer !== 'system' && s.caps && s.caps.scan).length;
      const filterActive = hasActiveTags();

      return '<div class="skills-page">' +
        '<div class="page-head">' +
          I.t('<div><div class="kicker">配置 / SKILL REVIEW</div><h2 class="page-title">技能审查</h2>') +
            I.t('<p class="page-desc">脚本类技能先扫描再放行。系统层只读；共享层由管理员维护；私有层由你自己扫描与放行，高危项仍需管理员。</p></div>') +
          '<div class="page-actions">' +
            '<div class="skill-counts" id="skill-counts">' + countsHTML(skills) + '</div>' +
            '<button type="button" class="btn ghost" id="scan-all"' + (scannable ? '' : ' disabled') + '>' + U.icon('refresh') + I.t('全部扫描</button>') +
            '<button type="button" class="btn signal" id="add-skill">' + U.icon('plus') + I.t('添加技能</button>') +
          '</div>' +
        '</div>' +

        '<div class="banner steel">' + U.icon('shield') +
          I.t('<div><b>放行门：</b>脚本类技能必须扫描后才能放行；系统层仓库内置、只读。共享层与私有层分开判定；普通用户只能扫描自己的私有技能，管理员可扫描共享技能与私有技能。匿名用户不能使用技能；普通用户只能使用已放行的共享技能，以及自己放行的安全私有技能。</div>') +
        '</div>' +

        '<div class="skills-grid">' +
          '<section class="skill-list">' +
            '<div class="skill-list-head"><div class="toolbar">' +
              '<div class="search">' + U.icon('search') + I.t('<input class="input" id="skill-search" placeholder="筛选技能…" autocomplete="off" value="') + U.esc(mock.ui.skillQuery || '') + I.t('"></div>') +
              '<div class="skill-filter-wrap">' +
                '<button type="button" class="icon-btn skill-filter-btn' + (filterActive ? ' active' : '') + '"' +
                  ' id="skill-filter-btn" aria-label="' + U.esc(I.t('筛选条件')) + '"' +
                  ' title="' + U.esc(I.t('筛选条件')) + '" aria-expanded="false"' +
                  ' aria-pressed="' + filterActive + '">' + U.icon('filter') + '</button>' +
                '<div class="skill-filter-pop" id="skill-filter-pop" hidden>' + filterPopHTML() + '</div>' +
              '</div>' +
            '</div></div>' +
            listHTML(sel) +
          '</section>' +
          '<div class="skill-detail-slot" id="skill-detail-slot">' + detailHTML(selected, admin) + '</div>' +
        '</div>' +
      '</div>';
    },

    mount(root) {
      const mock = NB.mock;
      pageRoot = root;
      loadSkills();

      const box = root.querySelector('.skill-items');
      if (box) {
        // 事件委托：滚动分页追加的条目不需要重新绑定
        box.addEventListener('click', (e) => {
          const b = e.target.closest('[data-skill]');
          if (!b) return;
          mock.ui.skillSelected = b.dataset.skill;
          mock.ui.skillFile = null;
          paintListSelection();
          const s = currentSkill();
          if (s && (mock.ui.skillTab || 'scan') === 'files') loadSkillFiles(s);
          paintDetail();
        });
        box.addEventListener('scroll', () => {
          if (box.scrollTop + box.clientHeight < box.scrollHeight - 48) return;
          const items = filteredSkills();
          const limit = mock.ui.skillLimit || PAGE_SIZE;
          if (limit >= items.length) return;
          const next = Math.min(items.length, limit + PAGE_SIZE);
          const more = box.querySelector('.skill-more');
          if (more) more.remove();
          box.insertAdjacentHTML('beforeend',
            items.slice(limit, next).map((s) => skillItemHTML(s, mock.ui.skillSelected)).join('') +
            (items.length > next ? '<div class="skill-more">' + I.t('滚动加载更多…') + '</div>' : ''));
          mock.ui.skillLimit = next;
        });
      }

      const filterBtn = root.querySelector('#skill-filter-btn');
      const filterPop = root.querySelector('#skill-filter-pop');
      if (filterBtn && filterPop) {
        filterBtn.onclick = (e) => {
          if (e && e.stopPropagation) e.stopPropagation();
          setFilterPopOpen(filterPop.hidden);
        };
        filterPop.addEventListener('click', (e) => {
          const target = e.target;
          if (!target || !target.closest) return;
          const chip = target.closest('[data-tag-group]');
          if (chip) {
            toggleTag(chip.dataset.tagGroup, chip.dataset.tagValue);
            mock.ui.skillLimit = PAGE_SIZE;
            paintFilterState();
            paintList(true);
            return;
          }
          if (target.closest('[data-clear-tags]')) {
            clearTags();
            mock.ui.skillLimit = PAGE_SIZE;
            paintFilterState();
            paintList(true);
          }
        });

        filterOutside = (e) => {
          if (filterPop.hidden) return;
          if (filterPop.contains(e.target) || filterBtn.contains(e.target)) return;
          setFilterPopOpen(false);
        };
        filterEscape = (e) => { if (e.key === 'Escape') setFilterPopOpen(false); };
        if (document.addEventListener) {
          document.addEventListener('click', filterOutside, true);
          document.addEventListener('keydown', filterEscape, true);
        }
      }

      const search = root.querySelector('#skill-search');
      if (search) {
        search.addEventListener('input', () => {
          mock.ui.skillQuery = search.value;
          mock.ui.skillLimit = PAGE_SIZE;
          paintList(true);
        });
      }

      const all = root.querySelector('#scan-all');
      if (all) all.onclick = () => {
        const scannable = mock.skills.filter((s) => s.layer !== 'system' && s.caps && s.caps.scan);
        if (!scannable.length) return;
        all.disabled = true;
        all.innerHTML = I.t('<span class="spinner"></span>扫描中…');
        Promise.all(scannable.map((s) =>
          NB.api.post('/skills/' + encodeURIComponent(s.name) + '/scan', {})
        )).then(() => {
          all.disabled = false;
          all.innerHTML = U.icon('refresh') + I.t('全部扫描');
          loadSkills(true);
          U.toast(I.t('扫描完成，共 ') + scannable.length + I.t(' 个技能'));
        }).catch((error) => {
          all.disabled = false;
          all.innerHTML = U.icon('refresh') + I.t('全部扫描');
          U.toast(error.message || NB.i18n.t('error.network'), 'bad');
        });
      };

      const addSkill = root.querySelector('#add-skill');
      if (addSkill) addSkill.onclick = addSkillModal;

      // 详情的点击统一委托：局部重绘后按钮不需要重新绑定
      if (root.addEventListener) root.addEventListener('click', (e) => {
        const t = e.target;
        if (!t || !t.closest) return;

        const tab = t.closest('[data-skill-tab]');
        if (tab) {
          mock.ui.skillTab = tab.dataset.skillTab;
          const s = currentSkill();
          if (mock.ui.skillTab === 'files' && s) loadSkillFiles(s);
          paintDetailBody();
          return;
        }

        const file = t.closest('[data-skill-file]');
        if (file) {
          const s = currentSkill();
          if (!s) return;
          mock.ui.skillFile = file.dataset.skillFile;
          loadSkillFile(s, mock.ui.skillFile);
          paintFilePreview();
          return;
        }

        const scanBtn = t.closest('[data-scan]');
        if (scanBtn) {
          const s = mock.skills.find((x) => x.id === scanBtn.dataset.scan);
          if (!s) return;
          scanBtn.disabled = true;
          scanBtn.innerHTML = I.t('<span class="spinner"></span>扫描中');
          act(s, 'scan');
          return;
        }

        const releaseBtn = t.closest('[data-release]');
        if (releaseBtn) {
          const s = mock.skills.find((x) => x.id === releaseBtn.dataset.release);
          if (s) act(s, 'release');
          return;
        }

        const rejectBtn = t.closest('[data-reject]');
        if (rejectBtn) {
          const s = mock.skills.find((x) => x.id === rejectBtn.dataset.reject);
          if (s) act(s, 'reject');
        }
      });

      const selected = currentSkill();
      if ((mock.ui.skillTab || 'scan') === 'files' && selected) loadSkillFiles(selected);
    },

    unmount() {
      if (document.removeEventListener) {
        if (filterOutside) document.removeEventListener('click', filterOutside, true);
        if (filterEscape) document.removeEventListener('keydown', filterEscape, true);
      }
      filterOutside = null;
      filterEscape = null;
      pageRoot = null;
    },
  };
})();
