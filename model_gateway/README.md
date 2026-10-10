# 参考模型网关

本目录提供当前 Qwen3-4B/Ollama 参考适配器。业务服务器通过 TLS 的 `POST /infer` 请求推理；Ollama 仍只监听推理服务器的 `127.0.0.1:11434`。网关不会下载模型、训练权重或启动 Ollama。

先在独立 Python 环境安装 `pip install -r requirements.txt`，通过进程环境或服务管理器设置 [.env.example](.env.example) 中的变量，再执行 `python gateway.py`。该程序不自动读取 `.env`。确保服务器已安装并核验所需模型与 tokenizer，导入 Ollama 固定别名 `cb-qwen3-4b`，取得其 manifest digest，准备可信 TLS 证书和至少32字符的独立随机令牌。

| 网络变量 | 行为 |
|---|---|
| `CB_GATEWAY_BIND_ADDRESS` | 明确的本机 IPv4 接口，默认 `192.168.0.1` |
| `CB_GATEWAY_CLIENT_ADDRESS` | 唯一受信业务服务器的实际 IPv4 来源，默认 `192.168.0.129` |
| `CB_GATEWAY_PORT` | 1..65535，默认8444 |

默认地址用于兼容早期局域网联调，部署者应按服务器网络修改。禁止未指定地址 `0.0.0.0`、组播、受限广播 `255.255.255.255`、IPv6和域名；监听接口必须存在于推理服务器。配置在启动时验证并冻结，环境改变需要重启。`/infer` 仅允许受信地址；`/health` 还允许 loopback。来源依据 TCP 连接地址，不读取 `Forwarded` 或 `X-Forwarded-For`；若有 NAT，配置实际可见来源并用防火墙保持业务服务器隔离，勿在会让多客户端共享来源的通用代理后部署。

业务服务器同时配置 `CB_MODEL_GATEWAY_URL=https://<推理接口或证书域名>:8444`、相同的 `CB_MODEL_GATEWAY_TOKEN` 和可信 `CB_MODEL_GATEWAY_CA`。服务端证书 SAN 必须匹配连接目标，客户端不能禁用证书验证。客户端只访问工作台入口，推理端口不向客户机开放。

网关固定模型名、GGUF SHA256、官方 tokenizer revision/文件SHA256和输出 Schema，模型文件与Ollama manifest身份校验同时进行。参考 GGUF来源未固定仓库commit，仅锁定文件SHA256；不得据此声明其来源版本已完整复现。需要具备匹配且依法取得的参考文件才可启动；部署生产模型应另建有官方来源及版本台账的适配器。

当前限制为4096总上下文token、最多512输出token、每次请求最多64KiB、单并发。完整渲染prompt提前计数，并再次验证运行时实际计数；截断、错误、证据不匹配等继续由业务层校验并记录。成功推理响应不代表中文识别准确率。

修改网络地址不会更换模型。大模型、微调后的权重、多模型协作和 vLLM 的 OpenAI 接口需要独立适配、重新固定模型/tokenizer/模板身份及验收，当前目录未实现这些能力。现有权重未在本项目中微调。

运行 `python -m unittest discover -s tests -v` 验证来源控制、配置拒绝、模型身份、Schema、预算、并发和输出元数据。测试使用假的 Ollama；部分已有HTTP传输测试仅在临时loopback端口运行测试服务器，不连接真实模型。官方 tokenizer 对比脚本需另外依法取得模型库并按其说明运行。
