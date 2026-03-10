# 云浏览器平台

这是一个面向 Agent 的云浏览器平台 MVP，用来为每个 Agent 提供独立的浏览器会话环境。

项目提供一个控制面，用于创建和管理浏览器 session，向 Agent 暴露 CDP，同时向用户暴露 noVNC 观看入口，让“Agent 操作的浏览器”和“用户看到的浏览器”保持一致。

## 项目目标

- 为每个 Agent 提供独立的浏览器环境
- 支持通过 CDP 连接浏览器，例如 Playwright、Puppeteer、`agent-browser`
- 支持通过浏览器网页实时观看当前 session
- 支持每个用户的浏览器 profile 持久化
- 在 MVP 阶段保持实现简单，同时保证后续可以长期扩展

## 项目解决的核心问题

- 多个 Agent 共用一个浏览器时，cookie、localStorage、缓存、登录态会互相污染
- 在单个容器或单个进程中运行多个 Chrome，隔离、资源控制和清理都很困难
- Agent 控制的浏览器和用户观看的浏览器如果不是同一个实例，就会出现“操作和画面不一致”
- 每次现启一个全新的浏览器实例，启动成本较高

这个项目的核心思路是：

- 每个 session 对应一个浏览器容器
- 控制面负责分配、回收、持久化和路由
- Agent 通过 CDP 控制浏览器
- 用户通过 noVNC 查看同一个浏览器
- 通过 warm pool 降低冷启动成本

## 架构概览

### 主要组件

- `browser/`
  - 浏览器运行时镜像
  - 内含 Chromium、Xvfb、x11vnc、noVNC 等组件
- `api/`
  - FastAPI 控制面
  - 使用 `uv` 管理 Python 依赖
  - 通过 Docker SDK 管理 session 和浏览器池
- `redis`
  - 保存 session 元数据和浏览器池状态
- `traefik`
  - 预留为入口中间件
  - 当前已包含在 Compose 中，但不是当前主要验证入口

### Session 模型

每个 session 都会获得：

- 独立的浏览器容器
- 独立的 Chromium profile
- 一个 CDP 访问地址
- 一个 noVNC 观看地址

当前实现中，API 返回的 `viewer_url` 已经自带正确的 noVNC `path` 参数，能够连接到当前 session 对应的 WebSocket 路径。

## 当前已验证的入口

目前已验证可用的主入口是：

- API 和 session 访问入口：`http://localhost:8000`

API 返回的 session 信息中会包含：

- `cdp_url`
- `cdp_http_url`
- `vnc_url`
- `viewer_url`

## 中间件访问方式

### API

- 基础地址：`http://localhost:8000`
- 健康检查：`GET /healthz`
- 指标接口：`GET /metrics`
- Session API：
  - `POST /api/sessions`
  - `GET /api/sessions`
  - `GET /api/sessions/{session_id}`
  - `DELETE /api/sessions/{session_id}`
  - `POST /api/sessions/{session_id}/keep-alive`
  - `GET /api/pool`

### Traefik

Traefik 已经在 `docker-compose.yaml` 中配置，并暴露在：

- `http://localhost:8080`

但需要注意：

- 当前仓库默认**没有开启 Traefik dashboard**
- 当前 MVP 主要通过 `8000` 的 API 入口完成验证
- 在部分 Windows + Docker Desktop 环境下，Traefik 的 Docker provider 可能需要额外配置才能稳定工作

如果你后续需要 Traefik dashboard，可以再打开相关参数并额外暴露一个 dashboard 端口。

### Redis

Redis 默认只在内部网络中使用，没有直接暴露到宿主机。

## 构建方式

当前项目有两个主要镜像：

- 浏览器镜像：`agent-desk:latest`
- API 镜像：`browser-platform-api:latest`

### 使用 Docker Compose 构建

```bash
docker compose build browser-image api
```

### 单独构建浏览器镜像

```bash
docker build -t agent-desk:latest ./browser
```

### 单独构建 API 镜像

