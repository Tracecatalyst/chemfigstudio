#!/bin/bash
# 把本仓库发布到 GitHub。
#
# 用法：
#   scripts/publish-to-github.sh git@github.com:<你的用户名>/chemfigstudio.git
#   scripts/publish-to-github.sh https://github.com/<你的用户名>/chemfigstudio.git
#
# 前提：已经登录 GitHub（GitHub Desktop 里点 Sign in，或配好 SSH key）。
set -euo pipefail

REPO_URL="${1:-}"
if [ -z "$REPO_URL" ]; then
  echo "用法：$0 <仓库地址>" >&2
  echo "示例：$0 git@github.com:yourname/chemfigstudio.git" >&2
  exit 2
fi

cd "$(dirname "$0")/.."

echo "── 1/4 检查工作区 ──"
if [ -n "$(git status --porcelain)" ]; then
  echo "⚠ 有未提交的改动，请先提交（git status 看一下）：" >&2
  git status --short >&2
  exit 1
fi
echo "  干净 ✓"

echo "── 2/4 检查提交署名 ──"
NAME="$(git config user.name || true)"
EMAIL="$(git config user.email || true)"
echo "  当前：${NAME:-未设置} <${EMAIL:-未设置}>"
# 三种情况都要提醒，第三种最危险：它会把**内网 IP** 写进每一条公开 commit。
if [ -z "$EMAIL" ] || [ "${EMAIL%.local}" != "$EMAIL" ]; then
  echo "  ⚠ 这个邮箱不会关联到你的 GitHub 账号（提交者头像/贡献图不会显示）。"
  echo "     想改就用： git config --global user.email \"你的GitHub邮箱\""
elif printf '%s' "$EMAIL" | grep -qE '@[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "  ⚠ 提交邮箱是本机地址（$EMAIL）——它会**明文写进每一条公开 commit**，"
  echo "     等于把你的内网 IP 公布出去，而且也不会关联 GitHub 账号。"
  echo "     推之前务必先改： git config user.email \"你的GitHub邮箱\""
  echo "     已经推出去的旧提交要改历史（git filter-repo），光改配置不会追认。"
fi
if [ "$NAME" = "local" ]; then
  echo "  ⚠ 提交用户名是 \"local\" —— 公开仓库里看起来像是没配置过。"
  echo "     想改就用： git config --global user.name \"你的名字\""
fi

echo "── 3/4 配置 remote origin ──"
if git remote get-url origin >/dev/null 2>&1; then
  echo "  已有 origin：$(git remote get-url origin) → 更新为 $REPO_URL"
  git remote set-url origin "$REPO_URL"
else
  git remote add origin "$REPO_URL"
  echo "  已添加 origin：$REPO_URL"
fi

echo "── 4/4 推送 ──"
git push -u origin "$(git rev-parse --abbrev-ref HEAD)"
echo
echo "完成。之后可以直接在 GitHub Desktop 里点 Publish/Push。"
