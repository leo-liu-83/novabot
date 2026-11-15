---
name: project-notes
description: Maintain project knowledge for NovaBot project sessions — decide what belongs in the project's AGENTS.md versus .novabot/notes/, keep both concise and project-scoped, and keep global memory free of project specifics. Use whenever the user asks to save a note (including the vague "记到笔记") or whenever you learn something durable about a project (its conventions, source-of-truth files, workflow steps, gotchas or decisions), regardless of whether the project is code, writing, research or data work.
---

# 项目笔记

## 适用范围（先确认再动手）

只用于**项目会话**——判断依据：系统提示里有「## 当前项目」这一段，说明当前会话已归属某个项目。

没有这一段时，不要创建 `AGENTS.md` 或 `.novabot/notes/`，也不要退化成写全局记忆。
先分清是哪种情况，再给出对应的下一步：

| 情况 | 怎么判断 | 告诉用户 |
|---|---|---|
| 账号没有项目功能 | 用户从来没建过项目，界面上也没有项目入口 | 需要管理员开通：Web「用户与授权」里对应账号的「项目」开关，或 TUI `/project-limit` |
| 有功能，但会话没归属 | 用户已有项目，只是这个会话不在里面 | 在 Web 项目页或 TUI `/project` 把会话转入项目，之后我再写 |

临时记录：征得用户同意后可以写进当前工作区的普通文件，并说明写到了哪里；不要写进 `/memory/`。
全局记忆只放跨项目成立的偏好与概括；本项目的术语表、资料清单、约定与决策都属于项目资产。
- 普通会话里的临时结论，直接写进当前工作区的普通文件并说明位置即可

项目可以是一个代码库，也可以是一个写作目录、研究资料夹、数据工作区或运营素材库——只要是"围绕一个目录长期做的事"，项目知识都值得沉淀。

项目知识分两层，各有边界。写之前先判断：这条信息下次会话还有用吗？一次性的过程不值得记。

## 写哪里

| 位置 | 放什么 | 形态 |
|---|---|---|
| 项目根 `AGENTS.md` | 任何人接手都要遵守的规则：目录职责、命名与格式约定、常用流程 | 短，10–20 行，条目式 |
| `.novabot/notes/*.md` | 专题细节：某类内容的处理办法、资料来源与取舍、排查过程、决策前后 | 一个主题一个文件，文件名即标题 |

`AGENTS.md` 常驻注入，写得越长每次会话越贵；笔记只注入文件名索引，正文按需读取——**规则放 `AGENTS.md`，细节放笔记**。

## 什么值得记

按项目类型举例，判断标准是"会不会重复用到"：

| 项目类型 | 值得沉淀的 |
|---|---|
| 代码 | 构建/测试/启动方式、目录职责、踩坑与绕法、接口或文件格式约定 |
| 写作 | 风格与语气要求、术语表、章节结构、已定稿与废弃的版本 |
| 研究 | 资料来源与可信度、引用格式、已排除的假设与原因 |
| 数据 | 字段含义与口径、清洗步骤、已知脏数据、产出物格式 |
| 运营 | 模板与检查清单、审批流程、发布节奏 |

任何类型都**不记**：一次性的执行过程、与项目无关的个人偏好（那属于全局记忆）、成段原文或日志、密钥与个人信息。

## 怎么写

1. 先读现有内容，做增量修改，不要整篇重写
2. 一条一句话，必要时补一句原因
3. 笔记开头写清"什么时候适用"，避免以后被误读成通用规则
4. 冲突时项目说明优先于全局记忆；同一件事不要在两处重复
5. 交付物本身（文章、报告、数据）放在项目目录里，笔记只写"怎么做、为什么这样做"

## 边界

- 用户手写的结构不要擅自改动；新增内容前说明打算加什么
- 不确定是否"长期有效"时，先问一句，或写进笔记而不是 `AGENTS.md`
- 不要在回复里贴出笔记全文或绝对路径，说明写到了哪个文件即可
