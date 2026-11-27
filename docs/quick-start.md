# 安装后的独立部署指南

先按 [README](../README.md#-一键安装) 安装并运行 `sudo /opt/fmo-ai-gateway/current/scripts/doctor.sh`。在中文向导中配置百炼、独立 AI 标识、测试 UID 和 MQTT。先以只读 MQTT ACL 检查连接和帧解析。需要发射时，再由 Broker 管理员授权指定主题的发布权限，并按 [验证顺序](../README.md#推荐验收顺序) 逐项启用。

## 通过 SSH 使用完整后台

以下命令在 Ubuntu/Debian 网关服务器执行。首次创建认证文件使用 `-c`，已有文件时去掉它。

```sh
sudo apt-get install nginx apache2-utils
sudo install -d -m 0755 /etc/nginx/auth
sudo htpasswd -c /etc/nginx/auth/fmo-ai.htpasswd admin
sudo chgrp www-data /etc/nginx/auth/fmo-ai.htpasswd
sudo chmod 0640 /etc/nginx/auth/fmo-ai.htpasswd
sudo curl -fsSLo /etc/nginx/fmo-ai-locations.conf https://raw.githubusercontent.com/54dashayu/fmo-ai-voice-gateway/main/deploy/nginx-ai.conf.example
sudoedit /etc/nginx/conf.d/fmo-ai-loopback.conf
```

在新文件中保存以下配置。已有 Nginx 时，确认主配置加载 `conf.d/*.conf` 且端口可用。

```nginx
server {
    listen 127.0.0.1:18789;
    server_name localhost;
    include /etc/nginx/fmo-ai-locations.conf;
}
```

执行 `sudo nginx -t && sudo systemctl reload nginx`。在个人电脑运行 `ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18789:127.0.0.1:18789 user@server`，打开 `http://127.0.0.1:18789/ai/` 并登录。已有 HTTPS 管理站点时，也可在对应 `server` 块加入同一份 include，再使用 `https://管理域名/ai/`。其他发行版请按本机包名和 Nginx worker 用户调整。

## 选择要启用的功能

下表中的环境变量写在 `/etc/fmo-ai-gateway.env`。把所需项从 `false` 改为 `true` 后重启 `fmo-ai-gateway` 和 `fmo-ai-mqtt-monitor`。启用前确认频道空闲。

v0.2.1 的 `FMO_ASR_ENABLED` 和 `FMO_TTS_ENABLED` 用于报告配置状态。实际 MQTT 语音链路依据业务路径调用百炼，发射由 `FMO_VOICE_AUTO_ENABLED` 及相应业务开关控制。保持语音总门关闭即可阻止这些业务发射。

| 功能 | 环境开关与依赖 | 网页位置 |
|---|---|---|
| AI 语音回复 | 百炼、MQTT；`FMO_AI_ENABLED`、`FMO_ASR_ENABLED`、`FMO_TTS_ENABLED`、`FMO_VOICE_AUTO_ENABLED` | `/ai/` 选择 AI 自动回复 |
| 鹦鹉原声回放 | MQTT、`FMO_VOICE_AUTO_ENABLED`；通过语音切换还需要百炼 ASR 与 `FMO_ASR_ENABLED` | `/ai/` 选择鹦鹉原声回放 |
| SSTV 固定素材或 NAS 随机图 | MQTT、ffmpeg；`FMO_VOICE_AUTO_ENABLED`、`FMO_AUDIO_PLAYBACK_ENABLED`；合成提示话术另需百炼与 `FMO_TTS_ENABLED` | `/ai/audio.html` 保存并启用计划 |
| NAS 知识库 | NAS 服务、独立 Token、百炼 Embedding；开启知识库配置 | `/ai/` 的 NAS 知识库 |
| NAS 音乐 | NAS 只读音乐目录、NAS 服务、Token、ffmpeg；语音选曲使用 ASR 与语音发送门 | NAS Compose、网关 NAS 配置 |
| 自动台网点名 | MQTT、百炼 ASR/TTS；`FMO_NET_CHECKIN_RUNTIME_ENABLED`、`FMO_ASR_ENABLED`、`FMO_TTS_ENABLED`、`FMO_VOICE_AUTO_ENABLED` | `/ai/net-checkin.html` 的「启用自动台网点名」 |

### SSTV

1. 打开音频页，导入固定音频或选择本机图片编码 Robot36，再保存素材。也可配置兼容远程目录的 `FMO_SSTV_CATALOG_API_URL` 和 `FMO_SSTV_CATALOG_FILES_URL` 后从目录选择。
2. 选定素材或 NAS 随机图库目录，填写播放时间、开场和结束话术。启用首段准备时，默认在开场话术之后等待 60 秒，再播正文。多段节目可配置段间等待。
3. 需要实际播放时，按表启用环境开关，在音频页打开音频播放总开关，再保存并启用该条计划。正文电平初值为 `-24 dB`，在接收端试听后调整。观察执行与控制区中的计划状态。频道忙、MQTT 未连接或点名优先时可能跳过。

### NAS 知识库、音乐和图库

1. 按 [NAS 服务部署](../nas-knowledge-service/README.md) 配置挂载、Token 与网络。先从网关检查 NAS 的 `/health`。
2. 在网关配置 NAS 地址与同一 Token。要做文档检索，在后台开启「使用 NAS 知识库」，确认文件入库并验证一次知识命中。
3. 音乐需要单独挂载音乐目录。随机图库需要单独挂载图库目录，在音频页刷新目录、选择是否包含子目录和抽取张数，再保存到 SSTV 计划中。网关按计划临时转码。

### 台网点名

1. 打开点名页，填写主题、日期或重复规则，以及开始和结束时间，保存计划。
2. 按表启用环境开关，再打开页面上的「启用自动台网点名」。检查实时状态，活动后检查历史记录。
3. 需要对接兼容外部服务时配置 `FMO_CHECKIN_API_URL` 和 `FMO_CHECKIN_API_TOKEN`。仅做本地点名时留空。
