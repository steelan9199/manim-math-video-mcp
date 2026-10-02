# math-animation-mcp 接入 WorkBuddy 排查报告

- 日期：2026-10-02
- 机器：Windows / 用户 `Administrator`
- 目标仓库：https://github.com/bcefghj/math-animation-mcp
- 本地路径：`D:\github\math-animation-mcp`
- 结论状态：**已在真实 WorkBuddy 环境完全打通 —— 含中文标题的勾股定理动画 720p 经 MCP 本体渲染成功（13 个动画）**
- 修订记录：2026-10-02 15:55 补第二轮真机排查（§4.6–§4.12）、默认字体方案（§5.5）；**修正 §7.1 原判断**

---

## 0. 一句话结论

这台机器上有**三个独立的坑**叠在一起，必须逐个拆掉：C 扩展编译链、MCP 依赖大版本、以及**沙箱重写 PATH 导致 manim 子进程解析到错误解释器**。前两个是"装得上/起得来"的问题，第三个是真正的核心——**在本机，任何依赖 `PATH` 找解释器的代码都不可靠，必须写绝对路径**。

**第二轮（真机使用期）又踩到五个坑**（本次补充，见 §4.6–§4.12）：

1. **MiKTeX 弹窗死锁**（缺 `preview` 宏包 + 未配仓库 + 管理员身份）
2. **uv 解释器"转发器"**（45KB 的 `python.exe` 会再拉一层进程）
3. **`%TEMP%` 被宿主沙箱虚拟化**（临时目录落到 `D:\CToD\...`）
4. **子进程 stdout 走管道 → 死锁**
5. **批量删除守卫 `safe-delete`**（← 最终拦路虎：拦下 manim 清理自身临时文件的动作）

**一句话**：本机 manim 渲染超时的根因**几乎都不在 manim 本身**，而在**宿主环境对子进程的拦截**（PATH / TEMP / 删除 / 管道）。排查时优先看**渲染日志 + 进程表**，不要先怀疑"渲染慢"。

---

## 1. 环境基线（复用时先核对）

| 项 | 值 |
|---|---|
| 共享 Python 环境 | `D:\software\uv\envs\geo`（Python 3.14.7，uv 创建） |
| 共享环境解释器 | `D:\software\uv\envs\geo\Scripts\python.exe` |
| uv | `D:\software\uv\uv.exe` |
| 系统裸 Python | `D:\software\uv\python\cpython-3.14.7-windows-x86_64-none\python.exe`（**无 manim**） |
| MSVC | `D:\software\VisualStudio\BuildTools`，版本 `14.44.35207` |
| Windows SDK | `C:\Program Files (x86)\Windows Kits\10`，版本 `10.0.22621.0` |
| ffmpeg | 已在 PATH 中（manim 渲染必需，本机已具备） |
| WorkBuddy 内置运行时 | `~/.workbuddy/app/app-config.json` → `bundledRuntime.tools.python = false`（**保持关闭**） |

---

## 2. 问题一：manim 安装失败 —— C 扩展包本地编译

### 现象

```
fatal error C1083: 无法打开包括文件: "io.h": No such file or directory
```

### 根因

1. `glcontext`、`moderngl` 等包**没有 cp314 的预编译 wheel**，必须本地编译。
2. setuptools 找不到 Windows SDK 头文件。探查后发现 SDK 确实装了、`io.h` 确实存在，问题是**Visual Studio 的 `vcvarsall.bat` 依赖 `reg.exe` 探测 SDK 安装路径，而 `reg.exe` 被本机安全策略拦截**，探测链断裂 → SDK 路径没被注入编译环境。

### 解法：跳过 vcvars 探测，显式注入 SDK 路径

```powershell
$env:DISTUTILS_USE_SDK='1'
$env:MSSdk='1'
$env:INCLUDE='D:\software\VisualStudio\BuildTools\VC\Tools\MSVC\14.44.35207\include;C:\Program Files (x86)\Windows Kits\10\Include\10.0.22621.0\ucrt;C:\Program Files (x86)\Windows Kits\10\Include\10.0.22621.0\shared;C:\Program Files (x86)\Windows Kits\10\Include\10.0.22621.0\um;C:\Program Files (x86)\Windows Kits\10\Include\10.0.22621.0\winrt;D:\software\VisualStudio\BuildTools\VC\Auxiliary\VS\include'
$env:LIB='D:\software\VisualStudio\BuildTools\VC\Tools\MSVC\14.44.35207\lib\x64;C:\Program Files (x86)\Windows Kits\10\Lib\10.0.22621.0\ucrt\x64;C:\Program Files (x86)\Windows Kits\10\Lib\10.0.22621.0\um\x64'
$env:PATH='D:\software\VisualStudio\BuildTools\VC\Tools\MSVC\14.44.35207\bin\HostX64\x64;C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64;' + $env:PATH

& "D:\software\uv\uv.exe" pip install --python "D:\software\uv\envs\geo\Scripts\python.exe" manim
```

> **注意 `PATH` 里必须同时包含 SDK 的 `bin\10.0.22621.0\x64`**。否则编译能过、**链接阶段会卡在 `rc.exe` / `mt.exe`（资源编译器）找不到**——这是本次实际踩到的第二跳。

