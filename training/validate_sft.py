"""Offline, read-only SFT preflight; never starts training or downloads files.

Use the pinned, installed LLaMA-Factory environment. Reports contain line
numbers and fixed error codes only, never messages, evidence, or metadata.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LF_REVISION = "ce9dc9e072f80fa3abe0989d4ab90da25f083438"
PROMPT_CAP, OUTPUT_CAP, TOTAL_CAP = 1536, 512, 2048
MESSAGE_FIELDS = {"message_id", "author_key", "reply_to", "timestamp", "relative_time_seconds", "text"}


class Rejected(ValueError):
    """A fixed, non-sensitive rejection code."""


def pairs_unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise Rejected("duplicate_json_key")
        value[key] = item
    return value


def read_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=pairs_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(Rejected("nonfinite_json")))
    except (ValueError, TypeError) as exc:
        if isinstance(exc, Rejected):
            raise
        raise Rejected("invalid_json") from None


def reject_synthetic(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"synthetic", "is_synthetic", "not_human_gold"} and item not in (False, None, 0, "false", "False"):
                raise Rejected("synthetic_or_not_human_gold")
            if key.lower() == "source_type" and isinstance(item, str) and "synthetic" in item.lower():
                raise Rejected("synthetic_or_not_human_gold")
            reject_synthetic(item)
    elif isinstance(value, list):
        for item in value:
            reject_synthetic(item)


def validate_shape(row, system_prompt, parse_model_observation):
    if not isinstance(row, dict):
        raise Rejected("row_not_object")
    reject_synthetic(row)
    # Unknown top-level fields are provenance metadata. They remain in the
    # original file; this validator never rewrites or inserts them in a prompt.
    turns = row.get("messages")
    if not isinstance(turns, list) or len(turns) != 3:
        raise Rejected("single_turn_required")
    for turn, role in zip(turns, ("system", "user", "assistant")):
        if not isinstance(turn, dict) or set(turn) != {"role", "content"} or turn.get("role") != role:
            raise Rejected("invalid_role_or_fields")
        if not isinstance(turn["content"], str) or not turn["content"]:
            raise Rejected("empty_content")
    if turns[0]["content"] != system_prompt:
        raise Rejected("system_prompt_mismatch")
    window = read_json(turns[1]["content"])
    if not isinstance(window, dict) or set(window) != {"messages", "focus_message_id"}:
        raise Rejected("invalid_window_fields")
    messages = window["messages"]
    if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
        raise Rejected("invalid_window_messages")
    ids = set()
    for message in messages:
        if not isinstance(message, dict) or set(message) != MESSAGE_FIELDS:
            raise Rejected("invalid_message_fields")
        identifier = message["message_id"]
        if not isinstance(identifier, str) or not identifier.strip() or identifier in ids:
            raise Rejected("invalid_message_id")
        ids.add(identifier)
        if not isinstance(message["text"], str) or not message["text"]:
            raise Rejected("invalid_message_text")
        for field in ("author_key", "reply_to", "timestamp"):
            if message[field] is not None and not isinstance(message[field], str):
                raise Rejected("invalid_message_metadata")
        relative = message["relative_time_seconds"]
        if relative is not None and type(relative) not in (int, float):
            raise Rejected("invalid_relative_time")
    focus = window["focus_message_id"]
    if focus is not None and (not isinstance(focus, str) or focus not in ids):
        raise Rejected("unknown_focus_message")
    # Check duplicate keys/NaN before calling the real business validator.
    output = read_json(turns[2]["content"])
    try:
        parse_model_observation(turns[2]["content"], messages)
    except Exception:
        raise Rejected("business_output_or_evidence_invalid") from None
    # Match the generation size policy as well as the business schema.
    if (len(output["observations"]) > 8 or len(output["evidence"]) > 4
            or len(output["rationale"]) > 350):
        raise Rejected("generation_size_policy_exceeded")
    for name in ("context_flags", "missing_info"):
        values = output[name]
        if len(values) > 4 or len(set(values)) != len(values) or any(len(item) > 80 for item in values):
            raise Rejected("generation_size_policy_exceeded")
    return turns


def setup(args):
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ[name] = "1"
    lf_root = args.llama_factory_root.resolve(strict=True)
    head = subprocess.run(["git", "-C", str(lf_root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    if head != LF_REVISION:
        raise Rejected("llama_factory_revision_mismatch")
    dirty = subprocess.run(["git", "-C", str(lf_root), "diff", "--quiet", "HEAD", "--", "src/llamafactory"],
                           capture_output=True, timeout=10)
    if dirty.returncode != 0:
        raise Rejected("llama_factory_source_modified")
    import yaml
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if (config.get("template") != "qwen3" or config.get("enable_thinking") is not False
            or config.get("cutoff_len") != TOTAL_CAP or config.get("packing") is not False):
        raise Rejected("unsupported_training_template_or_budget")
    tokenizer_path = args.tokenizer.resolve(strict=True)
    if Path(config["model_name_or_path"]).resolve() != tokenizer_path:
        raise Rejected("tokenizer_model_directory_mismatch")
    # Suppress dependency logs; setup never receives sample text.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        import llamafactory
        from llamafactory.data.template import get_template_and_fix_tokenizer
        from llamafactory.hparams import DataArguments
        from transformers import AutoTokenizer
        if Path(llamafactory.__file__).resolve().parent != lf_root / "src" / "llamafactory":
            raise Rejected("installed_llama_factory_source_mismatch")
        tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_path),
                                                  trust_remote_code=False, local_files_only=True)
        template = get_template_and_fix_tokenizer(
            tokenizer, DataArguments(template="qwen3", enable_thinking=False,
                                     cutoff_len=TOTAL_CAP, packing=False))
    sys.path.insert(0, str(ROOT))
    from backend.service.schema import SYSTEM_PROMPT, parse_model_observation
    return tokenizer, template, SYSTEM_PROMPT, parse_model_observation


def check_tokens(turns, tokenizer, template):
    pair_messages = turns[1:]
    # Actual pinned LF SFT processor uses encode_multiturn, not HF's template.
    source, target = template.encode_oneturn(tokenizer, pair_messages, system=turns[0]["content"], tools=None)
    pairs = template.encode_multiturn(tokenizer, pair_messages, system=turns[0]["content"], tools=None)
    if len(pairs) != 1 or pairs[0] != (source, target):
        raise Rejected("lf_single_multiturn_mismatch")
    target = list(target)
    if template.efficient_eos:
        target.append(tokenizer.eos_token_id)  # Same append as LF SFT processor.
    if tokenizer.eos_token_id is None or tokenizer.eos_token_id not in target[-4:]:
        raise Rejected("target_eos_unverified")
    hf_prompt = tokenizer.apply_chat_template(turns[:2], tokenize=True,
                                              add_generation_prompt=True, enable_thinking=False)
    if list(hf_prompt) != source:
        raise Rejected("hf_lf_prompt_token_ids_mismatch")
    if len(source) > PROMPT_CAP:
        raise Rejected("prompt_budget_exceeded")
    if len(target) > OUTPUT_CAP:
        raise Rejected("output_budget_exceeded")
    if len(source) + len(target) > TOTAL_CAP:
        raise Rejected("total_budget_exceeded")
    return len(source), len(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="Local train/dev JSONL files; no test files.")
    parser.add_argument("--tokenizer", required=True, type=Path, help="Pinned local unquantized base snapshot directory.")
    parser.add_argument("--llama-factory-root", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "training/templates/qwen3-32b-qlora.yaml")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-errors", type=int, default=100)
    args = parser.parse_args()
    if args.max_errors < 1:
        parser.error("--max-errors must be positive")
    summary = {"validator": "sft-preflight-v1", "llama_factory_revision": LF_REVISION,
               "offline": True, "training_performed": False,
               "caps": {"prompt": PROMPT_CAP, "output_including_eos": OUTPUT_CAP, "total": TOTAL_CAP},
               "rows": 0, "valid": 0, "invalid": 0, "max_prompt_tokens": 0,
               "max_output_tokens": 0, "errors": []}
    try:
        tokenizer, template, system_prompt, business_parser = setup(args)
    except Exception as exc:
        summary["setup_error"] = str(exc) if isinstance(exc, Rejected) else "offline_dependency_or_setup_failed"
        print(json.dumps(summary, ensure_ascii=False))
        return 2
    for file_index, path in enumerate(args.files, 1):
        try:
            with path.open("r", encoding="utf-8", newline="") as stream:
                for line_number, raw in enumerate(stream, 1):
                    summary["rows"] += 1
                    try:
                        if not raw.strip():
                            raise Rejected("empty_line")
                        row = read_json(raw)
                        turns = validate_shape(row, system_prompt, business_parser)
                        source_size, target_size = check_tokens(turns, tokenizer, template)
                        summary["valid"] += 1
                        summary["max_prompt_tokens"] = max(summary["max_prompt_tokens"], source_size)
                        summary["max_output_tokens"] = max(summary["max_output_tokens"], target_size)
                    except Exception as exc:
                        summary["invalid"] += 1
                        if len(summary["errors"]) < args.max_errors:
                            summary["errors"].append({"file_index": file_index, "line": line_number,
                                "code": str(exc) if isinstance(exc, Rejected) else "row_validation_failed"})
        except (OSError, UnicodeError):
            summary["invalid"] += 1
            if len(summary["errors"]) < args.max_errors:
                summary["errors"].append({"file_index": file_index, "code": "file_read_or_encoding_failed"})
    if summary["rows"] == 0:
        summary["invalid"] += 1
        summary["errors"].append({"code": "empty_dataset"})
    summary["passed"] = summary["invalid"] == 0
    output = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.report:
        # Do not overwrite a dataset or any existing file.
        with args.report.open("x", encoding="utf-8") as stream:
            stream.write(output + "\n")
    print(output)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
