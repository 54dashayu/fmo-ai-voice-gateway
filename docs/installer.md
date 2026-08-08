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
| `/opt/fmo-ai-gateway` | 程序、状态页、Python venv与诊断工具 |
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
- 需要能够安装 `libopus` 和 `paho-mqtt`。
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
```

必须按 `docs/validation.md` 完成只读、模拟和真实验证后再逐项开放。

## 回退

升级前保留 `/opt/fmo-ai-gateway`、`/etc/fmo-ai-gateway*` 和systemd单元备份。出现问题时恢复这三部分并执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart fmo-ai-gateway fmo-ai-mqtt-monitor
```

不要删除 `/var/lib/fmo-ai-gateway`，其中包含黑名单、呼号统计和业务开关状态。