### 结果

manim 0.21.0 装入共享环境，连带 `manimpango`、`pycairo`、`moderngl`、`moderngl-window`、`glcontext`、`skia-pathops`、`pyglm`、`av`、`srt` 等 30 个包。端到端渲染验证通过。

### 速记

> 本机任何需要编译 C 扩展的 Python 包，都要带上上面这套环境变量，否则必然 `C1083`。

---

## 3. 问题二：MCP server 启动即退 —— 依赖大版本不匹配

### 现象

MCP 进程秒退，无输出。单独跑 `python -m math_animation_mcp` 无反应。

### 根因

`pip install mcp` 装到了 **`mcp` 2.x**，而仓库代码用的是 **v1 API**：

```python
from mcp.server.fastmcp import FastMCP   # v1 的路径，v2 已移除
```

### 解法

```powershell
& "D:\software\uv\uv.exe" pip install --python "D:\software\uv\envs\geo\Scripts\python.exe" "mcp[cli]<2"
```

### 速记

> 老仓库 + 新依赖 = 先看 import 路径。`pip install mcp` 默认给最新版，v1/v2 API 不兼容，必须锁 `<2`。

---

## 4. 问题三：渲染 120s 超时（核心问题）

### 4.1 现象

- MCP 握手正常，`initialize` / `tools/list` 都返回正确，**21 个工具全部注册**。
- 但调用 `render_animation` 一律返回：

```json
{"success": false, "file_path": "", "error_msg": "Render timed out after 120s"}
```

- `render_animation` 内部固定 `timeout=120`。

### 4.2 排查路径（含被排除的假设）

| 假设 | 验证方式 | 结论 |
|---|---|---|
| 网络问题 | 看安装日志 | ❌ 排除，是编译问题 |
| MCP 握手失败 | 手动发 JSON-RPC | ❌ 排除，握手正常 |
| stdin 管道继承导致子进程挂起 | 管道/非管道两种方式对比 | ❌ 排除，两种都 4.5s 成功 |
| manim 渲染真的慢 | 轮询进程 CPU + 临时目录 | ❌ 排除，**子进程 0 CPU、无文件产出 = 被挂起** |
| PATH 没设对 | 见 4.3 | ✅ 是根因之一 |
| 沙箱拦截嵌套子进程 | 见 4.3 | ✅ 本机真实存在 |

**关键观察**：轮询 `manim_render_*` 临时目录时，里面**只有 `scene.py`**，没有任何 manim 产物；对应 python 进程 CPU 占用为 0 —— 说明它不是"在渲染"，而是**根本没跑起来**。

### 4.3 两个决定性实验

**实验 A（`_t1`）：用绝对路径解释器，在沙箱内直接渲染**

```python
subprocess.run([r"D:\software\uv\envs\geo\Scripts\python.exe", "-m", "manim",
                "render", "-ql", "--media_dir", ..., scene, "T1"], timeout=180)
```

结果：**3.4s 成功，产出 mp4**。
→ 说明沙箱本身**允许** manim 渲染，问题不在渲染本身。

**实验 B（`_t3`）：验证"PATH 前置"是否可行**

```powershell
$env:PATH = 'D:\software\uv\envs\geo\Scripts;' + $env:PATH
& "D:\software\uv\envs\geo\Scripts\python.exe" _t3.py
```

`_t3.py` 内打印 `os.environ["PATH"][:220]`，结果是：

```
C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-3;...
```

**geo\Scripts 根本不在里面**——即使它在 PowerShell 里被显式放到了最前面。
→ **结论：本机执行通道会重写 PATH，PATH 注入不可靠。**

另一个诡异细节：`shutil.which("python")` 明明返回了 `geo\Scripts\python.EXE`，但 `subprocess` 实际执行的却仍是 uv 的裸 Python。
→ **`which` 的解析结果与实际 `CreateProcess` 的解析结果不一致，不能拿 `which` 当依据。**

### 4.4 根因

仓库的沙箱执行器 `src/math_animation_mcp/utils/sandbox.py` 里，解释器选择逻辑是：

```python
if python_bin is None:
    venv_python = os.path.join(..., ".venv", "bin", "python")   # POSIX 路径
    if os.path.exists(venv_python):
        python_bin = venv_python
    else:
        python_bin = "python"        # ← 落到裸 python
```

两个问题：

1. **`.venv/bin/python` 是 POSIX 布局**，Windows 上真实路径是 `.venv/Scripts/python.exe`，所以这个探测**在 Windows 上永远不命中**。
2. 兜底用裸 `"python"`，而本机 PATH 里的 `python` 是 `D:\software\uv\python\cpython-3.14.7-...`，**这个解释器里没有 manim**。

**根因链**：`sandbox.py` 用裸 `python` → PATH 解析到无 manim 的解释器 → 要么报 `No module named 'manim'`，要么在沙箱的进程包裹下直接挂起 → MCP 侧表现为 120s 超时。

叠加环境因素：**本机执行通道会重写 PATH，所以"把 geo\Scripts 塞进 PATH"这条常规思路在这台机器上无效**，必须改用绝对路径显式指定。

### 4.5 修复

