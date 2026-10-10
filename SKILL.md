---
name: baidu-pan-ops
version: 1.0.3
display_name: 百度网盘全能管家
display_name_en: Baidu Pan All-in-One Manager
description: 免 API 配置的百度网盘全能管家：上传、下载、列出、新建文件夹、重命名、移动、复制、删除、批量整理分类归档，一句话直达。双引擎设计——官方 CLI 稳定可靠，网页接口可操作全盘任意路径，扫码登录一次后全自动无头运行，凭据只在内存不落盘。This skill should be used when the user asks to 操作百度网盘（Baidu Netdisk / bdpan）里的文件：列出、上传、下载、重命名、移动、复制、删除、批量整理目录结构。触发词：百度网盘、网盘、bdpan、增删改查、移动文件、整理网盘。
description_zh: 免 API 配置的百度网盘文件管理：上传、下载、列出、新建文件夹、重命名、移动、复制、删除、批量整理分类归档，一句话直达。双引擎设计——官方 CLI 稳定可靠，网页接口可操作全盘任意路径（含根目录整理）；首次扫码登录一次，之后全自动无头运行，凭据只在内存传递不落盘。深层目录整目录搬移，批量操作先预览清单再执行。触发词：百度网盘、网盘、bdpan、文件管理、增删改查、移动文件、复制、重命名、整理网盘、分类归档。
description_en: "Zero-config Baidu Netdisk (Baidu Pan) file manager driven by natural language - upload, download, list, create folders, rename, move, copy, delete, and batch-organize files into categories. Dual-engine design - the official bdpan CLI for stable in-scope operations, plus a web-API route that reaches any path on the drive (including root-level organizing). Log in by scanning a QR code once, then it runs headlessly and fully automatically, with credentials kept in memory only. Deep folders are moved as whole directories, and every batch operation shows a preview list before execution. Triggers - Baidu Netdisk, Baidu Pan, bdpan, file management, CRUD, move files, copy, rename, organize drive, categorize archive."
agent_created: true
---

# Baidu Pan Ops

## Overview

对百度网盘执行完整的文件/目录增删改查与批量整理。提供两条互补路线：

- **bdpan CLI 路线**（`scripts/netdisk_crud.py`）：官方客户端，作用域严格限定 `/apps/bdpan`，稳定可靠；
- **网页接口路线**（`scripts/pan_api.py` + `scripts/get_cookie.py`）：使用用户自己的登录态，可操作全盘任意路径（如根目录整理、下载真实目录里的文件）。

路线选择规则：用户目标都在 `/apps/bdpan` 内 → 用 CLI 路线；需要操作全盘、做根目录整理、或**下载 `/apps/bdpan` 之外的真实文件** → 用网页接口路线（须先获得用户明确授权使用其登录态）。

## 前置条件

- CLI 路线：已安装 bdpan 并登录（`bdpan whoami` 可用）。可执行文件定位顺序：PATH → 环境变量 `BDPAN_EXE` → `%LOCALAPPDATA%/bdpan/bdpan.exe`。
- 网页接口路线：登录态 Cookie。首次使用运行 `python scripts/get_cookie.py setup`（弹出专用 Chrome 窗口扫码登录一次）；之后 `eval "$(python scripts/get_cookie.py get)"` 无头自动获取。Cookie 只在内存中传递。

## 使用示例（用户怎么说 → 怎么做）

用自然语言提出需求即可，技能会自动选路线、必要时先给预览再执行。

| 用户说法 | 实际执行 | 路线 |
|---|---|---|
| 「看看网盘里都有什么」 | `ls` 列根目录 / 递归列出 | CLI |
| 「把这个文件夹传到网盘备份」 | 递归 `put` 到 `/apps/bdpan/<名字>` | CLI |
| 「下载网盘里的 xx.pdf 到桌面」 | `get` 到本地指定文件路径 | 两条皆可 |
| 「下一本在 xx 目录里的书」 | `ls` 逐层定位 → `pan_api.py get` 拉直链下载（支持断点续传） | 网页 |
| 「把网盘里所有 PDF 挑出来放到一个文件夹」 | 先 `ls` 摸底 → 生成移动清单 → 用户确认 → 批量 `move` | 网页 |
| 「网盘太乱了，按类型整理一下」 | 摸底 → 分组方案 → dry-run 预览 → 分批整目录搬移 | 网页 |
| 「把 A 文件夹移到 B 下面」 | `mv --remote A --dest B` | 两条皆可 |
| 「把 xx 文件夹改个名」 | `rename --remote xx --name 新名` | 两条皆可 |
| 「删掉网盘里的临时目录」 | 列清单 → 用户确认 → `rm`（需显式确认） | 两条皆可 |

**典型交互示例（批量整理）**

> **用户**：网盘里 100 多个文件和文件夹全堆在根目录，帮我按类型整理一下。
>
> **技能**：① 先扫描全盘（`ls /`）→ ② 输出分组方案（如：文档资料 / 编程与算法 / 电子书 / 设计素材 / 软件安装包 / 待人工确认…，按实际内容定）→ ③ `move --dry-run` 给你看「哪个文件搬到哪个目录」的完整清单 → ④ 你确认后分批执行（单批 ≤ 100 项，自动节流）→ ⑤ 复查到位情况，输出执行记录。
>
> 深层目录（例如 7 层 1249 个文件的资料包）**整目录搬移**，不逐个文件操作。

**首次使用（网页路线，仅一次）**

