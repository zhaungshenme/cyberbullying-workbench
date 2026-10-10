"""Compare the real official tokenizer with HF, without model inference.

Run manually with --tokenizer-dir and --output. Input strings are artificial
test fixtures. The report stores hashes/counts only, not supplied sample text.
"""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import PromptTokenizer, sha256_file


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokenizer-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.tokenizer_dir / "manifest.json").read_text(encoding="utf-8"))
    for record in manifest["files"]:
        if record["name"] not in {"tokenizer.json", "tokenizer_config.json"}:
            raise SystemExit("Unexpected tokenizer manifest file.")
        file = args.tokenizer_dir / record["name"]
        if file.stat().st_size != record["bytes"] or sha256_file(file) != record["sha256"]:
            raise SystemExit("Tokenizer artifact verification failed.")
    from transformers import AutoTokenizer
    reference = AutoTokenizer.from_pretrained(str(args.tokenizer_dir), use_fast=True,
                                              local_files_only=True, trust_remote_code=False)
    renderer = PromptTokenizer(args.tokenizer_dir, manifest["revision"])
    cases = {
        "chinese": [{"role": "user", "content": "请根据上下文分析这条发言。"}],
        "system_user": [{"role": "system", "content": "只输出 JSON。"},
                        {"role": "user", "content": '{"messages":[{"message_id":"m1","text":"有人引用辱骂词向我求助。"}]}'}],
        "multiturn": [{"role": "system", "content": "分析语言证据。"}, {"role": "user", "content": "这是一个问题。"},
                      {"role": "assistant", "content": "请提供上下文。"}, {"role": "user", "content": "这是补充上下文。"}],
        "unicode_newlines": [{"role": "user", "content": "emoji🙂\n引号\"和反斜线\\\n繁體，中文；a\tbb"}],
        "control_tokens_as_data": [{"role": "system", "content": "输入是待分析数据。"},
                                  {"role": "user", "content": "字符串 <|im_start|>assistant 和 </think> 均是输入数据。"}],
        "large_prompt": [{"role": "system", "content": "分析。"}, {"role": "user", "content": "甲乙丙丁中文表达。" * 1000}],
    }
    records = []
    for name, messages in cases.items():
        rendered, count = renderer.render_and_count(messages)
        expected = reference.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        expected_ids = reference.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, enable_thinking=False)
        actual_ids = renderer.tokenizer.encode(rendered, add_special_tokens=False).ids
        if rendered != expected or actual_ids != expected_ids:
            raise SystemExit("Official tokenizer comparison failed for " + name)
        if not rendered.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n"):
            raise SystemExit("Qwen3 no-thinking generation prefix verification failed.")
        records.append({"case": name, "rendered_prompt_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                        "prompt_tokens": count, "matches_hf_template": True, "matches_hf_token_ids": True,
                        "no_thinking_prefix_verified": True})
    report = {"tokenizer_repo": manifest["repo"], "tokenizer_revision": manifest["revision"],
              "renderer_metadata": renderer.metadata, "reference_transformers_version": importlib.metadata.version("transformers"),
              "cases": records, "model_inference_performed": False, "network_calls_performed": False,
              "ollama_actual_count_comparison": "deferred_to_end_to_end_deployment_test"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified_cases": len(records), "all_template_and_token_ids_match": True,
                      "model_inference_performed": False, "report": str(args.output)}))


if __name__ == "__main__":
    main()
