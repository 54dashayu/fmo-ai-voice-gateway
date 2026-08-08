# FMO NAS 知识与数据服务

目标设备：群晖 DS918+。本服务不运行任何 AI 模型，只存储知识库、百炼生成的向量、管理配置、脱敏审计数据和短期临时语音。

## 边界

- EMQX、SAS、FMO协议和PTT控制继续运行在公网VPS。
- ASR、Embedding、Rerank、LLM和TTS全部调用阿里云百炼。
- NAS没有公网入口；NAS主动建立反向SSH隧道，VPS仅通过本机 `127.0.0.1:18787` 访问NAS的 `8787` 端口。
- API Key不进入NAS知识库服务。NAS与VPS之间使用独立随机Bearer Token。
- 原始语音默认1小时自动过期。

## 数据目录

```text
data/
├── fmo-knowledge.sqlite3
├── documents/
└── temporary-audio/
```

## 部署前门禁

1. DSM安装兼容版本的Container Manager；NAS到VPS使用出站反向SSH隧道，不占用ZeroTier节点。
2. 创建独立目录，例如 `/volume1/docker/fmo-knowledge`。
3. 生成独立随机 `FMO_KB_ADMIN_TOKEN`，不得复用百炼API Key。
4. VPS反向转发端口必须只绑定 `127.0.0.1`；不得把18787或8787暴露到公网。
5. 保留现有NAS服务，不修改QuickConnect、Synology Drive、Photos或百度网盘配置。

## 资源限制

- CPU上限：1.5核。
- 内存上限：1 GiB。
- 进程数上限：100。
- 容器根文件系统只读。

## Docker Hub不可用时的原生运行方式

本服务仅依赖Python标准库，可使用NAS已安装的Python 3.13运行。`start-native.sh`从`.env`读取独立服务Token，自动定位群晖Python路径并写入PID；`stop-native.sh`用于停止。可在DSM任务计划中建立“开机”任务执行：

```sh
/bin/sh /volume1/docker/knowledge-service/start-native.sh
```

## NAS到VPS的反向SSH隧道

使用独立的VPS低权限账号和独立Ed25519密钥。VPS的授权公钥只允许监听
`127.0.0.1:18787`，NAS开机任务执行：

```sh
/bin/sh /volume1/docker/knowledge-service/start-reverse-tunnel.sh
```

VPS上的AI网关通过 `http://127.0.0.1:18787` 访问知识服务。隧道不会开放公网端口，
也不使用百炼API Key。