```bash
python scripts/get_cookie.py setup    # 弹出专用浏览器窗口，扫码登录一次
eval "$(python scripts/get_cookie.py get)" && export BDUSS STOKEN   # 之后全自动无头获取
```

## 任务速查

### 列目录 / 查看内容（CLI 路线）

```bash
python scripts/netdisk_crud.py ls                        # 列 /apps/bdpan 根
python scripts/netdisk_crud.py ls    --remote subdir     # 列子目录
python scripts/netdisk_crud.py names --remote subdir     # 每行一个名字（供脚本精确断言）
```

### 上传 / 下载

CLI 路线（限 `/apps/bdpan` 作用域）：

```bash
python scripts/netdisk_crud.py put --local ./a.txt --remote subdir/a.txt
python scripts/netdisk_crud.py put --local ./some_dir --remote some_dir   # 目录递归上传
python scripts/netdisk_crud.py get --remote subdir/a.txt --local ./dl/a.txt   # 第二参数是文件路径，不是目录
```

**网页接口路线（全盘任意路径，需 BDUSS 环境变量）** —— 下载真实目录里的文件只能走这条：

```bash
eval "$(python scripts/get_cookie.py get)" && export BDUSS STOKEN
python scripts/pan_api.py get --path "/某分类目录/某子目录/某本书.pdf" --dest "D:/下载/某本书.pdf"
python scripts/pan_api.py get --path "/某目录/某文件.zip" --dest "D:/下载"   # --dest 是已存在目录时自动沿用文件名
```

`get` 的三条实测要点：
- **断点续传**：带 `Range` 头下载，被限速或中途断流时保留已下部分，**重跑同一条命令即从断点继续**（脚本自己判断服务端是否返回 206，不是 206 就从头来，避免拼出坏文件）。想强制重下加 `--no-resume`。
- **大小核对**：下完比对本地与远端字节数，不一致直接报失败——非会员限速下常见的「静默下半个文件」不会溜过去。
- **不做 md5 校验**：filemetas 返回的 md5 是百度自家变体编码（含 `i`/`v` 等非十六进制字符），不是标准 MD5。要校验完整性就用大小 + 文件自身格式（zip/epub 可开则完整）。
- 大文件（>100MB）请放后台跑，非会员速度可能只有几百 KB/s。

目录上传前先清掉本地缓存目录（`__pycache__`、`.git`），否则会被一并上传。
写操作成功后工具会轮询确认落地（远端最终一致，刚写完立即列举可能看不到）。

### 重命名 / 移动 / 复制（两条路线都可）

```bash
python scripts/netdisk_crud.py rename --remote a --name b
python scripts/netdisk_crud.py mv  --remote a --dest dest_dir
python scripts/netdisk_crud.py cp  --remote a --dest dest_dir

# 网页接口路线（全盘任意路径，需 BDUSS 环境变量）
python scripts/pan_api.py rename --path /a --name b
```

### 批量整理（网页接口路线）

批量移动前**必须先 dry-run** 给用户确认清单，再执行：

```bash
eval "$(python scripts/get_cookie.py get)" && export BDUSS STOKEN
python scripts/pan_api.py ls --dir /                     # 摸底
python scripts/pan_api.py move --src /a /b /c --dest /分类目录 --dry-run
python scripts/pan_api.py move --src /a /b /c --dest /分类目录   # 单批 ≤ 100 项，脚本自动分批
```

批量整理原则：
- **整目录搬移**：目录很深、文件很多但同类的，直接移动整个文件夹，不逐文件操作；
- 每批之间脚本已内置 1.5s 间隔（降低风控概率），不要再加并发；
- 移动是可逆的（反向 move 恢复）；删除需显式 `--yes`，批量删除前逐条向用户确认。

### 删除

```bash
python scripts/netdisk_crud.py rm --remote path --force    # CLI 路线（限 /apps/bdpan）
python scripts/pan_api.py rm --path /a --yes               # 网页路线（需显式 --yes）
```

删除是**进回收站**（非物理删除），可在网盘回收站恢复；脚本已自动处理写操作必需的
`bdstoken`（缺它会得到无提示的 `errno=8001`，详见参考文档第二节）。
**递归清空回收站需人机验证**（`errno=132`），脚本不绕过，请引导用户在网页端操作。

## 行为坑（务必先读）

bdpan 与网页接口都有大量**文档未写、只能实测发现**的行为坑（ls 三坑、退出码恒 0、
同名上传改名、App-Bound 加密 Cookie、系统目录不可移等）。执行任何写操作前，
按需查阅 `references/bdpan-pitfalls.md`。

Windows 平台特别注意：本地路径必须写成 `D:/...` 形式；Git Bash 的 `/d/...`、`/tmp/...`
传给 Windows 原生 Python 会被误解析（详见参考文档第三节）。

## 安全边界（不可违反）

1. Cookie/BDUSS 是账号级凭据：只在用户明确授权下使用；不落盘、不写日志、不进版本库、不发给第三方。
2. CLI 路线严格限定 `/apps/bdpan`（工具内置 fail-closed 校验，`../` 逃逸在本地即被拒绝）；不要尝试绕过。
3. 批量写操作（移动/删除）先 dry-run 展示清单，获得用户确认后再执行。
4. 不解密、不迁移、不共享任何登录凭据；登录态失效时引导用户重跑 `get_cookie.py setup` 重新扫码。
5. 温和节奏、小批量、无并发——这是非官方接口自动化下对账号风险最低的做法。

## 自测

```bash
bash scripts/selftest.sh     # CLI 路线端到端自测，沙箱目录自动清理
```