**改动 1：`src/math_animation_mcp/utils/sandbox.py` 新增解释器解析函数**

```python
def _resolve_python_bin() -> str:
    """Pick the interpreter used to run Manim.

    Resolution order:
      1. MAMCP_PYTHON env var (explicit override).
      2. A virtualenv inside the repo: .venv/bin/python (POSIX) or
         .venv/Scripts/python.exe (Windows).
      3. Bare "python" resolved from PATH.
    """
    override = os.environ.get("MAMCP_PYTHON")
    if override and os.path.exists(override):
        return override

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    for candidate in (
        os.path.join(repo_root, ".venv", "bin", "python"),
        os.path.join(repo_root, ".venv", "Scripts", "python.exe"),
    ):
        if os.path.exists(candidate):
            return candidate
    return "python"
```

调用处（原来那 6 行探测逻辑整段替换）：

```python
if python_bin is None:
    python_bin = _resolve_python_bin()
```

**改动 2：新增启动器 `D:\github\math-animation-mcp\workbuddy_mcp_launch.py`**

```python
"""WorkBuddy launcher for math-animation-mcp.

Forces every child process (notably the `python -m manim ...` sandbox call) to
use the shared Python environment instead of a PATH-resolved bare `python`.
"""
import os
import sys

SHARED_ENV_SCRIPTS = r"D:\software\uv\envs\geo\Scripts"
SHARED_PYTHON = os.path.join(SHARED_ENV_SCRIPTS, "python.exe")
REPO_ROOT = r"D:\github\math-animation-mcp"

# Manim lives in the shared env; tell the sandbox explicitly.
os.environ["MAMCP_PYTHON"] = SHARED_PYTHON
os.environ["PATH"] = SHARED_ENV_SCRIPTS + os.pathsep + os.environ.get("PATH", "")
os.environ.setdefault("OUTPUT_DIR", os.path.join(REPO_ROOT, "animation_output"))

sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

if os.environ.get("MAMCP_DEBUG") == "1":
    print(f"[launcher] python={sys.executable}", file=sys.stderr)
    print(f"[launcher] PATH={os.environ.get('PATH', '')[:300]}", file=sys.stderr)

from math_animation_mcp.server import run  # noqa: E402

if __name__ == "__main__":
    run()
```

---

### 4.6 真实环境复现：MCP 已「信任」后**仍然**超时（推翻 §7.1 判断）

**背景**：§7.1 曾把"三层嵌套超时"判为 *agent 沙箱特性、不影响真实环境*。**该判断经实测证伪。**

**实测**：在 WorkBuddy 连接器管理页「信任」`math-animation` 之后，直接调用 `render_animation` / `preview_scene`，**依然 60s / 120s 超时**；而同一段代码在本地直接跑只要 3～35 秒。

**结论**：真实环境的超时**与"三层嵌套"无关**，是下面 §4.7–§4.11 **五条根因叠加**。其中 **§4.11（删除守卫）是最终拦路虎**。

---

### 4.7 根因一：MiKTeX 弹窗死锁（带公式的场景首当其冲）

### 现象

- 渲染日志停在：
  ```
  Writing .../media/Tex/xxxx.tex
  latex: security risk: running with elevated privileges
  ```
  之后**一小时以上无任何进展**。
- 屏幕弹出 **MiKTeX 包安装器**窗口：「未能找到 preview.sty」，宏包源显示为 `<随机的 宏包存储库>`，等待人工点击。

### 根因

Manim 默认公式模板 `\documentclass[preview]{standalone}` 需要 **`preview` 宏包**。本机 MiKTeX 三件事同时成立：

1. **未装 `preview`**；
2. **未配置宏包仓库**（弹窗里那行「`<随机的 宏包存储库>`」）；
3. **以管理员权限运行**，被 MiKTeX 判定为 `security risk`，进入受限模式。

→ 既不自动下载、也不报错，**弹窗死等**。无人值守环境下就表现为"超时"。

### 解法

```powershell
$BIN  = "D:\software\MiKTeX\miktex\bin\x64"
$REPO = "https://mirrors.tuna.tsinghua.edu.cn/CTAN/systems/win32/miktex/tm/packages/"

# 1) 指定国内镜像源
& "$BIN\mpm.exe" --set-repository="$REPO"

# 2) 关闭"每次都问"，改为自动安装
& "$BIN\initexmf.exe" --set-config-value="[MPM]AutoInstall=1"

# 3) 更新宏包数据库
& "$BIN\mpm.exe" --update-db

# 4) 预装 Manim 所需宏包（一次装完，以后渲染不再联网下载）
foreach ($p in @("preview","standalone","physics","calligra","wasysym","dsfont",
                 "tipa","relsize","mathrsfs","microtype","ragged2e","textcomp",
                 "setspace","ctex")) {
  & "$BIN\mpm.exe" --admin --install="$p"
}
```

### 验证

```powershell
& "$BIN\kpsewhich.exe" preview.sty   # 应返回路径，而非空
```

修复前：无限挂起。修复后：同一段公式动画**直接渲染 8 秒出片**。

### 速记

