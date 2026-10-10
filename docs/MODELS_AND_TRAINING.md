# 模型、服务器推理与训练微调落地方案

本项目面向“客户端提交材料、服务器执行工作流、模型提供语言观察、人工形成最终复核”的完整应用架构。目标是建立可验证、可升级、可追溯的研判系统；模型大小和公开排行榜成绩只是选型证据的一部分。正式上线型号应由本项目的独立中文测试集、延迟、吞吐、成本和许可共同决定。

**当前仓库没有完成领域训练或微调，没有独立人工金标检测准确率。** 已有网关针对 Qwen3-4B/Ollama 实现固定身份、结构约束、预算检查和逐字证据校验。以下大模型和训练配置是下一阶段的可实施方案，发布文档不等于已经下载或上线这些权重。

## 1. 型号选择：成熟基线、更新候选、大算力复核

官方资料核验日期：2026-10-10。以下均为可定位到发布方模型仓库的具体型号，不将任何一个型号宣称为所有任务的“全球最佳”。

| 用途 | 发布方 repo_id | 选择理由和实施位置 | 许可与注意事项 |
| --- | --- | --- | --- |
| 当前轻量联调 | `Qwen/Qwen3-4B` | 已有网关固定此型号和 tokenizer，适合接口联调与结构约束验证 | Apache-2.0；现有 GGUF 文件只有固定 SHA256，不能伪称已固定其来源仓库 commit |
| 第一阶段可微调主力基线 | `Qwen/Qwen3-32B` | 中文能力、成熟训练支持与部署规模之间的基线；先测原模型，再用领域 LoRA/QLoRA 对比 | Apache-2.0；部署/训练采用同一 tokenizer、模板和无思考模式 |
| 较新候选的并行评测 | `Qwen/Qwen3.8-27B` | 官方当前发布的更新型号；包含视觉能力，初期只测文本输入。应与 32B 在相同金标上比较 | Apache-2.0；vLLM 有官方 recipe。其训练模板与本方案固定 LLaMA-Factory commit 的兼容性尚未验证，不能直接套用32B训练模板 |
| 大算力复核/离线标注辅助 | `Qwen/Qwen3-235B-A22B-Instruct-2507` | 对复杂语境、含蓄攻击、动员关系、模型分歧进行二次语言观察；Instruct 版本避免把长思考内容带入业务响应 | Apache-2.0；235B 是需要驻留的总参数，A22B 是每 token 激活参数，不能按 22B 采购显存 |
| 极大集群的研究候选 | `Qwen/Qwen3.8-2.4T-A95B` | 官方模型卡可核验的 2.4T 总参数候选。仅在多节点资源、许可和工程预算充分时评估 | 自定义 Qwen3.8-Max 许可，不能写成 Apache-2.0；具有特定规模和商业服务的条件，必须审查原文 |

推荐路线是：固定 32B 基线 → 对比 3.8-27B → 用 235B 提供复杂样本复核 → 用独立结果决定正式主力；新模型取得更好结果后替换主力，而不是因为版本号较新就自动上线。2.4T 是远期集群评估路线，既不是本仓库默认依赖，也不构成当前部署要求。

官方链接：

