# Linux一键安装器

## 推荐：无需登录，自动从GitHub下载安装

在Linux服务器复制执行以下两条命令：

```bash
curl -fsSL https://raw.githubusercontent.com/54dashayu/fmo-ai-voice-gateway-installer/main/install.sh -o /tmp/fmo-ai-install.sh
sh /tmp/fmo-ai-install.sh
```

公开安装入口：[fmo-ai-voice-gateway-installer](https://github.com/54dashayu/fmo-ai-voice-gateway-installer)

此方式不需要GitHub账号、`gh auth login` 或Token。脚本会自动查询最新公开Release，下载压缩包及SHA-256文件，校验通过后解压并进入中文配置向导。

> [!IMPORTANT]
> SHA-256校验失败时脚本会立即停止。安装完成后AI、ASR、TTS、自动回复、定时报时和真实PTT仍然保持关闭。

## 已经手工下载安装包

下载并解压安装包后，普通用户只需执行：

```bash
sh install.sh
```

根目录脚本会自动检查Linux环境并申请sudo权限，然后调用正式安装器。

安装包中的 `install-from-github.sh` 也已经指向同一个公开安装仓库，不再要求登录私有源码仓库。

## 安装内容

| 路径 | 内容 |
|---|---|
| `/opt/fmo-ai-gateway/releases/<版本>` | 每个不可变程序版本、状态页和Python venv |
| `/opt/fmo-ai-gateway/current` | 当前生效版本的原子符号链接 |
| `/etc/fmo-ai-gateway.env` | 百炼Key、Token、呼号与安全开关，权限 `0600` |
| `/etc/fmo-ai-gateway/gateway-config.json` | MQTT、模型和可选NAS配置，权限 `0600` |
| `/var/lib/fmo-ai-gateway` | 状态、统计和短期音频目录 |
| `/etc/systemd/system/fmo-ai-*.service` | AI网关与MQTT监听服务 |

## 安装选项

```text
--non-interactive  跳过首次配置向导
--skip-packages    已自行安装依赖时跳过系统包安装
--dry-run          只打印计划执行的系统命令
```

## 安装前门禁

- 只支持Linux与systemd。
- Python必须为3.10至3.12；Python 3.13移除了当前音频链依赖的 `audioop`。
- 需要能够安装 `libopus`、`ffmpeg` 和 `paho-mqtt`。Rocky/AlmaLinux 9 的默认软件源不保证提供 `ffmpeg`；若安装器提示缺失，请先从管理员认可的软件源安装，再使用 `--skip-packages` 重试。本版本尚未在 RHEL 系发行版上完成真实安装验收。
- 已有FMO MQTT/SAS应先独立验证正常。
- 修改EMQX或Nginx前必须合并配置，不得覆盖主配置文件。

## 安装后状态

两个服务可以启动并连接MQTT，但以下能力仍保持关闭：

```text
FMO_AI_ENABLED=false
FMO_ASR_ENABLED=false
FMO_TTS_ENABLED=false
FMO_VOICE_AUTO_ENABLED=false
FMO_HOURLY_ANNOUNCEMENT_ENABLED=false
FMO_AUDIO_PLAYBACK_ENABLED=false
FMO_NET_CHECKIN_RUNTIME_ENABLED=false
```

必须按 `docs/validation.md` 完成只读、模拟和真实验证后再逐项开放。

## 后台地址

网关默认只监听服务器回环地址：

```text
http://127.0.0.1:18788/ai/
```

该端口用于网关健康检查和后台静态页面。完整管理功能需要 Nginx 做 API 路径映射。使用主分支更新后的示例，按 [README 的后台配置步骤](../README.md#安装后打开后台)配置 HTTPS 或仅回环监听的 SSH 隧道入口。将 `18788` 保持在回环地址。

## 升级与回退

再次执行同一条公开安装命令会下载最新 Release。升级先在新的版本目录创建 venv、安装依赖并完成导入检查，随后原子切换 `current` 链接并重启两个 AI 服务；不会覆盖：

- `/etc/fmo-ai-gateway.env` 中的密钥、开关和站点参数；
- `/etc/fmo-ai-gateway/gateway-config.json`；
- `/var/lib/fmo-ai-gateway` 中的话术、计划、黑名单、三态开关和其他运行状态。

新版本未通过有界健康检查时，安装器会自动恢复上一版本。人工回退使用：

```bash
sudo /opt/fmo-ai-gateway/current/scripts/rollback.sh
```

回退只切换程序版本并重启两个 AI 服务，不回退或删除用户配置、密钥、话术、计划和状态。旧版若不能理解新版状态，应保持所有真实发射开关关闭后再检查。
