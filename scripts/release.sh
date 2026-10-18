#!/bin/bash
# 一条命令走完发布流程。用法：
#
#   scripts/release.sh 0.5.2            # 真发布
#   scripts/release.sh 0.5.2 --dry-run  # 只检查，不改任何东西
#
# 它做的事（每一步失败就停，不会半途留下烂摊子）：
#   1. 工作区必须干净、tag 不能已存在
#   2. 改版本号（__init__.py / pyproject.toml / 两个 README）
#   3. 要求 CHANGELOG 里已经有 `## [新版本]` 段 —— **不替你写变更内容**
#   4. 跑 make check（lint + test + 隐私自检）
#   5. 提交 + 打 tag + 推送
#   6. 构建 .app + 打包
#   7. 建 GitHub Release + 上传 asset
#   8. 下载回来比对 sha256
#
# 为什么要有这个脚本：这套流程手工做过两次，两次都漏步骤（一次忘了同步
# README 版本号，一次把 asset 传成了不含 RDKit 的瘦包）。
set -uo pipefail

VERSION="${1:-}"
DRY_RUN=0
[ "${2:-}" = "--dry-run" ] && DRY_RUN=1

fail() { echo "  ❌ $*" >&2; exit 1; }
step() { echo; echo "── $* ──"; }
run()  { if [ "$DRY_RUN" = "1" ]; then echo "  [dry-run] $*"; else "$@"; fi; }

[ -n "${VERSION}" ] || fail "用法：scripts/release.sh <版本，如 0.5.2> [--dry-run]"
echo "${VERSION}" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$' || fail "版本号要 x.y.z 形式"
[ "$DRY_RUN" = "1" ] && echo "  （dry-run：只检查，不改任何东西）"

cd "$(dirname "${BASH_SOURCE[0]}")/.." || fail "找不到仓库根"

step "1/8 前置检查"
[ -z "$(git status --porcelain)" ] || {
  git status --short | sed 's/^/     /'
  fail "工作区不干净 —— 先提交或 stash"
}
git rev-parse "v${VERSION}" >/dev/null 2>&1 && fail "tag v${VERSION} 已存在"
git ls-remote --tags origin "v${VERSION}" 2>/dev/null | grep -q . && fail "远程已有 tag v${VERSION}"
echo "  工作区干净，tag v${VERSION} 可用"

step "2/8 改版本号"
for pair in "src/chemfigstudio/__init__.py:__version__" "pyproject.toml:version"; do
  file="${pair%%:*}"; key="${pair##*:}"
  run python3 - "$file" "$key" "${VERSION}" <<'PY'
import pathlib, re, sys
path, key, version = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
text = p.read_text(encoding="utf8")
if key == "__version__":
    new = re.sub(r'__version__ = "[0-9.]+"', '__version__ = "%s"' % version, text, count=1)
else:
    new = re.sub(r'^version = "[0-9.]+"', 'version = "%s"' % version, text, count=1, flags=re.M)
if new == text:
    sys.exit("  在 %s 里找不到 %s" % (path, key))
p.write_text(new, encoding="utf8")
PY
done
for readme in README.md README.zh-CN.md; do
  run python3 - "$readme" "${VERSION}" <<'PY'
import pathlib, re, sys
path, version = sys.argv[1], sys.argv[2]
p = pathlib.Path(path)
text = p.read_text(encoding="utf8")
new = re.sub(r"\*\*\d+\.\d+\.\d+\*\*", "**%s**" % version, text, count=1)
if new == text:
    sys.exit("  %s 里找不到版本号" % path)
p.write_text(new, encoding="utf8")
PY
done
echo "  版本 → ${VERSION}（代码 2 处 + README 2 处）"

step "3/8 CHANGELOG 段"
grep -q "^## \[${VERSION}\]" CHANGELOG.md || fail \
  "CHANGELOG.md 里没有 '## [${VERSION}]' 段。
     先写清楚这一版改了什么（这个脚本不替你编变更内容），把
     '## [Unreleased]' 下面的条目滚进 '## [${VERSION}] - YYYY-MM-DD'，再跑一次。"
