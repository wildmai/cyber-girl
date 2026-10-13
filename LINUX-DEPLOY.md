# Linux Docker CPU 部署

此部署包面向 Linux x86_64、Docker Engine 与 Docker Compose v2。
默认使用实时数字人模式，本地 CPU 运行 Silero VAD v5 与 Whisper base，对话、声音和人物画面通过已配置的云端服务生成。
界面统一显示“实时数字人”。
包内包含两个语音权重与默认人物图片，不包含密钥、用户人设、聊天记录、Windows 环境、Qwen3-TTS 或 MuseTalk 大权重。
建议服务器至少有 4 GB 内存和 6 GB 可用磁盘空间，具体识别速度取决于 CPU。
当前支持多人独立对话，默认最多 8 个对话页面、3 路实时数字人。
人设、数字人连接、语音检测状态和对话上下文按用户隔离，Whisper 等大模型权重共享。
画面连接超过 45 秒或 10 秒没有有效对话时自动挂断，避免空闲会话持续消耗。

## 上传与启动

将 `xiaoman-linux.tar.gz` 上传到服务器，并执行：

```bash
tar -xzf xiaoman-linux.tar.gz
cd xiaoman-linux
cp .env.example .env
chmod 600 .env
nano .env
```

在 `.env` 中填入服务器使用的 `MINIMAX_API_KEY` 与 `VIDU_API_KEY`。
这两个密钥需要在服务器重新配置，Windows 环境变量不会随包迁移。
需要包含 `$` 等字符的值时，用单引号包裹完整值，避免 Compose 插值。
通过 SSH 隧道使用时，保持 `SAIBOGIRL_PUBLIC_ORIGIN` 为空。

若同时下载了预构建镜像 `xiaoman-linux-cpu-image.tar.gz`，先执行：

```bash
docker load -i /上传目录/xiaoman-linux-cpu-image.tar.gz
```

然后启动：

```bash
bash deploy/start.sh
docker compose logs --tail=100 -f
```

已有 `xiaoman:linux-cpu` 镜像时，脚本直接启动，不重新构建。
没有镜像时，脚本自动执行 Docker 构建，首次需要联网下载基础镜像、系统包和 Python 依赖。
正常运行不下载模型，两个云端服务及浏览器 RTC 音视频仍需联网。
启动会自动加载当前模式的本地语音模型，日志出现模型加载失败时先检查文件、内存和服务密钥。

## 从自己电脑访问

默认只将服务发布到服务器的 `127.0.0.1:7860`，通过 SSH 隧道访问：

```bash
ssh -N -L 17860:127.0.0.1:7860 用户名@服务器地址
```

保持此命令运行，在自己电脑浏览器打开 <http://127.0.0.1:17860>。
麦克风采集、音频播放与人物画面显示都发生在自己电脑的浏览器，语音识别在服务器执行。
浏览器首次使用麦克风时需要授权。
这种方式使用 localhost 安全上下文，无需给服务器直接开放 7860 端口。
不要直接使用普通 HTTP 服务器 IP 作为语音页面，浏览器通常会限制非安全上下文的麦克风访问。

需要用外部 IP 测试文字对话时，将端口映射改为 `7860:7860`，并在 `.env` 中设置 `SAIBOGIRL_ALLOWED_HOSTS=服务器IP`。
接受任意访问地址时可设置 `SAIBOGIRL_ALLOWED_HOSTS=*`，这控制访问域名或 IP 的 Host 检查，不是客户端来源 IP 白名单。
使用此环境变量需要包含对应代码的新镜像，或挂载 `app/deployment.py` 修复补丁，旧镜像只添加环境变量不会生效。
公网正式使用建议设置 HTTPS 与访问认证，以保护麦克风功能和付费服务接口。

## 使用 HTTPS 域名

已有域名和宿主机 Nginx 时，可参考 `deploy/nginx.conf.example`。
将域名解析到服务器，准备有效 TLS 证书和 Nginx Basic Auth 密码文件，再替换示例中的域名与证书路径。
例如，已有 `htpasswd` 工具时运行 `htpasswd -c /etc/nginx/xiaoman.htpasswd xiaoman`，按提示设置访问密码。
在 `.env` 中设置：

```text
SAIBOGIRL_PUBLIC_ORIGIN=https://chat.example.com
```

重新创建容器并检查 Nginx 配置：

```bash
docker compose up -d --no-build
sudo nginx -t
sudo systemctl reload nginx
```

随后访问你的 HTTPS 域名，输入 Nginx 用户名与密码。
保留示例中的认证和 WebSocket 转发配置，原始 Origin 与域名 Host 会由应用严格校验。
该示例适用于宿主机 Nginx，容器中的 Nginx 需要按自己的 Docker 网络配置上游地址。
HTTPS 域名模式和 SSH 隧道模式二选一，切回隧道时清空 `SAIBOGIRL_PUBLIC_ORIGIN` 并重新创建容器。

## 日常维护

```bash
docker compose ps
docker compose logs --tail=100
docker compose restart
docker compose down
```

模型配置、人设、上传的人物图保存在 Compose 的 `config` 命名卷，应用日志保存在 `logs` 卷，生成视频保存在 `media` 卷。
浏览器聊天记录与播放偏好仍保存在当前浏览器中。
普通 `docker compose down` 保留数据，不要使用 `down -v`，除非明确要删除所有持久化数据。
更新代码后执行 `docker compose up -d --build`，持久化配置不会被默认配置覆盖。
实时数字人会话持续按服务商规则计费，使用后在“模型与连接”中断开会话。

## 验证与依赖

`MANIFEST.json` 记录部署包内每个文件的 SHA-256，`xiaoman-linux.tar.gz.sha256` 校验整个压缩包。
镜像使用 Python 3.11、PyTorch 2.6 CPU、Whisper 20250625 与项目固定的基础依赖。
当前 CPU 镜像不安装 Qwen3-TTS、MuseTalk 推理依赖，选择本地语音模式前需另行准备对应运行环境和模型。
Docker 配置参考 [Docker Compose 环境变量文档](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/)，容器结构参考 [FastAPI Docker 部署文档](https://fastapi.tiangolo.com/deployment/docker/)。
