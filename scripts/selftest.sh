#!/usr/bin/env bash
# netdisk_crud.py（bdpan CLI 路线）端到端自测
# 前提：bdpan 已安装并登录（bdpan whoami 可用）
# 全部操作限定在 /apps/bdpan/_st_<时间戳> 沙箱内，跑完自动清理
# 用法：bash selftest.sh

set -u
PY="${PY:-python}"
# Git Bash/MSYS 的 /c/... /tmp/... 路径传给 Windows 原生 Python 会解析失败，统一转换
to_win() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}
T="$(to_win "$(cd "$(dirname "$0")" && pwd)/netdisk_crud.py")"
A="$(to_win "$(cd "$(dirname "$0")" && pwd)/pan_api.py")"
W="$(to_win "$(mktemp -d)")"
STAMP="$(date +%s)"
D="/apps/bdpan/_st_$STAMP"
PASS=0; FAIL=0

step() { printf '\n=== %s ===\n' "$1"; }
ok()   { PASS=$((PASS+1)); echo "  ✓ $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  ✗ $1"; }
call() { "$PY" -u "$T" "$@"; }
names(){ call names --remote "$1" 2>/dev/null; }

step "0. 越界拒绝（fail-closed，不发请求；须以退出码 2 结束）"
for p in "/其他目录" "/apps/bdpan/../outside" "$D/../../etc"; do
  call ls --remote "$p" >/dev/null 2>&1; rc=$?
  if [ "$rc" -eq 2 ]; then ok "已本地拒绝: $p"; else bad "越界未按预期拒绝（rc=$rc）: $p"; fi
done

step "1. mkdir"
call mkdir --remote "$D" >/dev/null 2>&1 && ok "建目录" || bad "建目录"
call mkdir --remote "$D/sub" >/dev/null 2>&1 && ok "建子目录" || bad "建子目录"

step "2. put 上传"
echo "hello-1" > "$W/f1.txt"; echo "hello-2" > "$W/f2.txt"
call put --local "$W/f1.txt" --remote "$D/f1.txt" >/dev/null 2>&1 && ok "上传 f1" || bad "上传 f1"
call put --local "$W/f2.txt" --remote "$D/sub/f2.txt" >/dev/null 2>&1 && ok "上传 f2" || bad "上传 f2"

step "3. ls / names"
call ls --remote "$D" >/dev/null 2>&1 && ok "ls 根" || bad "ls 根"
names "$D" | grep -qxF "f1.txt" && ok "names 精确匹配 f1.txt" || bad "names 匹配 f1.txt"
names "$D" | grep -qxF "sub" && ok "names 匹配 sub" || bad "names 匹配 sub"

step "4. rename"
call rename --remote "$D/f1.txt" --name f1r.txt >/dev/null 2>&1 || bad "rename 执行"
names "$D" | grep -qxF "f1r.txt" && ok "新名可见" || bad "新名可见"
names "$D" | grep -qxF "f1.txt"  && bad "旧名残留" || ok "旧名消失"

step "5. mv / cp"
call mv --remote "$D/f1r.txt" --dest "$D/sub" >/dev/null 2>&1 && ok "mv 到子目录" || bad "mv"
names "$D/sub" | grep -qxF "f1r.txt" && ok "mv 后到位" || bad "mv 后到位"
call cp --remote "$D/sub/f1r.txt" --dest "$D" >/dev/null 2>&1 && ok "cp 回根" || bad "cp"
names "$D" | grep -qxF "f1r.txt" && ok "cp 后到位" || bad "cp 后到位"

step "6. get 下载并字节比对"
call get --remote "$D/sub/f2.txt" --local "$W/dl.txt" >/dev/null 2>&1 && ok "下载" || bad "下载"
[ "$(cat "$W/dl.txt")" = "hello-2" ] && ok "内容一致" || bad "内容不一致"

step "7. rm 递归删除"
call rm --remote "$D" --force >/dev/null 2>&1 && ok "删除" || bad "删除"
sleep 2
names "$D" >/dev/null 2>&1 && bad "删除后仍可见" || ok "已不可见"

step "8. pan_api 写操作必须带 bdstoken（离线，不需登录态）"
# 回归守卫：/api/filemanager 的 move/rename/delete 缺 bdstoken 时，服务端只回一个
# 无提示的 errno=8001，delete 必失败、move/rename 时好时坏 —— 曾因此误判成路径写错。
if "$PY" -u - "$A" <<'PYEOF'
import sys, os, importlib.util

path = sys.argv[1]
spec = importlib.util.spec_from_file_location("pan_api_uut", path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

calls = []
def fake_req(self, method, p, params, data=None, retries=3):
    calls.append((p, dict(params or {})))
    if p == "/api/gettemplatevariable":
        return {"errno": 0, "result": {"bdstoken": "T-TOKEN"}}
    return {"errno": 0, "taskid": None}
m.Pan._req = fake_req
pan = m.Pan("dummy-bduss-for-offline-test")

cases = [("delete", (["/a"],)),
         ("rename", ("/a", "newname")),
         ("move", ([{"path": "/a", "dest": "/b"}],))]
bad = []
for fn, argv in cases:
    calls.clear()
    getattr(pan, fn)(*argv)
    fm = [c for c in calls if c[0] == "/api/filemanager"]
    got = fm[0][1].get("bdstoken") if fm else None
    print("    %-8s bdstoken=%s" % (fn, got))
    if got != "T-TOKEN":
        bad.append(fn)
if bad:
    print("    缺少 bdstoken 的写操作: %s" % ", ".join(bad))
sys.exit(1 if bad else 0)
PYEOF
then ok "delete / rename / move 均自动带上 bdstoken"; else bad "有写操作未带 bdstoken（errno=8001 会复现）"; fi

rm -rf "$W"
printf '\n===== 结果: %d 通过 / %d 失败 =====\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
