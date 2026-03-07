#!/usr/bin/env python3
"""
GitHub Issue の内容を解析して index.html の政治家データを更新するスクリプト。
GitHub Actions から呼び出されます。
"""

import os
import re
import json
import sys

ISSUE_TITLE = os.environ.get('ISSUE_TITLE', '')
ISSUE_BODY  = os.environ.get('ISSUE_BODY', '')
ISSUE_NUM   = os.environ.get('ISSUE_NUMBER', '?')

INDEX_FILE  = 'index.html'

def load_html():
    with open(INDEX_FILE, 'r', encoding='utf-8') as f:
        return f.read()

def save_html(content):
    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

# ===== 政治家追加 =====
def process_add(html, body):
    """<!-- ADD_POLITICIAN ... --> ブロックからデータ行を取得して挿入"""
    m = re.search(r'<!-- ADD_POLITICIAN\n(.*?)\n-->', body, re.DOTALL)
    if not m:
        print('[ADD] ADD_POLITICIAN ブロックが見つかりません')
        return html

    data_line = m.group(1).strip()
    print(f'[ADD] 追加データ行: {data_line[:80]}…')

    # id重複チェック
    id_match = re.search(r"id:'([^']+)'", data_line)
    if id_match:
        pid = id_match.group(1)
        if pid in html:
            print(f'[ADD] 警告: ID {pid} は既に存在します。スキップします。')
            return html

    # データ配列の末尾に挿入
    # const DATA=[ ... ]; のパターンを探す（スペースなし版も対応）
    pattern = r'(const DATA\s*=\s*\[)(.*?)(\];)'
    def replacer(m):
        return m.group(1) + m.group(2) + '\n' + data_line + '\n' + m.group(3)

    new_html, count = re.subn(pattern, replacer, html, flags=re.DOTALL)
    if count == 0:
        # fallback: politicians配列を探す
        pattern2 = r'(const politicians\s*=\s*\[)(.*?)(\];)'
        new_html, count = re.subn(pattern2, replacer, html, flags=re.DOTALL)

    if count == 0:
        print('[ADD] エラー: データ配列が見つかりません')
        return html

    print('[ADD] ✅ 政治家を追加しました')
    return new_html

# ===== エビデンス追加 =====
def process_evidence(html, body):
    """<!-- ADD_EVIDENCE ... --> ブロックからエビデンスを追加"""
    m = re.search(r'<!-- ADD_EVIDENCE\n(.*?)\n-->', body, re.DOTALL)
    if not m:
        print('[EV] ADD_EVIDENCE ブロックが見つかりません')
        return html

    try:
        data = json.loads(m.group(1).strip())
    except json.JSONDecodeError as e:
        print(f'[EV] JSONパースエラー: {e}')
        return html

    pid = data.get('id')
    ev  = data.get('ev')
    if not pid or not ev:
        print('[EV] id または ev が不正')
        return html

    print(f'[EV] 対象: {pid} / カテゴリ: {ev.get("c")}')

    # ev_str 構築
    ev_str = "{c:'" + ev['c'] + "',q:'" + ev['q'].replace("'", "\\'") + "',src:'" + ev['src'] + "',d:'" + ev['d'] + "'"
    if 'url' in ev:
        ev_str += ",url:'" + ev['url'] + "'"
    ev_str += "}"

    # 対象政治家のev:[]を探して末尾に追記
    # パターン: id:'p_xxx' ... ev:[...]
    escaped_id = re.escape(pid)
    pattern = r'(id:\'' + escaped_id + r'\'.*?ev:\[)(.*?)(\])'

    def ev_replacer(m):
        existing = m.group(2).strip()
        if existing:
            return m.group(1) + existing + ',' + ev_str + m.group(3)
        else:
            return m.group(1) + ev_str + m.group(3)

    new_html, count = re.subn(pattern, ev_replacer, html, flags=re.DOTALL, count=1)
    if count == 0:
        print(f'[EV] エラー: {pid} が見つかりません')
        return html

    print('[EV] ✅ エビデンスを追加しました')
    return new_html

# ===== データ修正 =====
def process_fix(html, body):
    """<!-- FIX_DATA ... --> ブロックからフィールドを修正"""
    m = re.search(r'<!-- FIX_DATA\n(.*?)\n-->', body, re.DOTALL)
    if not m:
        print('[FIX] FIX_DATA ブロックが見つかりません')
        return html

    try:
        data = json.loads(m.group(1).strip())
    except json.JSONDecodeError as e:
        print(f'[FIX] JSONパースエラー: {e}')
        return html

    pid   = data.get('id')
    field = data.get('field')
    value = data.get('value')
    if not all([pid, field, value]):
        print('[FIX] 必須フィールドが不足')
        return html

    print(f'[FIX] 対象: {pid} / 修正: {field} → {value}')
    escaped_id = re.escape(pid)

    # 数値フィールド
    numeric_fields = ['age', 'x', 'y']
    # スコアフィールド
    score_fields = ['security','economy','china','russia','okinawa','nuclear','imperial','surname','gender']

    if field in numeric_fields:
        pattern = r'(id:\'' + escaped_id + r'\'.*?' + re.escape(field) + r':)([^,}]+)'
        new_html, count = re.subn(pattern, r'\g<1>' + str(value), html, flags=re.DOTALL, count=1)
    elif field in score_fields:
        # s:{...field:N...} の中を修正
        pattern = r'(id:\'' + escaped_id + r'\'.*?s:\{.*?' + re.escape(field) + r':)(\d+)'
        new_html, count = re.subn(pattern, r'\g<1>' + str(value), html, flags=re.DOTALL, count=1)
    else:
        # 文字列フィールド
        pattern = r"(id:'" + escaped_id + r"'.*?" + re.escape(field) + r":')" + r"([^']*)" + r"'"
        new_html, count = re.subn(pattern, r"\g<1>" + str(value).replace("'", "\\'") + "'", html, flags=re.DOTALL, count=1)

    if count == 0:
        print(f'[FIX] エラー: {pid} の {field} が見つかりません')
        return html

    print('[FIX] ✅ データを修正しました')
    return new_html

# ===== メイン =====
def main():
    print(f'Issue #{ISSUE_NUM}: {ISSUE_TITLE}')
    print(f'本文長: {len(ISSUE_BODY)} 文字')

    html = load_html()
    original = html

    if '[ADD]' in ISSUE_TITLE:
        html = process_add(html, ISSUE_BODY)
    elif '[EV]' in ISSUE_TITLE:
        html = process_evidence(html, ISSUE_BODY)
    elif '[FIX]' in ISSUE_TITLE:
        html = process_fix(html, ISSUE_BODY)
    else:
        print('未知のIssueタイプ。スキップします。')
        return

    if html != original:
        save_html(html)
        print('✅ index.html を保存しました')
    else:
        print('変更なし')

if __name__ == '__main__':
    main()
