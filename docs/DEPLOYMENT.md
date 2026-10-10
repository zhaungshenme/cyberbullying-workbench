# 服务器部署指南

本指南把工作台源码与业务服务安装在 Linux 服务器。用户从自己的电脑浏览器访问 HTTPS 地址。推荐 Ubuntu 24.04 LTS / Debian 的受支持版本；生产环境使用业务服务器与 GPU 服务器分离的架构，训练另设资源池。

本次提供的是安装流程和参考配置，未声称已在所有系统上执行验收。现有参考实现使用 SQLite 单业务进程，适合验证业务闭环；正式规模必须先完成容量测试与存储升级。

## 1. 资源与网络

| 节点 | 起步建议 | 承担职责 |
|---|---|---|
| 业务服务器 | 8 vCPU、32 GB RAM、200 GB+ SSD | 工作台、API、材料、数据库、备份 |
| Dify | 建议独立资源预算，按部署版本官方要求设置 | 工作流管理、任务引用与调用状态 |
| 推理服务器 | GPU/显存按所选模型与并发规划，见模型文档 | 模型与受限适配服务 |
| 训练服务器 | 独立 GPU、数据卷、实验与检查点空间 | SFT / LoRA / QLoRA 与评测 |
| 客户机 | 现代浏览器 | 登录、提交、复核、报告查看 |

服务器内部端口：前端 3001、业务 API 18788；对用户开放 HTTPS 443。Dify 与模型管理端口限制在管理网络。不要直接公开数据库、Ollama、vLLM 或网关到互联网。

当前 `DIFY_API_URL` 限制为 HTTP loopback。为保持当前代码兼容，在业务主机部署 Dify，或用**受控本地反向代理**将 `http://127.0.0.1:18080/v1` 转发到可信 Dify 内网 HTTPS。跨主机直连需要后续有验收的配置升级，不应关闭 URL 或证书校验。参考 `deploy/nginx/dify-loopback.conf.example`；替换上游与CA，验证只监听本机。

## 2. 准备系统与源码

先安装 Git、Python 3.12、其 venv 模块、Node.js 24 LTS、pnpm 11.25.0。Node 必须支持项目的类型剥离测试；依赖以锁文件为准。检查真实版本与安装路径：

```bash
git --version
python3.12 --version
node --version
pnpm --version
command -v node
```

以下操作在**目标 Linux 服务器**执行，需管理员权限。已存在的安装目录不要覆盖：

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin workbench
sudo git clone https://github.com/zhaungshenme/cyberbullying-workbench.git /opt/workbench
sudo chown -R workbench:workbench /opt/workbench
sudo install -d -m 750 -o workbench -g workbench /var/lib/workbench
sudo install -d -m 750 -o root -g workbench /etc/workbench
cd /opt/workbench
sudo -u workbench git -C /opt/workbench rev-parse HEAD
sudo -u workbench python3.12 -m venv /opt/workbench/.venv
sudo -u workbench /opt/workbench/.venv/bin/python -m pip install -r backend/requirements.txt
sudo -u workbench sh -c 'cd /opt/workbench/frontend && pnpm install --frozen-lockfile --ignore-scripts && pnpm typecheck && pnpm test && pnpm build'
```

如果系统已有 `workbench` 用户，复用并核实其权限，不重复创建。如果 pnpm 位于当前用户私有目录，应提供可信的系统级安装，确保服务用户可执行。禁止用浮动依赖替代锁文件来掩盖安装错误。

## 3. 生成私有环境配置

```bash
sudo /opt/workbench/.venv/bin/python scripts/init_env.py \
  --output /etc/workbench --data-dir /var/lib/workbench
