/* 轻量 Markdown 渲染：聊天正文与人格页共用。
   解析交给本地 vendor 的 markdown-it（见 vendor/README.md），离线可用；
   这里只保留项目的安全策略与展示约定：
   - 原始 HTML 不透传（html: false）
   - 链接/图片只放行 http(s)、mailto 与站内 #/// 路径
   - 站外链接新开窗口
   - 围栏代码块带复制按钮
   - 列表项 [ ] / [x] 渲染成勾选框
*/
(function () {
  const NB = (window.NB = window.NB || {});
  const I = NB.i18n;
  const MarkdownIt = window.markdownit;
  const hljs = window.hljs;

  if (typeof MarkdownIt !== 'function') {
    throw new Error('markdown-it is not loaded; include js/vendor/markdown-it.min.js first');
  }

  const md = new MarkdownIt({
    html: false,
    linkify: false,
    breaks: true,
    typographer: false,
  });

  // 与旧渲染器保持同一套放行规则：站内只认 # 与 / 开头
  md.validateLink = (url) => /^(https?:|mailto:|[#/])/i.test(String(url || '').trim());

  // 站外链接新开窗口；站内锚点/路径保持当前页
  const defaultLinkOpen = md.renderer.rules.link_open
    || ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));

  md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
    const href = tokens[idx].attrGet('href') || '';
    if (/^https?:/i.test(href)) {
      tokens[idx].attrSet('target', '_blank');
      tokens[idx].attrSet('rel', 'noopener noreferrer');
    }
    return defaultLinkOpen(tokens, idx, options, env, self);
  };

  // 图片保持旧渲染器的懒加载约定
  const defaultImage = md.renderer.rules.image
    || ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));

  md.renderer.rules.image = function (tokens, idx, options, env, self) {
    tokens[idx].attrSet('loading', 'lazy');
    return defaultImage(tokens, idx, options, env, self);
  };

  // 表格：包一层横向滚动容器；短单元格标 md-tight 不折行。
  // 中文可以在任意两字之间断行，表格的自动列宽只保证 min-content，于是
  // “更新日期”这类表头会被长摘要列挤成两行。短内容（表头/文件名/日期/状态）
  // 按整格宽度参与列宽计算，长正文列继续正常折行。
  md.renderer.rules.table_open = () => '<div class="md-table-wrap"><table>\n';
  md.renderer.rules.table_close = () => '</table></div>\n';

  function cellWidth(text) {
    let width = 0;
    for (const ch of String(text == null ? '' : text)) {
      width += ch.codePointAt(0) > 0x2e80 ? 2 : 1;
    }
    return width;
  }

  function tightCells(openType) {
    const defaultOpen = md.renderer.rules[openType]
      || ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));
    md.renderer.rules[openType] = function (tokens, idx, options, env, self) {
      const inline = tokens[idx + 1];
      if (inline && inline.type === 'inline' && cellWidth(inline.content) <= 24) {
        tokens[idx].attrJoin('class', 'md-tight');
      }
      return defaultOpen(tokens, idx, options, env, self);
    };
  }
  tightCells('th_open');
  tightCells('td_open');

  // 围栏代码块：已知语言用 highlight.js 上色，未知语言保持转义纯文本；右上角放复制图标
  md.renderer.rules.fence = function (tokens, idx) {
    const token = tokens[idx];
    const lang = String(token.info || '').trim().split(/\s+/)[0] || '';
    const raw = token.content.replace(/\n$/, '');
    let code = md.utils.escapeHtml(raw);
    let codeClass = '';

    if (lang && hljs && typeof hljs.highlight === 'function' && hljs.getLanguage(lang)) {
      try {
        code = hljs.highlight(raw, { language: lang, ignoreIllegals: true }).value;
        codeClass = ' class="hljs language-' + md.utils.escapeHtml(lang) + '"';
      } catch (e) {
        code = md.utils.escapeHtml(raw);
      }
    }

    return '<div class="md-code">' +
      '<button type="button" class="md-copy" data-copy-code aria-label="' + md.utils.escapeHtml(I.t('复制')) + '"' +
        ' title="' + md.utils.escapeHtml(I.t('复制')) + '">' + NB.ui.icon('copy') + '</button>' +
      '<pre class="md-pre"' + (lang ? ' data-lang="' + md.utils.escapeHtml(lang) + '"' : '') + '><code' + codeClass + '>' +
      code + '</code></pre>' +
    '</div>\n';
  };

  // 任务列表：- [ ] / - [x] 只转换列表项开头的标记
  function taskLists(state) {
    const tokens = state.tokens;
    let depth = 0;

    for (let i = 0; i < tokens.length; i += 1) {
      const token = tokens[i];
      if (token.type === 'bullet_list_open' || token.type === 'ordered_list_open') depth += 1;
      else if (token.type === 'bullet_list_close' || token.type === 'ordered_list_close') depth -= 1;
      else if (depth > 0 && token.type === 'inline' && token.children && token.children.length) {
        const first = token.children[0];
        if (first.type !== 'text') continue;
        const match = /^\[([ xX])\]\s+/.exec(first.content);
        if (!match) continue;

        first.content = first.content.slice(match[0].length);
        const box = new MarkdownIt.Token('html_inline', '', 0);
        box.content = '<input type="checkbox" disabled' + (match[1].toLowerCase() === 'x' ? ' checked' : '') + '> ';
        token.children.unshift(box);
      }
    }
  }

  md.core.ruler.push('task_lists', taskLists);

  NB.markdown = {
    render: (text) => md.render(String(text == null ? '' : text)),
    inline: (text) => md.renderInline(String(text == null ? '' : text)),
  };
})();
