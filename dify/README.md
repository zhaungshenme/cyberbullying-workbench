# Dify 任务引用工作流

先用 `scripts/render_workflow.py` 将模板中的材料服务地址换为 Dify 执行容器可访问的受控地址，然后在 Dify 导入。具体网络、发布、应用密钥与启用流程见 [部署指南](../docs/DEPLOYMENT.md)。

secret 环境变量 `backend_token` 的实际值是 **CB_SERVICE_TOKEN**；源码导出的值保持空。现有节点检查材料服务，再提交幂等任务，只传窗口ID/哈希/版本，不传原文或模型结论。

当前是任务提交编排，尚无多模型 LLM 节点。多模型分工在 [生产架构](../docs/ARCHITECTURE.md) 中给出后续落地步骤。
