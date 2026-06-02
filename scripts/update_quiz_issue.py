#!/usr/bin/env python3
"""
月次クイズイシュー更新スクリプト
毎月1日 02:00 JST（UTC前日17:00）に GitHub Actions で実行される
月番号 (1-12) を 15 で割った余りからイシューを決定的に決定し、
index.html の CURRENT_QUIZ_ISSUE を更新してコミット・プッシュする
"""

import re
import sys
from datetime import datetime, timezone, timedelta

ISSUE_ORDER = [
    'defense', 'spy', 'surname', 'imperial', 'middleeast', 'ukraine',
    'constitution', 'nuclear', 'tax', 'immigration', 'china', 'pension',
    'history', 'judicial', 'okinawa',
]

JST = timezone(timedelta(hours=9))


def get_current_issue(month: int) -> str:
    """月番号（1-12）からイシューキーを返す（決定的）"""
    idx = (month - 1) % len(ISSUE_ORDER)
    return ISSUE_ORDER[idx]


def update_index_html(filepath: str, issue_key: str, year_month: str) -> bool:
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    pattern = r'(/\* QUIZ_ISSUE_START \*/\n)(const CURRENT_QUIZ_ISSUE = \'[^\']*\';[^\n]*)(\n/\* QUIZ_ISSUE_END \*/)'
    replacement = (
        r'\g<1>'
        f"const CURRENT_QUIZ_ISSUE = '{issue_key}'; // {year_month} — 毎月1日に自動更新"
        r'\g<3>'
    )

    new_content, n = re.subn(pattern, replacement, content)
    if n == 0:
        print(f'ERROR: QUIZ_ISSUE_START/END マーカーが見つかりません', file=sys.stderr)
        return False

    if new_content == content:
        print(f'変更なし: CURRENT_QUIZ_ISSUE は既に "{issue_key}" です')
        return False

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f'更新完了: CURRENT_QUIZ_ISSUE = "{issue_key}" ({year_month})')
    return True


def main():
    now = datetime.now(JST)
    month = now.month
    year_month = now.strftime('%Y-%m')
    issue_key = get_current_issue(month)

    print(f'[quiz-update] {year_month} month={month} → issue={issue_key}')

    changed = update_index_html('index.html', issue_key, year_month)
    sys.exit(0 if True else 1)


if __name__ == '__main__':
    main()
