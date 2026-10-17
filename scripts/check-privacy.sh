#!/bin/bash
# 公开前隐私自检：查 commit 署名、内容里的内网地址/家目录/凭据、内部目录名。
#
# 用法：
#   scripts/check-privacy.sh              # 检查当前仓库
#   scripts/check-privacy.sh <仓库路径>    # 检查指定仓库
#
# 退出码：0 = 没有 ERROR（可能有 WARN）；1 = 有 ERROR，**别公开**。
#
# 检查项：commit 署名、内网地址、真实家目录、凭据、内部编号。
# 目的很朴素 —— 公布之前确认仓库里没有不该出现的东西。
set -uo pipefail

REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO" || { echo "找不到仓库：$REPO" >&2; exit 2; }

errors=0
warns=0
err()  { echo "  ❌ $*"; errors=$((errors + 1)); }
warn() { echo "  ⚠  $*"; warns=$((warns + 1)); }
ok()   { echo "  ✓  $*"; }

echo "隐私自检：$REPO"
echo

# --- 1) commit 署名 ---------------------------------------------------------
echo "── 1/5 commit 署名 ──"
bad_authors=$(git log --all --format='%an <%ae>' | sort -u | grep -vE \
  'users\.noreply\.github\.com|@users\.noreply' || true)
if [ -n "$bad_authors" ]; then
  echo "$bad_authors" | sed 's/^/     /'
  ip_authors=$(echo "$bad_authors" | grep -cE '<[^>]*@[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+>' || true)
  if [ "$ip_authors" -gt 0 ]; then
    err "有 commit 用**本机 IP** 作邮箱 —— 这是明文泄露内网地址，且已随 commit 公开"
    echo "     修：改写历史（git filter-branch），光改 git config 不会追认"
  else
    warn "有 commit 没用 GitHub noreply 身份（不一定错，但公开前确认一下）"
  fi
else
  ok "全部 commit 都用 GitHub 身份"
fi
echo

# --- 2) 内网地址 ------------------------------------------------------------
echo "── 2/5 内网地址 / 主机名 ──"
hits=$(git grep -nE '192\.168\.[0-9]+\.[0-9]+|(^|[^0-9])10\.[0-9]+\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+' \
       -- . 2>/dev/null | grep -v 'check-privacy.sh' || true)
if [ -n "$hits" ]; then echo "$hits" | sed 's/^/     /'; err "内容里有内网 IP 地址"
else ok "没有内网 IP"; fi
echo

# --- 3) 家目录真实路径 ------------------------------------------------------
echo "── 3/5 家目录真实路径 ──"
# 只报含真实用户名的：/Users/<name>、/home/<name>；放过占位符 /Users/you
hits=$(git grep -nE '/(Users|home)/[A-Za-z0-9._-]+' -- . 2>/dev/null \
       | grep -vE '/(Users|home)/(you|user|username|example|name)\b' \
       | grep -v 'check-privacy.sh' || true)
if [ -n "$hits" ]; then echo "$hits" | sed 's/^/     /'; err "内容里有真实的用户家目录路径"
else ok "没有真实家目录路径（占位符除外）"; fi
echo

# --- 4) 凭据 / 密钥 ---------------------------------------------------------
echo "── 4/5 凭据与密钥 ──"
hits=$(git grep -nE 'ghp_[A-Za-z0-9]{20,}|github_pat_|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9]{20,}|xox[baprs]-' \
       -- . 2>/dev/null | grep -v 'check-privacy.sh' || true)
if [ -n "$hits" ]; then echo "$hits" | sed 's/^/     /'; err "疑似凭据/私钥"
else ok "没有明显的凭据或私钥"; fi
# 邮箱：排除 noreply 与示例域
hits=$(git grep -nE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' -- . 2>/dev/null \
       | grep -viE 'noreply\.github\.com|example\.(com|org)|yourname|your-?email|chemfigstudio@' \
       | grep -viE 'git@[A-Za-z0-9.-]+:' \
       | grep -v 'check-privacy.sh' || true)
if [ -n "$hits" ]; then echo "$hits" | sed 's/^/     /'; warn "内容里有邮箱地址，确认是否想公开"
else ok "没有多余的邮箱地址"; fi
echo

# --- 5) 内部上下文（不一定是隐私，但读者会困惑） ----------------------------
echo "── 5/5 内部上下文残留 ──"
hits=$(git grep -nE '验收 [0-9]+|req-gate|01-课程|02_图表|BCD_副本|作业目录' -- . 2>/dev/null \
       | grep -v 'check-privacy.sh' || true)
if [ -n "$hits" ]; then
  echo "$hits" | head -5 | sed 's/^/     /'
  count=$(echo "$hits" | wc -l | tr -d ' ')
  warn "有 $count 处内部编号/目录引用（不是隐私，但公开后读者看不懂）"
else ok "没有内部编号或课程目录残留"; fi
echo

# --- 截图提醒（无法自动判读，只能提醒） -------------------------------------
echo "── 额外：截图请人工过一眼 ──"
shots=$(git ls-files | grep -iE '\.(png|jpe?g|webp|gif)$' | grep -iE 'gui|screenshot|界面' || true)
if [ -n "$shots" ]; then
  echo "$shots" | sed 's/^/     /'
  warn "界面截图里常带**文件路径/用户名**——这些无法自动检查，请逐张打开看"
  echo "     踩过：右侧「文件」面板显示 /Users/<用户名>/... 直接公开了目录结构"
else ok "没有界面截图"; fi
echo

# --- 汇总 -------------------------------------------------------------------
echo "──────────────────────────────────────"
if [ "$errors" -gt 0 ]; then
  echo "  ERROR $errors 项，WARN $warns 项 → **先修再公开**"
  echo "  改历史：git filter-branch --env-filter ...（记得清 refs/original/）"
  exit 1
fi
echo "  没有 ERROR（WARN $warns 项）→ 可以公开"
exit 0
