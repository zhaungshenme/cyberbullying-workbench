"""Generate server-local secrets for a new offline workbench installation.

Never prints credentials, overwrites configuration or starts any service.
"""
import argparse
import os
from pathlib import Path, PurePosixPath
import secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data-dir', required=True)
    args = parser.parse_args()
    if not PurePosixPath(args.data_dir).is_absolute():
        parser.error('--data-dir must be an absolute Linux path')
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    files = [directory / name for name in ('backend.env', 'frontend.env')]
    if any(path.exists() for path in files):
        parser.error('Configuration already exists; refusing to overwrite')
    data_dir = args.data_dir
    if any(c in data_dir for c in '\n\r"\\'):
        parser.error('Use a Linux absolute data path without quotes, newlines or backslashes')
    backend_token = secrets.token_hex(32)
    service_token = secrets.token_hex(32)
    password = secrets.token_urlsafe(24)
    values = [
        f'WORKBENCH_DATA_DIR="{data_dir}"\nWORKBENCH_BACKEND_TOKEN={backend_token}\n'
        f'CB_SERVICE_TOKEN={service_token}\nWORKBENCH_BOOTSTRAP_ADMIN_PASSWORD={password}\n'
        'DIFY_API_URL=http://127.0.0.1/v1\nDIFY_SUBMIT_KEY=\n'
        'CB_MODEL_GATEWAY_URL=\nCB_MODEL_GATEWAY_TOKEN=\nCB_MODEL_GATEWAY_CA=\n'
        'CB_MODEL_NAME=cb-qwen3-4b\nCB_PIPELINE_VERSION=cb-workbench-ollama-v1.2\n'
        'CB_ENABLE_WORKER=0\nWORKBENCH_ENABLE_DISPATCHER=0\n',
        'WORKBENCH_BACKEND_URL=http://127.0.0.1:18788\n'
        f'WORKBENCH_BACKEND_TOKEN={backend_token}\nWORKBENCH_COOKIE_SECURE=true\n'
        'NEXT_TELEMETRY_DISABLED=1\n',
    ]
    for path, content in zip(files, values):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as target:
            target.write(content)
    print('Created backend.env and frontend.env. Credentials remain in these private files; inference is disabled.')


if __name__ == '__main__':
    main()
