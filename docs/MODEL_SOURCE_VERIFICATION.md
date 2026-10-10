# 模型与训练来源核验摘要

核验日期：2026-10-10。详细证据清单见 [MODEL_SOURCE_EVIDENCE.json](sources/MODEL_SOURCE_EVIDENCE.json)，每个条目记录发布方URL、实际读取URL、是否发布方端点、证据文件SHA256、字节数和核验范围。

| 核验对象 | 实际取得证据 | 可以确认的内容 |
| --- | --- | --- |
| Qwen3-32B/235B Instruct-2507 | QwenLM/Qwen3 官方README；HF镜像模型卡与元数据 | 官方型号身份、用途/架构描述；镜像卡载明Apache-2.0；固定HF revision须下载服务器再向官方端点复核 |
| Qwen3.8-27B | QwenLM/Qwen3.5 官方README；vLLM官方recipe；HF镜像模型卡 | 官方README载明2026-08-14发布；vLLM有部署方案；镜像卡载明Apache-2.0及27B视觉语言架构。没有验证本项目训练/模型效果 |
| Qwen3.8-2.4T-A95B | 同一官方README链接；HF镜像卡及固定revision的LICENSE | 2.4T总参数/95B激活描述；使用自定义Qwen3.8-Max许可，不能归为Apache-2.0。不是默认部署模型 |
| LLaMA-Factory | 官方GitHub README、训练/导出样例、数据格式说明；固定commit字段定义及pyproject | 固定commit `ce9dc9e072f80fa3abe0989d4ab90da25f083438` 可用于Qwen3模板；独立Instruct样例的`qwen3_nothink`、双模式32B的`qwen3 + enable_thinking:false`、真实encode接口/SFT EOS处理、LoRA及NF4参数来自官方定义；Python≥3.11；此commit没有旧教程optional extras |
| vLLM | 官方GitHub release；稳定结构化输出文档 | 观察到v0.31.0发布于2026-10-05；当前应使用`structured_outputs`，不能继续抄已移除的`guided_json` |
| 项目当前接口 | 本机实际gateway.py/service.worker/schema/settings/DSL | 当前固定Qwen3-4B、Ollama、受限infer协议；公开副本的网络地址已可配置；Dify只提交任务引用。32B/vLLM及多模型分工需要后续适配，不能声称现已兼容 |
| 格式模板 | 自行编写ShareGPT示例、dataset_info和训练配置 | 两条示例已通过项目真正的schema与逐字证据检查；仅为合成格式示例，不是真实来源/人工金标，未登记为训练集 |

研究期间`huggingface.co`官方API直连超时，转而读取`hf-mirror.com`。镜像证据与官方端点证据在清单中明确区分；没有下载模型权重、训练、微调、启动Ollama、虚拟机或容器，也没有测量识别准确率、吞吐或显存。

训练yaml按已核验官方字段编写，三份yaml已通过本机现有PyYAML 6.0.3解析，JSON模板成功解析；没有将训练配置加载进LLaMA-Factory运行。新增离线预检脚本调用固定LF真实encode接口，分别核验1536/512/2048预算并逐token比较HF无思考prompt；脚本尚未在LF真实环境执行。正式执行须在指定服务器环境完成配置解析、20步流程试跑、依赖锁定、硬件容量与独立数据评测。文档中的算力表是按参数量/字节数明确假设的估算，不能作为测量结果。

核验清单中的原始抓取文件由维护者留档，本仓库发布其URL、核验范围与内容哈希；不嵌入大型HTML抓取页或任何模型权重。
