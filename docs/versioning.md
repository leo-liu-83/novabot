# 版本与发布

版本号、发布产物和变更日志的口径。分支与推送的具体操作记录在仓库根目录的 `AGENTS.md`（开发分支专用，
不随发布内容分发）。

## 1. 版本号规则

采用 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)：`MAJOR.MINOR.PATCH`。

当前处于 `0.x` 阶段，按下表判定：

| 改了什么 | 动作 | 例子 |
|---|---|---|
| 新功能、行为变化 | MINOR +1，PATCH 归零 | 新增项目功能 → `0.2.0` |
| 只修 bug、只改文档、依赖更新 | PATCH +1 | 补齐文档 → `0.1.1` |
| 破坏性变更（配置结构、HTTP API） | 0.x 期间也用 MINOR，并在 CHANGELOG 标「不兼容」 | |
| 同一批次既有功能又有修复 | 按 MINOR | |

`0.x` 期间不承诺配置格式与 HTTP API 稳定。`1.0.0` 留给这两者冻结的时候；在此之前不要为了
「看起来稳定」而提前跳号。

预发布**不加** `-rc1` 这类后缀：预发布是分支概念（开发环境的 `main`），同一个版本可以预发布
多次，每次只同步内容，版本号不变。

## 2. 单一真源

版本号的唯一来源是 `config/base_config.py` 的 `APP_VERSION`。其余位置要么引用它，要么被测试
盯住：

| 位置 | 处理 |
|---|---|
| `README.md` / `README.en.md` 顶部版本行 | 手写，测试强制与 `APP_VERSION` 一致 |
| `web/js/mock.js` 的 `version` | 手写，测试强制一致 |
| 安装示例的目录名 | 不写版本号（`cd /path/to/novabot`） |
| Web 界面显示 | 运行时读 `/system` 返回的版本 |

改版本号时只改 `APP_VERSION`，然后跑测试看还有哪里没跟上。

## 3. 什么时候改

- 只在开发分支 `master` 上改，一个提交：`chore(release): 0.2.0`
- 改完连同其他改动一起推 `origin master`
- 预发布（`scripts/prerelease.sh`）只同步内容到 `origin main`，不动版本号
- 发布（`scripts/publish.sh`）只读版本号，不再在发布时临时决定

## 4. 发布产物

GitHub 上每个版本对应：

- 一条提交：`release: <版本>`，作者与提交者固定为 noreply 身份
- 一个注解 tag：`v<版本>`

tag 与提交一一对应。**同一版本不能重复发布**：内容有变化就必须先 bump 版本号，发布脚本遇到
「tag 已指向别的提交」会直接拒绝。这样 GitHub 的历史就能一眼对上版本。

需要发布说明时，用 tag 建 GitHub Release，正文取 `CHANGELOG.md` 对应段落——不要贴开发提交
消息，避免把开发过程带进发布环境。

## 5. 变更日志

`CHANGELOG.md` 分三类记录：**新增 / 变更 / 修复**，出现破坏性变更时额外加「不兼容」一节。

- 发布前更新，随发布内容一起发布
- 只写对外可见的变化，不写内部重构过程
- 发布脚本会检查当前版本的小节是否存在，缺了就不允许发布

## 6. 发布检查清单

1. 在 `master` 上 bump `APP_VERSION`，补 `CHANGELOG.md` 的对应小节
2. 全量测试通过（`pytest`）
3. 说「推送预发布」→ 同步到 `origin main`
4. 说「发布」→ 生成 `release: <版本>` 提交与 `v<版本>` tag，推到 GitHub
5. 需要时用 tag 建 GitHub Release
