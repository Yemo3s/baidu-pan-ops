# -*- coding: utf-8 -*-
"""百度网盘网页版接口客户端（与网盘网页前端同一套接口）。

适用场景：需要操作 bdpan CLI 作用域（/apps/bdpan）之外的文件，或做批量目录整理。
登录态来源：环境变量 BDUSS（必需）/ STOKEN（可选），或用同目录 get_cookie.py 自动获取。
Cookie 只在内存中使用，本脚本绝不把 Cookie 写入文件或日志。

安全边界：
  - rm 需要显式 --yes 才执行
  - batch-move 支持 --dry-run 先看清单
  - 只做 建目录/移动/重命名/删除/列举/下载 六类操作，不碰分享、不碰账号设置

用法：
  python pan_api.py quota
  python pan_api.py ls --dir /
  python pan_api.py mkdir --path /目标目录
  python pan_api.py move --src /a /b --dest /目标目录
  python pan_api.py rename --path /a --name 新名
  python pan_api.py rm --path /a /b --yes
  python pan_api.py get --path "/目录/文件.pdf" --dest "D:/下载/文件.pdf"

关于 get：
  走 filemetas 拿直链（d.pcs.baidu.com），带 Cookie 下载，支持 Range 断点续传 ——
  非会员被限速或中途断流时，重跑同一条命令即从已下部分继续，不会从头再来。
  下载完成后会核对本地大小与远端是否一致；不一致时报错而不是静默成功。
  filemetas 返回的 md5 是百度自家变体编码（含 i/v 等非十六进制字符），不是标准 MD5，
  因此脚本不做 md5 校验，只做大小核对。

常见 errno：
  0 成功 | 2 参数错误/路径不存在 | 12 已存在 | -6 身份验证失败（BDUSS 无效）
  -8 目标已有同名 | 111 token 失效 | -70 文件不存在
  8001 filemanager 写操作缺 bdstoken（客户端已自动获取并缓存，正常不会遇到）

关于 delete / move / rename：
  三者都走 /api/filemanager，必须带 bdstoken（从 /api/gettemplatevariable 现取）。
  缺 bdstoken 时服务端只回一个光秃秃的 errno=8001，没有任何提示文字 ——
  极易被误读成"文件不存在"而去反复检查路径。客户端已在 _fm_params() 里统一补齐。
  删除进入回收站（非物理删除），可在网盘回收站内恢复。
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
    8001: "缺少/无效的 bdstoken（filemanager 写操作必带，客户端已自动获取）",
}


class Pan:
    def __init__(self, bduss: str, stoken: str = ""):
        if not bduss:
            raise SystemExit("缺少 BDUSS：设置环境变量 BDUSS（可用 get_cookie.py 自动获取）")
        cookie = "BDUSS=" + bduss.strip()
        if stoken.strip():
            cookie += "; STOKEN=" + stoken.strip()
        self.cookie = cookie
        self._bdstoken: str | None = None

    def bdstoken(self) -> str:
        """取（并缓存）bdstoken。

        /api/filemanager 的写操作（尤其 delete）需要它；缺了会返回 errno=8001，
        且**不带任何文字说明**，极易被误判成"文件不存在"。move/rename 偶尔能不带
        它成功，但 delete 必然失败，因此统一带上。
        """
        if self._bdstoken is None:
            r = self._req("GET", "/api/gettemplatevariable",
                          {"fields": json.dumps(["bdstoken"])})
            tok = (r.get("result") or {}).get("bdstoken")
            if not tok:
                raise RuntimeError(f"获取 bdstoken 失败: errno={r.get('errno')} {r}")
            self._bdstoken = tok
        return self._bdstoken

    def _fm_params(self, opera: str) -> dict:
        """filemanager 写操作的公共查询参数（对齐网页前端）。"""
        return {
            "opera": opera, "async": 1, "web": 1,
            "bdstoken": self.bdstoken(),
            "channel": "chunlei", "app_id": "250528",
            "clienttype": "0", "onnest": "fail",
        }

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
        r = self._req("POST", "/api/filemanager", self._fm_params("move"),
                      {"filelist": json.dumps(items, ensure_ascii=False),
                       "onsuccess": "", "onerror": ""})
        if r.get("taskid"):
            return self._wait_task(r["taskid"])
        errno = r.get("errno")
        return {"ok": errno == 0 and not r.get("taskid"),
                "detail": r if errno == 0 else f"errno={errno} ({ERRNO_TEXT.get(errno, r)})"}

    def rename(self, path: str, name: str, isdir: int = 0) -> dict:
        r = self._req("POST", "/api/filemanager", self._fm_params("rename"),
                      {"filelist": json.dumps([{"path": path, "newname": name, "isdir": isdir}],
                                              ensure_ascii=False)})
        if r.get("taskid"):
            return self._wait_task(r["taskid"])
        errno = r.get("errno")
        return {"ok": errno == 0, "detail": r if errno == 0 else f"errno={errno} ({ERRNO_TEXT.get(errno, r)})"}

    def delete(self, paths: list[str]) -> dict:
        r = self._req("POST", "/api/filemanager", self._fm_params("delete"),
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

    # -------------------------------------------------------------- 下载
    def find(self, path: str) -> dict | None:
        """按完整路径找一个条目，返回 list 接口的原始记录（含 fs_id / size / isdir）。"""
        path = path.rstrip("/") or "/"
        parent, _, name = path.rpartition("/")
        parent = parent or "/"
        if not name:
            return None
        for it in self.list_dir(parent):
            if it.get("server_filename") == name:
                return it
        return None

    def dlink(self, fs_id: int, target_path: str = "") -> dict:
        """取文件直链。返回 filemetas 的 info 项（dlink / size / md5 …）。

        注意：百度返回的 md5 是自家变体编码（含 i/v 等非十六进制字符），
        不是标准 MD5，不能直接拿来做校验。
        """
        params = {"dlink": 1, "fsids": json.dumps([fs_id]), "thumb": 0, "web": 1}
        if target_path:
            params["target"] = json.dumps([target_path], ensure_ascii=False)
        r = self._req("GET", "/api/filemetas", params)
        errno = r.get("errno")
        if errno != 0:
            raise RuntimeError(f"filemetas 失败: errno={errno} ({ERRNO_TEXT.get(errno)})")
        info = (r.get("info") or [{}])[0]
        if not info.get("dlink"):
            raise RuntimeError("未取得直链：filemetas 未返回 dlink"
                               "（常见于文件已失效，或该账号/文件受限）")
        return info

    def download(self, remote_path: str, local_path: str, *, resume: bool = True,
                 show_progress: bool = True) -> dict:
        """下载单个文件到本地，支持断点续传（重跑同一命令即从已下部分继续）。"""
        entry = self.find(remote_path)
        if entry is None:
            raise RuntimeError(f"网盘路径不存在：{remote_path}")
        if entry.get("isdir"):
            raise RuntimeError(f"{remote_path} 是目录。批量下载请逐项调用，"
                               f"或先用 ls 列出内容。")
        total = int(entry.get("size") or 0)
        info = self.dlink(int(entry["fs_id"]), remote_path)
        url = info["dlink"]

        os.makedirs(os.path.dirname(os.path.abspath(local_path)) or ".", exist_ok=True)
        have = 0
        if resume and os.path.exists(local_path):
            have = os.path.getsize(local_path)
            if have >= total:  # 已下完，直接确认
                return {"ok": True, "path": local_path, "size": total, "resumed_from": have}

        headers = {"User-Agent": UA, "Referer": "https://pan.baidu.com/", "Cookie": self.cookie}
        if have:
            headers["Range"] = f"bytes={have}-"

        req = urllib.request.Request(url, headers=headers)
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=60) as resp:
            if have and resp.status == 206:          # 服务端接受续传
                mode, done = "ab", have
            else:                                    # 206 之外一律从头来，避免拼出坏文件
                mode, done = "wb", 0
            last_tick = 0.0
            with open(local_path, mode) as fh:
                while True:
                    try:
                        chunk = resp.read(262144)
                    except (TimeoutError, OSError):
                        break                        # 中途断流：保留已下部分，重跑可续
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if show_progress and time.time() - last_tick > 1.0:
                        last_tick = time.time()
                        rate = done / max(time.time() - t0, 0.001) / 1024**2
                        pct = (done / total * 100) if total else 0
                        print(f"\r  已下载 {done/1024**2:7.1f}/{total/1024**2:.1f} MB"
                              f" ({pct:5.1f}%)  {rate:5.2f} MB/s", end="", file=sys.stderr)
        if show_progress:
            print(file=sys.stderr)

        got = os.path.getsize(local_path)
        if total and got != total:
            return {"ok": False, "path": local_path, "size": got, "expected": total,
                    "detail": f"大小不符：本地 {got} / 远端 {total}。文件可能被限速截断，"
                              f"重跑同一命令可断点续传补齐。"}
        return {"ok": True, "path": local_path, "size": got, "resumed_from": have}


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


def cmd_get(a) -> int:
    dest = a.dest
    if os.path.isdir(dest):
        dest = os.path.join(dest, a.path.rstrip("/").split("/")[-1])
    res = _client_from_env().download(a.path, dest, resume=not a.no_resume)
    if res.get("ok"):
        print(f"已下载 {res['path']}（{res['size']/1024**2:.1f} MB）")
        return 0
    print(f"失败：{res.get('detail') or res}", file=sys.stderr)
    return 1


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
    p = sub.add_parser("get", help="下载单个文件到本地（支持断点续传）")
    p.add_argument("--path", required=True, help="网盘文件完整路径")
    p.add_argument("--dest", required=True, help="本地文件路径，或已存在的目录")
    p.add_argument("--no-resume", action="store_true", help="丢弃已有部分，从头下载")
    p.set_defaults(fn=cmd_get)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
