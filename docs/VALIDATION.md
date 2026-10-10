# 本次公开发布验证

日期：2026-10-10。对象为本仓库公开副本，验证没有启动真实模型、训练、虚拟机、Dify 或容器。

| 检查 | 结果 | 可以说明的范围 |
|---|---|---|
| 后端自动测试 | 59 项通过 | 任务、输入、结果协议、权限、会话、复核与分页；使用临时库和假的外部服务 |
| 前端自动测试 | 33 项通过 | 输入、权限视图、错误处理、会话上下文、跨标签页及分页逻辑 |
| 模型网关自动测试 | 39 项通过 | Schema、预算、模型身份、来源/路径、冻结网络配置；Ollama为假对象，HTTP测试仅临时loopback |
| 前端类型与构建 | 路由类型生成、严格类型检查、生产构建通过 | 当前检出代码可以构建；不证明真实GPU运行 |
| 私有配置生成 | 生成、独立凭据、HTTPS Cookie、调度关闭、拒绝覆盖通过 | 只生成本地临时配置；正式服务器须设置文件权限与凭据管理 |
| DSL 渲染 | 两个URL替换、秘密值为空、拒绝含凭据URL通过 | 可生成部署目标DSL；尚需在所选Dify版本中导入验收 |
| 输入与训练格式 | 4个合成输入可解析；2个合成监督格式通过真实输出协议和引用校验 | 格式正确；不是实际语料、独立金标或模型准确率 |
| 训练配置 | YAML/JSON语法解析通过 | 尚未进行框架完整参数解析、GPU训练或效果评测 |
| 依赖可获取性 | npm/PyPI核验主要固定版本存在 | Next16.4.0、React19.3.0、pnpm11.25.0、FastAPI0.143.0、Uvicorn0.54.0、Pydantic2.14.0、vLLM0.31.0；实际平台兼容性另验收 |

本次前端在 Node 24.15.0 / pnpm 11.25.0 上检查。后端测试产生一项 Starlette 对其 TestClient 使用 HTTPX 的弃用提示，没有测试失败；后续依赖升级时应处理兼容迁移。

首次干净检出曾发现 Next 生成的路由类型缺失，公开版 `typecheck` 已改为先运行 `next typegen` 再执行 TypeScript 检查。

提交前还检查实际Git文件清单、常见凭据/私钥模式、私密目录及大型运行产物、文档内部链接与结构化文件。模式扫描不是任意秘密不存在的数学证明，发布仍使用必要源码白名单与人工审查。

尚未验证：所选服务器上的Nginx/systemd完整部署、不同Dify版本的DSL兼容、生产多节点/GPU能力、LLaMA-Factory实际训练、真实中文识别指标与司法应用可靠性。CI工作流已提供，远端结果以仓库Actions的实际状态为准，不将本地通过写成远端已通过。

复查命令：

```bash
cd backend
python -m pip install -r requirements-test.txt
python -m pytest -q tests
cd ../model_gateway
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
cd ../frontend
pnpm install --frozen-lockfile --ignore-scripts
pnpm typecheck
pnpm test
pnpm build
```

模型效果报告须在独立冻结金标上另行发布，包括误报/漏报、语境与证据错误、数据来源、模型版本、量化精度、失败率、时延与置信区间。不得用本页工程测试数字代替效果指标。
