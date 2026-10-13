# 已部署的 IP HTTPS

访问地址为 <https://150.158.124.148/>，无需域名。
应用目录为 `/opt/saibo/xiaoman-linux`，Nginx 在单独的 Docker 容器中运行。
80 端口保留证书验证路径，其余请求重定向至 HTTPS，443 端口代理应用和 WebSocket。
浏览器首次使用麦克风时需要允许该 HTTPS 站点的麦克风权限。
应用支持多人独立对话，每个页面拥有独立的对话上下文、语音检测状态和数字人连接。
人设和人物照片按当前浏览器的独立身份保存在 `config/profiles/`，共享 Whisper 等大模型权重。

## 会话和计费控制

默认最多允许 8 个对话页面、3 路实时数字人连接。
可在 `compose.ip-https.yaml` 中调整 `SAIBOGIRL_MAX_DIALOGUES` 与 `SAIBOGIRL_MAX_AVATARS`。
应用上限仍受供应商账号的实际并发配额约束。
超过数字人上限时，在调用供应商接口前返回忙碌提示，不创建新的计费会话。
数字人画面连接超过 45 秒时自动挂断，10 秒没有有效对话时自动回收。
静音音频和状态轮询不会延长空闲会话；本地麦克风持续监听，新语音会触发自动重连，并保留触发重连的这一句话。
本地 MuseTalk 模式没有空闲超时限制。
关闭页面、停止会话或连接失败时，会发送挂断信令并关闭控制连接。
一个用户的停止、断线和人设修改不会影响其他用户。
各用户可独立选择对话 API 模型，共享本地语音权重的切换仅允许在没有其他用户时执行。
CPU 服务器的 Whisper 推理按共享模型锁依次执行，多人说话时可能增加识别等待时间。

## 启动和检查

```bash
cd /opt/saibo/xiaoman-linux
bash deploy/start.sh
bash deploy/ip-https/manage.sh ps
bash deploy/ip-https/manage.sh logs --tail=100 nginx
```

启动脚本已识别 HTTPS 配置，管理命令会自动加载基础、外部访问补丁和 HTTPS 三份 Compose 文件。
不要只使用基础 `compose.yaml` 重建应用，否则 HTTPS 同源配置会被移除。
现有 7860 端口映射保持不变，麦克风对话请使用上面的 HTTPS 地址。

## 证书续期

使用 Let's Encrypt 公网 IP 短期证书，服务端保存证书与私钥，不需要手动下载或复制到客户端。
`xiaoman-ip-cert-renew.timer` 每天检查两次是否需要续期，完成后校验并重新加载 Nginx。
已通过 Certbot 模拟续期和 systemd 服务实际运行检查。
请持续开放公网 TCP 80 和 443，以便浏览器访问及后续证书验证。

```bash
systemctl list-timers xiaoman-ip-cert-renew.timer
systemctl start xiaoman-ip-cert-renew.service
journalctl -u xiaoman-ip-cert-renew.service --no-pager -n 50
```

`certificates/` 包含私钥和 ACME 账户，应与 `config` 数据卷一起安全备份，不要公开上传。
Nginx 目前未配置访问登录，应用的付费对话与实时数字人接口可被访问页面的人使用。