- [Qwen3 官方仓库](https://github.com/QwenLM/Qwen3)，[Qwen3-32B 模型卡](https://huggingface.co/Qwen/Qwen3-32B)，[235B Instruct 模型卡](https://huggingface.co/Qwen/Qwen3-235B-A22B-Instruct-2507)。
- [Qwen3.5/后续系列官方仓库](https://github.com/QwenLM/Qwen3.5)，[Qwen3.8-27B 模型卡](https://huggingface.co/Qwen/Qwen3.8-27B)，[2.4T 模型卡](https://huggingface.co/Qwen/Qwen3.8-2.4T-A95B)，[2.4T 原始许可](https://huggingface.co/Qwen/Qwen3.8-2.4T-A95B/blob/main/LICENSE)。
- [Qwen3.8-27B vLLM 部署 recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)。

## 2. 获取路径与来源锁定

权重从发布方 Hugging Face `Qwen` 组织获取；国内网络可使用发布方 [ModelScope 组织](https://modelscope.cn/organization/qwen)。正式环境不要使用身份不明的网盘权重，也不要把镜像站视为原始发布者。HF 和 ModelScope 的 revision 命名并不保证相同，不能把 HF commit 直接当成 ModelScope revision。

下表记录研究时从 Hugging Face 镜像元数据读到的具体 revision，官方 Qwen GitHub 的链接确认了型号身份。**本次研究环境直连 `huggingface.co` 超时；下列 SHA 尚需在下载服务器通过发布方端点复核，不表述为本次已完成官方权重下载或文件哈希核验。**

| repo_id | 镜像元数据观察到的 revision |
| --- | --- |
| `Qwen/Qwen3-32B` | `9216db5781bf21249d130ec9da846c4624c16137` |
| `Qwen/Qwen3-235B-A22B-Instruct-2507` | `ac9c66cc9b46af7306746a9250f23d47083d689e` |
| `Qwen/Qwen3.8-27B` | `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` |
| `Qwen/Qwen3.8-2.4T-A95B` | `207bd685a7e3696cfaff12ded7c6a7ea0f88c996` |

在可访问官方端点的下载服务器执行以下代码。它核验固定 revision，而不是悄悄改用 `main`；下载后对模型、tokenizer、配置与许可文件生成哈希清单。`huggingface_hub` 安装在专用模型下载环境，版本写入依赖锁和清单。

```bash
# 新服务器先由管理员分配受控目录，已有目录核实属主后复用。
sudo install -d -m 750 -o "$(id -un)" -g "$(id -gn)" /srv/venvs /srv/models
python3.11 -m venv /srv/venvs/model-download
source /srv/venvs/model-download/bin/activate
python -m pip install huggingface_hub
mkdir -p /srv/models
```

```python
# 保存为 /srv/models/download_model.py 后执行 python /srv/models/download_model.py
import hashlib
import json
from pathlib import Path
from importlib.metadata import version
from huggingface_hub import HfApi, snapshot_download

repo_id = "Qwen/Qwen3-32B"
revision = "9216db5781bf21249d130ec9da846c4624c16137"
target = Path("/srv/models") / (repo_id.split("/")[-1] + "-" + revision)
if target.exists():
    raise RuntimeError("use a fresh version directory; never mix snapshots")
api = HfApi(endpoint="https://huggingface.co")
info = api.model_info(repo_id=repo_id, revision=revision)
if info.sha != revision:
    raise RuntimeError("model revision identity mismatch")
snapshot_download(repo_id=repo_id, revision=revision, local_dir=target,
                  endpoint="https://huggingface.co")
files = {}
for path in sorted(target.rglob("*")):
    if not path.is_file() or ".cache" in path.parts or path.name == "SOURCE_MANIFEST.json":
        continue
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    files[path.relative_to(target).as_posix()] = {
        "bytes": path.stat().st_size, "sha256": digest.hexdigest()
    }
manifest = {"repo_id": repo_id, "revision": info.sha,
            "endpoint": "https://huggingface.co",
            "huggingface_hub_version": version("huggingface_hub"), "files": files}
(target / "SOURCE_MANIFEST.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
```

下载使用全新 revision 目录。完成哈希与许可审核后，在尚不存在批准模型路径的新服务器创建只指向此次快照的链接：

```bash
ln -s /srv/models/Qwen3-32B-9216db5781bf21249d130ec9da846c4624c16137 /srv/models/Qwen3-32B
```

若批准路径已存在，先核查当前版本并走受控发布切换，不覆盖旧快照。后文推理/训练配置使用 `/srv/models/Qwen3-32B` 这一批准路径。更换型号时同时修改 repo_id、对应 revision、目标目录，并重新核验模型卡和许可。完成后归档完整快照、下载日志、依赖锁和清单。服务器运行时引用本地目录，避免每次启动下载浮动版本。官方 SHA256 清单如有发布也应比对；本地计算哈希证明的是文件一致性，不独立证明来源真实性。

镜像可作为网络加速手段，仍须用可信发布方元数据或组织批准的内部模型制品库验证来源、revision 和文件哈希。不能因为文件名相同就建立可信模型身份。

## 3. 算力规划与服务器划分

下面是采购/试跑起点，不是实测容量或性能承诺。采用短文本窗口、模型 context 上限 8192、当前主应用总预算为 2048（提示1536、输出512），扩大业务窗口前须同步验收、初始并发 1—4。BF16 理论权重 `参数量×2字节`，4-bit 理论权重 `参数量×0.5字节`；均未计量化元数据、非量化层、KV cache、框架开销、通信缓存和激活值。表内 GB 为十进制粗估，GPU 标称容量与实际可用空间还需实测。

| 阶段 | 理论权重下限 | 建议试跑资源 | 具体用途 |
| --- | --- | --- | --- |
| 32B BF16 推理 | 约 64GB | 2×80GB GPU；互联良好；主机内存至少 256GB | 给 KV、并发和故障重启留余量；单 80GB 是否足够需压测，不能直接承诺 |
| 32B 4-bit 推理 | 约 16GB，实际更大 | 1×48GB 或 1×80GB GPU | 必须单独测量化后的细粒度中文观察和证据表现；不能把 BF16 的评测结果沿用 |
| 3.8-27B BF16 文本推理 | 约 54GB，加视觉/运行时开销 | 1×80GB 起步，或 2×80GB | 按官方 recipe 确认具体硬件/版本；不启用视觉输入不等于所有视觉权重必然不加载 |
| 235B BF16 推理 | 约 470GB | 8×80GB 或 8×141GB GPU；建议 NVLink/NVSwitch；主机内存约 1TB 起步 | 8×80GB 的长窗口/高并发空间仍有限；初期仅低并发复杂复核 |
| 235B 量化推理 | 4-bit 下限约 117.5GB | 4×80GB 起步 | 此下限不代表已有受支持的量化 checkpoint；需要制备/核验量化格式、MoE kernel 和再评测 |
| 32B QLoRA | 4-bit 底座约 16GB，加训练激活和适配器 | 单 80GB GPU 起步；2048序列、batch 1、梯度累积 | 适合第一轮领域 SFT；48GB 可另行容量试验 |
| 32B BF16 LoRA | 64GB底座，加训练激活 | 2×80GB 配 ZeRO-3 起步，4×80GB 更从容 | 与 QLoRA 对照，检查量化训练是否影响关键语境 |
| 32B 全参 | Adam 常见状态粗估约 16字节/参数，即512GB，另加激活等 | 8×141GB 或16×80GB 起步；ZeRO-3/FSDP | 上述16字节假设为BF16权重/梯度、FP32主权重/Adam双状态，优化器策略不同会改变估算 |
| 2.4T BF16 集群推理 | 约4.8TB权重，另加服务开销 | 多节点、多TB聚合显存，专门并行设计 | A95B 不能把总权重缩成95B；需要发布方recipe、网络和容量专项评审 |

推荐分离业务节点、GPU推理节点、训练节点和只读制品库。前端、FastAPI、Dify、数据存储、审计及模型服务全部位于受控服务器侧。客户机仅需浏览器和授权账户，不承担模型存储或训练。HTTP入口由反向代理提供HTTPS；GPU接口置于内网，不直接供浏览器访问。

当前 SQLite 和单 worker 是已有实现。多节点高可用、数据库迁移、推理池扩容和自动故障转移应列为下一阶段工程任务，不能因为架构图包含这些模块就写成仓库已具备。

## 4. vLLM 推理服务的可实施路径

官方 vLLM GitHub 在核验日的最新 release 为 [v0.31.0](https://github.com/vllm-project/vllm/releases/tag/v0.31.0)，发布日期为2026-10-05。此处以固定 `vllm==0.31.0` 作服务器试跑版本；安装前按照 [官方安装说明](https://docs.vllm.ai/en/stable/getting_started/installation/gpu.html) 核验 CUDA、驱动和GPU支持。训练和推理使用不同虚拟环境，避免框架依赖互相覆盖。

```bash
python3.11 -m venv /srv/venvs/cb-infer
source /srv/venvs/cb-infer/bin/activate
python -m pip install 'vllm==0.31.0'
python -m pip freeze > /srv/venvs/cb-infer/requirements.lock.txt

CUDA_VISIBLE_DEVICES=0,1 vllm serve /srv/models/Qwen3-32B \
  --served-model-name cb-qwen3-32b \
  --host 127.0.0.1 --port 8000 \
  --tensor-parallel-size 2 --dtype bfloat16 \
  --max-model-len 8192 --gpu-memory-utilization 0.90 \
  --max-num-seqs 4 --api-key "$CB_VLLM_API_KEY"
```

`CB_VLLM_API_KEY` 从服务器秘密管理注入，不提交 Git，不写入客户端。上例绑定本机回环，适配网关与 vLLM 同节点运行；业务服务器经受限TLS网关跨节点访问。若使用独立内网推理地址，应同时完成防火墙、TLS和调用身份配置。

235B 示例在专用8卡节点将本地模型目录换为 `/srv/models/Qwen3-235B-A22B-Instruct-2507`、服务名换为 `cb-qwen3-235b-review`、`--tensor-parallel-size 8`，初始 `--max-num-seqs 1`。这是低并发容量起点。专家并行、分布式后端和性能优化须依据该版本官方 MoE 文档实测决定。

32B 的请求需关闭思考模式，并在生成端传入业务 JSON Schema。vLLM 官方当前使用 `structured_outputs`；旧的 `guided_json` 字段在v0.12.0已移除。HTTP探针示意如下，只用于服务器接口测试：

```python
import os
import sys
import httpx
sys.path.insert(0, "/opt/workbench/backend")
from service.schema import SYSTEM_PROMPT, MODEL_OUTPUT_SCHEMA

window_json = '{"messages":[{"message_id":"m1","author_key":null,"reply_to":null,"timestamp":null,"relative_time_seconds":null,"text":"请尽快处理退款。"}],"focus_message_id":"m1"}'
response = httpx.post("http://127.0.0.1:8000/v1/chat/completions", timeout=180,
    headers={"Authorization": "Bearer " + os.environ["CB_VLLM_API_KEY"]},
    json={"model": "cb-qwen3-32b",
          "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                       {"role": "user", "content": window_json}],
          "temperature": 0, "max_tokens": 512,
          "chat_template_kwargs": {"enable_thinking": False},
          "structured_outputs": {"json": MODEL_OUTPUT_SCHEMA}})
response.raise_for_status()
print(response.json())
```

生产网关必须派生实际消息ID/作者白名单并核验输出，探针里的基础 Schema 不能替代这些限制。`temperature:0` 为可比较的工程基线；不同模型官方推荐采样策略可能不同，需保存策略并用独立测试比较，不能假定零温度必然最准确。

**当前网关不能直接代理 vLLM。** 现有 `gateway.py` 固定 Qwen3-4B 的名称、GGUF SHA、tokenizer SHA、来源地址白名单（现可配置）、模板、预算、并发和 Ollama `/api/generate` 协议。改一个环境变量不会解除这些有意设置的身份约束。后续需新增独立 `vllm_adapter`，在上线前完成：

1. 固定模型repo/revision/所有权重分片哈希、tokenizer与模板哈希；将允许的模型版本登记到服务配置。
2. 保留 `/infer` 输入契约；准确计数实际渲染后的完整prompt，禁止静默截断；确认 JSON Schema 是否作为提示词影响token计数。
3. 将 vLLM `usage.prompt_tokens/completion_tokens`、`finish_reason` 和 `choices[].message.content` 转为现有 worker 需要的 `prompt_eval_count`、`eval_count`、`preflight_prompt_tokens`、`done_reason`、`message.content` 等字段；只接受已完整完成的结果。
4. 拒绝型号身份变化、越界预算、不合法枚举、无逐字证据、未知消息ID、未知作者和虚构结论；把错误明确交人工复核。
5. 使用服务token、TLS、来源限制、并发/速率限制；浏览器不持有模型密钥。
6. 按同一批原始窗口跑基线/新适配器验收，保留窗口哈希、pipeline版本、模型标识和运行参数，再启用生产调度。

[vLLM结构化输出官方文档](https://docs.vllm.ai/en/stable/features/structured_outputs/)。Schema约束提升的是结构一致性，不能证明语义正确或法律成立。

Ollama用于轻量联调或单机开发；其 GGUF 文件和 manifest digest、量化类型、模板与实际token计数也须记录。vLLM服务端通常使用原生 Hugging Face safetensors或经核验的量化格式。不能将同名 GGUF 与 BF16 safetensors 视作完全相同的测试对象。

## 5. Dify与多模型分工

现有 Dify DSL 是材料引用、哈希、版本和幂等任务的提交工作流。案件原文留在业务服务；已有DSL没有完成多个LLM节点的分工或模型融合。下一阶段优先由受控服务实现分工，Dify负责流程编排和状态，不把秘密、完整案件原文或身份数据写入工作流导出。

建议按以下路径实现并保留每个节点的原结果：

1. **确定性预处理**：校验UTF-8/结构/时间关系，构建原文窗口；词表仅作召回提示，不能直接认定攻击。
2. **主观察模型**：32B或评测胜出的27B读取完整所需语境，输出标签、逐字证据、缺失信息。
3. **独立复核模型**：235B先独立读原始窗口，之后才比较主观察结果；避免第一份答案锚定第二份答案。硬规则无法核验身份或同意时保留未知。
4. **确定性一致性校验**：重新定位证据，检查ID、作者、预算、Schema、引用/转述、攻击标签与decision之间的一致性。
5. **分歧/上下文补充节点**：分歧、证据失配、模糊对象、多人语境、时序缺失和隐私披露模式进入人工复核；追加上下文须标记来源。
6. **人工复核与审计**：研判员/复核员确认语言观察，保留模型版本、原结果和改判原因；法律事实、违法犯罪、身份归属和执法决定由有权限人员结合其他证据判断。

两个或三个模型同意不等于独立正确：同家族训练数据和提示词可能产生相关错误。可以再加入经许可的其他模型家族作挑战集比较，但不得把“多数票”当作金标或司法结论。高风险类别应要求有效证据和人工复核，不能使用模型自报“置信度”直接自动处置人员。

首版可对经过授权的样本双模型离线对照，取得统计证据后再决定在线复核触发条件；若只有单模型且无已验证不确定性估计，仅按其语气选择跳过复核并不可靠。在线路由可用结构失败、上下文不足、类别、客观分歧和经验证的校准评分，保存策略版本。随机抽查低风险结果以测量漏检，不能只检查“已命中”样本。

## 6. 训练数据准备：先定义任务，再收集、标注和划分

细粒度任务沿用当前仓库：`supported / not_supported / insufficient_context` 三种语言观察决定；攻击类、语境类标签；`evidence.message_id / quote / occurrence`；实际输入作者键或null；理由与缺失信息。领域语料标签不能直接替换成法律结论。

计划从约5万条训练候选中形成约3万条可用训练窗口、5000条开发窗口、1万条冻结测试窗口，规模是采集标注目标，不是当前仓库库存。每个关键稀有类别、引用/求助/消费者投诉/否定/反讽/方言/谐音/缺上下文等切片均应有独立样本；不能用总体accuracy掩盖严重误报。必要时先用较小但双人核验的数据验证流程，再扩库。

数据必须具备原发布/获取URL、数据集版本/commit、原文件SHA、原始样本ID、取得时间、许可或使用依据、脱敏方式、上下文是否完整、标注人/版本和裁决记录。具体来源及许可见 [数据来源台账](DATASETS.md)。公开论文的“辱骂/仇恨”标签只能作为起点，不能自动证明符合本项目的网暴行为定义。

以事件/帖子/线程/作者等关联组划分，并增加时间外测试；近重复、改写、同一次攻击事件不能跨train/dev/test。通常先固定组划分，再生成窗口。利用全部语料做近重复分析可以防泄漏，但不能把测试标签、答案或测试样本提示传给训练模型。冻结测试集独立管理，训练者不得用测试结果反复调参。

建议两名独立标注员先盲标，分歧由第三名有领域能力者裁决；标注指南明确“批评与辱骂”“引用与实施”“求助与动员”“多人同向与明确协同”的界限。记录一致性、裁决比例及难例。现有单人复核结果仍是单人复核，未建成上述独立金标流程前不能改称human gold。

**敏感命中必须包含对应非攻击语境**：投诉、举报、法律教育、受害者复述、医学术语、文学对白、新闻转述、保护性否定等。保留真实来源的上下文和脱敏对应表，不把姓名、电话、住址和账号的泄露扩散到公开Git仓库。

多模型可以筛选和建议标签，实际金标由人工流程确定；合成谐音/替换/对抗样本需明确标记，在分布内真实测试之外单独报告，不能充作独立真实测试样本。

## 7. 可复制的LLaMA-Factory配置

采用 [LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) 官方框架，Apache-2.0。研究核验的固定commit为 `ce9dc9e072f80fa3abe0989d4ab90da25f083438`。该commit的官方独立Instruct-2507训练样例使用 `qwen3_nothink`；双模式的Qwen3-32B在本项目改用 `qwen3` 与显式 `enable_thinking: false`，由ReasoningTemplate把空思考前缀纳入prompt，匹配官方HF无思考推理。已逐项核验固定源码的模板注册、encode接口与SFT处理器EOS行为；字段定义确认了bitsandbytes、4-bit、nf4与double_quantization参数。该commit要求Python≥3.11；框架、CUDA/PyTorch、bitsandbytes和DeepSpeed还需在目标GPU完成一次安装与训练试跑后锁定完整版本。

安装在训练服务器，仓库假设克隆到 `/opt/workbench`。先按 [PyTorch官方安装页](https://pytorch.org/get-started/locally/) 配置与驱动兼容的CUDA PyTorch构建，再安装训练框架；不能混用CPU torch和CUDA训练。以下命令仅准备环境，不启动训练：

```bash
sudo install -d -m 750 -o "$(id -un)" -g "$(id -gn)" /srv/venvs /srv/data /srv/artifacts
python3.11 -m venv /srv/venvs/cb-train
source /srv/venvs/cb-train/bin/activate
# 管理员允许训练账号在此专用目录检出框架。
sudo install -d -m 750 -o "$(id -un)" -g "$(id -gn)" /srv/llama-factory
git clone https://github.com/hiyouga/LLaMA-Factory.git /srv/llama-factory
git -C /srv/llama-factory checkout ce9dc9e072f80fa3abe0989d4ab90da25f083438
python -m pip install -e /srv/llama-factory
python -m pip install bitsandbytes deepspeed
python -m pip check
python -m pip freeze > /srv/venvs/cb-train/requirements.lock.txt
mkdir -p /srv/data/cb_gold/sft /srv/artifacts
```

该已核验commit的pyproject没有旧教程中的`[torch,metrics]` optional extras，因此这里直接安装项目与必要后端。实际依赖锁、Python版本、CUDA、驱动、GPU型号、源码commit和配置SHA应随训练产物归档；这份方案没有伪造已经验证过的硬件锁文件。

模板位于 `training/templates/`：

- `dataset_info.json`：ShareGPT字段映射；放到 `/srv/data/cb_gold/sft/dataset_info.json`。
- `sharegpt-format-demo.jsonl`：两条**自造格式示例**，均已通过现有后端的输出Schema与逐字证据校验；用于了解格式，未登记为训练集，不是来源真实数据或人工金标。
- `qwen3-32b-qlora.yaml`：单卡4-bit NF4 QLoRA起点。
- `qwen3-32b-lora.yaml`：BF16 LoRA + ZeRO-3起点。
- `ds_z3.json`：模板实际引用的DeepSpeed配置，包含在仓库，不依赖不存在的文件。
- `qwen3-32b-export.yaml`：将所选QLoRA adapter合并到**原始未量化底座**。

真实准备好的文件应为 `/srv/data/cb_gold/sft/cb_train.jsonl`、`cb_dev.jsonl` ；最终 `cb_test.jsonl` 放在训练账号不能访问的独立评测目录。每行ShareGPT消息包含与线上一致的system prompt、JSON窗口user message和裁决后的assistant JSON对象。额外source/provenance列保留在数据台账，不拼进用户提示词；训练文件的作者键均为受控脱敏键。

```bash
cp /opt/workbench/training/templates/dataset_info.json /srv/data/cb_gold/sft/
# 完成真实数据、来源许可、双人标注、分组划分与Schema验证后执行：
cd /opt/workbench
source /srv/venvs/cb-train/bin/activate
python training/validate_sft.py \
  --llama-factory-root /srv/llama-factory \
  --tokenizer /srv/models/Qwen3-32B \
  --report /srv/artifacts/sft-preflight-run-001.json \
  /srv/data/cb_gold/sft/cb_train.jsonl /srv/data/cb_gold/sft/cb_dev.jsonl
# 仅在预检退出码为0且 passed=true 时执行；失败须修正/重构数据后重新验证。
CUDA_VISIBLE_DEVICES=0 llamafactory-cli train training/templates/qwen3-32b-qlora.yaml
```

先复制配置并做独立20步/100窗口的**训练流程试跑**，将 `max_steps:20`、`max_samples:100`、`eval_steps:10`、`save_steps:10` 与独立输出目录写进试跑配置，验证数据加载、loss非NaN、OOM、保存/恢复、证据输出和日志脱敏。这只是流程验收，不能报告为模型训练效果。

完整QLoRA起点：LoRA rank32、alpha64、dropout0.05、全线性层、序列2048、每卡batch1、梯度累积16、学习率5e-5、2epochs、BF16 compute。所有这些是待开发集验证的初始超参数。当前线上还分别限制prompt1536、输出512，训练预检应使用固定LF模板（含特殊token/EOS）分别核验这两项及总长2048；超长样本先按原文关系重构窗口，不能静默截断掉攻击或引用语境。

BF16 LoRA对照：

```bash
FORCE_TORCHRUN=1 CUDA_VISIBLE_DEVICES=0,1 \
  llamafactory-cli train training/templates/qwen3-32b-lora.yaml
```

多卡有效batch是每卡batch×梯度累积×卡数，本模板2卡时为32；与单卡QLoRA比较时应明确记录此差别，或另复制配置把累积改为8以保持有效batch16。不能把不同batch/数据步数的结果当成纯量化差异。

全参方案先复制LoRA模板，改 `finetuning_type: full`、去掉所有`lora_*`与量化参数、使用独立output目录、学习率从1e-5起点搜索，保留ZeRO-3；在足够显存节点运行。全参并不必然优于LoRA，数据少时可能忘记原通用能力。235B先作为未微调复核基线；其MoE全参或适配训练需要单独校验专家参数/并行支持，不直接套用32B dense模板。

第一轮采用监督微调SFT。DPO等偏好训练仅在已有人工确认的同输入chosen/rejected成对语料、可验证的错误理由以及固定测试时开展；不要用一个教师模型自动生成整套“正确”答案后训练另一模型，再用同教师打分宣布达到司法正确率。报告训练loss/验证loss是诊断工具，最终以独立任务指标选择。

## 8. 导出、再部署与模型版本管理

保存base repo/revision、原始快照哈希、adapter/config SHA、训练集manifest、分组划分manifest、标注指南版本、命令、依赖锁、随机种子、训练checkpoint、开发集选择理由、测试报告。QLoRA输出通常是adapter，不能把目录当成一套完整底座权重。

确认用于发布的adapter checkpoint后，修改export模板中的adapter路径，再执行：

```bash
llamafactory-cli export training/templates/qwen3-32b-export.yaml
```

合并加载原始BF16/未量化底座，不对已经4-bit的底座做merge；CPU合并预留至少能容纳32B原始底座及过程缓冲的主机内存。合并后为safetensors、config、tokenizer重新生成哈希清单，并验证adapter挂载模式与合并权重的输出一致性。改用BF16 LoRA时将adapter路径改为对应LoRA产物。

生产优先用vLLM部署合并的safetensors并新建服务身份，例如 `cb-qwen3-32b-sft-v1`。如转换GGUF给Ollama开发环境，按模型当前架构支持情况使用固定commit的 [llama.cpp](https://github.com/ggml-org/llama.cpp) 官方转换工具，再量化；转换/量化是额外模型变体，须生成新hash并重做任务评测。本方案未执行或核验3.8的GGUF转换，不承诺任意新模型可直接导入当前Ollama网关。

上线采用影子运行→有限用户试用→扩量，保留旧模型/adapter/pipeline的回滚能力。历史任务保持原模型结果和版本，新模型只生成追加结果，不覆盖旧证据或复核记录。

## 9. 评价与投入应用的门槛

测试集必须在训练前冻结并独立保存。报告至少包括：类别precision/recall/F1、macro-F1、混淆矩阵、引用/求助/投诉误报率、含蓄/谐音漏检率、`insufficient_context`比例、模型分歧率、证据定位正确率、无效/截断响应率、人工改判率；按来源、时间、方言/文本变形、性相关攻击、隐私披露模式、多人场景等切片给出样本量和置信区间。

效率在**同硬件、同窗口分布、同并发、同量化精度**下报告p50/p95端到端延迟、排队延迟、任务/分钟、每千窗口成本、峰值显存、超时/失败率；预填充与生成token速率不能替代用户等待时间。阈值按真实业务错误成本由项目负责人确认，不能从用户要求“保证正确率”推导出一个未经测量的承诺。

测试还应包含：原文嵌入“忽略规则”的指令、伪造JSON/角色、上下文长到预算边缘、模型版本变化、网络故障、重复任务、失效会话和越权提交。确定性证据/权限检查失败不得转成“安全/无攻击”结论，需保留明确失败状态。

自动辅助提示、人工确认的语言观察与正式法律/执法决定应保持可区分。即使有高质量金标与高指标，模型结果仍须结合事件全貌、身份/来源真实性、取证依据和审查程序使用；仓库当前不提供自动认定违法、自动处罚或真实身份追踪能力。

项目将持续更新：完善独立金标和数据台账、vLLM适配器、多模型分工、来源关系与事件时间线、受控模型训练和版本管理。每次更新同时发布已实现范围、固定版本和可复查的测试结果。
