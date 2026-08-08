# FMO AI Voice Gateway

面向 FMO MQTT 语音服务器的可配置 AI 语音网关。它在不修改 FMO 核心 Broker/SAS 链路的前提下，将 FMO 语音接入阿里云百炼的 ASR、LLM、Embedding 和 TTS 能力，并提供网页状态与配置界面。

> 本仓库默认私有发布。FMO 协议、服务器授权和无线电操作责任仍由部署者自行确认；不得使用 AI 冒充真实持证呼号。

## 业务链路

```text
FMO客户端
  -> FMO MQTT Broker / FMO/RAW
  -> MQTT语音监听与PTT时序
  -> 阿里云百炼ASR
  -> 普通聊天或知识库RAG
  -> 阿里云百炼LLM
  -> 阿里云百炼TTS
  -> 频道空闲检查
  -> FMO/RAW语音回传
```

## 主要能力

- 无唤醒词普通聊天与知识问答双模式
- 阿里云百炼 `chat / embedding / ASR / TTS` 统一配置
- 可选NAS知识库；未启用时直接使用普通聊天与百炼联网知识回答
- FMO标准通联语义识别与AI身份声明
- 呼号统计、黑名单、网页开关、语音命令和人格设置
- 整点播报、夜间提示、频道繁忙跳过
- 45秒AI回复上限、60秒呼入上限、PTT首字保护
- Web状态页与表单化配置，JSON仅作为高级调试入口

## 安全默认值

- AI、ASR、TTS和真实语音发射默认关闭。
- API Key、网关Token、MQTT密码和证书不得提交到Git。
- 管理API只监听回环地址，并应由Nginx认证后反向代理。
- 同机EMQX建议使用仅回环可达的独立AI监听端口。
- 初次部署必须先做只读订阅，再做模拟解析，最后才做约定呼号的真实PTT测试。

## 快速开始

### 一键安装包

直接从GitHub自动获取最新版并安装：

```bash
sh install-from-github.sh
```

仓库为私有状态时，服务器需要先执行 `gh auth login`，或者临时设置具有该仓库只读权限的 `GITHUB_TOKEN`。脚本不会显示或保存Token。公开仓库可直接匿名执行。

私有仓库可用GitHub CLI把自动安装脚本直接拉到Linux服务器：

```bash
gh auth login
gh api -H "Accept: application/vnd.github.raw+json" repos/54dashayu/fmo-ai-voice-gateway/contents/install-from-github.sh > /tmp/fmo-ai-install.sh
sh /tmp/fmo-ai-install.sh
```

这三条命令只需首次安装时执行；脚本随后会自动下载最新Release，不需要手工填写版本号。

支持范围：

| Linux发行版 | 架构 | Python |
|---|---|---|
| Ubuntu 22.04 / 24.04 | x86_64、ARM64 | 3.10 / 3.12 |
| Debian 12 | x86_64、ARM64 | 3.11 |
| Rocky Linux / AlmaLinux 9 | x86_64、ARM64 | 3.11 |

从GitHub Release下载并校验安装包后执行：

```bash
tar -xzf fmo-ai-voice-gateway-0.1.2.tar.gz
cd fmo-ai-voice-gateway-0.1.2
sh install.sh
```

安装器会自动：

- 安装Python、venv、pip和libopus依赖。
- 创建专用 `fmo-ai` 系统用户。
- 安装到 `/opt/fmo-ai-gateway`。
- 创建权限受限的环境文件和网关配置。
- 运行交互式配置向导，安全录入百炼Key、呼号、UID、MQTT与可选NAS。
- 安装并启动两个systemd服务。
- 保存Nginx与EMQX配置示例，但不覆盖现有服务配置。

非交互式安装：

```bash
sh install.sh --non-interactive
sudo /opt/fmo-ai-gateway/venv/bin/python /opt/fmo-ai-gateway/scripts/configure.py
sudo systemctl restart fmo-ai-gateway fmo-ai-mqtt-monitor
```

安装后诊断：

```bash
sudo /opt/fmo-ai-gateway/scripts/doctor.sh
```

安装器不会安装或修改FMO SAS/CA，不会覆盖已有EMQX/Nginx配置，也不会自动开启AI、ASR、TTS、自动回复、报时或真实PTT。

### 从源码验证

详见：

