# 百度网盘全能管家 · baidu-pan-ops

**免 API 配置的百度网盘文件管理技能** —— 上传、下载、列出、新建文件夹、重命名、移动、复制、删除、批量整理分类归档，一句话直达。

<p>
  <img alt="python" src="https://img.shields.io/badge/python-3.8%2B-blue">
  <img alt="deps" src="https://img.shields.io/badge/dependencies-none-brightgreen">
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green">
</p>

这是一个 [WorkBuddy](https://www.workbuddy.cn) Agent Skill，也可以脱离 Agent **当普通命令行工具用**（纯 Python 标准库，零第三方依赖）。

---

## 它和同类工具的差别

| | 常见做法 | 本项目 |
|---|---|---|
| 上手门槛 | 需申请 AppID / Secret，或配置 MCP | **不用申请任何 API**，登录一次即可 |
| 能操作的路径 | 多数受限于官方应用沙箱 `/apps/<app>` | **双引擎**：CLI 路线守沙箱；网页接口路线可达全盘任意路径 |
| 批量整理 | 靠循环调接口，逐文件操作 | **整目录搬移**，单批 ≤ 100 项自动分批 + 节流 |
| 可靠性 | 只看接口返回码 | 识别「退出码恒 0 的假成功」+ 写入后轮询校验，**端到端自测 20 项** |
| 凭据处理 | 常落盘 / 写日志 | **只在内存传递**，不落盘、不写日志、不进版本库 |

## 两条路线

```
                    ┌─────────────────────────────┐
   目标在 /apps/bdpan │  路线 A：官方 bdpan CLI      │  scripts/netdisk_crud.py
                    │  作用域严格受限，稳定可靠    │
                    └─────────────────────────────┘
                    ┌─────────────────────────────┐
   目标在全盘任意路径 │  路线 B：网盘网页接口         │  scripts/pan_api.py
   （如根目录整理）   │  使用你自己的登录态          │  + scripts/get_cookie.py
                    └─────────────────────────────┘
```

- **路线 A（CLI）**：调用官方 `bdpan` 客户端，作用域由官方限定在 `/apps/bdpan`。本工具在此基础上做了 fail-closed 路径校验（`../` 逃逸在本地就被拒绝，不发请求）。
- **路线 B（网页接口）**：与网盘网页前端同一套接口，用你自己的登录态，可操作**全盘任意路径**——适合「根目录太乱，按类型整理」这类真实场景。

> 路线 B 是**非官方开放接口**，等价于你本人手点网页，但请自行评估账号风控风险（见 [安全与合规](#安全与合规)）。

## 功能

- **增删改查**：`ls` / `names` / `mkdir` / `put`（上传，支持目录递归）/ `get`（下载）/ `rename` / `mv` / `cp` / `rm`
- **批量整理**：建分类目录 → 生成移动清单 → `--dry-run` 预览 → 分批执行 → 逐组校验
- **登录态自动化**：扫码登录一次，之后无头复用（走 CDP，不碰你日常浏览器的数据）
- **能力探测**：`quota` 查容量（同时可判断登录态是否有效）

## 快速开始

### 环境要求

- Python 3.8+（无第三方依赖）
- 路线 A：已安装并登录官方 `bdpan` 客户端
- 路线 B：本机有 Chrome

### 安装

作为 Agent Skill 使用：把本仓库放进技能目录即可。

```bash
git clone https://github.com/Yemo3s/baidu-pan-ops.git ~/.workbuddy/skills/baidu-pan-ops
```

当命令行工具用：直接跑 `scripts/` 下的脚本，无需安装。

### 路线 A：官方 CLI

```bash
PY=python
CRUD=scripts/netdisk_crud.py

$PY $CRUD ls                                   # 列作用域根
$PY $CRUD ls   --remote subdir                 # 列子目录
$PY $CRUD names --remote subdir                # 只输出名字（每行一个，方便脚本断言）
$PY $CRUD mkdir --remote subdir/new
$PY $CRUD put  --local ./a.txt --remote subdir/a.txt
$PY $CRUD put  --local ./some_dir --remote some_dir    # 目录递归上传
$PY $CRUD get  --remote subdir/a.txt --local ./dl/a.txt  # 第二参数是「文件路径」不是目录
$PY $CRUD rename --remote subdir/a.txt --name b.txt
$PY $CRUD mv   --remote subdir/a.txt --dest subdir2
$PY $CRUD cp   --remote subdir/a.txt --dest subdir2
$PY $CRUD rm   --remote subdir --force         # 不加 --force 会要求确认
```

`bdpan` 可执行文件定位顺序：`PATH` → 环境变量 `BDPAN_EXE` → `%LOCALAPPDATA%/bdpan/bdpan.exe`。

### 路线 B：网页接口

首次（只做一次）：

```bash
python scripts/get_cookie.py setup
# 会弹出一个「专用 Chrome 窗口」（独立配置，不影响你日常浏览器）
# 在里面扫码登录 pan.baidu.com，脚本检测到登录态后自动完成配置
```

之后全自动：

```bash
eval "$(python scripts/get_cookie.py get)" && export BDUSS STOKEN

python scripts/pan_api.py quota                        # 容量 / 登录态探针
python scripts/pan_api.py ls --dir /                    # 列根目录
python scripts/pan_api.py mkdir --path /资料
python scripts/pan_api.py move --src /a /b /c --dest /资料 --dry-run   # 先预览
python scripts/pan_api.py move --src /a /b /c --dest /资料             # 再执行
python scripts/pan_api.py rename --path /a --name b
python scripts/pan_api.py rm --path /a --yes            # 需显式 --yes
```

也可以直接给 `BDUSS` 环境变量（值来自浏览器 Cookie），跳过 `get_cookie.py`：

```bash
export BDUSS='...' STOKEN='...'
python scripts/pan_api.py quota
```

### 批量整理示例

```bash
eval "$(python scripts/get_cookie.py get)" && export BDUSS STOKEN

# 1) 摸底
python scripts/pan_api.py ls --dir /

# 2) 建分类目录
for d in 文档资料 编程与算法 电子书 设计素材 软件安装包; do
  python scripts/pan_api.py mkdir --path "/$d"
done

# 3) 预览再执行（脚本内置单批 ≤ 100 项分批 + 1.5s 间隔）
python scripts/pan_api.py move --src /old_a /old_b --dest /文档资料 --dry-run
python scripts/pan_api.py move --src /old_a /old_b --dest /文档资料
```

**整理原则**：目录很深、文件很多但同类的，**直接搬整个文件夹**，不逐文件操作。

## 实测行为坑（本项目的核心价值）

`bdpan` 与网页接口都有大量文档未写、只能实测发现的行为。全部坑位说明见
[`references/bdpan-pitfalls.md`](references/bdpan-pitfalls.md)，摘几条最要命的：

| 行为 | 若不知道的后果 |
|---|---|
| `ls` 显式传作用域根，返回的是「根目录自身」而非其内容 | 根目录被误读成只含一个文件夹 |
| `--json` 下「不存在」返回 `{"code":1,...}` 而不是空数组 | 「不存在」与「空目录」混为一谈 |
| **退出码恒为 0**（失败也是 0） | 判成败不能看退出码，必须解析输出 |
| 所有写操作**最终一致**：上传成功后立即列举仍可能查不到 | 误判为上传失败、重复上传 |
| `download` 第二个参数是目标**文件**路径，不是目录 | 目录被当文件改写，报 `Access is denied` |
| 同名上传**不覆盖**，而是改名成 `x(1)` | 以为覆盖了，实际堆了重复文件 |
| Chrome 127+ Cookie 启用 App-Bound 加密 | 从数据库解密 Cookie 的路子已不可行 |

本项目对这些坑都有内置处理（`write_op` 失败标记扫描 + 写入后轮询校验、`get` 落盘校验、`ls` 三坑规整等）。

## 自测

```bash
bash scripts/selftest.sh        # 越界拒绝 3 项 + C/R/U/D 全链路，跑完自动清理沙箱
```

覆盖：fail-closed 越界拒绝 → mkdir → 上传 → 列目录/精确名匹配 → 重命名 → 移动 → 复制 → 下载字节比对 → 递归删除 → 校验无残留。

## 安全与合规

- **凭据**：`BDUSS` / `STOKEN` 是**账号级凭据**。本工具只在内存中使用，不落盘、不写日志、不进版本库、不发往任何第三方。
- **不解密、不迁移**官方客户端的加密凭据（`enc:v1:...`）。
- **不读取**你日常浏览器的配置或 Cookie；Cookie 获取器只使用自己创建的专用配置目录。
- **不绕过官方限制**：CLI 路线严格限定 `/apps/bdpan`；网页接口路线使用你自己的登录态，与你本人操作网页等效。
- **删除类操作**：CLI 路线默认需要确认，网页接口路线需显式 `--yes`；建议先用 `--dry-run` 看清单。
- **风控提示**：网页接口是非官方开放接口，自动化操作存在账号被风控的**理论风险**。本工具采取温和节奏（批量自动分批、批间 1.5s 间隔、无并发）以降低概率，但无法保证为零，请自行评估后使用。

## 免责声明

- 本项目与百度公司无任何关联，非官方项目。
- 本项目仅调用你本人账号的常规文件操作能力，不提供任何破解、越权或绕过付费限制的功能。
- 使用本项目产生的任何后果（包括但不限于账号风控、数据误操作）由使用者自行承担。涉及删除等不可逆操作前请务必自行确认。

## 许可

[MIT](LICENSE)

---

## English

**baidu-pan-ops** is a zero-config Baidu Netdisk (Baidu Pan) file manager, usable both as a WorkBuddy Agent Skill and as a plain CLI tool (pure Python standard library, no third-party dependencies).

- **Dual engine**: route A drives the official `bdpan` CLI (sandboxed to `/apps/bdpan`, fail-closed path checks); route B talks to the same web endpoints the Pan web UI uses, so it can reach **any path on the drive** — ideal for reorganizing a messy root directory.
- **No API keys**: no AppID/Secret to apply for. Log in once with a QR scan through a dedicated Chrome profile (your daily browser is untouched), then run headlessly.
- **Batch organizing**: move whole directories instead of file-by-file, auto-split into batches of ≤ 100 with throttling.
- **Battle-tested**: a catalogue of undocumented `bdpan` behaviors is handled in code — including the fact that its exit code is always `0` (even on failure) and that every write is eventually consistent.
- **Privacy-first**: credentials live in memory only — never written to disk, logs, or the repo.

```bash
python scripts/netdisk_crud.py ls                    # route A
eval "$(python scripts/get_cookie.py get)"; export BDUSS STOKEN
python scripts/pan_api.py ls --dir /                 # route B
```

See [`references/bdpan-pitfalls.md`](references/bdpan-pitfalls.md) for the full list of field-tested pitfalls (in Chinese).

Unofficial project, not affiliated with Baidu. Use at your own risk. Licensed under MIT.
