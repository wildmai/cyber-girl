# 现有 Linux 镜像的外部访问补丁

此补丁让已有镜像接受任意访问地址，解决 `Invalid host header`。
它不修改你已经调整的端口、服务密钥、模型或持久化数据，也无需重新导入完整镜像。

将 `xiaoman-public-access-fix.tar.gz` 上传到服务器，在当前 `compose.yaml` 所在目录执行：

```bash
tar -xzf /上传目录/xiaoman-public-access-fix.tar.gz
docker compose -f compose.yaml -f compose.public-access.yaml up -d --no-build
```

确认 `.env` 中 `SAIBOGIRL_PUBLIC_ORIGIN` 为空，以便当前 HTTP IP 地址的文字对话和 WebSocket 连接通过同源校验。
刷新 <http://150.158.124.148:7860/> 即可。
HTTP 公网 IP 无法提供浏览器麦克风所需的安全上下文，语音输入应使用 HTTPS 或 localhost SSH 隧道。
对外正式开放时应加 HTTPS 和访问认证，避免他人使用付费服务接口。

补丁通过只读挂载替换容器内 `app/deployment.py`，并设置 `SAIBOGIRL_ALLOWED_HOSTS=*`。
后续使用 Compose 管理该实例时，请保留两个 `-f` 参数，单独运行原来的 Compose 文件会移除这次挂载。
切换到 HTTPS 域名时设置正确的 `SAIBOGIRL_PUBLIC_ORIGIN`，然后仍用上述两个配置文件重新创建容器。

撤销补丁只需执行：

```bash
docker compose -f compose.yaml up -d --no-build
```