- [架构与安全边界](docs/architecture.md)
- [配置字段说明](docs/configuration.md)
- [阿里云部署说明](docs/deployment-aliyun.md)
- [接口清单](docs/api.md)
- [验证分级](docs/validation.md)
- [Linux一键安装器](docs/installer.md)

基础测试：

```bash
cd ai-gateway
python3 -m unittest discover -p 'test_*.py' -v
python3 gateway.py --callsign YOUR_CALLSIGN --text 测试 --validate-only
```

## 配置总览

配置分为两层：

1. `/etc/fmo-ai-gateway.env`：密钥、Token、呼号和安全门禁等服务器环境变量。
2. `/etc/fmo-ai-gateway/gateway-config.json`：网页可编辑的百炼模型、MQTT和可选NAS知识库设置。

真实凭据只能保存在服务器权限受限的文件中，建议所有者为专用 `fmo-ai` 用户、文件权限为 `0600`。不要把生产配置复制回Git仓库。

### 最小必填项

```text
DASHSCOPE_API_KEY=YOUR_BAILIAN_API_KEY
FMO_GATEWAY_TOKEN=YOUR_RANDOM_GATEWAY_TOKEN
FMO_STATION_CALLSIGN=YOUR_CALLSIGN
FMO_AI_CALLSIGN=AI-YOUR-STATION
FMO_TEST_UID=YOUR_SERVER_UID
FMO_ALLOWED_CALLSIGNS=YOUR_TEST_CALLSIGN
```

首次启动时继续保持：

```text
FMO_AI_ENABLED=false
FMO_ASR_ENABLED=false
FMO_TTS_ENABLED=false
FMO_VOICE_AUTO_ENABLED=false
FMO_HOURLY_ANNOUNCEMENT_ENABLED=false
```

这些开关必须经过分级验证后逐项开启，不能因为健康页面显示正常就直接启用PTT。

## 阿里云百炼配置

本项目的全部AI能力统一使用阿里云百炼 DashScope。

| 能力 | 默认模型 | 用途 | 是否必需 |
|---|---|---|---|
| Chat | `qwen-plus` | 普通聊天、知识回答与通联话术 | 是 |
| ASR | `qwen3-asr-flash` | 将FMO呼入语音转成文字 | 语音问答时必需 |
| TTS | `cosyvoice-v3-flash` | 将模型回复合成为语音 | 语音回复时必需 |
| Embedding | `text-embedding-v4` | NAS知识库向量检索 | 仅启用NAS时需要 |

标准百炼兼容端点示例：

```text
https://dashscope.aliyuncs.com/compatible-mode/v1
```

也可以填写同一百炼业务空间分配的专用Endpoint。Provider必须保持 `dashscope`，后端会拒绝其他模型供应商。

API Key输入框遵循以下规则：

- “已配置”表示服务器已经有Key，但页面不会返回明文。
- 留空保存表示保留原Key。
- 只有首次配置或轮换Key时才输入新值。
- 不要把百炼API Key复用为MQTT密码、NAS Token或网页密码。

## MQTT配置

### AI和EMQX部署在同一台服务器

推荐使用独立的回环监听器：

```text
host: 127.0.0.1
port: 1884
topic: FMO/RAW
client_id: FMO-AI-MONITOR-YOUR-STATION
```

这种情况下通常不需要MQTT用户名、密码或TLS，因为数据只在服务器内部回环网络传输。`deploy/emqx-ai-loopback.conf` 提供了仅允许本机连接的示例。

### AI连接远程FMO MQTT服务器

需要由目标服务器所有者提供并确认：

| 字段 | 含义 |
|---|---|
| Host | 目标Broker的公网、专网地址或域名 |
| Port | 目标Broker提供的MQTT端口 |
| Topic | FMO语音主题，通常需要确认是否为 `FMO/RAW` |
| Client ID | MQTT连接唯一标识，不是账号或真实呼号 |
| Username/Password | 目标Broker的客户端鉴权账号，仅在对方要求时填写 |
| TLS/CA/客户端证书 | 目标Broker要求加密或双向证书认证时填写 |

远程服务器还必须给AI客户端最小ACL：只允许订阅和发布约定的FMO语音主题。不要对全部MQTT Topic授予 `#` 权限。

## NAS知识库配置（可选）

管理页面采用“先开启、再配置”的两层交互。

### 不使用NAS

```text
FMO_KB_ENABLED=false
FMO_KB_ADMIN_TOKEN=
```

关闭后：

