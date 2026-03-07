#!/usr/bin/env python3
"""
夜間自動更新スクリプト
毎日深夜2時 JSTに GitHub Actions で実行される
pending-review ラベルの Issue を Claude API で審査し、index.html を更新する
"""

import os
import re
import json
import requests
import anthropic
from datetime import datetime

ANTHROPIC_API_KEY = os.environ['ANTHROPIC_API_KEY']
GH_TOKEN          = os.environ['GH_TOKEN']
GH_OWNER          = os.environ['GH_OWNER']
GH_REPO           = os.environ['GH_REPO']
INDEX_HTML        = 'index.html'

GH_HEADERS = {
    'Authorization': f'Bearer {GH_TOKEN}',
    'Accept': 'application/vnd.github.v3+json',
    'Content-Type': 'application/json',
}

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def get_pending_issues():
    url = f'https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/issues'
    params = {'labels': 'pending-review', 'state': 'open', 'per_page': 100}
    r = requests.get(url, headers=GH_HEADERS, params=params)
    r.raise_for_status()
    issues = r.json()
    print(f'未処理Issue: {len(issues)}件')
    return issues

def close_issue(issue_number, comment, success=True):
    requests.post(
        f'https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/issues/{issue_number}/comments',
        headers=GH_HEADERS, json={'body': comment}
    )
    label = 'processed' if success else 'rejected'
    requests.post(
        f'https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/issues/{issue_number}/labels',
        headers=GH_HEADERS, json={'labels': [label]}
    )
    requests.patch(
        f'https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/issues/{issue_number}',
        headers=GH_HEADERS, json={'state': 'closed'}
    )

def load_html():
    with open(INDEX_HTML, 'r', encoding='utf-8') as f:
        return f.read()

def save_html(content):
    with open(INDEX_HTML, 'w', encoding='utf-8') as f:
        f.write(content)

def get_politician_block(content, pid):
    id_positions = [(m.group(1), m.start()) for m in re.finditer(r"id:'(p_[^']+)'", content)]
    for i, (p_id, pos) in enumerate(id_positions):
        if p_id == pid:
            end = id_positions[i+1][1] if i+1 < len(id_positions) else len(content)
            return pos, end, content[pos:end]
    return None, None, None

def review_evidence_with_claude(issue_body, politician_info):
    prompt = f"""あなたは日本の政治情報の審査員です。
以下のエビデンス投稿を審査し、信頼性を評価してください。

## 投稿内容
{issue_body}

## 対象議員の現在情報
{json.dumps(politician_info, ensure_ascii=False, inde
