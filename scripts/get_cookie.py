# -*- coding: utf-8 -*-
"""百度网盘 Cookie 自动获取器（CDP 路线）。

背景：Chrome 127+ 对 Cookie 启用 App-Bound 加密，外部进程直接解密 Cookie
数据库不可行。本工具改用受控调试端口（CDP）从一个「专用 Chrome 配置目录」读取：

  setup : 首次使用 —— 打开一个专用小 Chrome（独立配置，不影响日常浏览器），
          人工登录 pan.baidu.com 一次，脚本轮询到 BDUSS 出现并验证后即完成。
  get   : 日常使用 —— 用同一配置无头启动 Chrome，经 CDP 读出 BDUSS/STOKEN，
          以 KEY=VALUE 行打印到 stdout（可直接被 shell eval 消费）。
  test  : 验证给定登录态是否有效（调 /api/quota）。

安全说明：
  - 登录态保存在本机专用配置目录（与普通浏览器配置同级的加密存储）
  - 本脚本不把 Cookie 写入任何文件/日志；get 模式只输出到 stdout
  - 不读取、不修改日常使用的 Chrome/Edge 配置
  - 登录态失效后重跑 setup 重新扫码即可

用法：
  python get_cookie.py setup
  python get_cookie.py get          # 输出 BDUSS=... STOKEN=...
  eval "$(python get_cookie.py get)" && python pan_api.py quota
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request

CHROME_CANDIDATES = [
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
]
PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                       "baidu-pan-helper", "profile")
PORT = 9223
LOGIN_URL = "https://pan.baidu.com/disk/main"
TIMEOUT = 600  # setup 模式等人工登录的最长秒数


# ------------------------------------------------------------------ CDP
def _wait_port(port: int, timeout: float = 20) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


class WS:
    """最小 WebSocket 客户端（够 CDP 用：文本帧收发）。"""

    def __init__(self, url: str):
        assert url.startswith("ws://")
        rest = url[5:]
        host, path = rest.split("/", 1)
        h, _, p = host.partition(":")
        self.sock = socket.create_connection((h, int(p or 80)), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET /{path} HTTP/1.1\r\nHost: {host}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        head, _, rest = buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise RuntimeError("websocket 握手失败: " + head[:200].decode("utf-8", "replace"))
        self.buf = rest

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("websocket 已断开")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text: str) -> None:
        payload = text.encode()
        mask = os.urandom(4)
        header = bytes([0x81])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def recv_text(self) -> str:
        while True:
            b1, b2 = self._read(2)
            opcode = b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                (n,) = struct.unpack(">H", self._read(2))
            elif n == 127:
                (n,) = struct.unpack(">Q", self._read(8))
            payload = self._read(n)  # 服务端帧不掩码
            if opcode == 0x1:
                return payload.decode("utf-8", "replace")
            if opcode == 0x9:  # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
            # 0x8 close / 0xA pong / 0x2 binary：忽略继续收


def cdp_cookies(port: int, timeout: float = 20) -> dict[str, str]:
    """连接浏览器级 CDP，取全部 cookie，返回 {name: value}（仅 .baidu.com 相关）。"""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=10) as r:
        ws_url = json.loads(r.read())["webSocketDebuggerUrl"]
    ws = WS(ws_url)
    ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = json.loads(ws.recv_text())
        if msg.get("id") == 1:
            if "error" in msg:
                raise RuntimeError("CDP 错误: " + json.dumps(msg["error"], ensure_ascii=False))
            out = {}
            for c in msg["result"]["cookies"]:
                if "baidu.com" in c.get("domain", ""):
                    out[c["name"]] = c["value"]
            return out
    raise TimeoutError("等待 CDP 响应超时")


def launch(headless: bool) -> subprocess.Popen:
    chrome = next((p for p in CHROME_CANDIDATES if os.path.exists(p)), None)
    if not chrome:
        raise FileNotFoundError("未找到 Chrome，请安装或修改 CHROME_CANDIDATES")
    args = [
        chrome,
        f"--user-data-dir={PROFILE}",
        f"--remote-debugging-port={PORT}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    if headless:
        args += ["--headless=new", "--window-size=1280,900"]
    else:
        args += ["--window-size=1100,800"]
    args.append(LOGIN_URL)
    return subprocess.Popen(args)


def fetch(headless: bool) -> dict[str, str]:
    p = launch(headless)
    try:
        if not _wait_port(PORT, timeout=30):
            raise RuntimeError("Chrome 调试端口未就绪")
        for _ in range(30):
            try:
                return cdp_cookies(PORT)
            except Exception:
                time.sleep(1)
        raise RuntimeError("CDP 连接失败")
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()


# ------------------------------------------------------------------ 模式
def quota_ok(bduss: str, stoken: str) -> bool:
    req = urllib.request.Request(
        "https://pan.baidu.com/api/quota?checkfree=1&checkexpire=1",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126",
            "Cookie": f"BDUSS={bduss}; STOKEN={stoken}",
        })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("errno") == 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "get"
    if mode == "setup":
        print("即将打开一个专用 Chrome 窗口（独立配置，不影响日常浏览器）。")
        print(f"请在窗口里登录 pan.baidu.com（扫码即可），最长等待 {TIMEOUT}s …")
        p = launch(headless=False)
        try:
            if not _wait_port(PORT, timeout=30):
                raise RuntimeError("调试端口未就绪")
            start = time.time()
            while time.time() - start < TIMEOUT:
                try:
                    ck = cdp_cookies(PORT, timeout=10)
                except Exception:
                    ck = {}
                if "BDUSS" in ck and ck["BDUSS"]:
                    if quota_ok(ck["BDUSS"], ck.get("STOKEN", "")):
                        print("登录态有效，配置完成。之后可直接运行: get_cookie.py get")
                        return 0
                    print("  已取到 BDUSS，但验证失败，10s 后重试…")
                else:
                    print(f"  …等待登录中（已等 {int(time.time() - start)}s）", flush=True)
                time.sleep(5)
            print("超时未完成登录，请重跑 setup。")
            return 1
        finally:
            p.terminate()
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()

    if mode == "get":
        ck = fetch(headless=True)
        bduss = ck.get("BDUSS", "")
        if not bduss:
            print("未取到 BDUSS：专用配置里的登录态可能已失效，请重跑 setup", file=sys.stderr)
            return 1
        print(f"BDUSS={bduss}")
        if ck.get("STOKEN"):
            print(f"STOKEN={ck['STOKEN']}")
        return 0

    if mode == "test":
        bduss = stoken = ""
        for a in sys.argv[2:]:
            if a.startswith("BDUSS="):
                bduss = a[6:]
            if a.startswith("STOKEN="):
                stoken = a[7:]
        print("登录态有效" if quota_ok(bduss, stoken) else "登录态无效")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
