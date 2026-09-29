# -*- coding: utf-8 -*-
"""百度网盘网页版接口客户端（与网盘网页前端同一套接口）。

适用场景：需要操作 bdpan CLI 作用域（/apps/bdpan）之外的文件，或做批量目录整理。
登录态来源：环境变量 BDUSS（必需）/ STOKEN（可选），或用同目录 get_cookie.py 自动获取。
Cookie 只在内存中使用，本脚本绝不把 Cookie 写入文件或日志。

安全边界：
  - rm 需要显式 --yes 才执行
  - batch-move 支持 --dry-run 先看清单
  - 只做 建目录/移动/重命名/删除/列举 五类操作，不碰分享、不碰账号设置

用法：
  python pan_api.py quota
  python pan_api.py ls --dir /
  python pan_api.py mkdir --path /目标目录
  python pan_api.py move --src /a /b --dest /目标目录
  python pan_api.py rename --path /a --name 新名
  python pan_api.py rm --path /a /b --yes

常见 errno：
  0 成功 | 2 参数错误/路径不存在 | 12 已存在 | -6 身份验证失败（BDUSS 无效）
  -8 目标已有同名 | 111 token 失效 | -70 文件不存在
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://pan.baidu.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
REFERER = "https://pan.baidu.com/disk/main"

ERRNO_TEXT = {
    0: "成功", 2: "参数错误/路径不存在", 12: "文件或目录已存在",
    -6: "身份验证失败（BDUSS 无效或过期）", 111: "token 失效",
    -70: "文件不存在", -8: "目标目录下已有同名文件",
}


class Pan:
    def __init__(self, bduss: str, stoken: str = ""):
        if not bduss:
            raise SystemExit("缺少 BDUSS：设置环境变量 BDUSS（可用 get_cookie.py 自动获取）")
        cookie = "BDUSS=" + bduss.strip()
        if stoken.strip():
            cookie += "; STOKEN=" + stoken.strip()
        self.cookie = cookie

    def _req(self, method: str, path: str, params: dict, data: dict | None = None,
             retries: int = 3) -> dict:
        url = BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"User-Agent": UA, "Referer": REFERER, "Cookie": self.cookie}
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        last = ""
        for attempt in range(1, retries + 1):
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}: {e.read()[:200]!r}"
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(2 * attempt)
        raise RuntimeError(f"请求失败 {path}: {last}")

    # -------------------------------------------------------------- 接口
    def quota(self) -> dict:
        return self._req("GET", "/api/quota", {"checkfree": 1, "checkexpire": 1})

    def list_dir(self, dir_path: str = "/", num: int = 1000) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            r = self._req("GET", "/api/list", {
                "dir": dir_path, "web": 1, "page": page, "num": num,
                "order": "name", "desc": 0,
            })
            if r.get("errno") != 0:
                raise RuntimeError(f"list {dir_path} 失败: errno={r.get('errno')} "
                                   f"({ERRNO_TEXT.get(r.get('errno'))})")
            rows = r.get("list") or []
            out.extend(rows)
            if len(rows) < num:
                return out
            page += 1

    def create_dir(self, path: str) -> tuple[bool, str]:
        r = self._req("POST", "/api/create", {"a": "commit"},
                      {"path": path, "isdir": 1, "size": "", "blocks": ""})
        errno = r.get("errno")
        if errno == 0:
            return True, "created"
        if errno == 12:
            return True, "已存在（跳过）"
        return False, f"errno={errno} ({ERRNO_TEXT.get(errno, r)})"

    def move(self, items: list[dict]) -> dict:
        """items: [{"path": 源路径, "dest": 目标目录, "newname": 名字}]（单批 ≤ 100 项）"""
        r = self._req("POST", "/api/filemanager",
                      {"opera": "move", "web": 1, "async": 1},
                      {"filelist": json.dumps(items, ensure_ascii=False),
                       "onsuccess": "", "onerror": ""})
        if r.get("taskid"):
            return self._wait_task(r["taskid"])
        errno = r.get("errno")
        return {"ok": errno == 0 and not r.get("taskid"),
                "detail": r if errno == 0 else f"errno={errno} ({ERRNO_TEXT.get(errno, r)})"}

    def rename(self, path: str, name: str, isdir: int = 0) -> dict:
        r = self._req("POST", "/api/filemanager",
                      {"opera": "rename", "web": 1, "async": 1},
                      {"filelist": json.dumps([{"path": path, "newname": name, "isdir": isdir}],
                                              ensure_ascii=False)})
        if r.get("taskid"):
            return self._wait_task(r["taskid"])
        errno = r.get("errno")
        return {"ok": errno == 0, "detail": r if errno == 0 else f"errno={errno} ({ERRNO_TEXT.get(errno, r)})"}

    def delete(self, paths: list[str]) -> dict:
        r = self._req("POST", "/api/filemanager",
                      {"opera": "delete", "web": 1, "async": 1},
                      {"filelist": json.dumps(paths, ensure_ascii=False)})
        if r.get("taskid"):
            return self._wait_task(r["taskid"])
        errno = r.get("errno")
        return {"ok": errno == 0, "detail": r if errno == 0 else f"errno={errno} ({ERRNO_TEXT.get(errno, r)})"}

    def _wait_task(self, task_id, timeout: int = 60) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            r = self._req("GET", "/api/taskquery", {"taskid": task_id, "web": 1})
            status, errno = r.get("status"), r.get("errno")
            if status == "success" or errno == 0:
                return {"ok": True, "detail": r}
            if status == "failed":
                return {"ok": False, "detail": r}
        return {"ok": False, "detail": f"task {task_id} 超时未完成"}


def _client_from_env() -> Pan:
    return Pan(os.environ.get("BDUSS", ""), os.environ.get("STOKEN", ""))


def _print_res(label: str, res: dict) -> None:
    print(f"{label}: {'OK' if res.get('ok') else '失败 ' + str(res.get('detail'))[:200]}")


# ------------------------------------------------------------------ 子命令
def cmd_quota(_a) -> int:
    q = _client_from_env().quota()
    if q.get("errno") != 0:
        print(f"失败: errno={q.get('errno')} ({ERRNO_TEXT.get(q.get('errno'))})")
        return 1
    print(f"容量: {q.get('used', 0)/1024**3:.1f} / {q.get('total', 0)/1024**3:.1f} GB")
    return 0


def cmd_ls(a) -> int:
    rows = _client_from_env().list_dir(a.dir)
    for r in rows:
        kind = "DIR " if r.get("isdir") else "FILE"
        size = r.get("size") or 0
        print(f"{kind} {size/1024**2:10.1f}MB  {r.get('server_filename', '')}")
    print(f"-- 共 {len(rows)} 项", file=sys.stderr)
    return 0


def cmd_mkdir(a) -> int:
    ok, msg = _client_from_env().create_dir(a.path)
    print(f"建目录 {a.path}: {msg}")
    return 0 if ok else 1


def cmd_move(a) -> int:
    items = [{"path": s, "dest": a.dest, "newname": s.rstrip("/").split("/")[-1]} for s in a.src]
    if a.dry_run:
        for it in items:
            print(f"    {it['path']}  ->  {it['dest']}")
        print(f"（dry-run，共 {len(items)} 项；去掉 --dry-run 执行）")
        return 0
    pan = _client_from_env()
    fail = 0
    for i in range(0, len(items), 100):  # 单批 ≤ 100 项
        batch = items[i:i + 100]
        res = pan.move(batch)
        _print_res(f"移动 {len(batch)} 项 -> {a.dest}", res)
        if not res.get("ok"):
            fail += 1
        time.sleep(1.5)  # 温和节奏，降低风控概率
    return 0 if fail == 0 else 1


def cmd_rename(a) -> int:
    _print_res(f"重命名 {a.path} -> {a.name}", _client_from_env().rename(a.path, a.name))
    return 0


def cmd_rm(a) -> int:
    if not a.yes:
        print("拒绝执行：删除需要显式 --yes。删除操作尽量通过回收站恢复窗口处理。")
        return 2
    _print_res(f"删除 {len(a.path)} 项", _client_from_env().delete(a.path))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="百度网盘网页版接口客户端（需 BDUSS 环境变量）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("quota").set_defaults(fn=cmd_quota)
    p = sub.add_parser("ls"); p.add_argument("--dir", default="/"); p.set_defaults(fn=cmd_ls)
    p = sub.add_parser("mkdir"); p.add_argument("--path", required=True); p.set_defaults(fn=cmd_mkdir)
    p = sub.add_parser("move"); p.add_argument("--src", nargs="+", required=True)
    p.add_argument("--dest", required=True); p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_move)
    p = sub.add_parser("rename"); p.add_argument("--path", required=True)
    p.add_argument("--name", required=True); p.set_defaults(fn=cmd_rename)
    p = sub.add_parser("rm"); p.add_argument("--path", nargs="+", required=True)
    p.add_argument("--yes", action="store_true"); p.set_defaults(fn=cmd_rm)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
