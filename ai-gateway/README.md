# FMO AI 文本网关（阶段 B 骨架）

当前实现为**可配置产品化 AI 网关**：本机 HTTP 文本输入、NAS 检索、百炼模型文本回复、ASR/TTS + MQTT/PTT 语音链路已串联。已引入“网关配置文件驱动”模式，支持多套 MQTT Profile 与多项模型配置。

## AI 供应商硬性边界

- 本 FMO 语音中继的全部 AI 能力统一使用阿里云百炼。
- 对话模型、ASR、TTS、向量化/重排及后续知识库模型均不得接入 OpenAI 或其他 AI 模型供应商。
- 当前所用“OpenAI 兼容接口”仅表示百炼支持该 HTTP 请求格式；请求地址、API Key、计费和实际模型均属于阿里云百炼，不调用 OpenAI 服务。
- 新增模型配置时必须使用百炼模型标识和百炼地域端点，不得添加其他供应商的备用模型或自动回退。

## 双模式路由

- 知识库问答模式：检测到唤醒词或明确的软件/知识问题时，先检索指定知识库，再由百炼生成有依据的回答。
- 普通聊天模式：没有唤醒词时也进入百炼对话，不静默丢弃。
- 两种模式都必须先通过 AI 总开关和允许呼号门禁；首次部署只允许明确的测试呼号。

安全默认值：网关关闭；允许呼号为空；密钥由权限受限的环境文件托管；HTTP错误不输出上游响应正文。

离线验证：

```bash
cd ai-gateway
python3 -m unittest -v
FMO_AI_ENABLED=true FMO_ALLOWED_CALLSIGNS=YOUR_CALLSIGN python3 gateway.py --callsign YOUR_CALLSIGN --text 测试 --validate-only
```

真实文本调用前，在当前终端安全设置 `DASHSCOPE_API_KEY`，并显式设置 `FMO_AI_ENABLED=true`。不要把密钥写入 `.env.example`、Git、日志或命令历史。

北京地域默认兼容端点为 `https://dashscope.aliyuncs.com/compatible-mode/v1`，密钥必须与地域匹配。

部署后仅监听VPS `127.0.0.1:18788`：

- `GET /health`：不含凭据的状态检查。
- `GET /aprs/position?callsign=YOUR_CALLSIGN-SSID`：返回 APRS-IS 中最近收到的FMO经纬度。
- `POST /v1/chat`：使用独立 `FMO_GATEWAY_TOKEN`，请求体包含 `callsign` 和 `text`。
- 知识检索通过反向隧道 `http://127.0.0.1:18787` 访问NAS。

服务启动时会以只接收模式连接 APRS-IS，并用 `u/APFMO*` 过滤所有 FMO
位置广播。最新位置写入 `/var/lib/fmo-ai-gateway/aprs-positions.json`；
Nginx 仅公开只读入口 `/aprs/api/position`。

## ASR/TTS离线闭环

`voice_roundtrip.py`用于不接FMO频道的真实语音闭环测试：

```text
CosyVoice生成测试WAV → qwen3-asr-flash识别 → qwen-plus回答 → CosyVoice生成回复WAV
```

测试音频写入权限受限目录并默认1小时清理。该脚本不订阅MQTT、不控制PTT，也不会向FMO发射。


## 配置化接入包（可复制部署）

1. 上传与运行时文件：

- 配置主文件：`/etc/fmo-ai-gateway/gateway-config.json`（启动时自动加载）
- 示例配置：`ai-gateway/gateway-config.example.json`
- 运行时状态与控件：`ai-gateway/status-page/`（Nginx 反代到 `/ai/`）
- 服务端点：`server.py` 支持 `/admin/gateway-config`（GET/POST）

2. 典型配置场景：

- 一个文件管理多个 MQTT FMO 服务器：`mqtt_servers` 列表可放多条
- 通过 `FMO_MQTT_PROFILE` 选择默认接入条目（例如 `primary/backup`）
- `chat/asr/tts/embedding` 四类能力可独立设置 `provider/base_url/model/api_key_env`

3. 建议上线步骤：

- `cp ai-gateway/gateway-config.example.json /etc/fmo-ai-gateway/gateway-config.json`
- 写入真实密钥到环境：`DASHSCOPE_API_KEY`、`DASHSCOPE_ASR_API_KEY`、`DASHSCOPE_TTS_API_KEY`（或对应自定义 `..._API_KEY` 名称）
- 在系统服务添加 `FMO_GATEWAY_CONFIG_PATH=/etc/fmo-ai-gateway/gateway-config.json`
- 重启 `fmo-ai-gateway` 与 `fmo-ai-mqtt-monitor`
- 打开 `/ai/` 页面左下“配置网关”完成可视化编辑与保存
