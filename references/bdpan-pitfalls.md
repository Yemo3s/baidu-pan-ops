# bdpan CLI 与网盘网页接口：实测行为坑与安全边界

本文记录的是**文档没写、只能实测发现**的行为，是本技能存在的主要价值。

## 一、bdpan CLI（官方客户端）路线

### 四个 ls 行为坑（`netdisk_crud.py` 已内置处理）

| # | 行为 | 若不处理的后果 |
|---|---|---|
| 1 | `ls` 显式传作用域根（`/apps/bdpan`）时，返回「根目录自身」这一条，而不是它的内容 | 根目录被误读成只含一个 `bdpan` 目录 |
| 2 | `--json` 模式下「目录不存在」返回 `{"code":1,"error":"...错误码 -9..."}`，而不是空数组 | 「不存在」和「空目录」混为一谈 |
| 3 | 查询一个空目录时，回显该目录自身这一条 | 空目录被显示成 1 项 |
| 4 | 以上所有场景退出码都是 `0` | 判断成败不能看退出码，必须解析输出 |

### 其他实测行为

- **同名上传不覆盖**：`upload` 遇同名会改名为 `x(1)` 落盘，而不是覆盖。要覆盖需先删后传。
- **download 第二参数是目标文件路径**，不是目录。传目录会把目录当文件改写，报 `rename ... Access is denied`。
- **远端接口偶发 `errno=-9`**（限流/抖动），对同一命令重试即可（工具内置 3 次重试）。
- **退出码恒 0，写操作也一样**：`upload` / `mv` / `rm` 失败时退出码仍可能是 `0`。
  必须扫描输出中的失败标记（`失败` / `错误` / `errno=` / `access is denied`），
  更可靠的是**写入后轮询目标路径确认落地**——本工具两者都做了（`write_op`）。
- **所有写操作都是最终一致的**：上传/移动/删除返回成功后，**紧接着的列举可能看不到变化**
  （实测：上传目录报成功后立即列举该目录仍报「不存在」，数秒后才出现）。
  断言必须轮询重试，不能「查一次就判失败」。
- **递归上传会带上本地缓存目录**：`__pycache__`、`.git` 等会被一并上传，
  上传代码目录前先清理。

### 本地/远端路径写法

工具用 `resolve()` 把远端路径强制限定在 `/apps/bdpan`：

| 远端输入 | 结果 |
|---|---|
| `subdir/a.txt` | 相对作用域根 → `/apps/bdpan/subdir/a.txt` |
| `/apps/bdpan/subdir/a.txt` | 绝对且合法 → 通过 |
| `/subdir/a.txt` | 绝对但在作用域外 → **拒绝**（想表达相对路径时不要以 `/` 开头） |
| `/apps/bdpan/../outside` | 规范化后越界 → **拒绝** |

本地路径必须用 Windows 形式（见第三节）。

### 作用域边界（重要）

bdpan 官方把写命令作用域声明为 `/apps/bdpan`：
- 越界写操作由**客户端 PathGuard** 在本地拦截，报「路径超出授权目录范围」，无任何开关放宽；
- 对作用域外的真实路径，`ls` 也不可读（只有 `search` 关键词搜索能命中全盘）；
- CLI 的 token 加密落盘（`enc:v1:...` + `.token_key`），**不要**尝试解密或迁移凭据。

要操作 `/apps/bdpan` 之外的文件，用 `pan_api.py`（网页接口路线）。

## 二、网页接口路线（`pan_api.py`）

### 接口要点

| 接口 | 说明 |
|---|---|
| `GET /api/quota` | 容量；也可用作登录态有效性探针（`errno=-6` 表示 BDUSS 失效） |
| `GET /api/list` | 列目录，分页 `page`/`num`，注意按返回行数是否满页判断翻页 |
| `POST /api/create` | 建目录；`errno=12` 表示已存在（可视为成功） |
| `POST /api/filemanager?opera=move` | 移动，`async=1` 返回 `taskid`，用 `GET /api/taskquery` 轮询至 `status=success` |
| `POST /api/filemanager?opera=rename/delete` | 同上，走异步任务 |

- 移动/删除**单批 ≤ 100 项**，超了要分批；批间 `sleep` 1-2 秒降低风控概率。
- `apps`、`百度云解压` 等系统目录**不可移动/改名**（返回 `errno=2`），属服务端保护，不是故障。
- 移动是可逆的（反向 move 即可恢复）；删除会进回收站，但批量删除仍需谨慎。

### Cookie 获取：为什么走 CDP 而不是解密数据库

Chrome 127+ 对 Cookie 启用 **App-Bound 加密**（Local State 出现 `app_bound_encrypted_key`，
密文前缀 `v20`），外部进程拿 DPAPI 解出的密钥已无法解出 Cookie 明文。
`get_cookie.py` 的替代路线：

1. 维护一个**专用 Chrome 配置目录**（`%LOCALAPPDATA%/baidu-pan-helper/profile`），与日常浏览器隔离；
2. `setup` 模式弹出该配置的 Chrome，人工登录一次（脚本轮询 CDP `Storage.getCookies` 直到 BDUSS 出现）；
3. 之后 `get` 模式无头启动同一配置，经 CDP 读 Cookie 输出到 stdout，用完即关。

Chrome 136 起 `--remote-debugging-port` 对**默认用户配置**失效，必须配自定义
`--user-data-dir` —— 本方案恰好符合（专用目录），因此长期可用。

## 三、本地路径写法（Windows + Git Bash 必读）

在 Git Bash / MSYS 环境里，`bdpan` 是 Windows 原生程序，而 Python 也是 Windows 原生解释器，
因此**本地路径必须用 Windows 形式**：

| 写法 | 结果 |
|---|---|
| `D:/data/a.txt` | ✅ 正常 |
| `D:\data\a.txt` | ✅ 正常 |
| `/d/data/a.txt` | ❌ 被解析为 `d:\d\data\a.txt`，报「本地路径不存在」 |
| `/tmp/x.txt`（`mktemp -d` 产物） | ❌ 被解析为 `d:\tmp\x.txt` |

bash 脚本里传路径给 Windows Python 前，先转换：

```bash
to_win() { command -v cygpath >/dev/null 2>&1 && cygpath -m "$1" || printf '%s' "$1"; }
T="$(to_win "$(cd "$(dirname "$0")" && pwd)/netdisk_crud.py")"
W="$(to_win "$(mktemp -d)")"
```

另：本机默认 `bash` 若指向 WSL 启动器（`C:\Program Files\WSL\wsl.exe`）会被安全策略拦截，
需改用 Git 自带的 bash 绝对路径执行脚本。

## 四、安全与合规边界（发布者与使用者都应遵守）

1. Cookie/BDUSS 是**账号级凭据**（不止文件权限），只在用户明确授权下使用；
   不落盘、不写日志、不进版本库、不发给任何第三方。
2. `rm`/批量移动前先 `--dry-run` 或列举确认；宁可多问一句，不做不可逆猜测。
3. 本技能不绕过任何官方限制：bdpan 路线严格限 `/apps/bdpan`；网页接口路线使用用户
   自己的登录态，与用户本人手点网页等效。
4. 非官方接口自动化存在账号风控的理论风险：温和节奏、小批量、无并发，是实践上
   的最低风险姿势。
