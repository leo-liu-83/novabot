# 前端第三方依赖（本地 vendor）

本目录的前端库随仓库一起分发，页面不访问任何 CDN，离线/内网可直接运行。

| 文件 | 版本 | 许可 | 来源 |
|---|---|---|---|
| `markdown-it.min.js` | 15.0.3 | MIT | <https://www.npmjs.com/package/markdown-it>（`dist/browser/markdown-it.umd.min.js`） |
| `highlight.min.js` | 11.12.0 | BSD-3-Clause | <https://www.npmjs.com/package/@highlightjs/cdn-assets>（`highlight.min.js`，common 语言子集） |

许可证原文分别是 `markdown-it.LICENSE.txt` 与 `highlight.LICENSE.txt`。

## 更新方式

```bash
npm pack markdown-it@<version>
tar -xzf markdown-it-<version>.tgz
cp package/dist/browser/markdown-it.umd.min.js web/js/vendor/markdown-it.min.js
cp package/LICENSE web/js/vendor/markdown-it.LICENSE.txt

npm pack @highlightjs/cdn-assets@<version>
tar -xzf highlightjs-cdn-assets-<version>.tgz
cp package/highlight.min.js web/js/vendor/highlight.min.js
cp package/LICENSE web/js/vendor/highlight.LICENSE.txt
```

更新后把新版本号与 SHA256 记在这里，并跑一遍 `tests/web_markdown_*.js` 与 `tests/test_web_markdown_vendor.py`。

当前 SHA256：

```text
391f2aa638e75e6c7859d0a5c5650ef0956722a45998f17e05b83bc655778e1e  markdown-it.min.js
8ab71eb09c51f501e5e25157d9cff100e46cc29bcbfc744d0b746d451fca7f53  highlight.min.js
```
