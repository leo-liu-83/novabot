# 随包分发的签名规则（vendored signatures）

本目录下的 `*.yaml` 来自 **cisco-ai-defense/skill-scanner** 的
`skill_scanner/data/packs/core/signatures/`，版本 **2.3.1**（2026-10-21 发布）。

- 上游项目：https://github.com/cisco-ai-defense/skill-scanner
- 许可：**Apache License 2.0**（原文见 `LICENSE-APACHE-2.0.txt`）
  - Copyright 2026 Cisco Systems, Inc. and its affiliates
- 本目录的文件**原样保留**（未做删改），仅由 NovaBot 的扫描器加载使用。

## 为什么 vendored、又为什么只用它的一小部分

上游的**判定价值主要在 LLM 裁判**（实测：纯规则在 HIGH 档只抓 7.7% 的恶意技能，
裁判才到 66.7%），而裁判要 API key 与每次扫描的推理开销；上游的依赖树
（litellm / onnxruntime / FastAPI / Textual + wheel 内置的 Go 版 CEL helper）
也与本项目"clone 即跑、零外部依赖"的部署理念冲突。所以**不装它，只借它最原始的一层**。

借的动机只有一个：它的 `prompt_injection` / `hardcoded_secrets` 规则带
**实测标定的 `exclude_patterns` 误报抑制**（上游拿 188 万真实技能标定——某规则的
命中里 64% 是引号内的提及，裁判清掉其中 85%）。我们自己的 MD005 只有 6 个短语。

## 作用域：**只用于 markdown（SKILL.md）**，不用于 .py / .sh

这不是保守，是踩出来的。最初把这批 YAML 原样铺到所有文件类型上，
在**真实技能** `dev-expert-pro` 上直接出三条误报：

| 命中 | 实际代码 | 判定 |
|---|---|---|
| `RESOURCE_ABUSE_INFINITE_LOOP` | `while True:` 往下四行就是 `if parent == d: return` | 误报——规则宣称"无明确退出条件"，但它看不见退出条件 |
| `RESOURCE_ABUSE_INFINITE_LOOP` | 向上爬目录的同类循环 | 误报 |
| `PATH_TRAVERSAL_OPEN` | 自测块里 `open(os.path.join(work, victim))`，`victim` 是硬编码名 | 误报——没做污点分析就断言 "user-controlled" |

原因：这批 YAML 是上游 30 阶段流水线里**最粗的一层**。这些类别在代码文件上，
上游其实交给 **AST/数据流的 Python 规则**（另 120 条）+ `low-noise`/`quiet` preset
+ `unreferenced-script context` + `core-signature refinement` 去承担——我们把最粗的
一层接到了该由最细一层负责的位置。那 120 条与降噪层**不搬**（要搬就得连 AST 一起重写）。

**要扩到代码文件时的前提**：先拿一批真实技能测误报率（参照上游口径：纯规则 HIGH 档
官方 FPR 就有 4%），别直接接。`tests/test_skill_signatures.py` 里的
`TestPackDoesNotTouchCodeFiles` 是这条决定的回归锁。

## 更新方式

1. 取上游新版本的 wheel（`pip download cisco-ai-skill-scanner --no-deps`）
2. 用其中的 `skill_scanner/data/packs/core/signatures/*.yaml` 覆盖本目录
3. 同步 `LICENSE-APACHE-2.0.txt`，并把上面的版本号改掉
4. 跑 `tests/test_skill_signatures.py`（规则数、正则可编译、markdown 适用范围、真实技能零误报）

## 生效范围与不搬的部分

**本目录的 YAML 虽全量 vendor 了 47 条，但当前只有 `file_types` 含 markdown 的那部分生效**
（prompt_injection 全部、hardcoded_secrets 大部分、harmful_content、以及 command_injection 里
两条面向 markdown 的）。代码类文件的规则**保留在文件里但不启用**——见上面"作用域"一节，
要启用得先测误报。

不搬的东西：

- **上游的 120 条 Python 规则**（AST / 数据流 / 控制流那批）—— 这是它在代码文件上真正干活的一层，
  搬它等于重写一个分析器，我们的 AST 层已覆盖其中一小部分（shell=True/eval/os.system/反序列化/出网/凭据）
- **上游的 30 阶段降噪**（`unreferenced-script context`、`core-signature refinement`、
  `disabled-rule filter`）与 `low-noise`/`quiet` preset —— 没有它们，代码类规则误报率明显上升
- **YARA-X 规则**（`data/packs/core/yara/`）—— 用的是 YARA-X 专有语法（正则字面量、
  `nocase` 修饰、数字下划线分隔），`yara-python` 跑不了，本项目不打算引入 Rust 依赖
- **`atr` 规则包**（712 条）、**LLM 裁判 / meta 裁判 / 云端分析器** —— 后者是它真正的召回来源，
  但要 API key 与推理开销，与"clone 即跑"冲突
