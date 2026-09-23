# 验证记录

本地环境：Windows amd64、Python 3.10。核心使用 sing-box 官方 `v1.12.25` 源码，提交 `73bfb99ebce7923c485435e4faf8571b412065a9`，Go 1.25.8 构建，启用 `with_quic,with_utls,with_v2ray_api`。

已完成：

- 19 项 Python 单元测试通过：五协议配置、接口别名、SS2022 密钥、空用户撤权、节点流量隔离、重启后的计数、断网持久化重试、部分节点上报成功、面板地址迁移保护、配置校验失败保留原文件、采样失败禁止重启、启动失败回滚和无变化不重启。
- 使用官方预编译版以及本地构建的统计版核心，检查 AnyTLS、Hysteria2、TUIC、VLESS Reality/Vision、SS AEAD、SS2022 AES-128 配置，均通过 `sing-box check`。官方预编译版检查时去掉其未编译的统计模块，源码构建版保留完整统计配置。
- 使用源码构建的核心进行真实本机传输：SOCKS 客户端 → 各协议服务端 → 本机 HTTP 服务。VLESS、AnyTLS、Hysteria2、TUIC、SS chacha20-ietf-poly1305、SS2022 AES-128 均收到响应，gRPC 读到对应 `1:7` 用户的非零上下行字节数，重复读取未清零。
- Bash 安装/更新/卸载/管理脚本语法检查通过。

尚未验证：

- 你的真实 XBoard 面板接口、节点数据和最终流量入账。
- Linux Docker 镜像构建、systemd 安装及服务器防火墙；本机没有 Docker，已提供 CI 构建与验证流程，但本轮没有在远程执行该 CI。
- 公网 Reality 握手及你的证书/SNI。Reality 本轮完成配置校验，真实 VLESS 传输测试使用普通 TCP。
- UDP 业务端到端传输、压力测试、ARM64 运行。Hysteria2/TUIC 测试覆盖了 QUIC 传输中的 TCP 代理请求。

测试使用临时测试证书和固定测试用户 UUID；交付包不包含面板通讯密钥、真实用户数据或生产证书。

## 面板路由更新验证

- 新增 14 项路由测试，总计 33 项单元测试通过，覆盖截图的多行域名及 IPv4/IPv6 DNS 列表、注释、默认规则排序、节点隔离、空规则、代理出站校验、本地规则合并和完整 API 到配置转换路径。
- 五协议的生成配置加入面板阻断规则、域名 DNS、默认 DNS 和 DoH 地址后，通过 sing-box 1.12.25 实际配置检查。
- `tests/runtime_routes.py` 启动三个独立本地 DNS 服务及两个真实 VLESS 节点，验证具体域名选中对应 DNS、默认 DNS 不覆盖具体域名、同一域名在不同节点使用不同 DNS，以及直连例外和阻断规则生效。测试不依赖公网 DNS。
- 五协议的真实传输和 gRPC 流量测试再次通过；更新脚本语法检查通过。
- 已核实初始提交 `b1c14ae` 的 GitHub Actions 成功完成 Linux Docker 构建及运行测试；路由更新的 CI 也已纳入真实 DNS 路由测试。本地仍未连接用户的生产面板和节点。