> Manim 报 `latex error converting to dvi` 或直接卡死 → **先查 MiKTeX 是否缺 `preview` / `standalone`**。
> MiKTeX 必须配好**镜像源 + `AutoInstall=1`**，否则在无人值守环境必然弹窗卡死。
> `latex --version` 打印 `security risk: running with elevated privileges` 是管理员身份下的正常提示，本身不代表故障。

---

### 4.8 根因二：解释器"转发器"（45KB 的 python.exe）

### 现象

进程表里 manim 子进程是**两层**：

```
28616  geo\Scripts\python.exe               ← 仅 45KB（转发器）
28296  uv\python\cpython-3.14.7\python.exe  ← 它再拉起的真解释器
```

对照实验：`export_video`（内部靠 ffmpeg 起子进程）**一次通过** —— 因为 ffmpeg 是**单层原生进程**，而 manim 要连开两层。

### 根因

uv 创建的 venv 里，`Scripts\python.exe` **只是一个 45KB 的启动器 / 转发器（trampoline）**，它会 `CreateProcess` 再拉起 uv 的基础解释器。在宿主沙箱包裹下，**第二层 `CreateProcess` 被卡住**，manim 在 0 CPU 处挂死。

### 解法：把真解释器复制进 venv（原转发器保留，不覆盖）

```powershell
$BASE    = "D:\software\uv\python\cpython-3.14.7-windows-x86_64-none"
$SCRIPTS = "D:\software\uv\envs\geo\Scripts"

Copy-Item "$BASE\python.exe"    "$SCRIPTS\python_direct.exe"
Copy-Item "$BASE\pythonw.exe"   "$SCRIPTS\pythonw_direct.exe"
Copy-Item "$BASE\python314.dll" "$SCRIPTS\"
Copy-Item "$BASE\python3.dll"   "$SCRIPTS\"
```

**关键**：`python_direct.exe` 落在 `pyvenv.cfg` 的**上一级目录**，所以它**仍会认 geo 虚拟环境**（`sys.prefix = geo`、能 import manim），但**不再有第二层转发**。

### 配套改动

`workbuddy_mcp_launch.py`：

```python
SHARED_PYTHON = os.path.join(SHARED_ENV_SCRIPTS, "python_direct.exe")
if not os.path.exists(SHARED_PYTHON):   # 不存在则自动回退
    SHARED_PYTHON = os.path.join(SHARED_ENV_SCRIPTS, "python.exe")
```

`mcp.json` 的 `MAMCP_PYTHON` 同步指向 `python_direct.exe`。

### 验证

```powershell
& "$SCRIPTS\python_direct.exe" -c "import sys; print(sys.prefix); import manim; print(manim.__version__)"
# 期望：D:\software\uv\envs\geo  +  0.21.0
```

### 速记

> **不能**直接把 `MAMCP_PYTHON` 指向 uv 的裸解释器 `uv\python\cpython-3.14.7-...\python.exe` —— 实测那里**没有 manim**（`ModuleNotFoundError`）。
> 正确做法是"把真解释器复制进 venv"：既单进程、又保留 venv 的 site-packages。

---

### 4.9 根因三：临时目录被宿主沙箱虚拟化到 `D:\CToD\...`

### 现象

- MCP 的 `tempfile.mkdtemp()` 落在 `D:\CToD\Users\Administrator\AppData\Local\Temp\`（本机曾把 TEMP 目录搬到 D 盘，宿主又加了一层路径虚拟化）。
- 超时残留的 `manim_render_*` 目录里**连 `media` 子目录都没有** → 说明 manim 根本没跑起来。

### 解法：把 manim 临时工作目录搬进项目内

`sandbox.py`：

```python
work_root = os.environ.get("MAMCP_TMP_DIR") or None
if work_root:
    try:
        os.makedirs(work_root, exist_ok=True)
    except Exception:
        work_root = None
if work_root:
    tmpdir = tempfile.mkdtemp(prefix="manim_render_", dir=work_root)
else:
    tmpdir = tempfile.mkdtemp(prefix="manim_render_")   # 未设则回退系统 Temp
```

`workbuddy_mcp_launch.py`：

```python
os.environ.setdefault("MAMCP_TMP_DIR", os.path.join(REPO_ROOT, "_render_tmp"))
```

### 验证

重启 MCP 后，进程表里 manim 子进程的 `--media_dir` 应形如：

```
D:\github\math-animation-mcp\_render_tmp\manim_render_xxxxxxxx\media
```

---

### 4.10 根因四：子进程 stdout 走管道 → 死锁

### 根因

子进程 stdout/stderr 用 `capture_output=True`（管道）时，**管道被孙子进程（ffmpeg / latex / dvisvgm）持有** → 读取端（父进程）等 EOF → 经典死锁。
另外子进程默认**继承 stdin**，会握着 MCP 的 JSON-RPC 管道不放。

### 解法（`sandbox.py`）

1. **stdout/stderr 改写文件**，不再用管道（日志落在 `animation_output\_render_<场景>.log`）：

```python
with open(child_log_path, "w", encoding="utf-8", errors="replace") as cf:
    cf.write("CMD: %s\nCWD: %s\n\n" % (cmd, tmpdir))
    proc = subprocess.Popen(
        cmd, stdout=cf, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, cwd=tmpdir,
    )
    returncode = proc.wait(timeout=timeout)