- 不连接或探测NAS地址。
- 不调用Embedding模型。
- 普通聊天、ASR和TTS继续工作。
- 无法从私有资料回答的知识问题使用百炼联网回答。

### 使用NAS

先部署 `nas-knowledge-service/`，然后在管理页面开启“使用NAS知识库”，再配置：

| 字段 | 示例 | 含义 |
|---|---|---|
| NAS知识服务地址 | `http://127.0.0.1:18787` | AI网关访问知识服务的地址；可由SSH反向隧道映射到回环端口 |
| Token环境变量 | `FMO_KB_ADMIN_TOKEN` | 存放Token的环境变量名称，不是Token内容 |
| 独立访问Token | 随机保密值 | AI网关与NAS知识服务之间的认证凭据 |
| 单次检索条数 | `3` | 每次最多交给模型的相关知识片段数量 |
| 请求超时 | `8`秒 | NAS在该时间内未响应则视为本次访问失败 |

独立Token需要同时配置在NAS知识服务和AI网关，两端内容必须一致。它不能使用百炼API Key，也不得提交到Git。页面显示“留空不改（已配置）”时表示现有Token有效，日常保存无需重新输入。

## 呼号与AI身份

| 变量 | 说明 |
|---|---|
| `FMO_STATION_CALLSIGN` | 服务器管理员或测试站呼号 |
| `FMO_AI_CALLSIGN` | AI发言标识，必须与真人用户清晰区分 |
| `FMO_TEST_UID` | 服务器或约定测试UID |
| `FMO_ALLOWED_CALLSIGNS` | 初次测试允许触发AI的呼号列表 |
| `FMO_ALLOW_ALL_CALLSIGNS` | 是否改用“所有呼号允许、黑名单排除”模式 |
| `FMO_VOICE_CONTROL_CALLSIGN` | 可执行管理员语音命令的呼号 |
| `FMO_ANNOUNCEMENT_NAME` | 整点播报和标准通联时使用的AI台站名称 |

AI发言标识不得与当前在线真实用户相同，不得声称自己拥有真实个人身份、执照、RST、QTH、设备或功率信息。

## 语音与PTT参数

| 参数 | 推荐初值 | 说明 |
|---|---:|---|
| `FMO_PTT_END_GAP_SECONDS` | `0.9` | 松开PTT后判断一轮语音结束的静音间隔 |
| `FMO_VOICE_MAX_INPUT_MS` | `60000` | 单次真人呼入最长60秒 |
| `FMO_VOICE_MAX_REPLY_MS` | `45000` | AI单次语音回复最长45秒 |
| `FMO_PTT_LEAD_IN_MS` | `480` | 发射后正式语音前的保护静音，避免削首字 |
| `FMO_AUDIO_FADE_IN_MS` | `80` | 回复音频淡入时间 |
| `FMO_PTT_TAIL_MS` | `120` | 语音结束后的PTT保持时间 |

参数应通过真实接收端试听调整。MQTT发布成功不等于对方已经听到完整回复。

## 配置保存与生效

网页读取接口只返回脱敏数据。密码、API Key和Token输入框留空表示保持原值。

配置保存后，模型网关和MQTT监听需要重新加载：

```bash
sudo systemctl restart fmo-ai-gateway
sudo systemctl restart fmo-ai-mqtt-monitor
```

重启前应确认频道空闲。模型配置或NAS开关变更不会自动授权真实PTT；PTT仍受独立环境开关和网页业务开关控制。

## 推荐验收顺序

1. 只读检查现有EMQX、SAS、Nginx和业务服务状态。
2. 保持PTT关闭，验证MQTT连接、Topic和FMO帧解析。
3. 执行百炼文本输入到文本回复验证。
4. 执行离线ASR、LLM、TTS闭环，不发布MQTT语音。
5. 若启用NAS，验证健康检查、文件列表和一次知识命中。
6. 由服务器所有者授权，仅约定呼号进行短促真实PTT测试。
7. 以另一台FMO实际听到完整AI语音作为验收证据。

## 目录

```text
ai-gateway/            AI、ASR/TTS、MQTT/PTT与管理页面
nas-knowledge-service/ 可选NAS知识库服务
deploy/                systemd、EMQX、Nginx通用模板
docs/                  架构、配置、接口与验收说明
```

## 供应商边界

本版本锁定阿里云百炼 DashScope。所谓“OpenAI兼容接口”仅指百炼提供的HTTP协议形态，不代表调用OpenAI服务，也不提供其他供应商自动回退。
