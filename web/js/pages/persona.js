/* 人格：NovaBot 头像、SOUL.md 与长期记忆只读 */
(function () {
  const NB = window.NB;
  const U = NB.ui;
  const I = NB.i18n;
  NB.pages = NB.pages || {};

  let pageRoot = null;
  let clickHandler = null;

  function pageHead() {
    return '<div class="page-head">' +
      '<div>' +
        '<div class="kicker">' + U.esc(I.t('persona.kicker')) + '</div>' +
        '<h2 class="page-title">' + U.esc(I.t('persona.title')) + '</h2>' +
      '</div>' +
    '</div>';
  }

  function memoryRows(persona, current) {
    const rows = persona.memory || [];
    if (!rows.length) return '<p class="muted">' + U.esc(I.t('persona.memoryEmpty')) + '</p>';
    return '<div class="persona-memory-list">' + rows.map((item) =>
      '<button type="button" class="persona-memory-row" data-memory="' + U.esc(item.name) + '"' +
        ' aria-current="' + (item.name === current) + '">' +
        '<span>' + U.esc(item.name) + '</span>' +
        '<span class="lines">' + U.esc(I.t('persona.lines', { n: item.lines })) + '</span>' +
      '</button>').join('') + '</div>';
  }

  function bodyHTML() {
    const persona = NB.mock.persona;
    if (!persona) {
      return pageHead() + '<section class="panel"><div class="panel-body">' +
        '<div class="empty"><div class="empty-title">' + U.esc(I.t('common.loading')) + '</div></div>' +
      '</div></section>';
    }
    if (persona.error) {
      return pageHead() + '<section class="panel"><div class="panel-body">' +
        '<div class="banner bad">' + U.icon('alert') +
          '<div><b>' + U.esc(I.t('persona.loadFailed')) + '</b>' +
          '<p class="hint">' + U.esc(persona.error) + '</p>' +
          '<button type="button" class="btn ghost sm" data-persona-retry>' + U.esc(I.t('persona.retry')) + '</button></div>' +
        '</div>' +
      '</div></section>';
    }

    const current = persona.current || 'core.md';
    const content = persona.contents && persona.contents[current] != null
      ? persona.contents[current] : '';
    const soulHTML = persona.soul
      ? NB.markdown.render(persona.soul)
      : '<p class="hint">' + U.esc(I.t('persona.soulEmpty')) + '</p>';
    const memoryHTML = content ? NB.markdown.render(content) : '';

    return pageHead() +
      '<section class="panel">' +
        '<div class="panel-head"><div class="panel-title">' + U.icon('brain') +
          U.esc(I.t('persona.soulTitle')) + '</div><span class="kicker">NOVABOT</span></div>' +
        '<div class="panel-body">' +
          '<div class="persona-face">' +
            '<button type="button" class="persona-avatar" data-persona-avatar' +
              ' aria-label="' + U.esc(I.t('persona.changeAvatar')) + '"' +
              ' title="' + U.esc(I.t('persona.changeAvatar')) + '">' +
              '<span class="avatar">' + U.avatarInner('N', persona.avatar || '') + '</span>' +
            '</button>' +
            '<div class="persona-meta">' +
              '<b>NovaBot</b>' +
              '<span class="hint">' + U.esc(I.t('persona.avatarHint')) + '</span>' +
              (persona.avatar
                ? '<button type="button" class="btn ghost sm" data-persona-remove style="margin-top:8px">' +
                    U.esc(I.t('persona.removeAvatar')) + '</button>'
                : '') +
            '</div>' +
          '</div>' +
          '<div class="persona-md">' + soulHTML + '</div>' +
        '</div>' +
      '</section>' +
      '<section class="panel">' +
        '<div class="panel-head"><div class="panel-title">' + U.icon('layers') +
          U.esc(I.t('persona.memoryTitle')) + '</div><span class="kicker">READ ONLY</span></div>' +
        '<div class="panel-body persona-memory-grid">' +
          '<div class="persona-memory-side">' + memoryRows(persona, current) + '</div>' +
          '<div class="persona-md persona-memory-body">' + memoryHTML + '</div>' +
        '</div>' +
      '</section>';
  }

  function paint() {
    if (NB.app.route !== 'persona') return;
    const box = document.querySelector('#persona-page');
    if (box) box.innerHTML = bodyHTML();
  }

  function load() {
    const mock = NB.mock;
    if (mock.personaLoading) return;
    mock.personaLoading = true;
    if (!mock.persona || mock.persona.error) mock.persona = null;
    paint();
    const uid = NB.mock.me().uid;
    NB.api.get('/persona').then((data) => {
      if (NB.mock.me().uid !== uid) return;  // 切换账号后旧请求不能写回新账号
      const persona = {
        avatar: data.avatar || '',
        soul: data.soul || '',
        memory: data.memory || [],
        current: 'core.md',
        contents: {},
      };
      if (data.core) persona.contents[data.core.name || 'core.md'] = data.core.content || '';
      mock.persona = persona;
      mock.personaLoading = false;
      paint();
    }).catch((error) => {
      if (NB.mock.me().uid !== uid) return;
      mock.persona = { error: error.message || I.t('persona.loadFailed') };
      mock.personaLoading = false;
      paint();
    });
  }

  function openMemory(name) {
    const persona = NB.mock.persona;
    if (!persona || !name) return;
    if (persona.contents && persona.contents[name] != null) {
      persona.current = name;
      paint();
      return;
    }
    NB.api.get('/persona/memory/' + encodeURIComponent(name)).then((data) => {
      persona.contents = persona.contents || {};
      persona.contents[name] = data.content || '';
      persona.current = name;
      paint();
    }).catch((error) => U.toast(error.message || I.t('persona.memoryReadFailed'), 'bad'));
  }

  function setAgentAvatar(avatar) {
    const me = NB.mock.me();
    if (NB.app.user) NB.app.user.agent_avatar = avatar;
    const mine = (NB.mock.users || []).find((u) => u.uid === me.uid);
    if (mine) mine.agentAvatar = avatar;
    if (NB.mock.persona) NB.mock.persona.avatar = avatar;
    NB.app.render();
  }

  function removeAvatar() {
    NB.api.delete('/persona/avatar').then(() => {
      setAgentAvatar('');
      U.toast(I.t('persona.removed'));
    }).catch((error) => U.toast(error.message || NB.i18n.t('error.network'), 'bad'));
  }

  NB.pages.persona = {
    title: 'persona.title', kicker: 'persona.kicker', full: false,

    render() {
      return '<div class="persona-wrap">' +
        '<input type="file" id="persona-avatar-file" accept="image/*" hidden>' +
        '<div class="persona-page" id="persona-page">' + bodyHTML() + '</div>' +
      '</div>';
    },

    mount(root) {
      pageRoot = root;
      if (!NB.mock.persona) load();

      const file = root.querySelector('#persona-avatar-file');
      if (file) {
        file.onchange = () => {
          const picked = file.files && file.files[0];
          file.value = '';
          if (!picked) return;
          U.pickAvatar({
            file: picked,
            endpoint: '/persona/avatar',
            kicker: 'NOVABOT',
            title: I.t('persona.changeAvatar'),
            saveLabel: I.t('avatar.save'),
            onSaved: (avatar) => {
              setAgentAvatar(avatar);
              U.toast(I.t('persona.avatarUpdated'));
            },
          });
        };
      }

      if (clickHandler) root.removeEventListener('click', clickHandler);
      clickHandler = (e) => {
        const target = e.target;
        if (!target || !target.closest) return;
        if (target.closest('[data-persona-retry]')) { load(); return; }
        if (target.closest('[data-persona-avatar]')) {
          const input = root.querySelector('#persona-avatar-file');
          if (input) input.click();
          return;
        }
        if (target.closest('[data-persona-remove]')) { removeAvatar(); return; }
        const memory = target.closest('[data-memory]');
        if (memory) openMemory(memory.dataset.memory);
      };
      root.addEventListener('click', clickHandler);
    },

    refresh() {
      load();
    },

    unmount() {
      if (pageRoot && clickHandler) pageRoot.removeEventListener('click', clickHandler);
      pageRoot = null;
      clickHandler = null;
    },
  };
})();