echo "  CHANGELOG 已有 [${VERSION}] 段"

step "4/8 闸门"
run make -s check || fail "make check 没过"

step "5/8 提交 + tag + 推送"
run git add -A
run git -c user.name="$(git config user.name || echo Tracecatalyst)" \
        -c user.email="$(git config user.email || echo Tracecatalyst@users.noreply.github.com)" \
        commit -q -m "release: v${VERSION}"
run git tag -a "v${VERSION}" -m "v${VERSION}"
run git push origin main "v${VERSION}" || echo "  ⚠️ 推送失败（网络？）—— 本地 tag 已建，稍后手动 git push origin main v${VERSION}"

step "6/8 构建 .app"
run make -s bundle || fail "构建失败"
ZIP="dist/ChemFigStudio-${VERSION}-macos-arm64.app.zip"
if [ "$DRY_RUN" = "0" ]; then
  ( cd dist && rm -f "$(basename "$ZIP")" && ditto -c -k --keepParent ChemFigStudio.app "$(basename "$ZIP")" )
fi
echo "  $ZIP"

step "7/8 GitHub Release + asset"
if [ "$DRY_RUN" = "1" ]; then
  echo "  [dry-run] 跳过上传"
else
  CRED=$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill 2>/dev/null)
  TOKEN=$(echo "$CRED" | grep '^password=' | cut -d= -f2-)
  [ -n "$TOKEN" ] || fail "拿不到 GitHub 凭据（git credential fill）"
  GH_TOKEN="$TOKEN" VERSION="${VERSION}" ZIP="$ZIP" python3 - <<'PY' || exit 1
import json, os, pathlib, urllib.request, urllib.error
token, version, zip_path = os.environ["GH_TOKEN"], os.environ["VERSION"], os.environ["ZIP"]
repo = "Tracecatalyst/chemfigstudio"
notes_file = pathlib.Path("dist/release-notes-v%s.md" % version)
if not notes_file.is_file():
    raise SystemExit("  ❌ 缺少 %s —— 先写 release notes（15 行以内，别写成报告）" % notes_file)
def api(method, url, data=None, ctype="application/json"):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "token " + token)
    req.add_header("Accept", "application/vnd.github+json")
    if data is not None:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=300) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:200]
status, rel = api("GET", "https://api.github.com/repos/%s/releases/tags/v%s" % (repo, version))
if status == 200:
    raise SystemExit("  ❌ release v%s 已存在" % version)
payload = json.dumps({"tag_name": "v" + version, "name": "v" + version,
                      "body": notes_file.read_text(encoding="utf8")}).encode()
status, rel = api("POST", "https://api.github.com/repos/%s/releases" % repo, payload)
if status not in (200, 201):
    raise SystemExit("  ❌ 建 release 失败: %s %s" % (status, rel))
print("  ✓ release:", rel["html_url"])
blob = pathlib.Path(zip_path).read_bytes()
status, asset = api("POST",
    "https://uploads.github.com/repos/%s/releases/%d/assets?name=%s"
    % (repo, rel["id"], pathlib.Path(zip_path).name), blob, "application/zip")
if status not in (200, 201):
    raise SystemExit("  ❌ 上传失败: %s %s" % (status, asset))
print("  ✓ asset: %s（%.1f MB）" % (asset["name"], asset["size"] / 1e6))
print("  ✓ digest:", asset.get("digest"))
PY
fi

step "8/8 校验"
if [ "$DRY_RUN" = "0" ]; then
  echo "  本地 sha256: $(shasum -a 256 "$ZIP" | cut -d' ' -f1)"
  echo "  与上面 release 的 digest 比对，一致即完成。"
fi
echo
echo "── 发布完成：v${VERSION} ──"
