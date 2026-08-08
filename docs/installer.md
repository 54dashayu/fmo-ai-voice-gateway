# Linux一键安装器

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
