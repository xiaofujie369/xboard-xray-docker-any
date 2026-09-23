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
