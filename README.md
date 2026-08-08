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

详见：

- [架构与安全边界](docs/architecture.md)
- [配置字段说明](docs/configuration.md)
- [阿里云部署说明](docs/deployment-aliyun.md)
- [接口清单](docs/api.md)
- [验证分级](docs/validation.md)

基础测试：

```bash
cd ai-gateway
python3 -m unittest discover -p 'test_*.py' -v
python3 gateway.py --callsign YOUR_CALLSIGN --text 测试 --validate-only
```

## 目录

```text
ai-gateway/            AI、ASR/TTS、MQTT/PTT与管理页面
nas-knowledge-service/ 可选NAS知识库服务
deploy/                systemd、EMQX、Nginx通用模板
docs/                  架构、配置、接口与验收说明
```

## 供应商边界

本版本锁定阿里云百炼 DashScope。所谓“OpenAI兼容接口”仅指百炼提供的HTTP协议形态，不代表调用OpenAI服务，也不提供其他供应商自动回退。
