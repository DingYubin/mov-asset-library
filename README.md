# mov-asset-library

MOV 资产库：用于资产建设、存储、检索、预览和发布展示。

当前版本提供：

- 单管理员登录、退出、会话过期和 CSRF 防护
- 资产创建、上传、搜索、分类筛选和发布状态
- 图片与视频上传时自动生成 WebP 预览图，视频优先使用首帧，并提供历史素材回填脚本
- 视频卡片支持悬停静音预览，点击后在站内弹窗播放，移开后自动暂停并恢复首帧
- 本地文件存储适配器，便于开发和测试
- SQLAlchemy 数据层，可使用 SQLite 或腾讯云 MySQL 8.0
- COS 存储适配器接口，生产环境接入腾讯云 COS
- React + Vite 管理台

## 本地运行

需要 Python 3.11+、Node.js 20+；视频首帧预览还需要系统提供 `ffmpeg` 和 `ffprobe`。

cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python backend/scripts/create_admin.py
uvicorn app.main:app --app-dir backend --reload --port 8800

另开终端：

cd frontend
npm install
npm run dev

管理台默认地址：http://127.0.0.1:5173

## 生产部署

生产环境建议使用腾讯云轻量数据库 MySQL 8.0、腾讯云 COS 私有存储桶、Nginx HTTPS 反向代理和 systemd。

当前部署模板将应用部署在站点根路径 `/`，旧的 `/mov-asset-library/` 地址会重定向到根路径；后端只监听 `127.0.0.1:8800`。生产入口使用 `https://119.91.33.40/`，80 端口除 ACME 验证路径外统一以 `308` 跳转到 HTTPS，避免登录凭据或 API 请求走明文 HTTP。

服务器首次部署可先使用 `deploy/nginx/mov-asset-library-http.conf` 完成静态站点和 ACME 验证，再切换到 HTTPS 配置。生产环境的 `APP_SECRET_KEY`、数据库文件和 COS 密钥只放在服务器 `/etc/mov-asset-library.env`，不要提交到 Git。原 APEC 静态文件保留在 `/var/www/apec`，但不再占用站点根入口。

## 视频工作流接入

Toonflow/DMC 先通过独立 Bearer 令牌把参考图上传到资产库，再发布并验证公网可读性。资产库令牌必须与 `DMC_API_KEY` 分开配置，不要复用供应商密钥。

先在服务器 `/etc/mov-asset-library.env` 配置：

```env
ASSET_LIBRARY_API_TOKEN=<独立的随机令牌>
ASSET_LIBRARY_PUBLIC_BASE_URL=https://119.91.33.40
ASSET_LIBRARY_PUBLISH_VERIFY_TIMEOUT_SECONDS=15
```

上传时资产默认保持 `draft/private`。相同 SHA-256 的文件会复用已有资产记录：

```bash
BASE=https://119.91.33.40
curl -fsS -X POST "$BASE/api/integration/assets" \
  -H "Authorization: Bearer $ASSET_LIBRARY_API_TOKEN" \
  -F "title=视频参考图" \
  -F "file=@frame.png"
```

从响应中取 `asset_id`，然后发布：

```bash
ASSET_ID=<上传响应中的 asset_id>
curl -fsS -X POST "$BASE/api/integration/assets/$ASSET_ID/publish" \
  -H "Authorization: Bearer $ASSET_LIBRARY_API_TOKEN"
```

只有发布响应的 `verified` 为 `true` 且 `public_url` 非空时，才把该 URL 传给 Toonflow/DMC。发布接口会匿名回读文件，并校验 HTTP 200、MIME、长度和 SHA-256；验证失败会回滚为私有资产。分镜帧可额外传 `project_id` 和 `scene_id`，资产库会校验分镜归属并建立关联。
