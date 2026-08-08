# 阿里云ECS部署

## 前提

- 已有可工作的FMO MQTT Broker与SAS/CA鉴权。
- Python 3.10或更高版本。
- EMQX管理端口与AI管理API仅监听回环地址。
- ECS资源允许增加两个轻量Python服务。

## 部署目录

```text
/opt/fmo-ai-gateway/
/etc/fmo-ai-gateway.env
/etc/fmo-ai-gateway/gateway-config.json
/var/lib/fmo-ai-gateway/
```

## 推荐步骤

1. 创建专用系统用户 `fmo-ai`。
2. 安装 `ai-gateway/` 到 `/opt/fmo-ai-gateway`。
3. 根据示例创建环境文件与网关配置，权限设为 `0600`。
4. 将 `deploy/emqx-ai-loopback.conf` 合并到EMQX配置并验证，不要覆盖现有监听器。
5. 安装两个systemd服务模板，先保持所有AI和PTT开关关闭。
6. 启动文本网关并测试 `/health`。
7. 启动MQTT监听，只验证订阅与帧解析。
8. 配置带认证的Nginx `/ai/` 管理入口。
9. 完成模拟ASR/TTS闭环后，再申请真实PTT测试授权。

任何修改前都应记录现有EMQX、SAS、Nginx、数据库和业务服务状态，并准备逐文件回退备份。

