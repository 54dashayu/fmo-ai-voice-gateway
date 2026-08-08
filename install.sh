#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALLER="$PROJECT_DIR/scripts/install.sh"

printf '\nFMO AI 语音网关一键安装程序\n'
printf '%s\n' '--------------------------------'
printf '%s\n' '将自动安装程序依赖、AI 网关、管理页面和 systemd 服务。'
printf '%s\n' '不会覆盖现有 EMQX、SAS/CA、Nginx、MySQL。'
printf '%s\n' 'AI、ASR、TTS、自动回复、报时和 PTT 安装后均保持关闭。'
printf '\n'

if [ "$(uname -s)" != "Linux" ]; then
  printf '%s\n' '错误：本安装程序只能在 Linux 系统中运行。' >&2
  exit 1
fi

if [ ! -f "$INSTALLER" ]; then
  printf '%s\n' '错误：安装包不完整，请重新下载并完整解压。' >&2
  exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
  exec /bin/bash "$INSTALLER" "$@"
fi

if ! command -v sudo >/dev/null 2>&1; then
  printf '%s\n' '错误：需要 root 权限，请先切换到 root 后重新执行。' >&2
  exit 1
fi

printf '%s\n' '接下来可能要求输入当前 Linux 用户的 sudo 密码。'
exec sudo /bin/bash "$INSTALLER" "$@"
