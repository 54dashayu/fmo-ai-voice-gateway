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

不要让浏览器自行提供 `X-FMO-Admin` 作为安全边界；公网必须先通过Nginx Basic Auth、SSO或等价管理认证。

