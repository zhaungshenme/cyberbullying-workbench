"""Render Dify backend URLs without inserting credentials."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = urlsplit(args.backend_url)
    if (base.scheme not in {'http', 'https'} or not base.hostname or base.username or base.password
            or base.query or base.fragment or base.path not in {'', '/'}):
        parser.error('Use a backend origin without credentials, query, fragment or path')
    path = Path(__file__).resolve().parents[1] / 'dify/workflow_submit.yml'
    document = json.loads(path.read_text(encoding='utf-8'))
    for node in document['workflow']['graph']['nodes']:
        data = node['data']
        if data.get('type') == 'http-request':
            endpoint = urlsplit(data['url']).path
            if endpoint not in {'/integration/health', '/integration/analysis-jobs'}:
                raise ValueError('Unexpected workflow endpoint')
            data['url'] = args.backend_url.rstrip('/') + endpoint
    document['app']['name'] = '网暴行为溯源追踪工作台｜任务提交'
    document['app']['description'] = '编排材料引用与幂等任务；语言推理由材料服务调度，结果须经证据校验。'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as target:
        json.dump(document, target, ensure_ascii=False, indent=2)
        target.write('\n')
    print('Rendered workflow; secret environment values remain empty.')


if __name__ == '__main__':
    main()
