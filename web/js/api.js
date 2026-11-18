/* Same-origin Web API client. API envelopes are {code,message,data}; SSE is parsed incrementally. */
(function () {
  const NB = (window.NB = window.NB || {});
  const TOKEN_KEY = 'novabot.web.token';
  // 上传大文件走 XHR（fetch 拿不到上传进度）；超时后中止，避免 composer 永远卡在“上传中”
  const UPLOAD_TIMEOUT_MS = 120000;

  const token = {
    get: () => localStorage.getItem(TOKEN_KEY) || '',
    set: (value) => value ? localStorage.setItem(TOKEN_KEY, value) : localStorage.removeItem(TOKEN_KEY),
  };

  /* fetch/XHR 共用：固定错误提示带 error code 时按 code 翻译（变量一并下发）。
     原始异常文本没有 code，原样展示——不做猜测式翻译。 */
  function envelopeError(payload, status, statusText) {
    const data = payload || {};
    const code = data.error;
    const localized = code && NB.i18n.has('error.' + code);
    const error = new Error(localized
      ? NB.i18n.t('error.' + code, data.vars)
      : (data.message || statusText || NB.i18n.t('error.network')));
    error.status = status;
    error.code = code || data.code || status;
    return error;
  }

  async function request(path, options) {
    const opts = Object.assign({ method: 'GET', headers: {} }, options || {});
    const headers = new Headers(opts.headers);
    if (token.get()) headers.set('Authorization', 'Bearer ' + token.get());
    if (opts.body && !(opts.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    opts.headers = headers;
    const response = await fetch('/api/v1' + path, opts);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || (payload.code && payload.code >= 400)) {
      throw envelopeError(payload, response.status, response.statusText);
    }
    return Object.prototype.hasOwnProperty.call(payload, 'data') ? payload.data : payload;
  }

  function json(method, path, data, options) {
    return request(path, Object.assign({}, options || {}, {
      method,
      body: data === undefined ? undefined : JSON.stringify(data),
    }));
  }

  /* 单文件上传：XMLHttpRequest 才有 upload.onprogress。
     options.onProgress(0..1)、options.timeoutMs；其余错误语义与 request 一致。 */
  function upload(sessionId, file, options) {
    const opts = options || {};
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/v1/sessions/' + encodeURIComponent(sessionId) + '/attachments');
      if (token.get()) xhr.setRequestHeader('Authorization', 'Bearer ' + token.get());
      xhr.timeout = opts.timeoutMs || UPLOAD_TIMEOUT_MS;
      xhr.upload.onprogress = (event) => {
        if (!opts.onProgress || !event.lengthComputable || !event.total) return;
        opts.onProgress(Math.max(0, Math.min(1, event.loaded / event.total)));
      };
      xhr.onload = () => {
        let payload = {};
        try { payload = JSON.parse(xhr.responseText || '{}'); } catch (e) { /* 非 JSON 视作失败 */ }
        if (xhr.status < 200 || xhr.status >= 300 || (payload.code && payload.code >= 400)) {
          reject(envelopeError(payload, xhr.status, xhr.statusText));
          return;
        }
        resolve(Object.prototype.hasOwnProperty.call(payload, 'data') ? payload.data : payload);
      };
      xhr.onerror = () => reject(new Error(NB.i18n.t('error.network')));
      xhr.ontimeout = () => {
        const error = new Error(NB.i18n.t('chat.uploadTimeout'));
        error.code = 'upload.timeout';
        reject(error);
      };
      xhr.onabort = () => {
        const error = new Error(NB.i18n.t('error.network'));
        error.code = 'upload.aborted';
        reject(error);
      };
      const form = new FormData();
      form.append('file', file, file.name);
      xhr.send(form);
    });
  }

  async function stream(path, data, signal, onEvent) {
    const headers = new Headers({ 'Content-Type': 'application/json', Accept: 'text/event-stream' });
    if (token.get()) headers.set('Authorization', 'Bearer ' + token.get());
    const response = await fetch('/api/v1' + path, {
      method: 'POST', headers, body: JSON.stringify(data), signal,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.message || response.statusText || NB.i18n.t('error.network'));
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = '';
    while (true) {
      const part = await reader.read();
      pending += decoder.decode(part.value || new Uint8Array(), { stream: !part.done });
      const frames = pending.split(/\r?\n\r?\n/);
      pending = frames.pop() || '';
      for (const frame of frames) {
        const line = frame.split(/\r?\n/).find((x) => x.startsWith('data:'));
        if (!line) continue;
        const raw = line.slice(5).trim();
        if (!raw || raw === '[DONE]') continue;
        try { onEvent(JSON.parse(raw)); } catch (e) { console.warn('Invalid SSE frame', e); }
      }
      if (part.done) break;
    }
    if (pending.trim().startsWith('data:')) {
      const raw = pending.trim().slice(5).trim();
      if (raw && raw !== '[DONE]') onEvent(JSON.parse(raw));
    }
  }

  NB.api = {
    token,
    request,
    get: (path, options) => request(path, options),
    post: (path, data, options) => json('POST', path, data, options),
    put: (path, data, options) => json('PUT', path, data, options),
    patch: (path, data, options) => json('PATCH', path, data, options),
    delete: (path, options) => request(path, Object.assign({}, options || {}, { method: 'DELETE' })),
    stream,
    authStatus: () => request('/auth/status'),
    me: () => request('/auth/me'),
    login: (username, password) => json('POST', '/auth/login', { username, password }),
    register: (username, password) => json('POST', '/auth/register', { username, password }),
    logout: () => json('POST', '/auth/logout', {}),
    setupAdmin: (payload) => json('POST', '/auth/setup', payload),
    sessions: (limit, projectId) => {
      const q = new URLSearchParams({ limit: String(limit || 100) });
      if (projectId) q.set('project_id', projectId);
      return request('/sessions?' + q.toString());
    },
    projects: () => request('/projects'),
    projectAgentsDraft: (id) => json('POST', '/projects/' + encodeURIComponent(id) + '/agents-draft', {}),
    generateProjectAgents: (id) => json('POST', '/projects/' + encodeURIComponent(id) + '/agents-generate', {}),
    readProjectAgents: (id) => request('/projects/' + encodeURIComponent(id) + '/agents'),
    updateProjectAgents: (id, content) => json('PUT', '/projects/' + encodeURIComponent(id) + '/agents', { content }),
    deleteProjectAgents: (id) => request('/projects/' + encodeURIComponent(id) + '/agents', { method: 'DELETE' }),
    writeProjectAgents: (id, content) => json('POST', '/projects/' + encodeURIComponent(id) + '/agents', { content }),
    createProject: (payload) => json('POST', '/projects', payload),
    updateProject: (id, patch) => json('PATCH', '/projects/' + encodeURIComponent(id), patch),
    archiveProject: (id) => request('/projects/' + encodeURIComponent(id), { method: 'DELETE' }),
    browseProjects: (path) => request('/projects/browse?path=' + encodeURIComponent(path || '')),
    models: () => request('/models'),
    setDefaultModel: (model) => json('POST', '/models/default', { model }),
    setSubagentModel: (role, model) => json(
      'PUT', '/models/subagents/' + encodeURIComponent(role), { model }),
    session: (id) => request('/sessions/' + encodeURIComponent(id)),
    messages: (id, limit, beforeId) => {
      const q = new URLSearchParams({ limit: String(limit || 30) });
      if (beforeId) q.set('before_id', String(beforeId));
      return request('/sessions/' + encodeURIComponent(id) + '/messages?' + q.toString());
    },
    createSession: (model, thinkingLevel, projectId) => json('POST', '/sessions', {
      model, thinking_level: thinkingLevel, project_id: projectId || undefined,
    }),
    transferSessionProject: (id, projectId) => json(
      'PATCH', '/sessions/' + encodeURIComponent(id) + '/project', { project_id: projectId }),
    setSessionLevel: (id, level) => json('PATCH', '/sessions/' + encodeURIComponent(id) + '/level', { level }),
    renameSession: (id, title) => json('PATCH', '/sessions/' + encodeURIComponent(id), { title }),
    deleteSession: (id) => request('/sessions/' + encodeURIComponent(id), { method: 'DELETE' }),
    adminUsers: () => request('/admin/users'),
    adminCreateUser: (payload) => json('POST', '/admin/users', payload),
    adminUpdateUser: (uid, patch) => json('PATCH', '/admin/users/' + encodeURIComponent(uid), patch),
    adminResetPassword: (uid, password) => json(
      'POST', '/admin/users/' + encodeURIComponent(uid) + '/reset-password',
      { new_password: password },
    ),
    adminDeleteUser: (uid) => request('/admin/users/' + encodeURIComponent(uid), { method: 'DELETE' }),
    changePassword: (oldPassword, newPassword) => json('POST', '/auth/password', {
      old_password: oldPassword, new_password: newPassword,
    }),
    deleteAccount: (password, purgeFiles) => json('DELETE', '/auth/account', {
      password, purge_files: !!purgeFiles,
    }),
    uploadAttachment: (sessionId, file, options) => upload(sessionId, file, options),
    uploadSkill: (file, target) => {
      const form = new FormData();
      form.append('file', file, file.name);
      form.append('target', target || 'private');
      return request('/skills/upload', { method: 'POST', body: form });
    },
    deleteAttachment: (sessionId, fileId) => request(
      '/sessions/' + encodeURIComponent(sessionId) + '/attachments/' + encodeURIComponent(fileId),
      { method: 'DELETE' }
    ),
  };
})();
