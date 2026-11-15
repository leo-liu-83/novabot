# NovaBot Web 控制台

原生 HTML + CSS + JavaScript，无框架、无构建链、无外部依赖。静态文件由 FastAPI 同源托管，
聊天、附件、账号与管理工作面都走真实后端 API；`js/mock.js` 只保留页面本地状态与少量初始占位。

## 预览

启动 API 后直接访问：

```bash
python main.py web --host 127.0.0.1
# http://127.0.0.1:9000/
```

单独调前端样式时可以起静态服务，但没有后端时登录与数据请求不可用：

```bash
cd web
python -m http.server 8777
```

## 页面

| hash | 页面 | 说明 |
|---|---|---|
| `#/setup` | 首次初始化 | 多用户且无管理员时：欢迎 → 创建管理员 → 自动进入控制台；单用户模式：提示开启多用户 |
| `#/login` | 登录 / 注册 | 真实 API；注册在无管理员时由后端拒绝 |
| `#/chat` | 会话 | SSE 流式、思考段、工具卡片、确认门、停止生成、附件、会话增删改 |
| `#/overview` | 概览 | 身份、执行档位、配额、MCP、技能、最近会话、用量（7 天 token 趋势 + 最常用模型） |
| `#/web` | 网络搜索 | 用户自备 provider 增删改、启用开关、配额与抓取边界 |
| `#/mcp` | MCP | server 增删改、server 级模式、逐工具覆盖、系统层只读 |
| `#/skills` | 技能 | 共享 / 私有分层、扫描、放行 / 拒绝、findings 详情 |
| `#/account` | 账号 | 改密、退出、注销（需密码 + 用户名确认） |
| `#/admin/users` | 用户与授权 | 增删用户、管理员升降、天花板、配额、MCP 授权 |
| `#/admin/web` | 全局搜索 | 管理员维护全局 provider |
| `#/admin/system` | 系统信息 | 运行时、执行权限、网络能力、MCP 限制、模型目录 |

## 主题

顶栏与登录/初始化页右上角都有三态主题开关：亮色 / 暗色 / 跟随系统，默认跟随系统。

- 选择存设备级 `localStorage['nb.theme']`，不同步后端。
- `<html data-theme>` 只写解析后的 `light` / `dark`；所有颜色来自 `css/tokens.css`，
  暗色值集中在 `[data-theme="dark"]`。
- `js/theme.js` 在 `<head>` 同步执行，首次绘制前应用主题，暗色下不会闪白。
- 跟随系统时监听 `prefers-color-scheme`，系统切换深浅色会实时生效。

## 侧栏收缩

桌面端（>860px）品牌行右侧有一个箭头按钮，把侧栏从 238px 收到 64px 的图标条。

- 状态存 `localStorage['nb.rail']`（`expanded` / `collapsed`），不同步后端；
  `js/rail.js` 在 `<head>` 同步执行，首次绘制前写 `<html data-rail>`，不会先画宽再跳窄。
- 收起后导航只剩图标；文字与计数（如「技能 2」）改由悬停浮层 `data-tip` 显示，
  浮层挂在 `body` 上，不会被 `.nav` 的 `overflow-y` 裁掉。
- 品牌区变成单字母 `N`，分组标题变成细分隔线，身份块只留头像。
- 窄屏（≤860px）不参与：那里侧栏本来就是抽屉，按钮隐藏，宽度与位移规则不变。

## 首次初始化

- **多用户 + 无管理员**：整个控制台被 `#/setup` 挡住，流程为欢迎 → 创建管理员 → 完成；
  创建成功后自动签发 token 并进入主界面，初始化入口随即关闭。刷新或手动输入 `#/setup`
  都不会再回到该页。
- **单用户模式**：整个 Web 只显示"需要开启多用户模式"，给出配置步骤与「重新检测」；
  TUI 与 API 不受影响。
- **不限制来源地址**：部署完成后从任何机器都能完成初始化。因此初始化完成前不要把端口
  暴露到公网；创建管理员后接口立即返回 409。
- 跨站请求由后端 `Origin` / `Host` 同源校验拒绝；反向代理需要保留原始 `Host` 头。

手动冒烟清单：

1. 多用户 + 无管理员：打开 `/` 只看到欢迎页；手动改 `#/chat` 仍回到 `#/setup`。
2. 填用户名与密码创建 → 自动进入会话页；刷新后不再出现初始化页。
3. 退出登录后手动访问 `#/setup` → 回到登录页。
4. 单用户模式：打开 `/` 只看到多用户提示；改配置重启后点「重新检测」→ 欢迎页。
5. 跨源请求被拒：

   ```bash
   curl -i -X POST http://127.0.0.1:9000/api/v1/auth/setup \
     -H 'Content-Type: application/json' \
     -H 'Origin: https://evil.example' \
     -d '{"username":"x","password":"secret123","password2":"secret123"}'
   # 期望 403，error=auth.cross_origin_blocked
   ```

## 目录

```
web/
├── index.html          # 外壳、图标 sprite、脚本入口
├── css/
│   ├── tokens.css      # 设计变量、字体栈、重置
│   ├── layout.css      # 侧栏 / 顶栏 / 工作区 / 认证页
│   ├── components.css  # 按钮、表单、表格、badge、modal、toast…
│   └── pages.css       # 各页面与响应式规则
└── js/
    ├── api.js          # /api/v1 客户端、token、SSE 解析
    ├── mock.js         # 页面本地状态与初始占位（不再提供演示账号）
    ├── ui.js           # esc / icon / toast / modal / menu / 格式化
    ├── app.js          # hash 路由、权限门控、顶栏、身份切换
    ├── i18n.js         # 中英文词典（zh / en）
    ├── i18n.en.js      # 历史英文词条与错误码（运行时合并）
    └── pages/          # auth / overview / chat / web / mcp / skills / account / admin
```

## 边界

- 前端提示不是安全边界：初始化、登录、改密、注销、授权都由后端再次校验。
- 模型输出、用户数据与后端原始异常文本不翻译；固定错误按 `error` code 翻译。
- 保持无构建链：新增页面继续用经典 `<script>` + `window.NB` 命名空间。

## 设计

深墨侧栏、暖纸画布、衬线标题 + 等宽数据；信号橙 `#cf4f28`、酸性黄绿 `#d9f26a`。
字体与图标均本地/内联，不请求外部资源。
