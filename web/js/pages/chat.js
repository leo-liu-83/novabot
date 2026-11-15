/* 会话：消息流、工具卡片、确认门、流式模拟 */
(function () {
  const NB = window.NB;
  const U = NB.ui;
  const I = NB.i18n;
  NB.pages = NB.pages || {};

  let stream = null;
  let escHandler = null;
  // 右侧对话导航：滚动时按帧更新高亮，避免每个 scroll 事件都查一遍布局
  let navScrollEl = null;
  let navScrollHandler = null;
  let navUnpinHandler = null;
  let navRaf = 0;
  // 刚点过的提问：程序化滚动（尤其滚到底部无法再滚时）不能把它顶掉，
  // 只有用户再次手动滚动才恢复"跟随视口"的高亮
  let navPinned = null;
  // 没有会话时先给一个只存在于前端的草稿：发送首条消息时才落库建会话。
  // 存在这里而不是 mock.sessions，是为了刷新/切换页面都不会假装已经有一条会话。
  let draft = null;

  function rich(text) {
    if (NB.markdown) return NB.markdown.render(text);
    // 词典/脚本缺失时不要让整条消息变成空白
    return U.esc(text).replace(/\n/g, '<br>');
  }

  function toolHTML(t) {
    const status = t.status || 'ok';
    // 中断/中止的调用没有返回值，不能写成“无输出”（那会被读成工具真的返回了空）
    const emptyText = status === 'aborted' ? I.t('（已中断）') : I.t('（无输出）');
    return '<details class="tool" data-status="' + status + '">' +
      '<summary><span class="tool-status"></span>' +
        '<span class="tool-name">' + U.esc(t.name) + '</span>' +
        '<span class="tool-arg">' + U.esc(t.arg || '') + '</span>' +
        '<span class="tool-chev">' + U.icon('chevron') + '</span></summary>' +
      '<div class="tool-detail"><div class="code">' + U.esc(t.detail || emptyText) + '</div></div>' +
    '</details>';
  }

  /* 确认卡的参数区：一条动作一段，参数只画一次。
     老事件只有一个 answer，前端曾同时拿它当 desc 与 args，同一段内容上下各出现
     一次、下面还套一个同样的框；这里对同值字段去重，结构化动作则各画各的。 */
  function interruptArgsHTML(it) {
    const actions = (Array.isArray(it.actions) ? it.actions : []).filter((a) => a && (a.tool || a.args));
    if (actions.length) {
      return actions.map((a) => {
        const args = String(a.args || '');
        const shown = args.length;
        return '<div class="it-action">' +
          (actions.length > 1 ? '<div class="it-tool">' + U.esc(a.tool) + ' · ask</div>' : '') +
          (args ? '<div class="code">' + U.esc(args) + '</div>' : '') +
          (a.truncated ? '<div class="it-trunc">' +
            U.esc(I.t('chat.argsTruncated', { shown: shown, total: Number(a.chars) || shown })) +
            '</div>' : '') +
          approvalHTML(a.approval) +
          '</div>';
      }).join('');
    }
    const args = it.args || '';
    const desc = it.desc || '';
    return '<div class="it-desc">' + U.esc(desc) + '</div>' +
      (args && args !== desc ? '<div class="code">' + U.esc(args) + '</div>' : '');
  }

  function approvalHTML(approval) {
    if (!approval) return '';
    if (approval.kind === 'path') {
      const sensitive = approval.sensitive ? '（' + I.t('chat.alwaysSensitive') + '）' : '';
      return '<div class="it-scope">' + U.icon('shield') +
        U.esc(I.t('chat.alwaysScopePath', { path: approval.path || '' })) + sensitive + '</div>';
    }
    if (approval.kind === 'command') {
      return '<div class="it-scope">' + U.icon('terminal') +
        U.esc(I.t('chat.alwaysScopeCommand', { command: approval.command || '' })) + '</div>';
    }
    if (approval.kind === 'tool') {
      const scope = approval.scope || '';
      return '<div class="it-scope">' + U.icon('pencil') +
        U.esc(I.t('chat.alwaysScopeTool', { scope })) + '</div>';
    }
    return '';
  }

  function interruptHTML(msg, mi) {
    const it = msg.interrupt;
    const resolved = it.resolved;
    const actions = (Array.isArray(it.actions) ? it.actions : []).filter((a) => a && a.tool);
    const desc = actions.length
      ? I.t(actions.length > 1 ? 'chat.approvalDescMany' : 'chat.approvalDesc',
        { tool: actions[0].tool, count: actions.length })
      : '';
    const tool = it.tool || (actions[0] ? actions[0].tool : '') ||
      ((/^([\w:.\-]+)\(/.exec(it.args || '') || [])[1] || '');
    const toolLabel = actions.length > 1 ? actions.length + ' tools' : tool;
    const stamp = resolved === 'approved' ? (I.locale() === 'en' ? 'Approved' : I.t('已批准')) :
      resolved === 'always' ? I.t('chat.alwaysDone') :
      resolved === 'rejected' ? (I.locale() === 'en' ? 'Rejected' : I.t('已拒绝')) :
        (I.locale() === 'en' ? 'Pending' : I.t('等待确认'));
    const hasAlways = actions.some((a) => a.approval);

    // 确认卡已处理完就折叠：会话里留下的是一行决定记录，需要复审再点开。
    // 待确认时必须展开，否则批准/拒绝按钮藏起来就等于卡死流程。
    const head = U.icon(resolved ? 'check' : 'shield') +
      '<span>' + (resolved ? 'TOOL DECISION' : 'APPROVAL REQUIRED') + '</span>' +
      (resolved && toolLabel ? '<span class="it-what">' + U.esc(toolLabel) + '</span>' : '') +
      '<span class="stamp">' + stamp + '</span>' +
      (resolved ? '<span class="interrupt-chev">' + U.icon('chevron') + '</span>' : '');

    const body = '<div class="interrupt-body">' +
      '<div class="it-title">' + U.esc(I.t(it.title)) + '</div>' +
      (desc ? '<div class="it-desc">' + U.esc(desc) + '</div>' : '') +
      interruptArgsHTML(it) +
      (resolved ? '' :
        '<div class="interrupt-actions">' +
          '<button type="button" class="btn signal sm" data-approve="' + mi + '">' + U.icon('check') + I.t('批准并执行</button>') +
          '<button type="button" class="btn ghost sm" data-reject="' + mi + '">' + U.icon('x') + I.t('拒绝</button>') +
          (hasAlways ? '<button type="button" class="btn ghost sm" data-always="' + mi + '">' +
            U.icon('check') + I.t('chat.always') + '</button>' : '') +
          '<span class="hint">' + U.esc(toolLabel) + ' · ask</span>' +
        '</div>') +
    '</div>';

    if (resolved) {
      return '<details class="interrupt resolved" data-interrupt="' + mi + '">' +
        '<summary class="interrupt-head">' + head + '</summary>' + body +
      '</details>';
    }
    return '<div class="interrupt" data-interrupt="' + mi + '">' +
      '<div class="interrupt-head">' + head + '</div>' + body +
    '</div>';
  }

  function tokShort(n) {
    const value = Number(n || 0);
    return value >= 10000 ? (value / 1000).toFixed(1) + 'k' : String(Math.round(value));
  }

  function secsShort(value) {
    const seconds = Math.max(0, Number(value || 0));
    if (seconds < 60) return seconds.toFixed(1) + 's';
    return Math.floor(seconds / 60) + 'm ' + String(Math.floor(seconds % 60)).padStart(2, '0') + 's';
  }

  function turnTotalHTML(usage) {
    if (!usage || (!usage.input && !usage.output)) return '';
    return '<div class="turn-total">' + U.esc(I.t('chat.turnTotal', {
      input: tokShort(usage.input), output: tokShort(usage.output), secs: secsShort(usage.elapsed),
    })) + '</div>';
  }

  function compactHTML(msg) {
    const st = msg.meta || {};
    const title = I.t('chat.compactedTitle', {
      n: st.messages || 0, before: tokShort(st.before), after: tokShort(st.after),
    });
    const summary = st.summary || I.t('chat.compactedEmpty');
    return '<details class="notice compact-notice">' +
      '<summary>' + U.icon('alert') + '<span>' + U.esc(title) + '</span>' + U.icon('chevron') + '</summary>' +
      '<div class="compact-body">' + U.esc(summary) + '</div>' +
    '</details>';
  }

  /* 一条助手消息内部按节点的发生顺序排列：思考 → 工具 → 思考 → 回答。
     后端 chunk 本身是有序的，思考不能统一塞进一个缓冲区，否则工具执行后的
     思考会被拼到工具执行前。 */
  function ensureBlocks(msg) {
    if (!msg.blocks) {
      const blocks = [];
      if (msg.think) blocks.push({ kind: 'think', text: msg.think, running: false });
      (msg.tools || []).forEach((tool) => blocks.push({ kind: 'tool', tool }));
      if (msg.content) blocks.push({ kind: 'answer', text: msg.content });
      msg.blocks = blocks;
    }
    return msg.blocks;
  }

  function lastBlock(msg, kind) {
    const blocks = ensureBlocks(msg);
    for (let i = blocks.length - 1; i >= 0; i -= 1) {
      if (blocks[i].kind === kind) return { block: blocks[i], index: i };
    }
    return null;
  }

  /* 参数还在生成的工具卡：进度事件只更新它，不能为每块增量重绘整条消息。
     并行调用时按 index 命中自己那张卡，避免把进度写到别人的摘要行里。 */
  function lastRunningTool(msg, index) {
    const blocks = ensureBlocks(msg);
    let fallback = null;
    for (let i = blocks.length - 1; i >= 0; i -= 1) {
      const block = blocks[i];
      if (block.kind !== 'tool' || !block.tool || block.tool.status !== 'running') continue;
      if (index == null || block.tool.index === index) return { block, index: i, tool: block.tool };
      if (!fallback) fallback = { block, index: i, tool: block.tool };
    }
    return fallback;
  }

  function appendAnswer(msg, text) {
    const blocks = ensureBlocks(msg);
    let block = blocks[blocks.length - 1];
    if (!block || block.kind !== 'answer') {
      block = { kind: 'answer', text: '' };
      blocks.push(block);
    }
    block.text += text;
    msg.content = (msg.content || '') + text;
  }

  /* 同一个 call 段里连续到达的思考合并；跨 call 或跨工具/回答就另起一块。 */
  function appendThink(msg, text, call) {
    const blocks = ensureBlocks(msg);
    let block = blocks[blocks.length - 1];
    msg.think = (msg.think || '') + text;
    if (!block || block.kind !== 'think' || block.call !== call) {
      blocks.push({ kind: 'think', text, call, running: true });
      return true;
    }
    block.text += text;
    return false;
  }

  function stopThinking(msg) {
    let changed = false;
    ensureBlocks(msg).forEach((block) => {
      if (block.kind === 'think' && block.running) { block.running = false; changed = true; }
    });
    return changed;
  }

  function thinkHTML(block, index, streaming) {
    const running = streaming && !!block.running;
    return '<details class="think' + (running ? ' running' : '') + '" data-block="' + index + '">' +
      '<summary>' + U.icon('chevron') +
        '<span class="think-label">' + U.esc(I.t('chat.thinking')) + '</span>' +
        (running ? '<span class="spinner think-spin" role="status" aria-label="' +
          U.esc(I.t('chat.thinking')) + '"></span>' : '') +
      '</summary>' +
      '<div class="think-body">' + U.esc(block.text || '') + '</div></details>';
  }

  /* 只看一条助手消息的内容块。抬头（NOVA · 模型 · 时间）由整轮渲染，
     流式重绘替换这个节点的 innerHTML 时不会连带把抬头/头像刷掉。 */
  function assistantHTML(msg, session) {
    const streaming = !!msg.streaming;
    const blocks = ensureBlocks(msg);
    const last = blocks[blocks.length - 1];
    const parts = blocks.map((block, index) => {
      if (block.kind === 'think') return thinkHTML(block, index, streaming);
      if (block.kind === 'tool') {
        return '<div class="blk" data-block="' + index + '">' + toolHTML(block.tool) + '</div>';
      }
      return '<div class="body" data-block="' + index + '">' + rich(block.text || '') +
        (streaming && block === last ? '<span class="caret-blink"></span>' : '') + '</div>';
    }).join('');
    return parts + (streaming ? '' : turnTotalHTML(msg.turnUsage));
  }

  function turnHeadHTML(msg, session) {
    return '<div class="turn-head"><b>NOVA</b><span>·</span><span>' + U.esc(NB.mock.model(session.model).name) + '</span>' +
      '<span class="time">' + U.fmtClock(msg.ts) + '</span></div>';
  }

  function userRoleHTML(msg) {
    return '<div class="msg-role user-role"><b>YOU</b><span>·</span><span>' + U.esc(I.t('chat.user')) + '</span>' +
      '<span class="time">' + U.fmtClock(msg.ts) + '</span></div>';
  }

  function faceHTML(kind) {
    const me = NB.mock.me();
    const avatar = kind === 'assistant' ? me.agentAvatar : me.avatar;
    const name = kind === 'assistant' ? 'N' : me.name;
    return '<span class="msg-face" aria-hidden="true"><span class="avatar">' +
      U.avatarInner(name, avatar) + '</span></span>';
  }

  function userHTML(msg, mi) {
    return '<div class="msg user" data-mi="' + mi + '">' +
      '<div class="bubble">' + U.esc(msg.content) + '</div>' +
      ((msg.attachments || []).length ? '<div class="web-attachments">' + msg.attachments.map((a) =>
        '<span class="web-attachment">' + U.icon(a.kind === 'image' ? 'image' : 'file') + U.esc(a.name) + '</span>'
      ).join('') + '</div>' : '') +
      // 整轮没有 assistant 正文时本轮统计挂在这条用户行上，不能只在 assistant 行显示
      turnTotalHTML(msg.turnUsage) +
    '</div>';
  }

  /* 一条非用户消息的内容：助手正文/思考/工具、确认卡、压缩与系统提示。
     它们都属于同一轮，头像与抬头由外层统一给一次。 */
  function partHTML(msg, mi, session) {
    if (msg.role === 'interrupt') return interruptHTML(msg, mi);
    if (msg.role === 'compact') return compactHTML(msg);
    if (msg.role === 'notice') {
      return '<div class="notice">' + U.icon('alert') + '<span>' + U.esc(msg.content) + '</span></div>';
    }
    return '<div class="msg assistant" data-mi="' + mi + '">' + assistantHTML(msg, session) + '</div>';
  }

  /* 一轮 = 一条用户消息 + 其后所有助手/工具/确认消息，直到下一条用户消息。
     真实会话一轮会落多行（每次模型调用一行），整轮只画一个 NovaBot 头像。 */
  function turnGroups(messages) {
    const groups = [];
    let current = null;
    (messages || []).forEach((msg, mi) => {
      if (msg.role === 'user') {
        current = { user: { msg, mi }, parts: [] };
        groups.push(current);
        return;
      }
      const item = { msg, mi };
      if (current) {
        current.parts.push(item);
        return;
      }
      // 用户消息之前的条目（上下文压缩、系统提示）不属于任何一轮，不挂头像
      const last = groups[groups.length - 1];
      if (last && !last.user) last.parts.push(item);
      else groups.push({ user: null, parts: [item] });
    });
    return groups;
  }

  function turnHTML(group, session) {
    if (!group.user) {
      return '<div class="turn turn-system">' +
        group.parts.map((p) => partHTML(p.msg, p.mi, session)).join('') +
      '</div>';
    }
    let html = userRoleHTML(group.user.msg) +
      '<div class="msg-row user">' + userHTML(group.user.msg, group.user.mi) +
        faceHTML('user') + '</div>';
    if (group.parts.length) {
      const head = group.parts.find((p) => p.msg.role === 'assistant') || group.parts[0];
      html += turnHeadHTML(head.msg, session) +
        '<div class="msg-row assistant">' + faceHTML('assistant') +
          '<div class="turn-content">' +
            group.parts.map((p) => partHTML(p.msg, p.mi, session)).join('') +
          '</div>' +
        '</div>';
    }
    return '<div class="turn">' + html + '</div>';
  }

  /* 右侧对话导航：只列用户提问。
     多轮会话里不论滚到哪都找得回某一句问题；只有一轮时没有导航价值。 */
  function userTurns(session) {
    const turns = [];
    (session.messages || []).forEach((m, mi) => {
      if (m.role !== 'user') return;
      const text = String(m.content || '').replace(/\s+/g, ' ').trim();
      const attachments = (m.attachments || []).length;
      turns.push({
        mi,
        label: text || I.t(attachments ? 'chat.navAttachmentOnly' : 'chat.navEmpty'),
      });
    });
    return turns;
  }

  function navHTML(session) {
    const turns = userTurns(session);
    if (turns.length < 2) return '';
    // 最多展示 10 个刻度；更多时导航与展开面板都只占 10 行，在面板内滚动
    const rows = Math.min(turns.length, 10);
    return '<nav class="chat-nav" id="chat-nav" style="--nav-rows:' + rows + '" aria-label="' + U.esc(I.t('chat.nav')) + '">' +
      '<div class="chat-nav-list">' +
        turns.map((t) =>
          '<button type="button" class="chat-nav-item" data-jump="' + t.mi + '" title="' + U.esc(t.label) + '">' +
            '<span class="chat-nav-label">' + U.esc(t.label) + '</span>' +
            '<span class="chat-nav-dash" aria-hidden="true"></span>' +
          '</button>'
        ).join('') +
      '</div>' +
    '</nav>';
  }

  function messagesHTML(session) {
    if (!session.messages.length) {
      return '<div class="chat-empty"><div class="chat-empty-inner">' +
        '<div class="mark" aria-hidden="true">' + U.avatarInner('N', NB.mock.me().agentAvatar) + '</div>' +
        '<h3>' + U.esc(I.t('chat.emptyTitle')) + '</h3>' +
        '<p>' + U.esc(I.t('chat.emptyDescription')) + '</p>' +
      '</div></div>';
    }
    return (session._hasMore ? '<div class="chat-load-earlier"><button type="button" class="chat-load-earlier-text" data-load-earlier>' +
      U.esc(I.t('chat.loadEarlier')) + '</button></div>' : '') +
      '<div class="chat-date">' + U.fmtDate(session.messages[0].ts) + '</div>' +
      turnGroups(session.messages).map((g) => turnHTML(g, session)).join('');
  }

  function pendingIndex(session) {
    for (let i = 0; i < session.messages.length; i++) {
      const m = session.messages[i];
      if (m.role === 'interrupt' && m.interrupt && !m.interrupt.resolved) return i;
    }
    return -1;
  }

  function makeDraft() {
    return {
      id: null, title: I.t('chat.new'), model: NB.app.defaultModel || null,
      thinking: NB.mock.ui.defaultThinking, level: NB.mock.ui.defaultLevel,
      projectId: null, projectName: null, projectPath: null,
      updatedAt: new Date().toISOString(), tokensIn: 0, tokensOut: 0,
      contextTokens: 0, contextLimit: 0, messages: [], uploads: [],
      _loaded: true, _hasMore: false, _beforeId: null, _draft: true,
    };
  }

  function discardDraft() {
    draft = null;
  }

  function current() {
    const m = NB.mock;
    if (draft) return draft;
    let s = m.session(m.ui.chatSession);
    if (!s) { s = m.sessions[0]; if (s) m.ui.chatSession = s.id; }
    if (s && !s._loaded && !s._loading) loadMessages(s, null, false);
    if (s) return s;

    // 已登录但一条会话都没有：直接进入可输入的新会话界面，不建库。
    const role = NB.app.user && NB.app.user.role;
    if (!role || role === 'anon' || role === 'setup') return null;
    draft = makeDraft();
    return draft;
  }

  function mapMessage(row) {
    const ts = new Date((row.created_at || 0) * 1000).toISOString();
    if (row.role === 'user') {
      let attachments = Array.isArray(row.attachments) ? row.attachments : [];
      if (!Array.isArray(row.attachments)) {
        try { attachments = row.attachments ? JSON.parse(row.attachments) : []; } catch (e) { /* legacy/bad row */ }
      }
      // 整轮没有 assistant 正文时（只调工具就被确认/取消打断）本轮用量挂在用户行上：
      // 不恢复它，用量趋势里能看到这笔账、会话页却看不到，两边对不上
      let userMeta = {};
      try { userMeta = row.meta ? (typeof row.meta === 'string' ? JSON.parse(row.meta) : row.meta) : {}; }
      catch (e) { userMeta = {}; }
      return { role: 'user', ts, content: row.content || '', attachments,
        turnUsage: userMeta.turn || null };
    }
    if (row.role === 'tool') {
      // 工具行的 content 是 TUI 的一行摘要（name(args)）；卡片要的
      // 工具名/参数/结果在 meta 里，老数据没有 meta 时退回 content。
      let toolMeta = {};
      try { toolMeta = row.meta ? (typeof row.meta === 'string' ? JSON.parse(row.meta) : row.meta) : {}; }
      catch (e) { toolMeta = {}; }
      return { role: 'assistant', ts, think: '', content: '',
        tools: [{
          name: toolMeta.tool || 'tool',
          arg: typeof toolMeta.args === 'string' ? toolMeta.args : (row.content || ''),
          status: row.think === 'fail' ? 'error' : 'ok',
          detail: toolMeta.output || '',
        }] };
    }
    if (row.role === 'compact') {
      let meta = {};
      try { meta = row.meta ? (typeof row.meta === 'string' ? JSON.parse(row.meta) : row.meta) : {}; }
      catch (e) { meta = {}; }
      return { role: 'compact', ts, content: row.content || '', meta };
    }
    // 每轮 token 统计随 assistant 行落库（meta.turn）：不恢复它，刷新后所有历史轮次的
    // 「本轮 in/out」就都消失了，只剩最后一条还在内存里
    let meta = {};
    try { meta = row.meta ? (typeof row.meta === 'string' ? JSON.parse(row.meta) : row.meta) : {}; }
    catch (e) { meta = {}; }
    return { role: 'assistant', ts, think: row.think || '', content: row.content || '', tools: [],
      turnUsage: meta.turn || null };
  }

  async function loadMessages(session, beforeId, prepend) {
    if (!session || session._loading) return;
    session._loading = true;
    const sc = document.getElementById('chat-scroll');
    const oldHeight = sc ? sc.scrollHeight : 0;
    try {
      const page = await NB.api.messages(session.id, 30, beforeId || null);
      const rows = (page.messages || []).map(mapMessage);
      session.messages = prepend ? rows.concat(session.messages || []) : rows;
      session._hasMore = !!page.has_more;
      session._beforeId = page.next_before_id || null;
      session._loaded = !prepend || session._loaded;
      session._loading = false;
      if (NB.pages.chat.current() && NB.pages.chat.current().id === session.id) {
        if (prepend) {
          renderThread(session);
          const next = document.getElementById('chat-scroll');
          if (next) next.scrollTop = next.scrollHeight - oldHeight;
          syncNavActive();
          syncJumpLatest();
        } else {
          NB.app.render();
        }
      }
    } catch (error) {
      session._loading = false;
      U.toast(error.message || I.t('session.loadFailed'), 'bad');
    }
  }

  function sessionListHTML() {
    const m = NB.mock;
    const fs = m.ui.chatFilter.trim().toLowerCase();
    const list = m.sessions.filter((s) => !fs || s.title.toLowerCase().includes(fs));
    if (!list.length) {
      const empty = m.sessions.length
        ? U.empty('search', I.t('没有匹配的会话'), I.t('换个关键词，或新建一个。'))
        : U.empty('chat', I.t('chat.noSessions'), I.t('chat.emptySessionsHint'));
      return '<div style="padding:20px 10px">' + empty + '</div>';
    }
    return list.map((s) =>
      '<div class="chat-session" data-session="' + U.esc(s.id) + '" role="button" tabindex="0" aria-current="' + (s.id === m.ui.chatSession) + '" data-title="' + U.esc(I.t(s.title)) + '">' +
        '<div class="cs-title">' + U.esc(I.t(s.title)) + '</div>' +
        '<div class="cs-meta"><span>' + U.esc(m.model(s.model).name) + '</span><span class="dot-sep">·</span><span>' + U.fmtTime(s.updatedAt) + '</span>' +
          (s.projectName ? '<span class="dot-sep">·</span><span class="cs-project">' + U.icon('layers') + U.esc(s.projectName) + '</span>' : '') +
          (pendingIndex(s) >= 0 ? '<span class="dot warn" style="width:5px;height:5px;border-radius:50%;background:var(--warn)"></span>' : '') +
        '</div>' +
        '<div class="cs-actions">' +
          '<button type="button" class="icon-btn" data-rename="' + U.esc(s.id) + I.t('" title="重命名">') + U.icon('pencil') + '</button>' +
          '<button type="button" class="icon-btn danger" data-del="' + U.esc(s.id) + I.t('" title="删除">') + U.icon('trash') + '</button>' +
        '</div>' +
      '</div>'
    ).join('');
  }

  function speedText(speed) {
    const value = Number(speed || 0);
    return value > 0 ? value.toFixed(1) + ' t/s' : '';
  }

  /* 项目控件跟脚注右侧文案同款：纯图标 + 文字，名字后面带路径。
     只有项目名可点击（打开项目菜单）；路径是纯文字，和名字之间用居中的点分隔。
     没有项目权限时整个控件都不渲染，避免出现一个点不开的菜单。 */
  function projectBadge(session) {
    if (!session) return '';
    if (!(NB.app.canProjects && NB.app.canProjects())) return '';
    const path = session.projectPath || '';
    if (session.projectName) {
      const title = I.t('chat.projectBadge') + (path ? ' · ' + path : '');
      const name = '<span class="composer-project-name">' + U.esc(session.projectName) + '</span>';
      const label = '<button type="button" class="composer-project-btn" data-chat-project title="' +
        U.esc(title) + '">' + U.icon('layers') + name + '</button>';
      return label + (path
        ? '<span class="composer-project-sep" aria-hidden="true">·</span>' +
          '<span class="composer-project-path" title="' + U.esc(path) + '">' +
          U.esc(path) + '</span>'
        : '');
    }
    return '<button type="button" class="composer-project-btn" data-chat-project title="' +
      U.esc(I.t('chat.chooseProject')) + '">' + U.icon('layers') +
      '<span>' + U.esc(I.t('chat.noProject')) + '</span></button>';
  }

  function headHTML(session) {
    const m = NB.mock;
    const p = pendingIndex(session);
    const ctx = session.contextTokens || 0;
    const limit = session.contextLimit || 0;
    // 与 TUI 头部一致：只给已用量 + 百分比，不再展示总窗口数字
    const ctxText = ctx
      ? tokShort(ctx) + (limit > 0 ? ' (' + Math.round((ctx / limit) * 100) + '%)' : '')
      : '';
    const subtitle = session.id
      ? U.esc(session.id) + ' · ' + U.fmtTime(session.updatedAt)
      : U.esc(I.t('chat.draftHint'));
    const model = NB.mock.model(session.model);
    const thinking = session.thinking == null ? 'auto' : session.thinking ? 'on' : 'off';
    // 与 TUI 一致：进度事件给实时估算，usage 到达后用实测值覆盖；0 值不渲染空 chip
    const speed = speedText(session.speed);
    const actions = session.id
      ? '<button type="button" class="icon-btn" data-rename="' + U.esc(session.id) + I.t('" title="重命名">') + U.icon('pencil') + '</button>' +
        '<button type="button" class="icon-btn danger" data-del="' + U.esc(session.id) + I.t('" title="删除">') + U.icon('trash') + '</button>'
      : '';
    return '<header class="chat-head">' +
      I.t('<button type="button" class="icon-btn chat-side-toggle" id="chat-side-toggle" aria-label="会话列表" style="display:none">') + U.icon('menu') + '</button>' +
      '<div class="ch-title"><b data-title-text>' + U.esc(I.t(session.title)) + '</b>' +
        '<span>' + subtitle + (p >= 0 ? I.t(' · 等待确认') : '') + '</span></div>' +
      '<div class="ch-tools">' +
        (ctxText ? '<span class="chip chip-optional" title="' + U.esc(I.t('chat.contextUsage')) + '">ctx <b>' + ctxText + '</b></span>' : '') +
        (speed ? '<span class="chip chip-optional" data-speed-chip title="' + U.esc(I.t('chat.speedUsage')) + '">' +
          U.icon('pulse') + '<b>' + speed + '</b></span>' : '') +
        '<button type="button" class="chip" data-chat-model title="' + U.esc(I.t('选择模型')) + '">' +
          U.icon('cpu') + '<b>' + U.esc(model.name) + '</b><span class="caret">▾</span></button>' +
        '<button type="button" class="chip" data-chat-thinking title="' + U.esc(I.t('切换思考')) + '">' +
          U.icon('brain') + 'think <b>' + thinking + '</b></button>' +
        '<button type="button" class="chip" data-chat-level title="' + U.esc(
          session.projectName && session.level === 'T4'
            ? I.t('chat.projectOnlyHint') : I.t('web.levelManaged')) + '">' +
          U.icon('terminal') + '<b>' + U.esc(
            session.projectName && session.level === 'T4'
              ? 'T4 · ' + I.t('chat.projectOnly') : session.level) + '</b></button>' +
        actions +
      '</div>' +
    '</header>';
  }

  function uploadText(progress) {
    if (!progress || !progress.total) return I.t('chat.uploading');
    return I.t('chat.uploadingFile', {
      index: progress.index || 0, total: progress.total,
      name: progress.name || '', pct: progress.pct || 0,
    });
  }

  /* 进度事件每秒几十次：只改文本和进度条，不能重绘整个会话（会丢焦点/滚动位置）。 */
  function syncUploadProgress(session) {
    const el = document.querySelector('[data-upload-progress]');
    if (!el) return;
    const text = el.querySelector('.upload-progress-text');
    if (text) text.textContent = uploadText(session.uploadProgress);
    const bar = el.querySelector('.meter span');
    if (bar) bar.style.width = (session.uploadProgress ? session.uploadProgress.pct || 0 : 0) + '%';
  }

  function composerHTML(session) {
    const p = pendingIndex(session);
    const uploads = session.uploads || [];
    const busy = !!stream || p >= 0 || !!session.uploading;
    const progress = session.uploading ? session.uploadProgress : null;
    return '<div class="composer"><div class="composer-inner">' +
      (uploads.length ? '<div class="web-upload-list">' + uploads.map((a, i) =>
        '<span class="web-attachment">' + U.icon(a.kind === 'image' ? 'image' : 'file') + U.esc(a.name) +
        ' <button type="button" data-remove-upload="' + i + '" aria-label="' + U.esc(I.t('common.remove')) + '">×</button></span>'
      ).join('') + '</div>' : '') +
      (session.uploading ? '<div class="upload-progress" data-upload-progress>' +
        '<div class="upload-progress-text">' + U.esc(uploadText(progress)) + '</div>' +
        (progress ? '<div class="meter thin"><span style="width:' + (progress.pct || 0) + '%"></span></div>' : '') +
        '</div>' : '') +
      '<div class="composer-box">' +
        '<textarea id="chat-input" rows="1" placeholder="' +
          (p >= 0 ? I.t('chat.approvalPlaceholder') : I.t('chat.composerPlaceholder')) + '"' +
          (busy ? ' disabled' : '') + '></textarea>' +
        '<input type="file" id="attachment-input" multiple hidden>' +
        '<button type="button" class="send-btn attach" id="attach-btn" title="' + U.esc(I.t('chat.attach')) + '"' +
          (busy ? ' disabled' : '') + '>' + U.icon('paperclip') + '</button>' +
        (stream
           ? '<button type="button" class="send-btn stop" id="send-btn" data-action="stop" title="' + U.esc(I.t('chat.stop')) + '">' + U.icon('stop') + '</button>'
           : '<button type="button" class="send-btn" id="send-btn" title="' + U.esc(I.t('chat.send')) + '">' + U.icon('send') + '</button>') +
      '</div>' +
      '<div class="composer-foot"><span class="composer-project">' + projectBadge(session) + '</span>' +
        '<span>' + (p >= 0 ? I.t('chat.approvalPaused') : busy ? I.t('common.loading') : I.t('chat.levelLimited', { level: U.esc(session.level) })) + '</span></div>' +
    '</div></div>';
  }

  function replyFor(text) {
    const t = String(text);
    if (/技能|skill|安全|审查/i.test(t)) {
      return {
        think: '先查这个技能的审查结论，再决定要不要展开 findings。不要替用户下“没问题”的结论。',
        tool: { name: 'skill_inspect', arg: 'docx-tools', detail: 'released · 0 findings · 扫描于 2 天前' },
        content: '`docx-tools` 当前是 **released**，最近一次扫描没有 findings。\n\n它只有 `SKILL.md` 和两个 Python 脚本，脚本里没有网络请求，也没有读取环境变量。\n\n注意：这是共享层技能，你只能查看结论；要重新扫描或放行，需要管理员操作。',
      };
    }
    if (/搜索|搜一下|联网|网页|web/i.test(t)) {
      return {
        think: '先确认配额和 provider 顺序，再给出结果。抓取正文属于读操作，但外网地址仍要过 allowlist。',
        tool: { name: 'web_search', arg: 'FastAPI SSE best practices', detail: '3 results · via Exa · 0.8s' },
        content: '搜到 3 条比较可靠的结果：\n\n1. **FastAPI 官方文档**：用 `StreamingResponse` 配 `text/event-stream`，注意关闭代理缓冲。\n2. **Starlette 讨论**：`EventSourceResponse` 需要处理客户端断连，否则协程会悬挂。\n3. **实践文章**：心跳事件建议 15–30 秒一次，事件 id 用于断线续传。\n\n要我把第三条的正文抓下来，整理成实现清单吗？抓取会按当前 `ask` 策略先请求确认。',
      };
    }
    if (/权限|档位|工具|level/i.test(t)) {
      return {
        think: '用户要的是当前生效的边界，不是完整权限表。先给结论，再给一条可操作建议。',
        tool: { name: 'permission_report', arg: 'user=bob', detail: 'ceiling=T2 · session=T2 · mcp=3 servers' },
        content: '你当前的天花板是 **T2**，会话也在 T2。也就是说可以读写工作区、执行普通命令，但不能装包或改系统。\n\nMCP 已授权 2 个 server：`docs`（ask）和 `github`（allow，但写工具单独设为 ask）。\n\n如果想跑测试或装依赖，需要管理员把天花板提到 T3。',
      };
    }
    return {
      think: '先把问题拆成可验证的步骤，再给一个最小行动。不要一次做太多。',
      tool: { name: 'workspace_scan', arg: 'cwd=. · depth=2', detail: '42 files · 6 changed · 0 conflicts' },
      content: '收到。我建议先做最小的一步：\n\n1. 确认目标和验收标准；\n2. 只读检查相关文件，列出改动面；\n3. 写一个可回滚的小改动，跑测试；\n4. 通过后再扩大范围。\n\n当前工作区有 6 个未提交改动。要我先读出 diff，还是直接说你关心的那个文件？',
    };
  }

  function scrollBottom() {
    const el = document.getElementById('chat-scroll');
    if (el) el.scrollTop = el.scrollHeight;
    syncJumpLatest();
  }

  /* “跳到最新”：最新一条整块都掉到视口下方时给一个向下箭头。
     用“整块不可见”而不是常见的“离底部超过 N px”——读长回答读到一半时
     最后一条仍看得见，不该催人回底部。 */
  function jumpLatestNeeded(lastTop, viewBottom) {
    return lastTop >= viewBottom;
  }

  function syncJumpLatest() {
    const sc = document.getElementById('chat-scroll');
    const btn = document.getElementById('chat-jump-latest');
    if (!sc || !btn) return;
    const inner = document.getElementById('chat-inner');
    const last = inner && inner.lastElementChild;
    const show = !!(last && sc.getBoundingClientRect &&
      jumpLatestNeeded(last.getBoundingClientRect().top, sc.getBoundingClientRect().bottom));
    btn.classList.toggle('show', show);
    btn.tabIndex = show ? 0 : -1;
  }

  function renderNav(s) {
    const slot = document.getElementById('chat-nav-slot');
    if (slot) slot.innerHTML = navHTML(s);
  }

  /* 点击导航条目：把对应提问滚到可视区顶部附近。
     剩下的滚动位置交给 CSS 的 scroll-behavior: smooth。 */
  function scrollToTurn(mi) {
    const sc = document.getElementById('chat-scroll');
    const target = document.querySelector('#chat-inner [data-mi="' + mi + '"]');
    if (!sc || !target) return;
    const top = target.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop - 12;
    sc.scrollTop = Math.max(0, top);
    syncNavActive(mi);
  }

  /* 纯函数：按每条提问在视口中的位置挑当前项。
     常规取"视口顶部最近的一条"；滚到底时应取最后一条可见提问，
     否则点击句尾那几条时会被误判成页面范围内的第一条。 */
  function pickActiveTurn(turns, viewTop, viewBottom, atBottom) {
    if (!turns.length) return null;
    const line = atBottom ? viewBottom - 8 : viewTop + 24;
    let active = turns[0].mi;
    turns.forEach((turn) => {
      if (turn.top <= line) active = turn.mi;
    });
    return active;
  }

  /* 高亮当前提问，让导航条与正文位置对得上。
     手动点击时用 forceMi 立即落点，并钉住它，直到用户自己滚动。 */
  function syncNavActive(forceMi) {
    const sc = document.getElementById('chat-scroll');
    const nav = document.getElementById('chat-nav');
    if (!sc || !nav) return;
    if (forceMi != null) navPinned = forceMi;
    let active = forceMi;
    if (active == null && navPinned != null) {
      const pinned = sc.querySelector('#chat-inner [data-mi="' + navPinned + '"]');
      if (pinned) {
        const box = sc.getBoundingClientRect();
        const rect = pinned.getBoundingClientRect();
        // 钉住的提问还在视口里就保持选中；滚出视口后恢复跟随
        if (rect.bottom > box.top + 4 && rect.top < box.bottom - 4) active = navPinned;
        else navPinned = null;
      } else {
        navPinned = null;
      }
    }
    if (active == null) {
      const anchors = sc.querySelectorAll('#chat-inner .msg.user[data-mi]');
      if (!anchors.length) return;
      const box = sc.getBoundingClientRect();
      const atBottom = sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 2;
      active = pickActiveTurn(
        Array.prototype.map.call(anchors, (el) => ({
          mi: Number(el.dataset.mi),
          top: el.getBoundingClientRect().top,
        })),
        box.top, box.bottom, atBottom,
      );
    }
    let current = null;
    nav.querySelectorAll('[data-jump]').forEach((item) => {
      const on = Number(item.dataset.jump) === active;
      item.setAttribute('aria-current', on ? 'true' : 'false');
      if (on) current = item;
    });
    // 超过 10 个提问时导航只显示一屏：把当前项滚进这 10 行里
    const list = nav.querySelector('.chat-nav-list');
    if (list && current) {
      const top = current.offsetTop;
      const bottom = top + current.offsetHeight;
      if (top < list.scrollTop) list.scrollTop = top;
      else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
    }
  }

  function paint() {
    const s = current();
    if (!s) return;
    const inner = document.getElementById('chat-inner');
    if (inner) inner.innerHTML = messagesHTML(s);
    renderNav(s);
    const comp = document.getElementById('composer-slot');
    if (comp) {
      comp.innerHTML = composerHTML(s);
      bindComposer();
    }
    scrollBottom();
    syncNavActive();
  }

  function renderThread(s) {
    const inner = document.getElementById('chat-inner');
    if (inner) inner.innerHTML = messagesHTML(s);
    renderNav(s);
    const comp = document.getElementById('composer-slot');
    if (comp) {
      comp.innerHTML = composerHTML(s);
      bindComposer();
    }
    const title = document.querySelector('[data-title-text]');
    if (title) title.textContent = I.t(s.title);
    const rowTitle = document.querySelector('.chat-session[data-session="' + s.id + '"] .cs-title');
    if (rowTitle) rowTitle.textContent = I.t(s.title);
    scrollBottom();
    syncNavActive();
  }

  /* 流式输出期间按帧合并重绘。
     后端每个模型 chunk 都发一个 answer 事件；如果每个事件都重建整条线程，
     复杂度会退化成 O(事件数 × 历史长度)，长会话直接卡死。
     这里只改正在流式的那条助手消息正文，历史消息与 composer 都不动。 */
  // 超过 30fps 对肉眼看流式文本没有收益，却会让长回答的 Markdown 重解析占满主线程。
  // 正文越长，单次重排越贵，所以按长度再降一档频率。
  const MIN_PAINT_MS = 32;

  function paintInterval(length) {
    if (length > 50000) return 120;
    if (length > 20000) return 64;
    return MIN_PAINT_MS;
  }

  function isNearBottom() {
    const el = document.getElementById('chat-scroll');
    if (!el) return false;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }

  function nextFrame(fn) {
    if (typeof requestAnimationFrame === 'function') return requestAnimationFrame(fn);
    return setTimeout(fn, 16);
  }

  function cancelFrame(handle) {
    if (handle == null) return;
    if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(handle);
    else clearTimeout(handle);
  }

  function createStreamPainter(s, assistant) {
    let raf = null;
    let timer = null;
    let lastPaint = 0;
    let stopped = false;
    // 一次帧内可能同时收到 think 与 answer；用标记累积，避免后到的覆盖先到的。
    let needBody = false;
    let needThink = false;
    let needArg = false;
    let argIndex = null;
    let needFull = false;

    function flush() {
      raf = null;
      timer = null;
      if (stopped) return;
      const index = s.messages.indexOf(assistant);
      const inner = index < 0 ? null : document.getElementById('chat-inner');
      if (!inner) {
        needBody = needThink = needArg = needFull = false;
        return;
      }
      lastPaint = Date.now();
      const stick = isNearBottom();
      const el = inner.querySelector('[data-mi="' + index + '"]');
      // 展开状态属于用户操作：结构变化重绘时要还原，不能因为来了一个工具事件就折叠
      const openBlocks = [];
      if (el && el.querySelectorAll) {
        const opened = el.querySelectorAll('.think[open]');
        for (let i = 0; i < opened.length; i += 1) {
          const node = opened[i];
          const block = node.getAttribute ? node.getAttribute('data-block') : node.dataset.block;
          if (block != null) openBlocks.push(block);
        }
      }
      if (!el || needFull) {
        // 节点不在（例如刚插入了确认卡片）或工具/结构变化：整条消息重绘
        if (!el) inner.innerHTML = messagesHTML(s);
        else {
          el.innerHTML = assistantHTML(assistant, s);
          if (openBlocks.length && el.querySelectorAll) {
            const thinks = el.querySelectorAll('.think');
            for (let i = 0; i < thinks.length; i += 1) {
              const node = thinks[i];
              const block = node.getAttribute ? node.getAttribute('data-block') : node.dataset.block;
              if (openBlocks.indexOf(block) >= 0) node.open = true;
            }
          }
        }
      } else {
        if (needThink) {
          const target = lastBlock(assistant, 'think');
          const box = target && el.querySelector('.think[data-block="' + target.index + '"] .think-body');
          if (box) box.textContent = target.block.text || '';
          else el.innerHTML = assistantHTML(assistant, s);
        }
        if (needBody) {
          const target = lastBlock(assistant, 'answer');
          const body = target && el.querySelector('.body[data-block="' + target.index + '"]');
          if (body) {
            body.innerHTML = rich(target.block.text || '') +
              (assistant.streaming ? '<span class="caret-blink"></span>' : '');
          } else el.innerHTML = assistantHTML(assistant, s);
        }
        if (needArg) {
          const running = lastRunningTool(assistant, argIndex);
          const arg = running && el.querySelector('.blk[data-block="' + running.index + '"] .tool-arg');
          if (arg) arg.textContent = running.tool.arg || '';
          else el.innerHTML = assistantHTML(assistant, s);
        }
      }
      needBody = needThink = needArg = needFull = false;
      argIndex = null;
      if (stick) scrollBottom();
      else syncJumpLatest();
    }

    function reset() {
      cancelFrame(raf);
      raf = null;
      if (timer != null) { clearTimeout(timer); timer = null; }
      needBody = needThink = needArg = needFull = false;
    }

    function schedule(next, index) {
      if (stopped) return;
      if (next === 'full') needFull = true;
      else if (next === 'think') needThink = true;
      else if (next === 'arg') { needArg = true; argIndex = index == null ? null : index; }
      else needBody = true;
      if (raf != null || timer != null) return;
      const wait = Math.max(0, paintInterval((assistant.content || '').length) - (Date.now() - lastPaint));
      if (wait > 0) {
        timer = setTimeout(() => { timer = null; raf = nextFrame(flush); }, wait);
      } else {
        raf = nextFrame(flush);
      }
    }

    return {
      body() { schedule('body'); },
      think() { schedule('think'); },
      arg(index) { schedule('arg', index); },
      full() { schedule('full'); },
      /* 结构性变化：插入确认卡片/通知后必须让线程顺序正确 */
      thread() {
        reset();
        renderThread(s);
      },
      stop() {
        stopped = true;
        reset();
      },
    };
  }

  async function consumeEvents(s, assistant, endpoint, payload, controller, restore = null) {
    let gateRejected = false;
    let restoreInput = null;
    const painter = createStreamPainter(s, assistant);
    try {
      await NB.api.stream(endpoint, payload, controller.signal, (event) => {
        if (event.session_id && event.session_id !== s.id) s.id = event.session_id;
        const now = new Date().toISOString();
        let structureChanged = false;
        switch (event.event) {
          case 'think': {
            // 新建一块思考才需要插入 <details>；同一段的后续增量只更新正文
            const created = appendThink(assistant, event.answer || '', event.call);
            if (created) painter.full();
            else painter.think();
            break;
          }
          case 'answer': {
            const thinkingDone = stopThinking(assistant);
            appendAnswer(assistant, event.answer || '');
            if (thinkingDone) painter.full();
            else painter.body();
            break;
          }
          case 'tool_call': {
            stopThinking(assistant);
            assistant.tools = assistant.tools || [];
            // index 用于把后续的参数进度对回同一张卡片（并行调用时会同时存在多张）
            const tool = {
              name: event.answer, arg: '', status: 'running', detail: '',
              index: (event.stats || {}).index,
            };
            assistant.tools.push(tool);
            ensureBlocks(assistant).push({ kind: 'tool', tool });
            painter.full();
            break;
          }
          /* 工具参数还在生成：只把进度写进正在跑的那张卡。
             write_file 的正文就是一个参数，前端拿不到正文，只拿到路径与已生成规模。 */
          case 'tool_args': {
            const stats = event.stats || {};
            const active = lastRunningTool(assistant, stats.index);
            if (!active) break;
            const head = typeof stats.arg === 'string' ? stats.arg : '';
            const size = stats.chars
              ? I.t('生成参数中') + ' ' + tokShort(stats.chars) + I.t('字符')
              : '';
            active.tool.arg = [head, size].filter(Boolean).join(' · ');
            painter.arg(stats.index);
            break;
          }
          case 'tool_end': {
            stopThinking(assistant);
            assistant.tools = assistant.tools || [];
            const stats = event.stats || {};
            const arg = typeof stats.args === 'string' ? stats.args : (event.answer || '');
            const detail = typeof stats.output === 'string' ? stats.output : '';
            const status = event.ok === false ? 'error' : 'ok';
            const active = [...assistant.tools].reverse().find((t) => t.status === 'running');
            if (active) { active.arg = arg; active.detail = detail; active.status = status; }
            else {
              const tool = { name: event.tool || 'tool', arg, detail, status };
              assistant.tools.push(tool);
              ensureBlocks(assistant).push({ kind: 'tool', tool });
            }
            painter.full();
            break;
          }
          case 'interrupt':
            stopThinking(assistant);
            assistant.streaming = false;
            // 待确认的调用还没执行：不能把 tool_call 留下的 running 卡片继续闪黄灯。
            // 恢复流里真正执行完会另发 tool_end，在新消息里落一张带结果的卡片；
            // 待确认的动作本身由下面的确认卡展示。
            assistant.tools = (assistant.tools || []).filter((t) => t.status !== 'running');
            assistant.blocks = ensureBlocks(assistant)
              .filter((b) => !(b.kind === 'tool' && b.tool && b.tool.status === 'running'));
            const stats = event.stats || {};
            const actions = Array.isArray(stats.actions) ? stats.actions : [];
            s.messages.push({
              role: 'interrupt', ts: now,
              // actions 有结构化参数就按动作逐条画；answer 只作为老事件/兜底的一行摘要
              interrupt: {
                tool: (actions[0] || {}).tool || '',
                title: I.t('chat.approvalRequired'), desc: event.answer || '',
                args: event.answer || '', actions,
                interrupt_id: event.interrupt_id, resolved: null,
              },
            });
            // 确认按钮必须立刻出现，合并到下一帧会让用户以为界面无响应
            structureChanged = true;
            break;
          case 'context':
            s.contextTokens = (event.stats || {}).tokens || 0;
            s.contextLimit = (event.stats || {}).limit || 0;
            syncHead(s);
            break;
          // 实时速度：只在生成中刷新，与 TUI 一样由 progress 给估算值
          case 'progress':
            s.speed = (event.stats || {}).tps || s.speed || 0;
            syncSpeed(s);
            break;
          case 'usage': {
            const usage = event.stats || {};
            // 一次模型调用结束：思考段的转圈要停；下一段思考会另起一块
            const thinkingDone = stopThinking(assistant);
            s.tokensIn = (s.tokensIn || 0) + (usage.input || 0);
            s.tokensOut = (s.tokensOut || 0) + (usage.output || 0);
            // 实测速度覆盖 progress 的估算值（TUI 在 _on_token_usage 里做同一件事）
            if (usage.tps) s.speed = usage.tps;
            assistant.turnUsage = assistant.turnUsage || { input: 0, output: 0, elapsed: 0 };
            assistant.turnUsage.input += usage.input || 0;
            assistant.turnUsage.output += usage.output || 0;
            assistant.turnUsage.elapsed += usage.elapsed || 0;
            // 头部要看到实测值：usage 每个模型调用一条，只改速度数字，不重建控件
            if (thinkingDone) painter.full();
            else syncSpeed(s);
            break;
          }
          case 'compact': {
            const compact = event.stats || {};
            if (compact.ok === false) break;  // 失败没有改动历史，提示由紧随其后的 error 事件负责
            s.messages.push({ role: 'compact', ts: now, content: event.answer || '', meta: compact });
            structureChanged = true;
            break;
          }
          case 'warning': {
            const warnCode = (event.stats || {}).code;
            const warnText = warnCode && I.has('error.' + warnCode)
              ? I.t('error.' + warnCode) : (event.answer || '');
            s.messages.push({ role: 'notice', ts: now, content: warnText });
            structureChanged = true;
            break;
          }
          case 'error': {
            // 只有系统错误事件带 stats.code；模型文本永远没有 code，保持原样
            const errCode = (event.stats || {}).code;
            const errText = errCode && I.has('error.' + errCode)
              ? I.t('error.' + errCode) : event.answer;
            if ((event.stats || {}).source === 'gate') {
              gateRejected = true;
              restoreInput = restore ? restore.query : payload.query;
              const ai = s.messages.indexOf(assistant);
              if (ai >= 0) s.messages.splice(ai - 1, 2);
              if (restore && restore.attachments) s.uploads = restore.attachments.slice();
              U.toast(errText, 'warn');
              structureChanged = true;
            } else {
              appendAnswer(assistant, '\n\n' + errText);
              painter.full();
            }
            break;
          }
        }
        s.updatedAt = now;
        if (structureChanged) painter.thread();
      });
    } catch (error) {
      if (!controller.signal.aborted) {
        appendAnswer(assistant, '\n\n' + (error.message || I.t('error.network')));
      }
    } finally {
      painter.stop();
      assistant.streaming = false;
      stopThinking(assistant);
      // 流结束还挂在 running 的工具说明这次没跑完（ESC / 网络中断 / 报错）。
      // 不能让它继续闪黄灯：标成 aborted，与“正在执行”和“已成功”都区分开。
      (assistant.tools || []).forEach((t) => {
        if (t.status === 'running') t.status = 'aborted';
      });
      s.updatedAt = new Date().toISOString();
      if (stream && stream.controller === controller) stream = null;
      // 收尾必须整条重绘：清除光标、补上 turn 统计、恢复输入框可用状态
      renderThread(s);
      if (restoreInput != null) {
        const input = document.getElementById('chat-input');
        if (input) { input.value = restoreInput; input.focus(); }
      }
      NB.app.reloadSessions().catch((e) => console.warn('Session refresh failed', e));
    }
    return !gateRejected;
  }

  async function discardCreatedSession(session) {
    try { await NB.api.deleteSession(session.id); } catch (e) { /* best effort */ }
    NB.mock.sessions = NB.mock.sessions.filter((x) => x.id !== session.id);
    if (NB.mock.ui.chatSession === session.id) NB.mock.ui.chatSession = null;
  }

  async function materializeUploads(session, uploads) {
    const resolved = [];
    const pending = uploads.filter((item) => item.pending && item.file);
    if (pending.length) {
      // 首条消息的附件也要有进度：文件大时 composer 不能像卡死一样毫无反应
      session.uploading = true;
      session.uploadProgress = { index: 1, total: pending.length, name: pending[0].name, pct: 0 };
      renderThread(session);
    }
    try {
      let index = 0;
      for (const item of uploads) {
        if (!item.pending || !item.file) { resolved.push(item); continue; }
        index += 1;
        session.uploadProgress = { index, total: pending.length, name: item.file.name, pct: 0 };
        syncUploadProgress(session);
        resolved.push(await NB.api.uploadAttachment(session.id, item.file, {
          onProgress: (ratio) => {
            if (!session.uploadProgress) return;
            session.uploadProgress.pct = Math.round(ratio * 100);
            syncUploadProgress(session);
          },
        }));
      }
      return resolved;
    } catch (error) {
      // 回滚由 send 统一做：这里再删一次会重复调用删除接口（第一次已删掉）。
      throw error;
    } finally {
      if (pending.length) {
        session.uploading = false;
        session.uploadProgress = null;
      }
    }
  }

  async function send(text) {
    const v = String(text || '').trim();
    if (stream) return;
    let s = current();
    if (!s) return;
    if (pendingIndex(s) >= 0) { U.toast(I.t('chat.resolveApprovalFirst'), 'warn'); return; }

    let uploads = (s.uploads || []).slice();
    if (!v && !uploads.length) return;

    const createdNow = !s.id;
    const draftLevel = createdNow ? s.level : null;
    // 草稿页选的模型/思考必须带进新建会话；否则后端只能回落到 agent.model
    const draftModel = createdNow ? s.model : null;
    const draftThinking = createdNow ? s.thinking : null;
    const draftProject = createdNow ? s.projectId : null;
    if (createdNow) {
      try {
        s = await NB.app.createSession(draftModel, draftThinking, draftProject);
        if (draftLevel && draftLevel !== NB.mock.ui.defaultLevel) {
          const applied = await NB.api.setSessionLevel(s.id, draftLevel);
          s.level = applied.level;
        }
        uploads = await materializeUploads(s, uploads);
        s.uploads = uploads.slice();
        draft = null;
      } catch (error) {
        if (createdNow && s.id) await discardCreatedSession(s);
        // 上传时 composer 已被置为 disabled 并显示进度；失败回滚后必须重绘，
        // 否则进度条会停在最后一个百分比、输入框一直不可用。
        NB.app.render();
        U.toast(error.message || I.t('error.network'), 'bad');
        return;
      }
    }

    const counts = {};
    const tokens = uploads.map((a) => {
      const kind = a.kind || 'file';
      counts[kind] = (counts[kind] || 0) + 1;
      const labelKind = ({ image: 'Image', video: 'Video', audio: 'Audio' })[kind] || 'File';
      return `[${labelKind} ${counts[kind]}]`;
    });
    const query = [v, ...tokens].filter(Boolean).join('\n');

    const now = new Date().toISOString();
    const userMessage = { role: 'user', ts: now, content: query, attachments: uploads };
    const assistant = { role: 'assistant', ts: now, think: '', content: '', streaming: true, tools: [], blocks: [] };
    s.messages.push(userMessage, assistant);
    s.uploads = [];
    if (!s.title || s.title === I.t('chat.new') || s.title === '新对话') {
      const title = v || (uploads[0] && uploads[0].name) || I.t('chat.new');
      s.title = title.length > 30 ? title.slice(0, 30) : title;
    }
    s.updatedAt = now;

    const controller = new AbortController();
    stream = { sessionId: s.id, controller, stop: () => controller.abort() };
    if (createdNow) NB.app.render();
    else renderThread(s);
    await consumeEvents(s, assistant, '/chat/completions', {
      query, session_id: s.id, model: s.model || undefined,
      enable_thinking: s.thinking, show_thinking: s.thinking !== false, streamable: true,
      attachments: uploads.map((a) => ({ id: a.id, name: a.name })),
    }, controller, { query: v, attachments: uploads });
  }

  function localKind(file) {
    const type = String((file && file.type) || '');
    if (type.startsWith('image/')) return 'image';
    if (type.startsWith('video/')) return 'video';
    if (type.startsWith('audio/')) return 'audio';
    return 'file';
  }

  async function uploadFiles(session, fileList) {
    const files = Array.from(fileList || []);
    if (!files.length || !session || session.uploading || stream) return;
    const limit = NB.app.attachmentLimits && NB.app.attachmentLimits.max_per_message;
    if (limit != null && (session.uploads || []).length + files.length > limit) {
      U.toast(I.t('chat.attachmentLimit', { limit }), 'warn');
      return;
    }
    if (!session.id) {
      // 草稿阶段先留在本地：上传接口需要会话 id，等首条消息发送时再一起上传。
      session.uploads = (session.uploads || []).concat(files.map((file) => ({
        id: null, name: file.name, size: file.size, kind: localKind(file), file, pending: true,
      })));
      renderThread(session);
      return;
    }
    session.uploading = true;
    session.uploadProgress = { index: 1, total: files.length, name: files[0].name, pct: 0 };
    renderThread(session);
    try {
      let index = 0;
      for (const file of files) {
        index += 1;
        session.uploadProgress = { index, total: files.length, name: file.name, pct: 0 };
        syncUploadProgress(session);
        const uploaded = await NB.api.uploadAttachment(session.id, file, {
          onProgress: (ratio) => {
            if (!session.uploadProgress) return;
            session.uploadProgress.pct = Math.round(ratio * 100);
            syncUploadProgress(session);
          },
        });
        session.uploads = session.uploads || [];
        session.uploads.push(uploaded);
      }
    } catch (error) {
      U.toast(error.message || I.t('chat.uploadFailed'), 'bad');
    } finally {
      session.uploading = false;
      session.uploadProgress = null;
      renderThread(session);
    }
  }

  async function removeUpload(session, index) {
    const item = (session.uploads || [])[index];
    if (!item || session.uploading) return;
    if (item.pending || !item.id) {
      session.uploads.splice(index, 1);
      renderThread(session);
      return;
    }
    try {
      await NB.api.deleteAttachment(session.id, item.id);
      session.uploads.splice(index, 1);
      renderThread(session);
    } catch (error) { U.toast(error.message || I.t('chat.uploadFailed'), 'bad'); }
  }

  function openRename(id) {
    if (stream) { U.toast(I.t('chat.stopBeforeRename'), 'warn'); return; }
    const s = NB.mock.session(id);
    if (!s) return;
    const m = U.modal({
      kicker: 'SESSION',
      title: I.t('chat.renameTitle'),
      body: '<div class="field"><label for="rn-input">' + I.t('chat.renameLabel') + '</label><input class="input" id="rn-input" maxlength="80" value="' + U.esc(I.t(s.title)) + '"></div>',
      footer: '<button type="button" class="btn ghost" data-cancel>' + I.t('common.cancel') + '</button><button type="button" class="btn signal" data-save>' + I.t('common.save') + '</button>',
      onMount: (wrap, close) => {
        const input = wrap.querySelector('#rn-input');
        const save = async () => {
          const v = input.value.trim();
          if (!v) { input.focus(); return; }
          try {
            const updated = await NB.api.renameSession(id, v);
            s.title = updated.title;
            close();
            NB.app.render();
            U.toast(I.t('chat.renamed'));
          } catch (error) { U.toast(error.message || I.t('error.network'), 'bad'); }
        };
        wrap.querySelector('[data-cancel]').onclick = close;
        wrap.querySelector('[data-save]').onclick = save;
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); save(); } });
        setTimeout(() => { input.focus(); input.select(); }, 50);
      },
    });
  }

  function removeSession(id) {
    if (stream) { U.toast(I.t('chat.stopBeforeDelete'), 'warn'); return; }
    const m = NB.mock;
    const s = m.session(id);
    if (!s) return;
    U.confirm({
      kicker: 'DELETE',
      title: I.t('chat.deleteTitle'),
      message: I.t('chat.deleteConfirm', { title: s.title }),
      okLabel: I.t('common.delete'),
      danger: true,
    }).then(async (ok) => {
      if (!ok) return;
      try {
        await NB.api.deleteSession(id);
        m.sessions = m.sessions.filter((x) => x.id !== id);
        if (m.ui.chatSession === id) {
          m.ui.chatSession = m.sessions[0] ? m.sessions[0].id : null;
          if (!m.sessions.length) draft = null;
        }
        NB.app.render();
        U.toast(I.t('session.deleted'));
      } catch (error) { U.toast(error.message || I.t('session.deleteFailed'), 'bad'); }
    });
  }

  async function resolveInterrupt(mi, decision) {
    // 兼容旧调用（布尔）：true=approve，false=reject
    if (decision === true) decision = 'approve';
    else if (decision === false) decision = 'reject';

    const s = current();
    if (!s) return;
    const msg = s.messages[mi];
    if (!msg || msg.role !== 'interrupt' || !msg.interrupt || msg.interrupt.resolved) return;
    msg.interrupt.resolved = decision === 'reject' ? 'rejected'
      : decision === 'always' ? 'always' : 'approved';
    const now = new Date().toISOString();
    const assistant = { role: 'assistant', ts: now, think: '', content: '', streaming: true, tools: [], blocks: [] };
    s.messages.push(assistant);
    const controller = new AbortController();
    stream = { sessionId: s.id, controller, stop: () => controller.abort() };
    renderThread(s);
    await consumeEvents(s, assistant, '/chat/resume', {
      session_id: s.id,
      decision,
      interrupt_id: msg.interrupt.interrupt_id,
      model: s.model || undefined,
      enable_thinking: s.thinking,
      show_thinking: s.thinking !== false,
    }, controller);
  }

  function bindSidebar(root) {
    const side = root.querySelector('#chat-side');
    const toggle = root.querySelector('#chat-side-toggle');
    const scrim = document.getElementById('scrim');
    if (toggle && side) {
      toggle.onclick = () => {
        side.classList.add('open');
        if (scrim) { scrim.hidden = false; scrim.onclick = close; }
      };
      const close = () => {
        side.classList.remove('open');
        if (scrim) { scrim.hidden = true; scrim.onclick = null; }
      };
      if (scrim) scrim.onclick = close;
    }

    const filter = root.querySelector('#chat-filter');
    if (filter) {
      filter.value = NB.mock.ui.chatFilter;
      filter.addEventListener('input', () => {
        NB.mock.ui.chatFilter = filter.value;
        const box = root.querySelector('#chat-sessions');
        if (box) box.innerHTML = sessionListHTML();
        bindSessionRows(root);
      });
    }
  }

  function bindSessionRows(root) {
    root.querySelectorAll('[data-session]').forEach((el) => {
      const go = () => {
        const id = el.dataset.session;
        if (stream) { U.toast(I.t('chat.stopBeforeSwitch'), 'warn'); return; }
        discardDraft();
        NB.mock.ui.chatSession = id;
        NB.app.render();
      };
      el.addEventListener('click', (e) => {
        if (e.target.closest('[data-rename]') || e.target.closest('[data-del]')) return;
        go();
      });
      el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
      });
    });
    root.querySelectorAll('[data-rename]').forEach((b) => {
      b.onclick = (e) => { e.stopPropagation(); openRename(b.dataset.rename); };
    });
    root.querySelectorAll('[data-del]').forEach((b) => {
      b.onclick = (e) => { e.stopPropagation(); removeSession(b.dataset.del); };
    });
  }

  function bindComposer() {
    const input = document.getElementById('chat-input');
    const btn = document.getElementById('send-btn');
    if (!input) return;
    const attach = document.getElementById('attach-btn');
    const fileInput = document.getElementById('attachment-input');
    const session = current();
    if (attach && fileInput) {
      attach.onclick = () => fileInput.click();
      fileInput.onchange = () => {
        if (session) uploadFiles(session, fileInput.files);
        fileInput.value = '';
      };
    }
    if (!btn) return;
    const projectChip = document.querySelector('#composer-slot [data-chat-project]');
    if (projectChip) {
      projectChip.onclick = () => {
        if (sessionBusy()) return;
        const s = current();
        if (s) openProjectMenu(projectChip, s);
      };
    }
    const auto = () => {
      input.style.height = 'auto';
      input.style.height = Math.min(180, input.scrollHeight) + 'px';
    };
    input.addEventListener('input', auto);
    input.addEventListener('paste', (e) => {
      const files = e.clipboardData && e.clipboardData.files;
      if (files && files.length) {
        e.preventDefault();
        if (session) uploadFiles(session, files);
      }
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send(input.value);
      }
    });
    btn.onclick = () => {
      if (btn.dataset.action === 'stop') {
        if (stream) stream.stop();
        U.toast(I.t('chat.stopGeneration'), 'warn');
      } else {
        send(input.value);
      }
    };
    const box = document.querySelector('.composer-box');
    if (box) {
      box.addEventListener('dragover', (e) => { e.preventDefault(); box.classList.add('drag-over'); });
      box.addEventListener('dragleave', () => box.classList.remove('drag-over'));
      box.addEventListener('drop', (e) => {
        e.preventDefault(); box.classList.remove('drag-over');
        if (session) uploadFiles(session, e.dataTransfer && e.dataTransfer.files);
      });
    }
    document.querySelectorAll('[data-remove-upload]').forEach((button) => {
      button.onclick = () => { if (session) removeUpload(session, Number(button.dataset.removeUpload)); };
    });
    setTimeout(() => { if (!input.disabled) input.focus(); }, 30);
  }

  function sessionBusy() {
    if (stream) { U.toast(I.t('生成中，先停止再切换'), 'warn'); return true; }
    return false;
  }

  function resetContext(session) {
    // 旧占用是旧窗口下量出来的：留着它会在小窗口模型上算出 >100% 的荒谬百分比
    session.contextTokens = 0;
    session.contextLimit = 0;
  }

  function modelGroups() {
    const groups = [];
    const byKey = {};
    NB.mock.models.forEach((m) => {
      const key = m.providerCode || m.provider || '';
      const name = m.provider || m.providerCode || I.t('未分组');
      if (!byKey[key]) {
        byKey[key] = { name, models: [] };
        groups.push(byKey[key]);
      }
      byKey[key].models.push(m);
    });
    return groups;
  }

  function openModelMenu(anchor, session) {
    const items = [];
    modelGroups().forEach((group) => {
      items.push({ group: group.name });
      group.models.forEach((m) => {
        items.push({
          label: m.name,
          hint: m.thinking ? 'thinking' : '',
          active: session.model === m.id,
          disabled: m.configured === false,
          onPick: () => {
            session.model = m.id;
            NB.mock.ui.defaultModel = m.id;
            // 窗口跟着模型变：旧占用快照不能跨模型沿用
            resetContext(session);
            U.toast(I.t('模型 → ') + m.name);
            syncHead(session);
          },
        });
      });
    });
    U.menu(anchor, items, { head: I.t('选择模型'), align: 'right', className: 'model-menu' });
  }

  function toggleThinking(session) {
    session.thinking = !session.thinking;
    NB.mock.ui.defaultThinking = session.thinking;
    syncHead(session);
  }

  async function applySessionLevel(level) {
    const target = current();
    if (!target) return;
    if (!target.id) {
      // 草稿还没有后端会话：先记在本会话的草稿上，首条消息发送前再落到服务端
      target.level = level;
      syncHead(target);
      U.toast(I.t('level.changed', { level }));
      return;
    }
    try {
      const result = await NB.api.setSessionLevel(
        target.id, level === NB.mock.ui.defaultLevel ? null : level,
      );
      target.level = result.level;
      if (current() === target) syncHead(target);
      if (result.requested && result.requested !== result.level) {
        U.toast(I.t('level.clamped', { requested: result.requested, level: result.level }), 'warn');
      } else {
        U.toast(I.t('level.changed', { level: result.level }));
      }
    } catch (error) {
      U.toast(error.message || I.t('level.failed'), 'bad');
    }
  }

  /* 项目切换后整页重绘会重建输入框：把用户还没发出去的文字带回来。 */
  function restoreComposerText(text) {
    const box = document.getElementById('chat-input');
    if (box && text && !box.value) {
      box.value = text;
      box.dispatchEvent(new Event('input'));
    }
  }

  function openProjectMenu(anchor, session) {
    if (!(NB.app.canProjects && NB.app.canProjects())) {
      U.toast(I.t('projects.disabled'), 'warn');
      return;
    }
    NB.api.projects().then((rows) => {
      const projects = (rows || []).map((row) =>
        (NB.pages.projects && NB.pages.projects.mapProject)
          ? NB.pages.projects.mapProject(row) : row);
      const pick = (p) => {
        const nextId = p ? p.id : null;
        if (!session.id) {
          const typed = (document.getElementById('chat-input') || {}).value || '';
          session.projectId = nextId;
          session.projectName = p ? p.name : null;
          session.projectPath = p ? p.path || null : null;
          syncHead(session);
          syncComposer(session);
          restoreComposerText(typed);
          return;
        }
        if ((session.projectId || null) === nextId) return;
        const typed = (document.getElementById('chat-input') || {}).value || '';
        NB.api.transferSessionProject(session.id, nextId).then((row) => {
          session.projectId = row.project_id || null;
          session.projectName = row.project_name || null;
          session.projectPath = row.project_path || null;
          U.toast(nextId
            ? I.t('chat.projectTransferred', { name: session.projectName })
            : I.t('chat.projectCleared'));
          NB.app.render();
          restoreComposerText(typed);
        }).catch((error) => U.toast(error.message || I.t('error.network'), 'bad'));
      };
      const items = projects.map((p) => ({
        label: p.name,
        hint: p.path,
        active: session.projectId === p.id,
        onPick: () => pick(p),
      }));
      const menu = [{ label: I.t('chat.noProject'), active: !session.projectId,
        onPick: () => pick(null) }];
      U.menu(anchor, menu.concat(items), { head: I.t('chat.chooseProject'), align: 'right' });
    }).catch((error) => U.toast(error.message || I.t('error.network'), 'bad'));
  }

  function openLevelMenu(anchor, session) {
    const me = NB.mock.me();
    const currentLevel = session.level || NB.mock.ui.defaultLevel;
    const cap = parseInt(String(me.ceiling || 'T1').replace(/\D/g, ''), 10) || 0;
    const names = ['level.off', 'level.skill', 'level.readonly', 'level.confirm', 'level.allow'];
    const items = [];
    for (let i = 0; i <= cap; i += 1) {
      const lv = 'T' + i;
      items.push({
        label: lv + ' · ' + I.t(names[i]),
        active: lv === currentLevel,
        hint: lv === NB.mock.ui.defaultLevel ? I.t('level.accountDefault') : '',
        onPick: () => applySessionLevel(lv),
      });
    }
    U.menu(anchor, items, { head: I.t('level.sessionTitle'), align: 'right' });
  }

  /* 头部控件（模型 / 思考 / 档位 / 重命名 / 删除 / 侧栏开关）绑定。
     root 可以是整页容器，也可以直接是 #chat-head-slot。 */
  function bindHead(root) {
    if (!root) return;
    const slot = root.id === 'chat-head-slot' ? root : root.querySelector('#chat-head-slot');
    if (!slot) {
      // 没有会话时头部不存在，但侧栏开关属于外壳，单独处理
      bindSideToggle(root);
      return;
    }
    slot.querySelectorAll('[data-rename]').forEach((b) => {
      b.onclick = () => openRename(b.dataset.rename);
    });
    slot.querySelectorAll('[data-del]').forEach((b) => {
      b.onclick = () => removeSession(b.dataset.del);
    });
    slot.querySelectorAll('[data-chat-model]').forEach((b) => {
      b.onclick = () => { if (!sessionBusy()) { const s = current(); if (s) openModelMenu(b, s); } };
    });
    slot.querySelectorAll('[data-chat-thinking]').forEach((b) => {
      b.onclick = () => { if (!sessionBusy()) { const s = current(); if (s) toggleThinking(s); } };
    });
    slot.querySelectorAll('[data-chat-level]').forEach((b) => {
      b.onclick = () => { if (!sessionBusy()) { const s = current(); if (s) openLevelMenu(b, s); } };
    });
    bindSideToggle(root);
  }

  function bindSideToggle(root) {
    const toggle = root.querySelector('#chat-side-toggle');
    if (!toggle) return;
    toggle.onclick = () => {
      const side = document.getElementById('chat-side');
      const scrim = document.getElementById('scrim');
      if (side) side.classList.add('open');
      if (scrim) {
        scrim.hidden = false;
        scrim.onclick = () => { if (side) side.classList.remove('open'); scrim.hidden = true; scrim.onclick = null; };
      }
    };
  }

  /* 只重绘输入区：项目控件从头部挪到 foot 后，改项目不能只刷头部。 */
  function syncComposer(session) {
    const slot = document.getElementById('composer-slot');
    if (!slot) return;
    slot.innerHTML = composerHTML(session);
    bindComposer();
  }

  /* 只重绘会话头，不动消息流和输入框。
     切换模型/思考/档位时如果整页 render()，正文会被重建、滚动位置丢失，视觉上就是“刷一下”。 */
  function syncHead(session) {
    const slot = document.getElementById('chat-head-slot');
    if (!slot) return;
    slot.innerHTML = headHTML(session);
    bindHead(slot);
    // 侧栏行里的模型名跟着变，不用重绘整个列表
    const row = document.querySelector('.chat-session[data-session="' + session.id + '"] .cs-meta span');
    if (row) row.textContent = NB.mock.model(session.model).name;
  }

  /* 速度每秒都会变：整块 innerHTML 重建会让头部控件每秒被销毁重绑一次（点击丢失、
     焦点丢失、菜单锚点失效）。已有 speed chip 时只改它的数字，首次出现才整体重绘一次。 */
  function syncSpeed(session) {
    const slot = document.getElementById('chat-head-slot');
    if (!slot) return;
    const chip = slot.querySelector('[data-speed-chip]');
    const text = speedText(session.speed);
    if (!chip) {
      if (text) syncHead(session);
      return;
    }
    if (!text) { chip.remove(); return; }
    const value = chip.querySelector('b');
    if (value) value.textContent = text;
  }

  NB.pages.chat = {
    title: '会话', kicker: '工作台 / CHAT', full: true,
    async create() {
      // 新会话只建前端草稿；首条消息发送时才落库，避免空会话占列表。
      discardDraft();
      draft = makeDraft();
      NB.mock.ui.chatSession = null;
      NB.app.render();
      return draft;
    },
    discardDraft,
    current,
    isStreaming() { return !!stream; },
    // 供 Node harness 直接验证重放映射（没有构建链，只能挂在对象上导出）
    mapMessageForTest: mapMessage,
    headHTMLForTest: headHTML,
    pickActiveForTest: pickActiveTurn,
    jumpLatestNeededForTest: jumpLatestNeeded,
    syncJumpLatestForTest: syncJumpLatest,
    resetContextForTest: resetContext,
    resolveInterruptForTest: resolveInterrupt,
    projectBadgeForTest: projectBadge,
    composerHTMLForTest: composerHTML,
    interruptHTMLForTest: interruptHTML,

    render() {
      // 整页重绘意味着可能换了会话：点击钉住的旧下标不能再沿用
      navPinned = null;
      const s = current();
      if (!s) return '<div class="chat-no-session" style="padding:40px">' + U.empty('chat', I.t('chat.noSessions'), I.t('chat.createFirst')) +
        '<button type="button" class="btn signal" data-new-session>' + U.icon('plus') + I.t('chat.new') + '</button></div>';
      return '<div class="chat">' +
        '<aside class="chat-side" id="chat-side">' +
      '<div class="chat-side-head"><span class="kicker">SESSIONS / ' + NB.mock.sessions.length + '</span>' +
            '<button type="button" class="btn ghost sm" id="new-session">' + U.icon('plus') + I.t('chat.new') + '</button></div>' +
          '<div class="chat-side-search"><div class="search">' + U.icon('search') +
            '<input class="input" id="chat-filter" placeholder="' + U.esc(I.t('chat.search')) + '" autocomplete="off"></div></div>' +
          '<div class="chat-sessions" id="chat-sessions">' + sessionListHTML() + '</div>' +
          '<div class="chat-side-foot">' + U.esc(I.t('chat.localStorage')) + '</div>' +
        '</aside>' +
        '<section class="chat-main">' +
          '<div id="chat-head-slot">' + headHTML(s) + '</div>' +
          '<div class="chat-scroll-wrap">' +
            '<div class="chat-scroll" id="chat-scroll"><div class="chat-inner" id="chat-inner">' + messagesHTML(s) + '</div></div>' +
            '<div id="chat-nav-slot">' + navHTML(s) + '</div>' +
            '<button type="button" class="chat-jump-latest" id="chat-jump-latest" data-jump-latest' +
              ' aria-label="' + U.esc(I.t('chat.jumpLatest')) + '" title="' + U.esc(I.t('chat.jumpLatest')) + '">' +
              U.icon('arrow-down') + '</button>' +
          '</div>' +
          '<div id="composer-slot">' + composerHTML(s) + '</div>' +
        '</section>' +
      '</div>';
    },

    mount(root) {
      bindSidebar(root);
      bindSessionRows(root);
      bindComposer();

      /* 头部的重命名 / 删除 / 会话级控件 */
      bindHead(root);

      const newButton = root.querySelector('#new-session') || root.querySelector('[data-new-session]');
      if (newButton) newButton.onclick = async () => {
        if (stream) { U.toast(I.t('chat.stopGeneration'), 'warn'); return; }
        try { await NB.pages.chat.create(); }
        catch (error) { U.toast(error.message || I.t('error.network'), 'bad'); }
      };

      root.querySelectorAll('[data-suggest]').forEach((b) => {
        b.onclick = () => send(b.dataset.suggest);
      });

      // interrupt 是流式过程中才挂载的，不能在 mount 时一次性绑定；
      // 统一委托到页面根节点，renderThread 重绘后按钮仍然可点。
      root.addEventListener('click', (e) => {
        const jump = e.target.closest && e.target.closest('[data-jump]');
        if (jump) { scrollToTurn(Number(jump.dataset.jump)); return; }
        const latest = e.target.closest && e.target.closest('[data-jump-latest]');
        if (latest) { scrollBottom(); return; }
        // 加载更早的消息必须走委托：翻页后 renderThread 会重建按钮，
        // 直接绑定在旧元素上的 onclick 会随 innerHTML 一起丢失
        const earlier = e.target.closest && e.target.closest('[data-load-earlier]');
        if (earlier) {
          const s = current();
          if (s && s._beforeId) loadMessages(s, s._beforeId, true);
          return;
        }
        const approve = e.target.closest && e.target.closest('[data-approve]');
        if (approve) { resolveInterrupt(Number(approve.dataset.approve), 'approve'); return; }
        const reject = e.target.closest && e.target.closest('[data-reject]');
        if (reject) { resolveInterrupt(Number(reject.dataset.reject), 'reject'); return; }
        const always = e.target.closest && e.target.closest('[data-always]');
        if (always) { resolveInterrupt(Number(always.dataset.always), 'always'); return; }
      });

      navScrollEl = root.querySelector('#chat-scroll');
      if (navScrollEl) {
        navScrollHandler = () => {
          if (navRaf) return;
          navRaf = requestAnimationFrame(() => { navRaf = 0; syncNavActive(); syncJumpLatest(); });
        };
        // 用户自己滚动（滚轮/触摸/拖滚动条）时解除点击钉住，恢复跟随视口
        navUnpinHandler = () => { navPinned = null; };
        navScrollEl.addEventListener('scroll', navScrollHandler, { passive: true });
        navScrollEl.addEventListener('wheel', navUnpinHandler, { passive: true });
        navScrollEl.addEventListener('touchmove', navUnpinHandler, { passive: true });
        navScrollEl.addEventListener('mousedown', navUnpinHandler);
      }
      syncNavActive();

      escHandler = (e) => {
        if (e.key === 'Escape' && stream) {
          stream.stop();
          U.toast(I.t('chat.stopGeneration'), 'warn');
        }
      };
      document.addEventListener('keydown', escHandler);
      scrollBottom();
    },

    unmount() {
      if (escHandler) { document.removeEventListener('keydown', escHandler); escHandler = null; }
      if (navScrollEl && navScrollHandler) navScrollEl.removeEventListener('scroll', navScrollHandler);
      if (navScrollEl && navUnpinHandler) {
        navScrollEl.removeEventListener('wheel', navUnpinHandler);
        navScrollEl.removeEventListener('touchmove', navUnpinHandler);
        navScrollEl.removeEventListener('mousedown', navUnpinHandler);
      }
      navScrollEl = null;
      navScrollHandler = null;
      navUnpinHandler = null;
      navPinned = null;
      if (navRaf) { cancelAnimationFrame(navRaf); navRaf = 0; }
      if (stream) {
        /* 离开页面时保留已生成的部分，不继续消耗 */
        const s = current();
        if (s) s.updatedAt = new Date().toISOString();
        clearInterval(stream.timer);
        const msg = s && s.messages.find((m) => m.streaming);
        if (msg) msg.streaming = false;
        stream = null;
      }
      const side = document.getElementById('chat-side');
      if (side) side.classList.remove('open');
      const scrim = document.getElementById('scrim');
      if (scrim) { scrim.hidden = true; scrim.onclick = null; }
    },
  };
})();
