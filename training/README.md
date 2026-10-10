# 训练模板

这些模板是下一阶段服务器训练方案，不代表本项目已有训练成果。详细步骤与固定 LLaMA-Factory 版本见 [模型与训练说明](../docs/MODELS_AND_TRAINING.md)。

1. 按 [数据治理方案](../docs/DATASETS.md) 取得允许该用途的材料，完成独立标注、仲裁与事件/时间隔离。
2. 准备真实的 `cb_train.jsonl` 和 `cb_dev.jsonl`，复制 `templates/dataset_info.json` 到受控训练目录。最终测试由独立评测账号保管，本映射不注册测试集。
3. 运行下方离线预检，校验真实业务输出与逐字引用，并使用固定LF模板计数**完整输入及目标输出**：提示≤1536、输出（含EOS）≤512、总计≤2048。超界样本须重构，不能让 `cutoff_len` 静默截断。
4. 先做独立20步流程试跑，验证框架字段、数据加载、token预算、保存恢复与显存，再按开发集比较 QLoRA / BF16 LoRA。
5. 选定 adapter 后按 export 模板合并到原始未量化底座，登记完整模型来源、文件哈希、数据版本和独立评测。
6. 新模型需新的受限服务身份和适配层验收；不能导入当前固定4B网关后直接使用。

`sharegpt-format-demo.jsonl` 是两条明确标记的**合成格式样例**，没有注册为训练数据，不是独立人工金标。配置语法已解析验证，尚未在 LLaMA-Factory 或 GPU 上运行；实际训练环境需生成自己的完整依赖锁。

## 离线预检

先完成模型说明中的固定LF安装和官方来源的本地模型快照，激活训练环境，再执行：

```bash
cd /opt/workbench
source /srv/venvs/cb-train/bin/activate
python training/validate_sft.py \
  --llama-factory-root /srv/llama-factory \
  --tokenizer /srv/models/Qwen3-32B \
  --report /srv/artifacts/sft-preflight-run-001.json \
  /srv/data/cb_gold/sft/cb_train.jsonl /srv/data/cb_gold/sft/cb_dev.jsonl
```

只有退出码为0、`passed=true`时才进入训练。报告文件使用新的路径，脚本不覆盖已有文件；退出码1表示数据失败，2表示固定源码/配置/本地依赖等准备失败。默认读取QLoRA配置，BF16 LoRA试跑可加 `--config training/templates/qwen3-32b-lora.yaml`。tokenizer目录须与配置中的未量化底座目录一致。

Qwen3-32B是双模式模型，模板采用 `qwen3`、`enable_thinking: false`。固定官方LF源码的 `qwen3_nothink`样例针对独立Instruct-2507底座；直接用于32B会少掉HF无思考prompt的空思考前缀。预检同时使用真实 `get_template_and_fix_tokenizer`、`encode_oneturn`、SFT实际用的 `encode_multiturn`，按SFT处理器规则计EOS，并逐token比较HF无思考prompt。模板/来源版本不匹配则拒绝。

只接受system/user/assistant单轮、与线上一致的system prompt、实际窗口字段，以及合法的模型JSON目标。拒绝重复JSON键、非法引用、未知作者/消息ID、超量生成字段和`synthetic`/`not_human_gold`标记；没有这些标记并不自动证明样本是真实金标，来源和独立标注仍需数据治理审核。未知顶层字段仅作为来源元数据保留在原文件，不拼接到prompt，不写入报告；脚本不改写材料，不下载文件，不加载模型权重，不训练。日志只显示计数、文件序号、错误行和固定错误码。

本次完成了预检脚本语法与固定官方源码API核验；未安装或运行LLaMA-Factory，尚未在其真实环境验证token编码。正式服务器应先执行预检与20步流程试跑，并核验保存后的tokenizer/prompt格式再放行完整训练。
