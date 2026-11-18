/* 项目：登记已有目录、按项目查看与创建会话 */
(function () {
  const NB = window.NB;
  const U = NB.ui;
  const I = NB.i18n;
  NB.pages = NB.pages || {};

  function mapProject(row) {
    return {
      id: row.id,
      name: row.name || '',
      description: row.description || '',
      path: row.path || '',
      skills: Array.isArray(row.skills) ? row.skills : [],
      sessionCount: row.session_count || 0,
      lastActive: row.last_active || null,
      hasAgents: row.has_agents !== false,
      archived: !!row.archived_at,
    };
  }

  function projectName(id) {
    const found = (NB.mock.ui.projects || []).find((p) => p.id === id);
    return found ? found.name : id;
  }

  function reload(force) {
    if (!NB.app.canProjects || !NB.app.canProjects()) {
      NB.mock.ui.projects = [];
      return;
    }
    if (NB.mock.ui.projectsLoaded && !force) return;
    NB.api.projects().then((rows) => {
      NB.mock.ui.projects = (rows || []).map(mapProject);
      NB.mock.ui.projectsLoaded = true;
      NB.app.render();
    }).catch((error) => {
      NB.mock.ui.projectsLoaded = false;
      U.toast(error.message || I.t('error.network'), 'bad');
    });
  }

  /* 推荐技能列只显示数量或“添加”，点击打开技能选择弹窗。 */
  function skillCellHTML(project) {
    const refs = (project.skills || []).filter((s) => s && s.name);
    const label = refs.length
      ? I.t('projects.skillsCount', { n: refs.length })
      : I.t('projects.skillsAdd');
    const title = refs.length ? refs.map((s) => s.name).join('、') : I.t('projects.skillsAdd');
    return '<button type="button" class="text-action skills-entry" data-skills-edit="' +
      U.esc(project.id) + '" title="' + U.esc(title) + '">' +
      U.icon('shield') + '<span>' + U.esc(label) + '</span></button>';
  }

  /* 模板内容也走词典：英文界面复制出去的模板同样是英文。 */
  const AGENTS_TEMPLATE = [
    I.t('# 项目约定'),
    '',
    I.t('## 这个项目是什么'),
    I.t('一句话说明项目目标与主要模块。'),
    '',
    I.t('## 常用命令'),
    I.t('- 安装依赖：'),
    I.t('- 运行测试：'),
    I.t('- 本地启动：'),
    '',
    I.t('## 约束'),
    I.t('- 修改前先读现有实现与测试。'),
    I.t('- 不要提交密钥、构建产物与本地数据。'),
  ].join('\n');

  // 与后端 service/project/agents.py 的 DESCRIPTION_MIN 保持一致
  const AGENTS_DESCRIPTION_MIN = 6;

  function canDraftAgents(description) {
    return String(description || '').trim().length >= AGENTS_DESCRIPTION_MIN;
  }

  function skillRefId(ref) {
    return ref.layer + ':' + (ref.layer === 'private' && ref.owner_uid ? ref.owner_uid + ':' : '') + ref.name;
  }

  function skillLayerLabel(layer) {
    if (layer === 'system') return I.t('系统层');
    if (layer === 'shared') return I.t('共享层');
    return I.t('私有层');
  }

  function skillsFieldHTML() {
    return '<div class="field"><label>' + U.esc(I.t('projects.skills')) + '</label>' +
      '<div class="skill-pick" data-skill-pick>' +
        '<div class="skill-pick-empty">' + U.esc(I.t('projects.skillsLoading')) + '</div>' +
      '</div>' +
      '<span class="hint">' + U.esc(I.t('projects.skillsHint')) + '</span>' +
    '</div>';
  }

  const SKILL_PAGE_SIZE = 20;

  /* 技能很多时：搜索按名称与层名过滤；分页在前端做（/skills 一次返回全量列表）。 */
  function skillFilter(refs, query) {
    const q = String(query || '').trim().toLowerCase();
    if (!q) return refs || [];
    return (refs || []).filter((ref) => {
      const layer = skillLayerLabel(ref.layer)
        + (ref.stale ? ' ' + I.t('projects.skillUnavailable') : '');
      return String(ref.name || '').toLowerCase().includes(q)
        || layer.toLowerCase().includes(q);
    });
  }

  function skillPage(refs, page, pageSize) {
    const size = pageSize || SKILL_PAGE_SIZE;
    const total = (refs || []).length;
    const pages = Math.max(1, Math.ceil(total / size));
    const current = Math.min(Math.max(0, page || 0), pages - 1);
    return {
      page: current,
      pages,
      rows: (refs || []).slice(current * size, current * size + size),
    };
  }

  /* 全选作用于“当前筛选结果”，跨分页生效；已全选时再点一次就是取消。 */
  function toggleSkillAll(selected, ids) {
    const all = ids.length > 0 && ids.every((id) => selected.has(id));
    ids.forEach((id) => { if (all) selected.delete(id); else selected.add(id); });
    return !all;
  }

  /* 推荐技能多选：按层 + 归属区分同名技能；已有但当前不可见的引用保留为“不可用”，
     避免编辑其它字段时把旧配置悄悄丢掉。 */
  function mountSkillPicker(wrap, selectedRefs) {
    const box = wrap.querySelector('[data-skill-pick]');
    if (!box) return;
    box._original = selectedRefs || [];
    box._refs = new Map();
    box._order = [];
    box._selected = new Set((selectedRefs || []).map(skillRefId));
    box._query = '';
    box._page = 0;
    box._ready = false;

    const filtered = () => skillFilter(
      box._order.map((id) => box._refs.get(id)).filter(Boolean), box._query);

    const paint = () => {
      const list = box.querySelector('[data-skill-list]');
      if (!list) return;
      const rows = filtered();
      const view = skillPage(rows, box._page, SKILL_PAGE_SIZE);
      box._page = view.page;

      if (!box._order.length) {
        list.innerHTML = '<div class="skill-pick-empty">' + U.esc(I.t('projects.skillsEmpty')) + '</div>';
      } else if (!rows.length) {
        list.innerHTML = '<div class="skill-pick-empty">' + U.esc(I.t('projects.skillsNoMatch')) + '</div>';
      } else {
        list.innerHTML = view.rows.map((ref) => {
          const id = skillRefId(ref);
          return '<label class="skill-pick-row' + (ref.stale ? ' is-stale' : '') + '">' +
            '<input type="checkbox" data-skill-id="' + U.esc(id) + '"' +
              (box._selected.has(id) ? ' checked' : '') + '>' +
            '<span class="skill-pick-name">' + U.esc(ref.name) + '</span>' +
            '<span class="skill-pick-layer">' + U.esc(
              ref.stale ? I.t('projects.skillUnavailable') : skillLayerLabel(ref.layer)) + '</span>' +
          '</label>';
        }).join('');
      }

      const ids = rows.map(skillRefId);
      const all = ids.length > 0 && ids.every((id) => box._selected.has(id));
      const allBox = box.querySelector('[data-skill-all]');
      if (allBox) {
        allBox.checked = all;
        allBox.indeterminate = !all && ids.some((id) => box._selected.has(id));
        allBox.disabled = ids.length === 0;
        const label = box.querySelector('[data-skill-all-label]');
        if (label) label.textContent = I.t(all ? 'projects.skillsUnselectAll' : 'projects.skillsSelectAll');
      }
      const count = box.querySelector('[data-skill-count]');
      if (count) {
        count.textContent = I.t('projects.skillsSelected', {
          n: box._selected.size, total: box._order.length,
        });
      }
      const pageLabel = box.querySelector('[data-skill-page]');
      if (pageLabel) {
        pageLabel.textContent = I.t('projects.skillsPage', {
          page: view.page + 1, pages: view.pages,
        });
      }
      const prev = box.querySelector('[data-skill-prev]');
      const next = box.querySelector('[data-skill-next]');
      if (prev) prev.disabled = view.page <= 0;
      if (next) next.disabled = view.page >= view.pages - 1;
    };

    const build = () => {
      box.innerHTML =
        '<div class="skill-pick-tools">' +
          '<input type="search" class="input skill-pick-search" data-skill-search ' +
            'placeholder="' + U.esc(I.t('projects.skillsSearch')) + '" ' +
            'aria-label="' + U.esc(I.t('projects.skillsSearch')) + '">' +
          '<label class="skill-pick-all" title="' + U.esc(I.t('projects.skillsAllHint')) + '">' +
            '<input type="checkbox" data-skill-all>' +
            '<span data-skill-all-label>' + U.esc(I.t('projects.skillsSelectAll')) + '</span>' +
          '</label>' +
        '</div>' +
        '<div class="skill-pick-list" data-skill-list></div>' +
        '<div class="skill-pick-foot">' +
          '<span data-skill-count></span>' +
          '<span class="skill-pick-pager">' +
            '<button type="button" class="btn ghost sm" data-skill-prev>' +
              U.esc(I.t('projects.skillsPrev')) + '</button>' +
            '<span class="skill-pick-page" data-skill-page></span>' +
            '<button type="button" class="btn ghost sm" data-skill-next>' +
              U.esc(I.t('projects.skillsNext')) + '</button>' +
          '</span>' +
        '</div>';

      const search = box.querySelector('[data-skill-search]');
      search.addEventListener('input', () => {
        box._query = search.value;
        box._page = 0;
        paint();
      });
      box.querySelector('[data-skill-all]').addEventListener('change', () => {
        toggleSkillAll(box._selected, filtered().map(skillRefId));
        paint();
      });
      box.querySelector('[data-skill-list]').addEventListener('change', (e) => {
        const input = e.target.closest('input[data-skill-id]');
        if (!input) return;
        if (input.checked) box._selected.add(input.dataset.skillId);
        else box._selected.delete(input.dataset.skillId);
        paint();
      });
      box.querySelector('[data-skill-prev]').addEventListener('click', () => {
        box._page -= 1;
        paint();
      });
      box.querySelector('[data-skill-next]').addEventListener('click', () => {
        box._page += 1;
        paint();
      });
    };

    NB.api.get('/skills').then((data) => {
      const rows = data.skills || [];
      box._refs.clear();
      box._order = [];
      rows.forEach((row) => {
        const ref = { name: row.name, layer: row.layer, owner_uid: row.owner_uid || null };
        const id = skillRefId(ref);
        box._refs.set(id, ref);
        box._order.push(id);
      });
      // 旧引用（已不可见）排在最后并默认选中，保存时继续提交
      (box._original || []).forEach((ref) => {
        const id = skillRefId(ref);
        if (box._refs.has(id)) return;
        box._refs.set(id, {
          name: ref.name, layer: ref.layer, owner_uid: ref.owner_uid || null, stale: true,
        });
        box._order.push(id);
        box._selected.add(id);
      });
      build();
      box._ready = true;
      paint();
    }).catch((error) => {
      box.innerHTML = '<div class="skill-pick-empty">' + U.esc(error.message || I.t('error.network')) + '</div>';
      // 加载失败时不阻塞保存：选中项沿用进入弹窗时的原始引用
      box._ready = false;
    });
  }

  function selectedSkillRefs(wrap) {
    const box = wrap.querySelector('[data-skill-pick]');
    if (!box || !box._ready) return (box && box._original) || [];
    return box._order
      .filter((id) => box._selected.has(id))
      .map((id) => box._refs.get(id))
      .filter(Boolean);
  }

  function openAgentsEditor(project) {
    if (!project.hasAgents) {
      showAgentsEditor(project, false, AGENTS_TEMPLATE);
      return;
    }
    NB.api.readProjectAgents(project.id).then((res) => {
      const exists = !!(res && res.exists);
      showAgentsEditor(project, exists, exists ? (res.content || '') : AGENTS_TEMPLATE);
    }).catch((error) => U.toast(error.message || I.t('error.network'), 'bad'));
  }

  /* 项目约定弹窗：有文件时编辑/删除，没有文件时给模板 + 手工保存 + 模型快速创建。 */
  function showAgentsEditor(project, exists, content) {
    const hint = exists
      ? I.t('projects.agentsEditorHintView')
      : I.t('projects.agentsEditorHintCreate');
    const footer = (exists
      ? '<button type="button" class="btn danger" data-delete style="margin-right:auto">' +
          U.esc(I.t('projects.agentsDelete')) + '</button>'
      : '') +
      '<button type="button" class="btn ghost" data-cancel>' +
        U.esc(I.t('projects.cancel')) + '</button>' +
      (exists ? '' :
        '<button type="button" class="btn ghost" data-generate>' +
          U.esc(I.t('projects.agentsGenerate')) + '</button>') +
      '<button type="button" class="btn signal" data-save>' +
        U.esc(I.t(exists ? 'projects.agentsSave' : 'projects.agentsCreateSave')) + '</button>';

    U.modal({
      kicker: 'AGENTS.MD',
      title: I.t(exists ? 'projects.agentsEditorTitleView' : 'projects.agentsEditorTitleCreate'),
      wide: true,
      body: '<div class="stack">' +
        '<div class="banner steel">' + U.icon('file') +
          '<div>' + U.esc(hint) + '</div>' +
        '</div>' +
        (exists ? '<div class="muted agents-meta" data-agents-meta>' +
          U.esc(I.t('projects.agentsEditorPath')) + '</div>' : '') +
        '<textarea class="input agents-editor" data-agents-content rows="18" ' +
          'spellcheck="false" maxlength="131072">' + U.esc(content) + '</textarea>' +
      '</div>',
      footer,
      onMount: (wrap, close) => {
        const area = wrap.querySelector('[data-agents-content]');
        const meta = wrap.querySelector('[data-agents-meta]');
        const save = wrap.querySelector('[data-save]');
        wrap.querySelector('[data-cancel]').onclick = close;

        save.onclick = () => {
          if (!area.value.trim()) {
            U.toast(I.t('projects.agentsEmpty'), 'bad');
            return;
          }
          save.disabled = true;
          const request = exists
            ? NB.api.updateProjectAgents(project.id, area.value)
            : NB.api.writeProjectAgents(project.id, area.value);
          request.then(() => {
            close();
            U.toast(I.t(exists ? 'projects.agentsSaved' : 'projects.agentsCreated'));
            reload(true);
          }).catch((error) => {
            save.disabled = false;
            U.toast(error.message || I.t('error.network'), 'bad');
          });
        };

        const generate = wrap.querySelector('[data-generate]');
        if (generate) {
          generate.onclick = () => {
            const label = generate.textContent;
            generate.disabled = true;
            generate.textContent = I.t('projects.agentsGenerating');
            NB.api.generateProjectAgents(project.id).then((draft) => {
              area.value = draft.content || '';
              if (meta) meta.textContent = I.t('projects.agentsGenerated');
              U.toast(I.t('projects.agentsGenerated'));
            }).catch((error) => {
              U.toast(error.message || I.t('error.network'), 'bad');
            }).then(() => {
              generate.disabled = false;
              generate.textContent = label;
            });
          };
        }

        const remove = wrap.querySelector('[data-delete]');
        if (remove) {
          remove.onclick = () => {
            U.confirm({
              kicker: 'AGENTS.MD',
              title: I.t('projects.agentsDelete'),
              message: I.t('projects.agentsDeleteConfirm'),
              danger: true,
              okLabel: I.t('projects.agentsDelete'),
              onConfirm: () => {
                NB.api.deleteProjectAgents(project.id).then(() => {
                  close();
                  U.toast(I.t('projects.agentsDeleted'));
                  reload(true);
                }).catch((error) => U.toast(error.message || I.t('error.network'), 'bad'));
              },
            });
          };
        }
      },
    });
  }

  /* 创建项目后根据描述生成草稿：只预览，用户点“写入项目根”才落盘。 */
  /* 推荐技能统一在列表里编辑：与旧编辑弹窗里的选择器同一套搜索/分页/全选。 */
  function openSkillEditor(project) {
    U.modal({
      kicker: 'SUGGESTED SKILLS',
      title: I.t('projects.skills'),
      wide: true,
      body: '<div class="stack">' + skillsFieldHTML() + '</div>',
      footer: '<button type="button" class="btn ghost" data-cancel>' +
          U.esc(I.t('projects.cancel')) + '</button>' +
        '<button type="button" class="btn signal" data-save>' +
          U.esc(I.t('common.save')) + '</button>',
      onMount: (wrap, close) => {
        const save = wrap.querySelector('[data-save]');
        mountSkillPicker(wrap, project.skills || []);
        wrap.querySelector('[data-cancel]').onclick = close;
        save.onclick = () => {
          save.disabled = true;
          NB.api.updateProject(project.id, { skills: selectedSkillRefs(wrap) }).then(() => {
            close();
            U.toast(I.t('projects.skillsSaved'));
            reload(true);
          }).catch((error) => {
            save.disabled = false;
            U.toast(error.message || I.t('error.network'), 'bad');
          });
        };
      },
    });
  }

  function openAgentsDraft(project, draft) {
    U.modal({
      kicker: 'AGENTS.MD',
      title: I.t('projects.agentsDraftTitle'),
      wide: true,
      body: '<div class="stack">' +
        '<div class="banner steel">' + U.icon('file') +
          '<div>' + U.esc(I.t('projects.agentsDraftHint')) + '</div>' +
        '</div>' +
        ((draft.sources || []).length
          ? '<div class="muted" style="font-size:12px">' +
            U.esc(I.t('projects.agentsDraftSources', { sources: draft.sources.join('、') })) + '</div>'
          : '') +
        '<pre class="agents-template">' + U.esc(draft.content) + '</pre>' +
      '</div>',
      footer: '<button type="button" class="btn ghost" data-cancel>' +
          U.esc(I.t('projects.agentsDraftSkip')) + '</button>' +
        '<button type="button" class="btn signal" data-write>' +
          U.esc(I.t('projects.agentsDraftWrite')) + '</button>',
      onMount: (wrap, close) => {
        const writeBtn = wrap.querySelector('[data-write]');
        wrap.querySelector('[data-cancel]').onclick = close;
        writeBtn.onclick = () => {
          writeBtn.disabled = true;
          NB.api.writeProjectAgents(project.id, draft.content).then(() => {
            close();
            U.toast(I.t('projects.agentsDraftWritten'));
            reload(true);
          }).catch((error) => {
            writeBtn.disabled = false;
            U.toast(error.message || I.t('error.network'), 'bad');
          });
        };
      },
    });
  }

  function projectRows() {
    const list = NB.mock.ui.projects || [];
    if (!list.length) {
      return U.empty('layers', I.t('projects.empty'), I.t('projects.emptyHint'));
    }
    return '<section class="panel"><div class="table-wrap"><table class="table responsive project-table"><thead><tr>' +
      I.t('<th>项目</th><th class="hide-sm">目录</th><th>会话</th><th class="hide-sm">最近活动</th><th>项目约定</th><th class="hide-sm">推荐技能</th><th class="right">操作</th>') +
      '</tr></thead><tbody>' + list.map((p) =>
        '<tr data-project-row="' + U.esc(p.id) + '">' +
          '<td class="cell-user"><div class="u-name">' +
            U.esc(p.name) + '</div>' +
            (p.description
              ? '<div class="u-desc" title="' + U.esc(p.description) + '">' +
                U.esc(p.description) + '</div>'
              : '') + '</td>' +
          '<td class="hide-sm"><span class="mono-xs project-path" title="' + U.esc(p.path) + '">' +
            U.esc(p.path) + '</span></td>' +
          '<td class="num">' + p.sessionCount + '</td>' +
          '<td class="hide-sm">' + (p.lastActive ? U.fmtTime(new Date(p.lastActive * 1000).toISOString()) : '—') + '</td>' +
          '<td><button type="button" class="text-action agents-entry" data-agents-open="' + U.esc(p.id) + '">' +
            U.icon('file') + '<span>' +
            U.esc(p.hasAgents ? 'AGENTS.md' : I.t('projects.agentsCreate')) + '</span></button></td>' +
          '<td class="hide-sm">' + skillCellHTML(p) + '</td>' +
          '<td class="right cell-actions"><div class="actions">' +
            '<button type="button" class="btn ghost sm" data-project-new="' + U.esc(p.id) + '">' + U.icon('plus') + I.t('projects.newSession') + '</button>' +
            '<button type="button" class="icon-btn" data-project-rename="' + U.esc(p.id) + '" title="' + U.esc(I.t('projects.rename')) + '">' + U.icon('pencil') + '</button>' +
            '<button type="button" class="icon-btn danger" data-project-archive="' + U.esc(p.id) + '" title="' + U.esc(I.t('projects.archive')) + '">' + U.icon('trash') + '</button>' +
          '</div></td>' +
        '</tr>').join('') +
      '</tbody></table></div></section>';
  }

  /* 目录选择器：逐级浏览，选中当前目录后回填到路径输入框。
     只负责挑路径；可登记性仍由 POST /projects 校验（嵌套、应用数据等）。 */
  /* 目录展示名：workspace 只显示成 workspace，家目录显示成 ~，别把内部路径铺满弹窗 */
  let browseMeta = { workspace: '', home: '' };

  function displayPath(path) {
    const text = String(path || '');
    const ws = browseMeta.workspace;
    if (ws && (text === ws || text.startsWith(ws + '/'))) {
      return 'workspace' + text.slice(ws.length);
    }
    const home = browseMeta.home;
    if (home && home !== '/' && (text === home || text.startsWith(home + '/'))) {
      return '~' + text.slice(home.length);
    }
    return text;
  }

  /* 输入框里也只显示 workspace / ~，提交前再换回真实路径 */
  function toRealPath(value) {
    const text = String(value || '').trim();
    if (!text) return '';
    if (text === 'workspace') return browseMeta.workspace || text;
    if (text.startsWith('workspace/')) {
      return browseMeta.workspace ? browseMeta.workspace + text.slice('workspace'.length) : text;
    }
    return text;
  }

  function rootLabel(root) {
    return root === browseMeta.workspace ? 'workspace' : displayPath(root);
  }

  const MAX_ROOT_TABS = 4;

  function rootTabsHTML(roots, active) {
    const shown = roots.length > MAX_ROOT_TABS ? roots.slice(0, MAX_ROOT_TABS - 1) : roots;
    const tabs = shown.map((item) =>
      '<button type="button" class="tab" role="tab" aria-selected="' + (item === active) +
        '" data-root-tab="' + U.esc(item) + '" title="' + U.esc(item) + '">' +
        U.esc(rootLabel(item)) + '</button>').join('');
    // 当前根被折叠进「更多」时，由「更多」承担选中态，否则界面上看不到任何高亮
    const hiddenActive = roots.length > MAX_ROOT_TABS && !shown.includes(active);
    const more = roots.length > MAX_ROOT_TABS
      ? '<button type="button" class="tab" data-roots-more aria-selected="' + hiddenActive +
        '" title="' + U.esc(I.t('更多目录')) + '">' +
        I.t('更多（{n}）', { n: roots.length - MAX_ROOT_TABS + 1 }) + ' ▾</button>'
      : '';
    return tabs + more;
  }

  function openDirPicker(start, onPick) {
    let current = '';
    U.modal({
      kicker: 'SELECT DIRECTORY',
      title: I.t('projects.pickDir'),
      wide: true,
      body: '<div class="dir-picker">' +
        '<div class="dir-bar">' +
          '<button type="button" class="btn ghost sm" data-dir-up disabled>' +
            U.icon('arrow-up') + I.t('projects.up') + '</button>' +
          '<code class="dir-path" data-dir-path></code>' +
        '</div>' +
        '<div class="tabs dir-tabs" data-dir-tabs role="tablist"></div>' +
        '<div class="dir-list" data-dir-list></div>' +
      '</div>',
      footer: '<button type="button" class="btn ghost" data-cancel>' + U.esc(I.t('projects.cancel')) + '</button>' +
        '<button type="button" class="btn signal" data-pick>' + U.esc(I.t('projects.chooseThis')) + '</button>',
      onMount: (wrap, close) => {
        const pathEl = wrap.querySelector('[data-dir-path]');
        const listEl = wrap.querySelector('[data-dir-list]');
        const rootsEl = wrap.querySelector('[data-dir-tabs]');
        const upBtn = wrap.querySelector('[data-dir-up]');
        let parent = null;

        const load = (path) => {
          listEl.innerHTML = '<div class="muted">' + U.esc(I.t('common.loading')) + '</div>';
          NB.api.browseProjects(path).then((res) => {
            current = res.path;
            parent = res.parent || null;
            browseMeta = { workspace: res.workspace || '', home: res.home || '' };
            pathEl.textContent = displayPath(res.path);
            upBtn.disabled = !parent;
            // 白名单有多个根时给 tab 切换；只有一个（默认 workspace）就不占地方
            const roots = res.roots || [];
            if (rootsEl) {
              rootsEl.innerHTML = roots.length > 1 ? rootTabsHTML(roots, res.root) : '';
              rootsEl.querySelectorAll('[data-root-tab]').forEach((el) => {
                el.onclick = () => load(el.dataset.rootTab);
              });
              const moreBtn = rootsEl.querySelector('[data-roots-more]');
              if (moreBtn) {
                moreBtn.onclick = () => {
                  const rest = roots.slice(MAX_ROOT_TABS - 1);
                  U.menu(moreBtn, rest.map((item) => ({
                    label: rootLabel(item),
                    active: item === res.root,
                    onPick: () => load(item),
                  })), { align: 'left', head: I.t('更多目录') });
                };
              }
            }
            listEl.innerHTML = (res.dirs || []).length
              ? res.dirs.map((d) =>
                '<button type="button" class="dir-row" data-dir="' + U.esc(d.path) + '">' +
                  U.icon('folder') + '<span class="dir-name">' + U.esc(d.name) + '</span>' +
                  U.icon('chevron') + '</button>').join('')
              : '<div class="dir-empty">' + U.esc(I.t('projects.dirEmpty')) + '</div>';
            listEl.querySelectorAll('[data-dir]').forEach((el) => {
              el.onclick = () => load(el.dataset.dir);
            });
          }).catch((error) => {
            current = '';
            parent = null;
            pathEl.textContent = path || '';
            upBtn.disabled = true;
            listEl.innerHTML = '<div class="dir-empty">' + U.esc(error.message || I.t('error.network')) + '</div>';
            U.toast(error.message || I.t('error.network'), 'bad');
          });
        };

        upBtn.onclick = () => { if (parent) load(parent); };
        wrap.querySelector('[data-cancel]').onclick = close;
        wrap.querySelector('[data-pick]').onclick = () => {
          if (!current) return;
          onPick(current);
          close();
        };
        load(start);
      },
    });
  }

  function openCreate() {
    U.modal({
      kicker: 'NEW PROJECT',
      title: I.t('projects.create'),
      body: '<div class="stack">' +
        I.t('<div class="field"><label for="pj-name">项目名称</label>') +
          '<input class="input" id="pj-name" maxlength="80" autocomplete="off" placeholder="' + U.esc(I.t('projects.namePlaceholder')) + '"></div>' +
        I.t('<div class="field"><label for="pj-desc">项目描述</label>') +
          '<textarea class="input" id="pj-desc" rows="3" maxlength="500" placeholder="' + U.esc(I.t('projects.descriptionPlaceholder')) + '"></textarea>' +
          I.t('<span class="hint">可留空；用于在项目列表里说明这个项目做什么。</span></div>') +
        I.t('<div class="field"><label for="pj-root">项目根目录</label>') +
          '<div class="input-group">' +
            '<input class="input mono" id="pj-root" autocomplete="off" placeholder="' + U.esc(I.t('projects.rootPlaceholder')) + '">' +
            '<button type="button" class="btn ghost" data-browse>' + U.icon('folder') + I.t('projects.browse') + '</button>' +
          '</div>' +
          I.t('<span class="hint">选择一个已存在的根目录；实际项目目录 = 根目录 + 项目名称，不存在时会创建这个子目录。</span>') +
          I.t('<div class="dir-preview">项目目录：<code data-final-path></code></div></div>') +
        I.t('<div class="banner steel">') + U.icon('shield') +
          I.t('<div>登记后在项目里开新会话，文件读写以该目录为项目边界；项目外的访问需要确认。</div></div>') +
      '</div>',
      footer: I.t('<button type="button" class="btn ghost" data-cancel>取消</button>') +
        '<button type="button" class="btn signal" data-save>' + I.t('projects.create') + '</button>',
      onMount: (wrap, close) => {
        const nameInput = wrap.querySelector('#pj-name');
        const rootInput = wrap.querySelector('#pj-root');
        const finalEl = wrap.querySelector('[data-final-path]');
        const syncFinal = () => {
          const name = nameInput.value.trim();
          const root = rootInput.value.trim().replace(/\/+$/, '');
          finalEl.textContent = root
            ? root + '/' + (name || I.t('projects.namePlaceholder'))
            : I.t('先选根目录');
        };
        nameInput.oninput = syncFinal;
        rootInput.oninput = syncFinal;
        syncFinal();
        // 先拿一次 workspace 路径，输入框里显示 "workspace" 时提交才能换回真实路径
        NB.api.browseProjects('').then((res) => {
          browseMeta = { workspace: res.workspace || '', home: res.home || '' };
        }).catch(() => {});
        wrap.querySelector('[data-browse]').onclick = () => {
          openDirPicker(toRealPath(rootInput.value), (picked) => {
            rootInput.value = displayPath(picked);
            syncFinal();
          });
        };
        wrap.querySelector('[data-cancel]').onclick = close;
        wrap.querySelector('[data-save]').onclick = () => {
          const name = nameInput.value.trim();
          const root = toRealPath(rootInput.value);
          const description = wrap.querySelector('#pj-desc').value.trim();
          if (!name || !root) { U.toast(I.t('projects.needNameAndPath'), 'bad'); return; }
          NB.api.createProject({ name, parent: root, description }).then((row) => {
            close();
            U.toast(I.t('projects.created', { name: row.name }));
            reload(true);
            if (canDraftAgents(description)) {
              // 草稿失败不影响项目创建：用户仍可在项目页用模板
              NB.api.projectAgentsDraft(row.id).then((draft) => {
                if (draft && draft.eligible && draft.content) openAgentsDraft(row, draft);
              }).catch(() => {});
            }
          }).catch((error) => U.toast(error.message || I.t('error.network'), 'bad'));
        };
      },
    });
  }

  function openRename(project) {
    U.modal({
      kicker: 'EDIT PROJECT',
      title: I.t('projects.edit'),
      body: I.t('<div class="stack">') +
        I.t('<div class="field"><label for="pj-rename">项目名称</label>') +
          '<input class="input" id="pj-rename" maxlength="80" value="' + U.esc(project.name) + '" autocomplete="off"></div>' +
        I.t('<div class="field"><label for="pj-rename-desc">项目描述</label>') +
          '<textarea class="input" id="pj-rename-desc" rows="3" maxlength="500" placeholder="' + U.esc(I.t('projects.descriptionPlaceholder')) + '">' +
            U.esc(project.description || '') + '</textarea></div>' +
      '</div>',
      footer: I.t('<button type="button" class="btn ghost" data-cancel>取消</button>') +
        '<button type="button" class="btn signal" data-save>' + I.t('common.save') + '</button>',
      onMount: (wrap, close) => {
        wrap.querySelector('[data-cancel]').onclick = close;
        wrap.querySelector('[data-save]').onclick = () => {
          const name = wrap.querySelector('#pj-rename').value.trim();
          const description = wrap.querySelector('#pj-rename-desc').value.trim();
          if (!name) { U.toast(I.t('projects.needName')); return; }
          NB.api.updateProject(project.id, { name, description }).then(() => {
            close();
            U.toast(I.t('projects.renamed'));
            reload(true);
          }).catch((error) => U.toast(error.message || I.t('error.network'), 'bad'));
        };
      },
    });
  }

  function archiveProject(project) {
    U.confirm({
      kicker: 'ARCHIVE PROJECT',
      title: I.t('projects.archive'),
      message: I.t('projects.archiveConfirm', { name: project.name }),
      body: I.t('<p class="muted" style="font-size:13px">只归档项目登记，不删除目录，也不删除已有会话。</p>'),
      danger: true,
      okLabel: I.t('projects.archive'),
      onConfirm: () => {
        NB.api.archiveProject(project.id).then(() => {
          U.toast(I.t('projects.archived', { name: project.name }));
          reload(true);
        }).catch((error) => U.toast(error.message || I.t('error.network'), 'bad'));
      },
    });
  }

  function openProjectSession(projectId) {
    NB.api.createSession(undefined, NB.mock.ui.defaultThinkingLevel, projectId).then((created) => {
      if (NB.pages.chat && NB.pages.chat.discardDraft) NB.pages.chat.discardDraft();
      NB.mock.sessions.unshift({
        id: created.id, title: created.title || I.t('chat.new'), model: created.model || null,
        thinkingLevel: created.thinking_level, level: created.exec_level || NB.mock.ui.defaultLevel,
        projectId: created.project_id || projectId, projectName: created.project_name || projectName(projectId),
        projectPath: created.project_path || null,
        updatedAt: new Date().toISOString(), tokensIn: 0, tokensOut: 0, speed: 0,
        contextTokens: 0, contextLimit: 0, messages: [], uploads: [],
        _loaded: true, _hasMore: false, _beforeId: null,
      });
      NB.mock.ui.chatSession = created.id;
      NB.app.go('chat');
    }).catch((error) => U.toast(error.message || I.t('error.network'), 'bad'));
  }

  NB.pages.projects = {
    title: '项目', kicker: '工作台 / PROJECTS', full: false,
    mapProject,
    rowsHTMLForTest: projectRows,
    skillRefIdForTest: skillRefId,
    skillFilterForTest: skillFilter,
    skillPageForTest: skillPage,
    skillToggleAllForTest: toggleSkillAll,
    agentsTemplateForTest: () => AGENTS_TEMPLATE,
    agentsDraftEligibleForTest: canDraftAgents,
    rootTabsForTest: rootTabsHTML,
    displayPathForTest: displayPath,
    toRealPathForTest: toRealPath,
    setBrowseMetaForTest: (meta) => { browseMeta = { workspace: '', home: '', ...(meta || {}) }; },

    render() {
      const can = NB.app.canProjects && NB.app.canProjects();
      if (!can) {
        return '<div class="admin-page">' + U.empty('lock', I.t('projects.disabled'), I.t('projects.disabledHint')) + '</div>';
      }
      const list = NB.mock.ui.projects || [];
      return '<div class="admin-page">' +
        '<div class="page-head">' +
          I.t('<div><div class="kicker">工作台 / PROJECTS</div><h2 class="page-title">项目</h2>') +
            I.t('<p class="page-desc">把一个已存在的目录登记为项目。项目会话以该目录为工作根，文件读写默认限制在项目内，项目外访问需要确认。</p></div>') +
          '<div class="page-actions">' +
            '<span class="badge neutral">' + list.length + I.t(' 个项目</span>') +
            '<button type="button" class="btn signal" id="new-project">' + U.icon('plus') + I.t('projects.create') + '</button>' +
          '</div>' +
        '</div>' +
        projectRows() +
      '</div>';
    },

    refresh() { reload(true); },

    mount(root) {
      reload(false);
      const add = root.querySelector('#new-project');
      if (add) add.onclick = openCreate;

      root.querySelectorAll('[data-agents-open]').forEach((el) => {
        el.onclick = () => {
          const p = (NB.mock.ui.projects || []).find((x) => x.id === el.dataset.agentsOpen);
          if (p) openAgentsEditor(p);
        };
      });

      root.querySelectorAll('[data-skills-edit]').forEach((el) => {
        el.onclick = () => {
          const p = (NB.mock.ui.projects || []).find((x) => x.id === el.dataset.skillsEdit);
          if (p) openSkillEditor(p);
        };
      });

      root.querySelectorAll('[data-project-new]').forEach((el) => {
        el.onclick = () => openProjectSession(el.dataset.projectNew);
      });

      root.querySelectorAll('[data-project-rename]').forEach((el) => {
        el.onclick = () => {
          const p = (NB.mock.ui.projects || []).find((x) => x.id === el.dataset.projectRename);
          if (p) openRename(p);
        };
      });

      root.querySelectorAll('[data-project-archive]').forEach((el) => {
        el.onclick = () => {
          const p = (NB.mock.ui.projects || []).find((x) => x.id === el.dataset.projectArchive);
          if (p) archiveProject(p);
        };
      });
    },
  };
})();