```bash
docker build -t browser-platform-api:latest ./api
```

## 部署与启动

### 使用已构建镜像启动

```bash
docker compose up -d --no-build redis api traefik
```

### 需要时边构建边启动

```bash
docker compose up -d redis api traefik
```

## 停止服务

```bash
docker compose down
```

如果还要清理浏览器池里遗留的 session 容器，可以执行：

```bash
docker rm -f $(docker ps -aq --filter "name=browser-pool-")
```

请根据你的 shell 环境调整命令写法。

## API 鉴权

当前 Compose 默认使用静态 API Key：

- `API_KEY=change-me`

你可以通过以下两种方式传递：

- `x-api-key: change-me`
- `Authorization: Bearer change-me`

## 创建 Session 示例

```bash
curl -X POST "http://localhost:8000/api/sessions" \
  -H "x-api-key: change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "demo-user",
    "persist_profile": true,
    "metadata": {
      "source": "manual-test"
    }
  }'
```

典型返回结果如下：

```json
{
  "session_id": "...",
  "cdp_url": "ws://localhost:8000/sessions/.../cdp/devtools/browser/...?token=change-me",
  "cdp_http_url": "http://localhost:8000/sessions/.../cdp?token=change-me",
  "vnc_url": "http://localhost:8000/sessions/.../vnc/?token=change-me",
  "viewer_url": "http://localhost:8000/sessions/.../vnc/vnc.html?autoconnect=true&resize=scale&path=sessions/.../vnc/websockify&token=change-me"
}
```

## Agent 如何访问

### agent-browser

```bash
agent-browser.cmd --cdp "ws://localhost:8000/sessions/<session_id>/cdp/devtools/browser/<browser_id>?token=change-me" open https://example.com
```

### Playwright

```ts
import { chromium } from "playwright";

const browser = await chromium.connectOverCDP(
  "http://localhost:8000/sessions/<session_id>/cdp?token=change-me"
);
```

### Puppeteer

直接使用 API 返回的 `cdp_url` 作为 WebSocket 地址即可。

## 用户如何观看浏览器

直接打开 API 返回的 `viewer_url`。

注意：

- 请直接使用 API 返回的 `viewer_url`
- 不要手动删掉其中的 `path` 参数
- noVNC 当前采用“首次 token 校验，后续 cookie 访问”的方式，保证静态资源和 WebSocket 请求都能正常工作

## Profile 持久化

每个用户的浏览器 profile 归档会保存在 API 挂载的数据卷中：

- `profile-archives:/data/profiles`

当 session 启动时：

- 如果存在该用户历史 profile，API 会自动恢复

当 session 结束时：

- API 会将 profile 再次归档保存

## Warm Pool

API 会维护一个空闲浏览器池，用于减少新 session 的启动时间。

相关环境变量：

- `POOL_MIN_SIZE`
- `POOL_MAX_SIZE`
- `DEFAULT_TTL_SECONDS`
- `HOUSEKEEPING_INTERVAL_SECONDS`

## 安全说明

当前已经做的基础加固包括：

- API 和 Traefik 使用只读文件系统
- `no-new-privileges`
- 容器内存限制
- Redis 默认不对外暴露
- 基于 session 路径的访问隔离

如果进入生产环境，仍然建议继续补充：

- 用真实认证系统替换静态 API Key
- 全站 HTTPS
- 密钥轮换
- 审计日志
- 更完整的访问控制
- 更成熟的入口网关配置

## 本地 Python 开发

API 子项目使用 `uv`。

### 安装依赖

```bash
cd api
uv sync
```

### 本地启动 API

```bash
cd api
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 项目状态

当前仓库仍处于 MVP 阶段。

但目前的关键技术决策已经尽量对齐长期扩展方向：

- 每个 session 独立浏览器环境
- 显式的 session 生命周期管理
- 基于池的启动优化
- 控制面和浏览器运行时分离
- 返回稳定、明确的 API 入口 URL，而不是依赖前端页面 hack

## English Documentation

See `README.md`.