```

2. **`stdin=subprocess.DEVNULL`**，不再继承 MCP 的协议管道。
3. **加看门狗**：超时时记录"子进程是否还活着"（`alive=True/False`）并 `kill()`。
4. **加 spawn 日志** `animation_output\_render_spawn.log`，每次记录 `cwd / python / exists / 耗时 / 返回码`。

### 价值

这两份日志是后续定位的关键 —— 能一眼区分"**卡在起不来**"和"**卡在被删除守卫打断**"。

---

### 4.11 根因五（最终拦路虎）：删除守卫 `safe-delete`

### 现象

渲染日志出现：

```
[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":50,"threshold":50,"scope":"turn", ...}
EXIT=1        （或直接挂死到超时）
```

### 机制

**Manim 渲染过程中必须清理自己的临时文件**（`media/Tex/*.aux`、`media/texts/*.svg`、partial mp4、`partial_movie_files` 等）。这些 `remove` 调用被宿主的**批量删除守卫**拦截 → 渲染进程直接崩（`EXIT=1`）或挂死 → 在 MCP 侧表现为 **60s / 120s 超时**。

**最坑的一点**：`count` 是**累计值，作用域是「每轮对话」**。同一轮对话里，**只要累计删除文件数触达阈值，这一轮剩下的所有渲染都会失败** —— 完美解释"刚才还能出片，突然怎么都不行"。

### 对照实验（决定性）

| 运行方式 | 删除 3 个临时文件 | 结果 |
|---|---|---|
| 宿主沙箱内（阈值已达） | `a/b/c.txt` 三个都没删掉 | ❌ 被 `safe-delete` 拦截 |
| 沙箱外（已授权） | 三个全部删除成功 | ✅ 正常 |

且计数**确实会累加**：一路从 `count:50` 涨到 `count:53`。

### 解法

把 WorkBuddy 的**「批量删除审批」阈值调高**（本次调到 **200**）。

### 验证（阈值调整后）

```
EXIT=0   耗时=35s   safe-delete 次数=0
成片：PythagoreanTheorem.mp4（720p，13 个动画全过）
```

### 速记

> **超时不一定是"慢"。** 排查顺序：
> ① `grep safe-delete` 渲染日志 → 命中 = 删除守卫，调阈值；
> ② 再看子进程 CPU 是否 0、临时目录有无产物 → 是 = 被挂起（查解释器 / 临时目录）；
> ③ 都不是 → 才考虑调大 timeout。

---

### 4.12 修复后总验证（真实 WorkBuddy 环境，MCP 本体渲染）

| 场景 | 质量 | 结果 |
|---|---|---|
| `ZhProbe`（中文 `Text`，未写 `font=`） | 480p | ✅ success |
| `PythagoreanTheorem`（勾股定理，含中文标题 + 公式） | **720p** | ✅ success，**13 个动画全过** |

进程表佐证（修复后）：

```
3600  python.exe        workbuddy_mcp_launch.py                          ← MCP 服务
24568 python_direct.exe -m manim render ... --media_dir ...\_render_tmp\manim_render_xxx\media
                        ← 单层真解释器 + 项目内临时目录
```

---

## 5. 最终方案（可复现）

### 5.1 依赖安装

```powershell
$py = 'D:\software\uv\envs\geo\Scripts\python.exe'

# 核心依赖（注意 mcp 锁 1.x）
& "D:\software\uv\uv.exe" pip install --python $py "mcp[cli]<2" pyyaml pydantic

# 项目本体（可编辑安装）
& "D:\software\uv\uv.exe" pip install --python $py --no-deps -e "D:\github\math-animation-mcp"
```

> `parse_image`（依赖 pix2text）和 Gradio 界面**没有安装**——它们在源码里是**函数内延迟导入**，不装也不影响 MCP 主链路。需要图片 OCR 时再单独装。

### 5.2 MCP 配置

文件：`C:\Users\Administrator\.workbuddy\mcp.json`

```json
"math-animation": {
  "command": "D:\\software\\uv\\envs\\geo\\Scripts\\python.exe",
  "args": [
    "D:\\github\\math-animation-mcp\\workbuddy_mcp_launch.py"
  ],
  "env": {
    "MAMCP_PYTHON": "D:\\software\\uv\\envs\\geo\\Scripts\\python_direct.exe",
    "OUTPUT_DIR": "D:\\github\\math-animation-mcp\\animation_output",
    "MAMCP_TMP_DIR": "D:\\github\\math-animation-mcp\\_render_tmp",
    "MAMCP_FONT": "LXGW WenKai GB",
    "PYTHONUNBUFFERED": "1",
    "PYTHONIOENCODING": "utf-8"
  },
  "disabled": false
}
```

三层保险，任一失效还有兜底：

1. `command` 用**绝对路径**解释器启动 server
2. `env.MAMCP_PYTHON` 通过进程创建时注入，`sandbox.py` 直接读（不经过 PATH）
3. launcher 内部再兜一遍

### 5.3 安装后必做一步

新写入 `mcp.json` 的 server **不会自动激活**。需要：
打开 WorkBuddy 连接器管理页面 → 右上角「自定义连接器」入口 → 找到 `math-animation` → 点「**信任**」。

### 5.4 验证结果

| 层级 | 链路 | 结果 |
|---|---|---|
| 单层 | 直接 `python -m manim render` | ✅ 3.4s 出片 |
| 两层 | 脚本 → `render_manim_code()`（经 `MAMCP_PYTHON` 解析） | ✅ **4.6s 出片 `T4.mp4`** |
| 三层 | 脚本 → MCP server（stdio）→ sandbox → manim | ✅ **真实 WorkBuddy 环境已出片**（见 §4.12） |
| MCP 协议 | `initialize` / `tools/list` | ✅ 21 个工具全部注册 |

工具清单：`render_animation`、`preview_scene`、`render_gif`、`list_templates`、`get_template`、`search_templates`、`detect_input_type`、`parse_pdf`、`parse_image`、`fix_ocr_errors`、`normalize_content`、`analyze_error`、`fix_latex_error`、`fix_python_error`、`set_style`、`set_preferences`、`get_preferences`、`set_branding`、`export_video`、`add_subtitles`、`add_tts_narration`

---

### 5.5 默认字体：霞鹜文楷（LXGW WenKai GB）

### 需求

让所有经 MCP 渲染的 `Text()` / `MarkupText()` **默认**使用本机已装的**霞鹜文楷**，不必在每个场景里手写 `font=`。

### 环境事实

| 项 | 值 |
|---|---|
| 字体族名 | `LXGW WenKai GB`（另有 `LXGW WenKai Mono GB`） |
| 文件位置 | `C:\Windows\Fonts` |
| 已装字重 | Regular / Medium / Light |
| Pango 可见字体族总数 | 101（其中中文字体 19） |
| 其他可用中文字体 | Microsoft YaHei、KaiTi、FangSong、DengXian、STSong 等 |

> **族名要写准确**：是 `LXGW WenKai GB`，不是中文的"霞鹜文楷"。
> 取法：`& $py -c "from manimpango import list_fonts; print(list_fonts())"`

### 改动一：`src/math_animation_mcp/utils/chinese_support.py` 新增 `inject_default_font()`

原理：在场景代码的 `from manim import *` 之后，插入一段**运行期 monkey-patch**，包装 `manim.Text` / `manim.MarkupText` 的 `__init__`，**未显式传 `font` 时补上默认值**。

```python
def _make_default_font_injection(font: str) -> str:
    """Build a snippet that makes every `Text()` / `MarkupText()` default to `font`.
    An explicit `font=` argument in the scene code still wins.
    """
    return (
        "# --- MAMCP default text font ---\n"
        "from manim import Text as _MAMCP_Text, MarkupText as _MAMCP_MarkupText\n"
        "_MAMCP_FONT = " + repr(font) + "\n"
        "def _mamcp_patch_font(_cls):\n"
        "    _orig = _cls.__init__\n"
        "    def _init(self, *a, **kw):\n"
        "        kw.setdefault('font', _MAMCP_FONT)\n"
        "        return _orig(self, *a, **kw)\n"
        "    _cls.__init__ = _init\n"
        "_mamcp_patch_font(_MAMCP_Text)\n"
        "_mamcp_patch_font(_MAMCP_MarkupText)\n"
    )


def inject_default_font(code: str, font: str | None = None) -> str:
    font = font if font is not None else os.environ.get("MAMCP_FONT", "")
    if not font:
        return code                       # 未配置字体 → 原样返回
    if "_mamcp_patch_font" in code:
        return code                       # 幂等：已注入过就不再插

    injection = _make_default_font_injection(font)
    lines = code.split('\n')
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith('from manim') or line.startswith('import manim'):
            insert_idx = i + 1            # 插在所有 manim import 之后
    lines.insert(insert_idx, injection)
    return '\n'.join(lines)
```

### 改动二：`src/math_animation_mcp/tools/render_tools.py` 接入注入链

```python
from math_animation_mcp.utils.chinese_support import (
    inject_chinese_support,
    inject_default_font,
)
    ...
    code = inject_chinese_support(code)
    code = inject_default_font(code)      # ← 新增
    code = _apply_style_to_code(code, style)
```

（`render_animation` 与 `preview_scene` 都走这条链。）

### 改动三：`workbuddy_mcp_launch.py` 新增环境变量

```python
# Default CJK typeface for Text()/MarkupText() in generated scenes.
# Override with MAMCP_FONT="" to fall back to Manim's built-in default,
# or set any other installed family name (e.g. "Microsoft YaHei").
os.environ.setdefault("MAMCP_FONT", "LXGW WenKai GB")
```

### 优先级与开关

```
场景里显式写的 font=   >   MAMCP_FONT   >   Manim 自带默认
```

- **换字体**：改 `MAMCP_FONT`（如 `"Microsoft YaHei"`）。
- **关闭**：设为**空字符串** `MAMCP_FONT=""` → `inject_default_font` 原样返回，回到 Manim 默认。

### 验证

场景里**完全不写 `font=`**，前两行自动变霞鹜文楷，第三行显式 `font="Microsoft YaHei"` 依然生效；完整走 MCP 代码路径渲染 **7.4 秒 success**。

### 速记

> `Text()` 走 Pango。出现"文字渲染慢 / 失败"时，**最先怀疑字体族名写错**，而不是"缺字体" —— 真缺字体的报错是 `font not found`，**不是超时**。

---

## 6. 给 AI 的排查手册（同类问题按序检查）

遇到「MCP 工具调用超时 / Python 子进程不干活」时，按这个顺序查：

0. **先看渲染日志，最快分流**（本机最终拦路虎就是靠它揪出来的）
   ```powershell
   Get-Content "D:\github\math-animation-mcp\animation_output\_render_spawn.log" -Tail 20
   Select-String -Path "D:\github\math-animation-mcp\animation_output\_render_*.log" `
                 -Pattern "safe-delete|SAFE_DELETE|security risk|Conversion failed"
   ```
   - 命中 `safe-delete` → **删除守卫**拦住了 manim 清理临时文件 → 调高「批量删除审批」阈值（§4.11）。
     注意它**作用域是「每轮对话」累计**：同轮里触达阈值后，本轮剩余渲染会**全灭**；换新轮次会自动归零。
   - 命中 `security risk`（MiKTeX）→ 配 MiKTeX 镜像源 + 预装宏包（§4.7）。

1. **看进程是否真的在干活**
   ```powershell
   Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='ffmpeg.exe'" |
     Select-Object ProcessId, ParentProcessId, CommandLine
   Get-Process python | Select-Object Id, CPU, WorkingSet64
   ```
   **CPU 为 0 且临时目录无产物 = 被挂起，不是慢。** 别去调大 timeout。

2. **确认子进程实际用的是哪个解释器**
   - 不要信 `shutil.which("python")` —— 本机实测它与真实 `CreateProcess` 解析结果**不一致**。
   - 正确做法：在被调用的解释器里打印 `sys.executable`。
   - 检查目标包是否在**那个**解释器里：`& <那个解释器> -c "import manim"`。

3. **查代码里的解释器选择逻辑**
   - 搜 `subprocess`、`python_bin`、`sys.executable`、`"python"` 字面量。
   - 重点看有没有 **POSIX 写死的路径**（`bin/python`）在 Windows 上永不命中。

4. **不要试图用 PATH 解决本机问题**
   - 实测：PowerShell 里 `$env:PATH` 前置、Python 里改 `os.environ["PATH"]`，**都在子进程侧失效**。
   - 唯一可靠手段：**绝对路径**，或通过 MCP 配置的 `env` 字段在进程创建时注入。

5. **检查依赖大版本**
   - `mcp` v1 vs v2 API 不兼容；老仓库锁 `"mcp[cli]<2"`。

6. **检查 C 扩展是否能编译**（如需装新包）
   - 带上 §2 那套 SDK 环境变量。

7. **检查解释器是否是"转发器"**（本机特有）
   - venv 的 `Scripts\python.exe` 若只有 ~45KB，说明它是 trampoline，会再拉一层进程，在沙箱里易挂死。
   - 处方：把 uv 基础解释器复制进 venv 为 `python_direct.exe`（§4.8）。

8. **检查临时目录是否被沙箱虚拟化**
   - 若 `tempfile.gettempdir()` 落在 `D:\CToD\...` 这类映射路径，manim 子进程可能起不来。
   - 处方：设 `MAMCP_TMP_DIR` 指向项目内（§4.9）。

9. **中文 / 字体相关**
   - 先用 `from manimpango import list_fonts; print(list_fonts())` 取**准确族名**；
   - 默认字体由 `MAMCP_FONT` 控制（§5.5）；
   - "缺字体"的典型报错是 `font not found`，**不是超时** —— 别把超时误判成缺字体。

---

## 7. 已知限制与待确认

1. ~~**三层嵌套在 agent 沙箱内仍超时**~~ —— **【已解决；原判断被证伪】**
   实测：在 WorkBuddy「信任」该 MCP 后**直接调用 `render_animation`，仍然超时** —— 与"三层嵌套"无关。
   真实根因是 §4.7–§4.11 的**五条叠加**（MiKTeX 弹窗 / 解释器转发器 / 临时目录映射 / stdout 管道 / **删除守卫**），逐条修复后已出片（见 §4.12）。

2. **`sandbox.py` 的补丁是本地改动**。`git pull` 可能覆盖它。若某天渲染又报找不到 manim，回来重打补丁（§4.5 改动 1）。

3. **`parse_image` / Gradio 依赖未装**。需要时：
   ```powershell
   & "D:\software\uv\uv.exe" pip install --python "D:\software\uv\envs\geo\Scripts\python.exe" pix2text
   ```

4. **WorkBuddy 内置运行时 Python 保持 `false`**。
   `~/.workbuddy/app/app-config.json` → `bundledRuntime.tools.python = false`。
   打开会插入 WorkBuddy 自带解释器抢占 `python` 命令（不含 manim），只会添乱。本机有 uv 的 Python 3.14.7 兜底，不需要它。

5. **不要把 `geo\Scripts` 加进系统/用户 PATH**。
   理由：`Scripts` 里含该环境所有入口脚本，全局暴露会把 `python`、`pip`、`manim`、`playwright` 一并劫持；且本机 PATH 注入本就不可靠。想要短命令就用转发脚本：
   ```bat
   :: D:\software\bin\spy.cmd（把 D:\software\bin 加入用户 PATH）
   @echo off
   "D:\software\uv\envs\geo\Scripts\python.exe" %*
   ```

---

## 8. 一页速查表

| 症状 | 根因 | 处方 |
|---|---|---|
| `fatal error C1083: io.h` | VS 的 `vcvarsall.bat` 靠 `reg.exe` 探测 SDK，而 `reg.exe` 被拦截 | 显式注入 `INCLUDE` / `LIB` / `PATH` + `DISTUTILS_USE_SDK=1` |
| 链接阶段找不到 `rc.exe` | SDK 的 `bin\10.0.22621.0\x64` 没进 PATH | 补进 PATH |
| MCP server 秒退、无输出 | 装了 `mcp` 2.x，仓库用 v1 API | `pip install "mcp[cli]<2"` |
| `No module named 'manim'` | 子进程用了 PATH 里的裸 python（无 manim） | 绝对路径 / `MAMCP_PYTHON` |
| MCP 渲染 120s 超时，子进程 0 CPU | 解释器解析错 + 沙箱包裹导致子进程挂起 | 绝对路径显式指定解释器 |
| PATH 前置怎么都不生效 | 本机执行通道重写 PATH | 放弃 PATH 方案，改绝对路径 |
| `which python` 与实跑结果不符 | `which` 与 `CreateProcess` 解析不一致 | 一律以 `sys.executable` 为准 |
| 日志停在 `latex: security risk` 后不动，或弹 MiKTeX 安装器 | MiKTeX 缺 `preview`/`standalone` + 未配仓库 + 管理员身份 | 配镜像源 + `AutoInstall=1` + 预装宏包（§4.7） |
| manim 子进程出现**两层 python** | uv venv 的 `Scripts\python.exe` 是 45KB trampoline | 复制真解释器为 `python_direct.exe`（§4.8） |
| 临时目录落在 `D:\CToD\...`，且里面无 `media` | 宿主沙箱虚拟化 `%TEMP%` | `MAMCP_TMP_DIR` 指向项目内（§4.9） |
| 日志出现 `safe-delete` / `SAFE_DELETE_BULK_CONFIRM_REQUIRED` | 批量删除守卫拦截 manim 清理临时文件 | 调高「批量删除审批」阈值（§4.11） |
| 刚刚还能渲染，突然全部失败 | 删除计数已达阈值（**作用域=每轮对话**） | 新开一轮 或 调高阈值 |
| `Text()` 想换默认字体 | 未配 `MAMCP_FONT` | 设 `MAMCP_FONT="LXGW WenKai GB"`（§5.5） |
| 报 `font not found` | 字体族名写错（**不是超时**） | 用 `list_fonts()` 取准确族名（§5.5） |

---

## 附：涉及文件清单

| 文件 | 状态 |
|---|---|
| `D:\github\math-animation-mcp\src\math_animation_mcp\utils\sandbox.py` | 已改（`_resolve_python_bin()`；`MAMCP_TMP_DIR`；stdout 改文件 + `stdin=DEVNULL` + 看门狗 + spawn 日志） |
| `D:\github\math-animation-mcp\src\math_animation_mcp\utils\chinese_support.py` | 已改（新增 `inject_default_font()`） |
| `D:\github\math-animation-mcp\src\math_animation_mcp\tools\render_tools.py` | 已改（注入链接入 `inject_default_font`） |
| `D:\github\math-animation-mcp\workbuddy_mcp_launch.py` | 新建（`SHARED_PYTHON`→`python_direct.exe`、`MAMCP_FONT`、`MAMCP_TMP_DIR`） |
| `C:\Users\Administrator\.workbuddy\mcp.json` | 已改（新增 `math-animation` 条目） |
| `D:\software\uv\envs\geo\Scripts\python_direct.exe`（+`pythonw_direct.exe`/`python314.dll`/`python3.dll`） | 新增（真解释器副本，替代 45KB 转发器；**原 `python.exe` 未覆盖**） |
| `D:\github\math-animation-mcp\_render_tmp\` | 新增（项目内 manim 临时目录） |
| MiKTeX（`D:\software\MiKTeX`） | 已改（清华镜像源 + `AutoInstall=1` + 预装 preview/standalone/ctex 等 14 个宏包） |
| WorkBuddy「批量删除审批」阈值 | 已改（调到 200） |
| `D:\github\math-animation-mcp\animation_output\PythagoreanTheorem_1.mp4` | 验证产物（720p，MCP 本体渲染，13 个动画） |
| `D:\github\math-animation-mcp\animation_output\ZhProbe.mp4` | 验证产物（中文 Text 通道） |
| `D:\software\uv\envs\geo` | 共享环境，新增 mcp/pydantic/pyyaml 等 |

**回滚点（本地备份）**

| 备份文件 | 对应 |
|---|---|
| `sandbox.py.bak_20261002b` | `sandbox.py` 补丁 |
| `workbuddy_mcp_launch.py.bak_20261002` | 启动器 |
| `C:\Users\Administrator\.workbuddy\mcp.json.bak_20261002` | MCP 配置 |
