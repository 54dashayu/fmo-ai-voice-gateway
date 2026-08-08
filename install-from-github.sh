#!/bin/sh
set -eu

REPOSITORY=${FMO_AI_GITHUB_REPOSITORY:-54dashayu/fmo-ai-voice-gateway-installer}
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/fmo-ai-install.XXXXXX")
trap 'rm -rf "$WORK_DIR"' EXIT HUP INT TERM

printf '\nFMO AI 语音网关 GitHub 自动安装程序\n'
printf '%s\n' '--------------------------------------'

if [ "$(uname -s)" != "Linux" ]; then
  printf '%s\n' '错误：只能在 Linux 系统中安装。' >&2
  exit 1
fi

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  printf '%s\n' '正在通过已登录的 GitHub CLI 获取最新版本……'
  TAG=$(gh release view --repo "$REPOSITORY" --json tagName --jq .tagName)
  VERSION=${TAG#v}
  gh release download "$TAG" --repo "$REPOSITORY" --dir "$WORK_DIR" \
    --pattern "fmo-ai-voice-gateway-$VERSION.tar.gz" \
    --pattern "fmo-ai-voice-gateway-$VERSION.tar.gz.sha256"
else
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    AUTH_HEADER="Authorization: Bearer $GITHUB_TOKEN"
  else
    AUTH_HEADER=""
    printf '%s\n' '未发现 GitHub 登录信息，将尝试匿名下载公开仓库。'
  fi

  curl_api() {
    if [ -n "$AUTH_HEADER" ]; then
      printf 'header = "%s"\n' "$AUTH_HEADER" | curl --config - -fsSL "$@"
    else
      curl -fsSL "$@"
    fi
  }
  curl_file() {
    if [ -n "$AUTH_HEADER" ]; then
      printf 'header = "%s"\n' "$AUTH_HEADER" | curl --config - -fL "$@"
    else
      curl -fL "$@"
    fi
  }

  printf '%s\n' '正在查询 GitHub 最新版本……'
  RELEASE_JSON=$(curl_api "https://api.github.com/repos/$REPOSITORY/releases/latest") || {
    printf '%s\n' '下载失败：私有仓库请先安装并登录 gh，或设置只读 GITHUB_TOKEN。' >&2
    exit 1
  }
  TAG=$(printf '%s' "$RELEASE_JSON" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
  [ -n "$TAG" ] || { printf '%s\n' '错误：无法识别最新版本。' >&2; exit 1; }
  VERSION=${TAG#v}
  BASE_URL="https://github.com/$REPOSITORY/releases/download/$TAG"
  curl_file -o "$WORK_DIR/fmo-ai-voice-gateway-$VERSION.tar.gz" "$BASE_URL/fmo-ai-voice-gateway-$VERSION.tar.gz"
  curl_file -o "$WORK_DIR/fmo-ai-voice-gateway-$VERSION.tar.gz.sha256" "$BASE_URL/fmo-ai-voice-gateway-$VERSION.tar.gz.sha256"
fi

printf '已下载版本：%s\n' "$TAG"
cd "$WORK_DIR"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum -c "fmo-ai-voice-gateway-$VERSION.tar.gz.sha256"
elif command -v shasum >/dev/null 2>&1; then
  shasum -a 256 -c "fmo-ai-voice-gateway-$VERSION.tar.gz.sha256"
else
  printf '%s\n' '错误：系统缺少 sha256sum 或 shasum，不能安全校验安装包。' >&2
  exit 1
fi

tar -xzf "fmo-ai-voice-gateway-$VERSION.tar.gz"
cd "fmo-ai-voice-gateway-$VERSION"
printf '%s\n' '校验通过，开始安装……'
sh install.sh "$@"
