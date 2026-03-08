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
    label = 'completed' if success else 'rejected'
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
{json.dumps(politician_info, ensure_ascii=False, indent=2)}

## 審査基準
1. 出典URLが実在しそうか（公式HP・国会議事録・主要報道機関など）
2. 発言内容とカテゴリが一致しているか
3. スコア変更が妥当か（現在のスコアと発言内容の整合性）

## 回答形式（JSON形式で回答してください）
{{
  "approved": true または false,
  "reason": "審査理由（日本語）",
  "x_delta": 現在のX軸スコアへの変更値（-2〜+2、変更不要なら0）,
  "y_delta": 現在のY軸スコアへの変更値（-2〜+2、変更不要なら0）
}}

JSONのみ返してください。説明文は不要です。"""

    message = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=512,
        messages=[{'role': 'user', 'content': prompt}]
    )
    raw = message.content[0].text.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    return json.loads(raw)

def review_fix_with_claude(issue_body, politician_info):
    prompt = f"""あなたは日本の政治情報のデータ管理者です。
以下のデータ修正申請を審査してください。

## 申請内容
{issue_body}

## 対象議員の現在情報
{json.dumps(politician_info, ensure_ascii=False, indent=2)}

## 審査基準
1. 出典URLが実在しそうか（公式プロフィールページなど）
2. 修正内容が妥当か
3. 生年月日の場合：YYYY-MM-DD形式か確認

## 回答形式（JSON形式で回答してください）
{{
  "approved": true または false,
  "reason": "審査理由（日本語）",
  "field": "修正フィールド名（birthdate など）",
  "new_value": "修正後の値（承認時のみ）"
}}

JSONのみ返してください。"""

    message = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=512,
        messages=[{'role': 'user', 'content': prompt}]
    )
    raw = message.content[0].text.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    return json.loads(raw)

def extract_politician_info(block):
    info = {}
    for key in ['id', 'name', 'party', 'house', 'x', 'y', 'age']:
        m = re.search(rf"{key}:'([^']*)'", block) or re.search(rf"{key}:(-?[\d.]+)", block)
        if m:
            info[key] = m.group(1)
    return info

def update_score(content, pid, x_delta, y_delta):
    start, end, block = get_politician_block(content, pid)
    if block is None:
        return content, False

    def clamp(val, mn=-10, mx=10):
        return max(mn, min(mx, val))

    mx = re.search(r"x:(-?[\d.]+)", block)
    my = re.search(r"y:(-?[\d.]+)", block)
    if not mx or not my:
        return content, False

    old_x = float(mx.group(1))
    old_y = float(my.group(1))
    new_x = clamp(round(old_x + x_delta, 1))
    new_y = clamp(round(old_y + y_delta, 1))

    new_block = re.sub(r"x:(-?[\d.]+)", f"x:{new_x}", block)
    new_block = re.sub(r"y:(-?[\d.]+)", f"y:{new_y}", new_block)

    return content[:start] + new_block + content[end:], True

def update_birthdate(content, pid, new_value):
    start, end, block = get_politician_block(content, pid)
    if block is None:
        return content, False

    try:
        birth = datetime.strptime(new_value, '%Y-%m-%d')
        today = datetime.today()
        age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    except Exception:
        return content, False

    new_block = re.sub(r"age:-?\d+", f"age:{age}", block)
    return content[:start] + new_block + content[end:], True

def process_issue(issue, html_content):
    title = issue['title']
    body = issue['body'] or ''
    number = issue['number']

    pid_match = re.search(r'\*\*?ID[:\s]+\*\*?`?(p_[^`\s]+)`?', body)
    if not pid_match:
        close_issue(number, '❌ 自動審査：却下\n\n理由：議員IDが見つかりませんでした。', success=False)
        return html_content

    pid = pid_match.group(1)
    _, _, block = get_politician_block(html_content, pid)
    if block is None:
        close_issue(number, f'❌ 自動審査：却下\n\n理由：ID `{pid}` の議員が見つかりませんでした。', success=False)
        return html_content

    politician_info = extract_politician_info(block)

    try:
        if title.startswith('[EV]'):
            result = review_evidence_with_claude(body, politician_info)
            if result['approved']:
                x_delta = float(result.get('x_delta', 0))
                y_delta = float(result.get('y_delta', 0))
                if x_delta != 0 or y_delta != 0:
                    html_content, updated = update_score(html_content, pid, x_delta, y_delta)
                    score_msg = f'スコア更新: X{x_delta:+.1f} / Y{y_delta:+.1f}' if updated else 'スコア変更なし'
                else:
                    score_msg = 'スコア変更なし'
                close_issue(number,
                    f'✅ 自動審査：承認\n\n理由：{result["reason"]}\n\n{score_msg}',
                    success=True)
            else:
                close_issue(number,
                    f'❌ 自動審査：却下\n\n理由：{result["reason"]}',
                    success=False)

        elif title.startswith('[BIRTH]') or title.startswith('[FIX]'):
            result = review_fix_with_claude(body, politician_info)
            if result['approved']:
                field = result.get('field', '')
                new_value = result.get('new_value', '')
                if field == 'birthdate' and new_value:
                    html_content, updated = update_birthdate(html_content, pid, new_value)
                    msg = f'生年月日を `{new_value}` に更新しました。' if updated else '更新失敗'
                else:
                    msg = 'データ修正を確認しましたが、自動更新対象外です。管理者が手動で対応します。'
                close_issue(number,
                    f'✅ 自動審査：承認\n\n理由：{result["reason"]}\n\n{msg}',
                    success=True)
            else:
                close_issue(number,
                    f'❌ 自動審査：却下\n\n理由：{result["reason"]}',
                    success=False)
        else:
            close_issue(number, '❌ 自動審査：却下\n\n理由：不明なIssueタイプです。', success=False)

    except Exception as e:
        print(f'Issue #{number} 処理エラー: {e}')
        close_issue(number,
            f'⚠️ 自動審査：エラー\n\n理由：処理中にエラーが発生しました。管理者が確認します。\n\n```\n{e}\n```',
            success=False)

    return html_content


def main():
    issues = get_pending_issues()
    if not issues:
        print('処理するIssueはありません。')
        return

    html_content = load_html()
    original = html_content

    for issue in issues:
        print(f'処理中: Issue #{issue["number"]} - {issue["title"]}')
        html_content = process_issue(issue, html_content)

    if html_content != original:
        save_html(html_content)
        print('index.htmlを更新しました。')
    else:
        print('index.htmlの変更はありません。')


if __name__ == '__main__':
    main()
