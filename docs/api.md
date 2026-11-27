# HTTP接口清单

网关默认只监听 `127.0.0.1:18788`。

| 方法 | 路径 | 用途 | 鉴权 |
|---|---|---|---|
| GET | `/health` | 最小健康检查 | 回环访问 |
| GET | `/status` | 脱敏运行状态 | Nginx管理认证 |
| POST | `/v1/chat` | 文本问答 | Bearer Token |
| GET | `/admin/gateway-config` | 脱敏配置 | `X-FMO-Admin: 1`，仅由认证反代注入 |
| POST | `/admin/gateway-config` | 保存配置 | 同上 |
| GET/POST | `/admin/blacklist` | 黑名单 | 同上 |
| GET/POST | `/admin/control` | 自动回复与播报开关 | 同上 |
| GET/POST | `/admin/persona` | AI身份表达 | 同上 |
| GET/POST | `/admin/knowledge/*` | NAS知识库代理 | 同上 |
| GET/POST | `/admin/net-checkin` | 本地点名设置、状态与可选外部同步 | 同上 |
| GET | `/admin/audio` | 音频/SSTV素材、计划与运行状态 | 同上 |
| GET | `/admin/audio/catalog` | 可选公共SSTV目录 | 同上 |
| GET | `/admin/audio/gallery` | 可选NAS只读图库目录 | 同上 |
| POST | `/admin/audio` | 导入、计划、立即播放或停止 | `X-FMO-Admin: 1` + `X-FMO-Audio-Request: 1` + 同源检查 |

不要让浏览器自行提供 `X-FMO-Admin` 作为安全边界；公网必须先通过Nginx Basic Auth、SSO或等价管理认证。

本公开 API 不包含设备证书申请、签发、私钥上传、证书生命周期管理或发证服务代理。公共信任材料如由部署者另行使用，只能是公开证书数据，不能成为安装或运行网关的隐含依赖。

