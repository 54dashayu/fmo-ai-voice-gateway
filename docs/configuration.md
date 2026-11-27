# 配置说明

## 必填环境变量

复制 `ai-gateway/.env.example` 到服务器受限文件 `/etc/fmo-ai-gateway.env`，权限建议 `0600`。

```text
DASHSCOPE_API_KEY=百炼业务空间API Key
FMO_GATEWAY_TOKEN=管理文本接口独立随机Token
FMO_STATION_CALLSIGN=服务器管理员呼号
FMO_AI_CALLSIGN=清晰区分的AI发言标识
FMO_TEST_UID=服务器或测试UID
```

不要把真实值写进仓库。

## MQTT基础字段

| 字段 | 说明 |
|---|---|
| host | AI网关连接的Broker地址；同机通常为 `127.0.0.1` |
| port | AI专用监听端口；示例为 `1884` |
| topic | FMO语音主题，需与目标服务器确认 |
| client_id | MQTT连接唯一标识，不是呼号或账号 |
| enabled | 是否启用该配置 |

用户名、密码、TLS和客户端证书仅在远程Broker明确要求时使用。同机回环监听通常不需要。

## 百炼模型

默认示例：

| 能力 | 模型 |
|---|---|
| Chat | `qwen-plus` |
| Embedding | `text-embedding-v4` |
| ASR | `qwen3-asr-flash` |
| TTS | `cosyvoice-v3-flash` |

可改成同一百炼业务空间有权调用的其他模型，但Provider和Endpoint必须仍属于阿里云百炼。

## NAS知识库（可选）

NAS知识库默认关闭。未部署NAS服务时保持：

```text
FMO_KB_ENABLED=false
FMO_KB_ADMIN_TOKEN=
```

普通聊天、ASR、TTS和百炼联网知识回答仍可正常使用，也不会探测NAS地址。需要私有知识库时再部署 `nas-knowledge-service/`，设置独立Token并改为 `FMO_KB_ENABLED=true`。

管理页面采用两层设置：先选择“使用NAS知识库”，开启后才显示NAS地址、Token、检索条数和超时。Token读取时只显示是否已配置，不返回明文。

## 配置生效

网页保存写入 `gateway-config.json`。模型网关与MQTT监听在启动时加载配置，因此修改后需按变更范围重启：

```bash
systemctl restart fmo-ai-gateway
systemctl restart fmo-ai-mqtt-monitor
```

真实发射期间不要重启；应等待频道空闲并通知测试方。

# 可选音频、SSTV 与点名

这些能力均不随安装自动开启：

```text
FMO_AUDIO_PLAYBACK_ENABLED=false
FMO_NET_CHECKIN_RUNTIME_ENABLED=false
FMO_SSTV_CATALOG_API_URL=
FMO_SSTV_CATALOG_FILES_URL=
FMO_SSTV_BODY_LEVEL_DB=-24
FMO_CHECKIN_API_URL=
FMO_CHECKIN_API_TOKEN=
```

SSTV 公共目录的 API 和文件根地址必须由部署者明确配置；留空时只保留本地图片编码和已导入素材。`-24 dB` 是本项目当前链路的保守正文初值，并不是通用设备校准值。外部点名系统地址和 Token 同样可选；留空时本地点名状态机仍可独立使用，不产生断链依赖。

NAS 音乐和图库通过可选知识服务的只读接口访问。源目录由 NAS 服务部署者配置，本项目不会假设作者家庭网络、目录或域名。
