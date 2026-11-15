# Python 项目参考

`project-notes` 的类型文档：项目是 Python 时，先读这一份，再决定怎么写笔记、跑什么命令。

## 什么时候适用

项目根出现 `pyproject.toml`、`requirements.txt`、`setup.py`、`setup.cfg`、`Pipfile` 或任一锁文件，
或者代码以 `.py` 为主。拿不准就 `ls` 一下项目根，不要凭会话内容猜。

## 先探测，再决定

**按项目现有的工具走，不要另起一套。** 顺序：

1. **有工具配置或锁文件** → 用对应工具

   | 看到 | 用什么 | 安装 | 运行 | 测试 |
   |---|---|---|---|---|
   | `uv.lock` / `[tool.uv]` | uv | `uv sync` | `uv run <cmd>` | `uv run pytest` |
   | `poetry.lock` / `[tool.poetry]` | poetry | `poetry install` | `poetry run <cmd>` | `poetry run pytest` |
   | `pdm.lock` / `[tool.pdm]` | pdm | `pdm install` | `pdm run <cmd>` | `pdm run pytest` |
   | `environment.yml` / `conda` 目录 | conda | `conda env update -f environment.yml` | `conda run -n <env> <cmd>` | `conda run -n <env> pytest` |

2. **已有环境目录** → 复用，不重建：`.venv/`、`venv/`、`.conda/`。
3. **都没有** → 新建 `.venv`（见下节）。
4. **仍有歧义**（多个环境、工具混用、锁文件与清单冲突）→ 问用户，不要自己选一套。

先看版本要求再动手：`.python-version`、`pyproject.toml` 的 `requires-python`、poetry 的
`python` 约束。新建环境时用满足要求的解释器，不要默认系统 `python3` 的版本。

## 硬底线

- **不要用 NovaBot 自己的解释器或虚拟环境跑目标项目**，也不要往里装包：它的依赖是给
  NovaBot 用的，装进目标项目的包会把两边一起弄坏。
- **不靠 `activate` 跨命令生效**：每条 shell 命令都是新进程，上一条里的激活状态不会保留。
  始终显式指定解释器：`.venv/bin/python`、`uv run`、`poetry run`、`conda run`。
- **不猜依赖**：先读清单或锁文件；有锁文件就按锁文件装（`uv sync --frozen`、
  `poetry install --no-update`、`pip install -r requirements.txt`）。装不上就报告错误，
  不要连续试装一堆“可能需要的”包。
- **不改锁文件来绕过安装失败**：除非用户明确要求升级依赖。

## 新建环境（项目没有既有工具时）

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -r requirements.txt
```

Windows：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`python` 在 NovaBot 的 shell 里不一定存在，通常要用 `python3`；建好环境后一律用环境里的
解释器路径，避免解释器漂移。

## 运行与测试

- 测试命令优先看项目配置：`pytest.ini`、`pyproject.toml` 的 `[tool.pytest.ini_options]`、
  `tox.ini`、`noxfile.py`、`Makefile`；没有配置才用 `<解释器> -m pytest`。
- 用项目环境里的 pytest，不要依赖全局安装的版本。
- 长驻进程（开发服务器、watch 模式）不要前台阻塞：shell 命令有超时，超时会被终止。
  先跑测试和短命令；确需启动服务时说明它会阻塞，并由用户决定。
- 环境变量不会被 NovaBot 继承（包括代理设置）。需要时在同一条命令里显式给：
  `HTTP_PROXY=... HTTPS_PROXY=... .venv/bin/python -m pip install ...`。
- 搜索和批量读取时排除 `.venv/`、`__pycache__/`、`.pytest_cache/`、`.mypy_cache/`，
  否则会扫进成千上万个第三方文件。

## 该记进项目 AGENTS.md 的内容

只记下次还会用到的、项目特有的口径：

- 用哪个环境（`.venv` / uv / poetry / conda）以及解释器的相对路径
- 安装、运行、测试三条常用命令
- Python 版本要求
- 项目特有的坑（例如必须先跑代码生成、测试需要某个本地服务）

不要记：包的具体版本（锁文件才是唯一真源）、一次性的安装输出、通用的 Python 常识。

## 交付前检查

- 依赖有变化时，清单和锁文件同步更新
- 测试通过，且用的是项目环境里的解释器
- 不提交 `.venv/`、`__pycache__/`、`.pytest_cache/`；确认它们在 `.gitignore` 里
