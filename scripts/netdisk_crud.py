# -*- coding: utf-8 -*-
"""百度网盘 CRUD 工具（基于官方 bdpan CLI）

作用域：远端路径一律限定在 /apps/bdpan 之下 —— 这与 bdpan 官方声明的作用域一致，
本工具是在使用许可范围内提供完整增删改查，不是绕过任何限制。

设计原则：
  - fail-closed：任何越界路径在本地即被拒绝，不发起调用
  - 删除默认需要确认；仅当显式 --force 才跳过
  - 全部远端操作通过 bdpan CLI（官方客户端）执行

用法：
  python netdisk_crud.py ls    [--remote 目录]
  python netdisk_crud.py names --remote 目录       # 只列名字，每行一个（供脚本断言）
  python netdisk_crud.py mkdir --remote 新目录
  python netdisk_crud.py put   --local 本地路径 --remote 远端路径
  python netdisk_crud.py get   --remote 远端路径 --local 本地文件
  python netdisk_crud.py rename --remote 原路径 --name 新名
  python netdisk_crud.py mv    --remote 源 --dest 目标目录
  python netdisk_crud.py cp    --remote 源 --dest 目标目录
  python netdisk_crud.py rm    --remote 路径 [--force]

bdpan 可执行文件定位顺序：
  1. PATH 里的 bdpan
  2. 环境变量 BDPAN_EXE
  3. %LOCALAPPDATA%/bdpan/bdpan.exe
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import shutil
import subprocess
import sys
import time

SCOPED_ROOT = "/apps/bdpan"


def _find_bdpan() -> str:
    found = shutil.which("bdpan")
    if found:
        return found
    env = os.environ.get("BDPAN_EXE", "")
    if env and os.path.exists(env):
        return env
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    cand = os.path.join(base, "bdpan", "bdpan.exe")
    return cand if os.path.exists(cand) else "bdpan"


BDPAN = _find_bdpan()


class ScopeError(Exception):
    """请求的远端路径超出本工具允许的作用域。"""


def resolve(remote: str | None) -> str:
    """把用户输入的远端路径规范化，并强制限定在 SCOPED_ROOT 之下。"""
    if not remote or remote.strip() in ("", "/", "."):
        return SCOPED_ROOT
    p = remote.strip().replace("\\", "/")
    if not p.startswith("/"):
        p = f"{SCOPED_ROOT}/{p}"

    # 规范化 .. 与重复斜杠后再判定，避免用 ../ 逃逸
    norm = posixpath.normpath(p)
    if norm != SCOPED_ROOT and not norm.startswith(SCOPED_ROOT + "/"):
        raise ScopeError(
            f"远端路径超出允许范围：{remote}\n"
            f"  本工具仅可操作 {SCOPED_ROOT} 及其子目录。\n"
            f"  这是 bdpan 官方声明的作用域，不是本工具额外加的限制。"
        )
    return norm


def _is_transient(text: str) -> bool:
    """bdpan 对远端接口的偶发失败（限流/抖动）会返回 errno，可重试。"""
    low = text.lower()
    return "errno=-9" in text or "error errno" in low


def run(args: list[str], interactive: bool = False, retries: int = 1) -> tuple[bool, str]:
    last = ""
    for attempt in range(max(1, retries)):
        ok, last = _run_once(args, interactive)
        if ok:
            return True, last
        if attempt + 1 < max(1, retries) and _is_transient(last):
            time.sleep(2)
            continue
        break
    return False, last


def _run_once(args: list[str], interactive: bool) -> tuple[bool, str]:
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(k, None)
    cmd = [BDPAN, *args, "--no-check-update"]
    try:
        p = subprocess.run(
            cmd, capture_output=True, timeout=1800, env=env,
            input=None if interactive else b"",
        )
    except FileNotFoundError:
        return False, f"找不到 bdpan 可执行文件：{BDPAN}"
    except subprocess.TimeoutExpired:
        return False, "操作超时"

    out = (p.stdout or b"") + (p.stderr or b"")
    for enc in ("utf-8", "gbk", "latin1"):
        try:
            text = out.decode(enc)
            break
        except Exception:
            continue
    return p.returncode == 0, text.strip()


def show(ok: bool, text: str) -> int:
    print(text if text else ("(完成)" if ok else "(无输出)"))
    print(f"[{'OK' if ok else '失败'}]", file=sys.stderr)
    return 0 if ok else 1


# ------------------------------------------------------------------ 写操作判定
# bdpan 的退出码恒为 0（包括失败），因此写操作必须：
#   1) 扫描输出里的失败标记；2) 写入后轮询确认结果真的落地（远端是最终一致的）。
FAIL_MARKERS = ("失败", "错误", "errno=", "access is denied", "not exist")


def _failed_output(text: str) -> bool:
    low = text.lower()
    return any(m in low if m.isascii() else m in text for m in FAIL_MARKERS)


def _remote_present(remote: str, expect_present: bool = True,
                    tries: int = 6, delay: float = 2.0) -> bool:
    """轮询确认 remote 是否存在（处理最终一致性：刚写入马上查可能查不到）。"""
    if remote == SCOPED_ROOT:
        return expect_present
    parent = posixpath.dirname(remote) or SCOPED_ROOT
    base = posixpath.basename(remote)
    for _ in range(max(1, tries)):
        status, items = _list_items(parent)
        if status == "ok":
            present = any(it.get("server_filename") == base for it in items)
            if present == expect_present:
                return True
        elif not expect_present:
            return True  # 连父目录都没了，等价于「已不存在」
        time.sleep(delay)
    return False


def write_op(args: list[str], verify: str | None = None,
             expect_present: bool = True, interactive: bool = False,
             retries: int = 1) -> tuple[bool, str]:
    """执行写命令：识别失败标记 + 写入后校验，避免「退出码 0 的假成功」。"""
    ok, text = run(args, interactive=interactive, retries=retries)
    if ok and _failed_output(text):
        ok = False
    if ok and verify:
        state = "存在" if expect_present else "已不存在"
        if not _remote_present(verify, expect_present=expect_present):
            ok = False
            text = f"{text}\n[校验失败] 操作后轮询多次，{verify} 并未{state}（远端可能未真正生效）"
    return ok, text


# ------------------------------------------------------------------ 各操作
def _list_items(remote: str):
    """集中处理 bdpan ls 的三个坑，返回 (status, payload)。

    status:
      "ok"       -> payload 为条目列表
      "notfound" -> payload 为错误说明字符串
      "raw"      -> payload 为原始文本（非 JSON，无法解析成列表）
    """
    # 坑一：显式传作用域根时，bdpan 返回「根目录自身」而非其内容；不传路径才是列内容。
    args = ["ls"] if remote == SCOPED_ROOT else ["ls", remote]
    ok, text = run([*args, "--json"], retries=3)
    if not ok:
        return "raw", text
    try:
        data = json.loads(text)
    except Exception:
        return ("notfound", f"{remote} 不存在") if "不存在" in text else ("raw", text)

    # 坑二：--json 模式下「不存在」不是空数组，而是 {"code":1,"error":"...-9..."}
    #       若只取 data["list"] 会得到空值，被误判成「空目录」。
    if isinstance(data, dict):
        if data.get("error") or (data.get("code") not in (None, 0)):
            return "notfound", f"{remote} 不存在：{data.get('error') or data}"
        items = data.get("list") or []
    else:
        items = data or []

    # 坑三：查一个空目录时，bdpan 回显该目录自身这一条，而非空列表。
    if len(items) == 1 and items[0].get("isdir") and items[0].get("path") == remote:
        items = []
    return "ok", items


def _human(size) -> str:
    size = size or 0
    if size >= 1024 ** 3:
        return f"{size/1024**3:.2f} GB"
    if size >= 1024 ** 2:
        return f"{size/1024**2:.1f} MB"
    if size >= 1024:
        return f"{size/1024:.0f} KB"
    return f"{size} B"


def op_ls(a) -> int:
    remote = resolve(a.remote)
    status, payload = _list_items(remote)
    if status == "notfound":
        return show(False, payload)
    if status == "raw":
        return show(True, payload)
    items = payload
    print(f"{remote}  （{len(items)} 项）")
    print("-" * 64)
    if not items:
        print("  (空)")
    for it in items:
        kind = "DIR " if it.get("isdir") else "FILE"
        print(f"  {kind} {_human(it.get('size')):>10}  {it.get('server_filename', '')}")
    return 0


def op_names(a) -> int:
    """只打印条目名（每行一个）。供脚本做「精确名匹配」断言，
    避免依赖列格式解析 —— 名字子串（src vs renamed_src）不会误判。"""
    remote = resolve(a.remote)
    status, payload = _list_items(remote)
    if status != "ok":
        print(payload if isinstance(payload, str) else "", file=sys.stderr)
        return 1
    for it in payload:
        print(it.get("server_filename", ""))
    return 0


def op_mkdir(a) -> int:
    target = resolve(a.remote)
    if target == SCOPED_ROOT:
        return show(True, f"作用域根 {SCOPED_ROOT} 已存在（无需创建）")
    return show(*write_op(["mkdir", target], verify=target))


def op_put(a) -> int:
    local = os.path.abspath(a.local)
    if not os.path.exists(local):
        return show(False, f"本地路径不存在：{local}")
    remote = resolve(a.remote)
    return show(*write_op(["upload", local, remote], verify=remote, retries=3))


def op_get(a) -> int:
    remote = resolve(a.remote)
    local = os.path.abspath(a.local)
    # bdpan 的 <local> 是「目标文件路径」而非目录：
    # 传目录会让它把目录本身当文件改写（报 rename ... Access is denied）。
    if os.path.isdir(local) or a.local.endswith(("/", "\\")):
        os.makedirs(local, exist_ok=True)
        local = os.path.join(local, posixpath.basename(remote))
    else:
        os.makedirs(os.path.dirname(local), exist_ok=True)
    ok, text = run(["download", remote, local], retries=3)
    if ok and not os.path.exists(local):
        ok, text = False, f"{text}\n[校验失败] 下载命令未落盘：{local}"
    return show(ok, text)


def op_rename(a) -> int:
    src = resolve(a.remote)
    dst = posixpath.join(posixpath.dirname(src), a.name)
    return show(*write_op(["rename", src, a.name], verify=dst))


def op_mv(a) -> int:
    src = resolve(a.remote)
    dest = resolve(a.dest)
    return show(*write_op(["mv", src, dest],
                          verify=posixpath.join(dest, posixpath.basename(src))))


def op_cp(a) -> int:
    src = resolve(a.remote)
    dest = resolve(a.dest)
    return show(*write_op(["cp", src, dest],
                          verify=posixpath.join(dest, posixpath.basename(src))))


def op_rm(a) -> int:
    target = resolve(a.remote)
    if target == SCOPED_ROOT and not a.force:
        return show(False, f"拒绝对作用域根 {SCOPED_ROOT} 执行删除（如确需，请显式 --force）")
    args = ["rm", target] + (["-f"] if a.force else [])
    return show(*write_op(args, verify=target, expect_present=False,
                          interactive=not a.force))


def main() -> int:
    ap = argparse.ArgumentParser(description="百度网盘 CRUD（作用域 /apps/bdpan）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ls");      p.add_argument("--remote", default=None); p.set_defaults(fn=op_ls)
    p = sub.add_parser("names");   p.add_argument("--remote", default=None); p.set_defaults(fn=op_names)
    p = sub.add_parser("mkdir");   p.add_argument("--remote", required=True); p.set_defaults(fn=op_mkdir)
    p = sub.add_parser("put");     p.add_argument("--local", required=True); p.add_argument("--remote", required=True); p.set_defaults(fn=op_put)
    p = sub.add_parser("get");     p.add_argument("--remote", required=True); p.add_argument("--local", required=True); p.set_defaults(fn=op_get)
    p = sub.add_parser("rename");  p.add_argument("--remote", required=True); p.add_argument("--name", required=True); p.set_defaults(fn=op_rename)
    p = sub.add_parser("mv");      p.add_argument("--remote", required=True); p.add_argument("--dest", required=True); p.set_defaults(fn=op_mv)
    p = sub.add_parser("cp");      p.add_argument("--remote", required=True); p.add_argument("--dest", required=True); p.set_defaults(fn=op_cp)
    p = sub.add_parser("rm");      p.add_argument("--remote", required=True); p.add_argument("--force", action="store_true"); p.set_defaults(fn=op_rm)

    a = ap.parse_args()
    try:
        return a.fn(a)
    except ScopeError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
