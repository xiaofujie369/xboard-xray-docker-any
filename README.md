# XBoard sing-box Docker Sync

基于 [xboard-xray-docker-sync](https://github.com/xiaofujie369/xboard-xray-docker-sync) 的独立 sing-box 版本。使用官方 sing-box 源码构建 Docker 核心，以 Python 对接 XBoard：同步节点配置、同步用户、按用户统计上传/下载流量、向面板上报节点状态。

## 协议

| 面板节点 | NODES 写法 | 支持范围 |
| --- | --- | --- |
| AnyTLS | `101:anytls` | 多用户、TLS、padding_scheme |
| Hysteria2 | `102:hysteria2` 或 `102:hysteria` | 自动使用 XBoard `hysteria` API，version=2、带宽、Salamander |
| TUIC | `103:tuic` | v5、多用户、TLS、拥塞控制 |
| VLESS Reality | `104:vless` | Reality、Vision；也支持普通 TCP/TLS、WS、gRPC、HTTPUpgrade |
| Shadowsocks | `105:ss` 或 `105:shadowsocks` | TCP/UDP、多用户 AEAD 和 2022 AES |

SS 支持 `aes-128-gcm`、`aes-192-gcm`、`aes-256-gcm`、`chacha20-ietf-poly1305`、`2022-blake3-aes-128-gcm`、`2022-blake3-aes-256-gcm`。
SS2022 服务端密钥取面板 `server_key`，用户密钥按 XBoard `Helper::uuidToBase64` 从 UUID 前 16/32 个字符进行 Base64 编码。定制面板若使用其他密钥算法，需要调整转换逻辑。

## 安装（Debian / Ubuntu，systemd）

先安装 Docker Engine、Docker Compose plugin 和 Git，然后克隆项目并安装：

```bash
git clone https://github.com/xiaofujie369/xboard-xray-docker-any.git
cd xboard-xray-docker-any
sudo bash install.sh
```

按提示输入 **XBoard 面板 HTTPS 地址、通讯密钥、节点列表**：

```ini
PANEL_URL=https://panel.example.com
PANEL_TOKEN=你的面板通讯密钥
NODES=101:anytls,102:hysteria2,103:tuic,104:vless,105:ss
INTERVAL=60
```

这份配置存放在 `/opt/singbox-sync/.env`，不加引号。节点 ID 替换成你的实际 ID。
安装器从官方源码构建固定版本 `1.12.25`，首次构建需要访问 GitHub、Go 模块服务器和容器镜像仓库。

### TLS 证书

AnyTLS、Hysteria2、TUIC 和普通 VLESS TLS 节点需要有效证书。
每个节点单独放置证书；例如 101 节点：

```bash
sudo install -d -m 700 /opt/singbox/config/certs/101
sudo install -m 600 /你的证书目录/fullchain.pem /opt/singbox/config/certs/101/fullchain.pem
sudo install -m 600 /你的证书目录/privkey.pem /opt/singbox/config/certs/101/privkey.pem
```

其他 TLS 节点同样处理，可复制同一域名适用的证书。面板中的 SNI 应与证书一致。
本项目不自动申请证书，也不自动读取面板的 PEM 内容。证书续期后将新文件复制到上述路径，下一次同步会检测变化并重启加载。
**VLESS Reality 和 SS 不需要这些证书。** Reality 私钥、握手域名、short_id 从面板读取。

证书准备好后初始化并启动：

```bash
sudo xbs init
sudo xbs status
sudo xbs logs
```

`init` 只用于首次部署；已有配置使用 `xbs start` 或 `xbs sync`。
开放面板设置的节点端口：AnyTLS / VLESS 使用 TCP，Hysteria2 / TUIC 使用 UDP，SS 使用 TCP 和 UDP。云安全组也要开放。

## 管理

```bash
sudo xbs edit        # 修改面板地址、密钥、节点列表
sudo xbs sync        # 立即执行上报和同步
sudo xbs check       # 使用核心检查当前配置
sudo xbs logs        # 同步/上报日志
sudo xbs core-logs   # 核心连接日志（含用户标识和地址，分享前脱敏）
sudo xbs stop
sudo xbs start
```

运行路径：

| 路径 | 内容 |
| --- | --- |
| `/opt/singbox/config/config.json` | 自动生成的配置，不要手动修改 |
| `/opt/singbox/config/config.previous.json` | 上一次配置 |
| `/opt/singbox/config/certs/<ID>/` | TLS 证书 |
| `/opt/singbox/docker-compose.yml` | 核心容器定义 |
| `/opt/singbox-sync/.env` | XBoard 对接信息 |
| `/opt/singbox-sync/state.json` | 流量采样断点与待上报队列，不要随意删除 |
| `/opt/singbox-sync/routes.json` | 可选的 sing-box 原生出站/路由 |

容器名 `xboard-singbox`，服务名 `xboard-singbox.service`，命令 `xbs`。路径、名称与原 Xray 项目分开，端口仍需避免冲突。不要让两套服务同时对接同一个面板节点 ID，以免流量和状态混淆。

## 流量与变更行为

- 配置和用户通过 `/api/v1/server/UniProxy/config`、`user` 拉取，流量和节点状态通过 `/api/v2/server/report` 上报。
- 使用启用 `with_v2ray_api` 的官方源码构建；官方发行包默认不含该统计功能。API 仅监听 `127.0.0.1:10086`，不要映射或开放这个端口。
- 用户名采用 `节点ID:用户ID`，同一用户在多个节点的流量分别统计。
- 读取累计计数、不执行 reset；差值与待上报队列先写磁盘，再请求面板。失败时保留待发送流量；成功的节点不重复发送该批流量。
- 面板未提供幂等键，因此网络超时但面板实际已入账，或面板成功后进程来不及保存状态的极端情况下，重试可能重复入账；不承诺 exactly-once。
- 配置变化先用 `sing-box check` 校验，再保存上一版并重启；启动失败尝试回滚。无变化不重启。面板用户列表为空时删除对应入口，确保旧用户失效。
- 同步进程与命令行采用文件锁，避免并发同步和重复计费。程序控制的重启前会采样保存；采样到重启之间、异常退出或手动重启期间尚未采样的流量仍可能丢失。
- 密钥变更可编辑 `.env`；切换到另一面板需要先处理旧面板待上报数据，程序不会把旧流量自动发往新面板。

## 当前边界

本版本上报**用户流量和节点状态**，尚未实现真实用户在线 IP / 在线设备数上报，不伪造在线数据。设备数限制、单用户限速、面板审计规则尚未实现。

不支持 Hysteria v1、TUIC v4、SS 插件、Xray 的 XHTTP / mKCP / VLESS encryption。面板的 `routes` 路由组可自动转换，见下节；Xray 格式的 `custom_routes` / `custom_outbounds` 仍需迁移为 sing-box 原生 `routes.json`，见 [示例](docs/routes.example.json)。

配置或证书变动采用重启加载，现有连接会断开；当前不是无损热更新。上报仅支持原项目使用的 XBoard v2 report API，旧面板不自动降级。定制面板的字段差异需用真实响应继续验证。

## XBoard 面板路由

节点关联的路由组随 `config` API 中的 `routes` 拉取，不需要在本地重复填写。

| 面板动作 | sing-box 行为 |
| --- | --- |
| `block` | 阻断匹配目标 |
| `direct` | 匹配目标直连 |
| `proxy` | 使用 `action_value` 指定的出站标签；须先在本地 `routes.json` 中定义该出站 |
| `dns` | 为匹配域名选择 DNS，并在实际连接前使用该 DNS 解析目标 |

支持普通域名、`*.域名`、`domain:`、`full:`、`keyword:`、`regexp:`、IPv4/IPv6 CIDR，以及 `#` / `//` / `;` 开头的注释。普通域名和 `*.域名` 均匹配该域名及其子域。不同类别的匹配项使用“或”关系。
`*` / `*.*` 表示该节点所有目标；在 DNS 路由中，`0.0.0.0/0` / `::/0` 也视为默认 DNS 匹配。默认 DNS 规则统一排到具体域名 DNS 规则之后。

例如面板里填：

```text
# 常用电商类
taobao.com
*.taobao.com
tmall.com
*.tmall.com
jd.com
*.jd.com
```

动作选“指定 DNS 服务器进行解析”，填 `223.5.5.5,119.29.29.29,2400:3200::1,2402:4e00::`，即可自动转换。
DNS 地址支持 IPv4、IPv6、`local`、`udp://`、`tcp://`、`tls://`、`https://`、`quic://`；IPv6 带端口使用 `udp://[IPv6]:5353` 格式。

**多个 DNS 地址都会导入，但当前使用列表中的第一个地址；没有自动故障切换或并发竞速。** 程序会在日志中提示这一点。
DNS 规则只控制 sing-box 对目标域名的解析，不会强行改写客户端自行发送到其他 DNS 服务的查询。客户端若只提供目标 IP，也不能根据缺失的域名命中域名分流。

所有面板规则都限定到关联节点，DNS 缓存按解析器隔离。阻断/直连/代理规则保持面板返回的先后顺序；面板规则优先于本地规则。具体域名 DNS 优先于默认 DNS，均优先于本地 DNS 规则。
无法转换的面板匹配项会记录警告并跳过，保留同组的有效匹配项；无效 DNS 地址也会跳过，使用第一个有效地址。不认识的动作、无效路由结构或未定义的代理出站会跳过该条路由，不阻止节点初始化。整组匹配项都无效时不生成该组规则，绝不扩大为全局匹配。日志会标明节点、路由及匹配项位置，但不打印原始敏感字段。**被跳过的规则不会生效，包括阻断项**，应根据日志随后修正面板内容。
最终仍执行 `sing-box check`：核心配置、证书或未被转换器识别的底层错误仍会阻止应用，更新时保留旧配置。
旧的 `geosite:` / `geoip:国家代码` 不直接兼容 sing-box 1.12；可在本地 `route.rule_set` 定义规则集后使用 `rule-set:标签`。`geoip:private` 支持用于普通流量路由。

## 更新 / 卸载

在项目目录执行 `git pull --ff-only` 后运行 `sudo bash update.sh`：更新 Python 同步程序，保留配置、证书、统计状态和服务原先的运行状态，不自动升级核心镜像。
如果此前在首次初始化时因面板路由报错，更新后重新运行 `sudo xbs init`；已运行的节点使用 `sudo xbs sync`。
核心版本变更应先在测试节点验证，再手动构建并部署，避免未经验证的自动升级。

`sudo bash uninstall.sh` 停止并卸载服务，保留 `/opt/singbox` 和 `/opt/singbox-sync` 下的文件供备份。

## 验证

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
bash -n install.sh update.sh uninstall.sh sync/manage.sh
python tests/check_configs.py /path/to/sing-box
python tests/runtime_stats.py /path/to/sing-box
python tests/runtime_routes.py /path/to/sing-box
```

核心验证脚本需要包含 `with_v2ray_api` 的核心。仅检查官方发行包的协议配置时，可用 `check_configs.py ... --without-stats`。
CI 会构建实际 Docker 核心、检查各协议配置，并通过 VLESS、AnyTLS、Hysteria2、TUIC、SS 的真实连接验证 gRPC 用户流量统计。
当前本地验证情况见 [VALIDATION.md](docs/VALIDATION.md)。尚未连接你的真实面板或部署到你的节点服务器。

## 参考

- [原项目](https://github.com/xiaofujie369/xboard-xray-docker-sync)，参考提交 `5b826230b44d8d7e579dff392e2998d817877a4f`。
- [sing-box 1.12.25 官方源码](https://github.com/SagerNet/sing-box/tree/v1.12.25)，启用 `with_quic,with_utls,with_v2ray_api`。
- [V2Ray API 文档](https://sing-box.sagernet.org/configuration/experimental/v2ray-api/)。
- [XBoard 节点配置生成](https://github.com/cedar2025/Xboard/blob/master/app/Services/ServerService.php) 与 [订阅生成](https://github.com/cedar2025/Xboard/blob/master/app/Protocols/SingBox.php)。