sudo chown root:workbench /etc/workbench/backend.env /etc/workbench/frontend.env
sudo chmod 640 /etc/workbench/backend.env /etc/workbench/frontend.env
```

脚本为新的安装生成随机凭据，并拒绝覆盖已有文件；不会打印口令或启动服务。管理员在服务器通过授权工具读取 `WORKBENCH_BOOTSTRAP_ADMIN_PASSWORD`，首次以用户名 `admin` 登录后修改密码。后续启动不会覆盖已有账户。

`WORKBENCH_BACKEND_TOKEN` 是前端服务器代理到业务 API 的凭据，两端必须相同；`CB_SERVICE_TOKEN` 是 Dify 调用材料集成接口的另一凭据，两者必须不同。它们不能放入浏览器变量、源码或 Dify 导出。环境示例见 `deploy/*.env.example`，正式密钥保存在 `/etc/workbench`。

默认 `CB_ENABLE_WORKER=0` 与 `WORKBENCH_ENABLE_DISPATCHER=0`，先验收多用户与材料入库。默认安全 Cookie 需要 HTTPS；仅隔离开发环境的 HTTP 演示可将 `WORKBENCH_COOKIE_SECURE=false`，实际材料使用可信 HTTPS。

## 4. 安装服务与 HTTPS

```bash
sudo install -m 644 deploy/systemd/workbench-backend.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/workbench-frontend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now workbench-backend workbench-frontend
sudo systemctl status workbench-backend workbench-frontend
```

前端 unit 假设 Node 是 `/usr/bin/node`，根据 `command -v node` 的可信绝对路径修改。后端必须使用 `--workers 1`；同一数据目录不得同时运行第二个业务进程，否则破坏当前单进程 worker / 锁的假设。

配置域名与可信证书，依据 `deploy/nginx/workbench.conf` 配置 Nginx，替换域名和证书路径后执行 `nginx -t` 再加载。代理必须保留正确的 Host 与 `X-Forwarded-Proto`，入口服务独占代理信任边界。正式启用前从 HTTPS 客户端验证登录、改密、提交与跨来源拒绝，确认写操作来源校验和 Secure Cookie 正常。

## 5. 接入 Dify 工作流

1. 根据 [Dify 官方部署文档](https://docs.dify.ai/en/self-host/quick-start/docker-compose) 安装自托管 Dify；固定版本、保留其许可与卷，独立备份。不要将已有生产 Dify 的配置或数据卷替换为本仓库示例。
2. 确认 Dify 的 HTTP 节点能访问业务服务。默认 API 监听 loopback：若 Dify 在容器网络中，在反向代理增加仅容器网段可达的集成入口，且只开放 `/integration/health` 和 `/integration/analysis-jobs`；仍需 Bearer 验证。参考 `deploy/nginx/materials-internal.conf.example` 的两个精确路径、可信TLS和Dify网段允许列表。可以在内部网络指定业务监听地址，但防火墙必须阻断客户端直连 18788。
3. 生成 DSL，URL 必须是 **Dify HTTP 执行容器实际可达** 的地址；不能把该容器的 `127.0.0.1` 当作主机：

```bash
python3.12 scripts/render_workflow.py \
  --backend-url https://materials.internal.example.org:18789 \
  --output /tmp/workflow_submit.yml
```

4. 在 Dify 中导入生成文件，设置 secret 环境变量 `backend_token` 为实际 `CB_SERVICE_TOKEN`。变量名来自 DSL，值不是前端的 `WORKBENCH_BACKEND_TOKEN`。
5. 发布 Workflow 应用，创建应用 API key；写入服务器 `/etc/workbench/backend.env` 的 `DIFY_SUBMIT_KEY`，设置实际 loopback `DIFY_API_URL`。
6. 若部署版本有 HTTP/SSRF 限制，按其官方配置只允许所需内部目标；证书必须可验证。禁止全局关闭安全代理或 TLS 校验。先验证健康检查与提交节点。

工作流只接收 `window_id / input_hash / case_version / pipeline_version / idempotency_key`。Dify 成功返回表示任务已接受；模型完成与结果验收由材料服务管理，不能将 HTTP 200/202 视为研判正确。

## 6. 模型接入与启用

当前参考网关只支持固定的 Qwen3-4B / Ollama 模型资产与 tokenizer。迁移其服务器地址可用网关环境配置；获取与身份核验见 `model_gateway/README.md`。大算力服务器的 vLLM 服务需要按 [模型文档](MODELS_AND_TRAINING.md) 实现受限适配，**不是把 vLLM URL 填进网关变量即可**。

材料服务配置如下：

```text
CB_MODEL_GATEWAY_URL=https://实际推理网关地址:8444
CB_MODEL_GATEWAY_TOKEN=受控生成的独立凭据
CB_MODEL_GATEWAY_CA=/etc/workbench/model-ca.crt
CB_MODEL_NAME=与受限网关实际允许的模型ID完全相同
CB_PIPELINE_VERSION=此版本工作流/提示/模型适配的版本
```

网关 CA 必须可验证且证书 SAN 匹配访问地址。当前主应用预算固定为 2048 总 token、1536 提示 token、512 输出 token；主应用并不通过 `CB_TOKEN_BUDGET/CB_MAX_*` 自动调大预算。长上下文升级必须同步窗口策略、服务预算、适配层和评测，不能只改模型参数。

完成模型身份、超时、截断、逐字引用和完整链路验收后，将两个开关设为 1，重启后端。否则保持为 0。不自动启动训练、不把推理提示修改当作微调。

## 7. 上线验收、备份与更新

按顺序验收：HTTPS 登录改密 → 四角色授权 → 合成材料导入 → Dify 提交引用 → 模型健康与固定身份 → 结果引用验证 → 分配与人工复核 → 报告和审计 → 断网恢复与幂等。复核员不能把不完整模型任务确认为已确认观察。

真实材料上线前完成独立金标与误报评估、来源授权台账、单位身份接入、备份恢复测试与容量压测。备份覆盖整个 `WORKBENCH_DATA_DIR` 的三个 SQLite 数据库和原始材料，以及私有配置与固定发布版本。使用 SQLite backup API 或停止业务后完整备份，不只复制正在写入的 `.sqlite3` 文件而丢失 WAL。

升级先备份并在隔离环境演练，记录 `sudo -u workbench git -C /opt/workbench rev-parse HEAD`、依赖锁、配置版本和数据库迁移。代码回滚不能直接把旧数据库覆盖到新库；新任务与审计必须保留。模型与 pipeline 版本变化使用新版本标识，历史输出按原版本查看。更换服务器凭据会使签名 cursor 失效，客户端刷新首屏。

模板 `frontend/Dockerfile` 可供后续镜像发布参考；本次没有提供工作台、Dify、GPU 全栈已验收的 Compose，不能将单个 Dockerfile 当作完整生产部署。
