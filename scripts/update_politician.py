#!/usr/bin/env python3
"""
GitHub Issue の内容を解析して index.html の政治家データを更新するスクリプト。
GitHub Actions から呼び出されます。

対応タグ:
  <!-- AUTO_UPDATE -->   : 管理者投稿（即時反映）
  <!-- PENDING_REVIEW --> : ユーザー投稿（Claude APIでURL検証後に反映）
"""

import os
import re
import json
import sys
import urllib.request
import urllib.parse

ISSUE_TITLE  = os.environ.get('ISSUE_TITLE', '')
ISSUE_BODY   = os.environ.get('ISSUE_BODY', '')
ISSUE_NUM    = os.environ.get('ISSUE_NUMBER', '?')
INDEX_FILE   = 'index.html'
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GH_OWNER     = 'rai0103'
GH_REPO      = 'rai0103.github.io'

IS_PENDING   = '<!-- PENDING_REVIEW -->' in ISSUE_BODY
IS_ADMIN     = '<!-- AUTO_UPDATE -->' in ISSUE_BODY

def load_html():
    with open(INDEX_FILE, 'r', encoding='utf-8') as f:
        return f.read()

def save_html(content):
    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

def post_issue_comment(comment):
    """Issueにコメントを投稿する"""
    if not GITHUB_TOKEN:
        print("GITHUB_TOKEN not set, skipping comment")
        return
    url = f'https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/issues/{ISSUE_NUM}/comments'
    data = json.dumps({'body': comment}).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f'Bearer {GITHUB_TOKEN}')
    req.add_header('Accept', 'application/vnd.github.v3+json')
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"Comment post failed: {e}")

def close_issue():
    """Issueをクローズする"""
    if not GITHUB_TOKEN:
        return
    url = f'https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/issues/{ISSUE_NUM}'
    data = json.dumps({'state': 'closed'}).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='PATCH')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f'Bearer {GITHUB_TOKEN}')
    req.add_header('Accept', 'application/vnd.github.v3+json')
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"Close issue failed: {e}")

def fetch_url_content(url):
    """URLのコンテンツを取得する（最大5000文字）"""
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0')
        with urllib.request.urlopen(req, timeout=10) as res:
            content = res.read().decode('utf-8', errors='replace')
            return content[:5000]
    except Exception as e:
        return f"URL取得エラー: {e}"

def verify_with_claude(politician_name, category, quote, src, url):
    """Claude APIでエビデンスのURLと発言内容を検証する"""
    if not ANTHROPIC_API_KEY:
        return False, "ANTHROPIC_API_KEY未設定"

    url_content = fetch_url_content(url) if url and url.startswith('http') else "URLなし"

    prompt = f"""以下の政治家エビデンス投稿を検証してください。

【投稿内容】
- 政治家名: {politician_name}
- カテゴリ: {category}
- 発言内容: {quote}
- 出典: {src}
- 出典URL: {url}

【URLから取得したコンテンツ（先頭5000字）】
{url_content}

【判定基準】
1. URLのコンテンツに投稿された発言内容と一致または関連する情報があるか
2. 明らかな捏造・誇張・無関係な内容でないか
3. 政治家名と内容が一致しているか

以下のJSON形式のみで回答してください（他のテキスト不要）:
{{"approved": true/false, "reason": "判定理由（日本語50文字以内）"}}"""

    data = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 200,
        "messages": [{"role": "user", "content": prompt}]
    }).encode('utf-8')

    req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('x-api-key', ANTHROPIC_API_KEY)
    req.add_header('anthropic-version', '2023-06-01')

    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            result = json.loads(res.read().decode('utf-8'))
            text = result['content'][0]['text'].strip()
            # JSONを抽出
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                return parsed.get('approved', False), parsed.get('reason', '不明')
            return False, "レスポース解析失敗"
    except Exception as e:
        return False, f"API呼び出しエラー: {e}"

def replacer(m):
    """JSON文字列内の制御文字をエスケープ"""
    s = m.group(0)
    return s.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')

def process_add(html):
    """[ADD] 政治家追加"""
    m_id   = re.search(r'- ID: `([^`]+)`', ISSUE_BODY)
    m_name = re.search(r'- 名前: (.+)', ISSUE_BODY)
    m_en   = re.search(r'- 英語名: (.+)', ISSUE_BODY)
    m_age  = re.search(r'- 年齢: (\d+)', ISSUE_BODY)
    m_party= re.search(r'- 政党: (.+)', ISSUE_BODY)
    m_house= re.search(r'- 院: (.+)', ISSUE_BODY)
    m_area = re.search(r'- 選挙区: (.+)', ISSUE_BODY)
    m_x    = re.search(r'- X座標: ([-\d.]+)', ISSUE_BODY)
    m_y    = re.search(r'- Y座標: ([-\d.]+)', ISSUE_BODY)

    scores = {}
    for key in ['security','economy','china','russia','okinawa','nuclear','imperial','surname','gender']:
        ms = re.search(rf'- {key}: (\d+)', ISSUE_BODY)
        scores[key] = int(ms.group(1)) if ms else 5

    ev_list = []
    ev_blocks = re.findall(r'### エビデンス\d+\n(.*?)(?=### エビデンス|\Z)', ISSUE_BODY, re.DOTALL)
    for block in ev_blocks:
        ec = re.search(r'カテゴリ: (.+)', block)
        eq = re.search(r'発言内容: (.+)', block)
        es = re.search(r'出典: (.+)', block)
        ed = re.search(r'発言日: (.+)', block)
        if ec and eq and es:
            ev_list.append({
                'c': ec.group(1).strip(),
                'q': eq.group(1).strip(),
                'src': es.group(1).strip(),
                'd': ed.group(1).strip() if ed else '不明'
            })

    new_entry = {
        'id': m_id.group(1) if m_id else f"p_{m_name.group(1) if m_name else 'unknown'}",
        'name': m_name.group(1).strip() if m_name else '不明',
        'en': m_en.group(1).strip() if m_en else '',
        'age': int(m_age.group(1)) if m_age else -1,
        'party': m_party.group(1).strip() if m_party else '無所属',
        'house': m_house.group(1).strip() if m_house else '衆議院',
        'area': m_area.group(1).strip() if m_area else '',
        'x': float(m_x.group(1)) if m_x else 0.0,
        'y': float(m_y.group(1)) if m_y else 0.0,
        's': scores,
        'ev': ev_list
    }

    entry_js = json.dumps(new_entry, ensure_ascii=False)
    entry_js = re.sub(r'"[^"]*"', replacer, entry_js)

    new_html = html.replace('const DATA=[', f'const DATA=[\n  {entry_js},', 1)
    if new_html == html:
        print("ERROR: DATA配列が見つかりません")
        sys.exit(1)
    return new_html

def process_ev(html):
    """[EV] エビデンス追加"""
    m_id  = re.search(r'- ID: `([^`]+)`', ISSUE_BODY)
    m_cat = re.search(r'- カテゴリ: (.+)', ISSUE_BODY)
    m_q   = re.search(r'- 発言内容: (.+)', ISSUE_BODY)
    m_src = re.search(r'- 出典: (.+)', ISSUE_BODY)
    m_d   = re.search(r'- 発言日: (.+)', ISSUE_BODY)
    m_url = re.search(r'- URL: (.+)', ISSUE_BODY)

    if not all([m_id, m_cat, m_q, m_src]):
        print("ERROR: 必要なフィールドが不足")
        sys.exit(1)

    pid   = m_id.group(1).strip()
    cat   = m_cat.group(1).strip()
    quote = m_q.group(1).strip()
    src   = m_src.group(1).strip()
    date  = m_d.group(1).strip() if m_d else '不明'
    url   = m_url.group(1).strip() if m_url else ''

    # PENDING_REVIEWの場合はClaudeで検証
    if IS_PENDING:
        # 名前を取得
        m_name = re.search(r'- 名前: (.+)', ISSUE_BODY)
        name = m_name.group(1).strip() if m_name else pid
        print(f"Claude APIで検証中: {name} / {cat}")
        approved, reason = verify_with_claude(name, cat, quote, src, url)
        if not approved:
            post_issue_comment(f"❌ **自動審査：却下**\n\n理由: {reason}\n\nエビデンスの内容と出典URLが一致しませんでした。出典URLを確認して再投稿してください。")
            close_issue()
            print(f"REJECTED: {reason}")
            sys.exit(0)
        print(f"APPROVED: {reason}")

    new_ev = {'c': cat, 'q': quote, 'src': src, 'd': date}
    if url:
        new_ev['url'] = url
    new_ev_js = json.dumps(new_ev, ensure_ascii=False)
    new_ev_js = re.sub(r'"[^"]*"', replacer, new_ev_js)

    pattern = rf"({{id:'{re.escape(pid)}'[^}}]*ev:\[)"
    m = re.search(pattern, html)
    if not m:
        print(f"ERROR: {pid} が見つかりません")
        sys.exit(1)

    insert_pos = m.end()
    new_html = html[:insert_pos] + new_ev_js + ',' + html[insert_pos:]
    return new_html

def process_fix(html):
    """[FIX] データ修正"""
    m_id    = re.search(r'- ID: `([^`]+)`', ISSUE_BODY)
    m_field = re.search(r'- 修正箇所: (.+)', ISSUE_BODY)
    m_new   = re.search(r'- 正しい値: (.+)', ISSUE_BODY)

    if not all([m_id, m_field, m_new]):
        print("ERROR: 必要なフィールドが不足")
        sys.exit(1)

    pid   = m_id.group(1).strip()
    field = m_field.group(1).strip()
    new_val = m_new.group(1).strip()

    numeric_fields = ['age', 'x', 'y', 'security', 'economy', 'china', 'russia',
                      'okinawa', 'nuclear', 'imperial', 'surname', 'gender']
    score_fields   = ['security', 'economy', 'china', 'russia', 'okinawa',
                      'nuclear', 'imperial', 'surname', 'gender']

    if field in score_fields:
        pattern = rf"({{id:'{re.escape(pid)}'.*?s:{{[^}}]*?{field}:)\d+"
        replacement = rf"\g<1>{new_val}"
    elif field in numeric_fields:
        pattern = rf"({{id:'{re.escape(pid)}'[^}}]*?{field}:)[\d.-]+"
        replacement = rf"\g<1>{new_val}"
    else:
        pattern = rf"({{id:'{re.escape(pid)}'[^}}]*?{field}:')([^']*)'?"
        replacement = rf"\g<1>{new_val}'"

    new_html = re.sub(pattern, replacement, html, count=1, flags=re.DOTALL)
    if new_html == html:
        print(f"ERROR: {pid} の {field} が見つかりません")
        sys.exit(1)
    return new_html

def process_birth(html):
    """[BIRTH] 生年月日から年齢を計算して更新"""
    m_id    = re.search(r'- ID: `([^`]+)`', ISSUE_BODY)
    m_birth = re.search(r'- 生年月日: (.+)', ISSUE_BODY)

    if not all([m_id, m_birth]):
        print("ERROR: 必要なフィールドが不足")
        sys.exit(1)

    pid   = m_id.group(1).strip()
    birth = m_birth.group(1).strip()

    # 年だけ抽出して年齢計算
    m_year = re.search(r'(\d{4})', birth)
    if not m_year:
        print("ERROR: 生年が解析できません")
        sys.exit(1)

    from datetime import date
    birth_year = int(m_year.group(1))
    age = date.today().year - birth_year

    pattern = rf"({{id:'{re.escape(pid)}'[^}}]*?age:)-?\d+"
    new_html = re.sub(pattern, rf"\g<1>{age}", html, count=1, flags=re.DOTALL)
    if new_html == html:
        print(f"ERROR: {pid} が見つかりません")
        sys.exit(1)
    return new_html

# ── メイン処理 ──
if not IS_ADMIN and not IS_PENDING:
    print("AUTO_UPDATE / PENDING_REVIEW タグなし、スキップ")
    sys.exit(0)

html = load_html()

if ISSUE_TITLE.startswith('[ADD]'):
    html = process_add(html)
    action = "政治家追加"
elif ISSUE_TITLE.startswith('[EV]'):
    html = process_ev(html)
    action = "エビデンス追加"
elif ISSUE_TITLE.startswith('[FIX]'):
    html = process_fix(html)
    action = "データ修正"
elif ISSUE_TITLE.startswith('[BIRTH]'):
    html = process_birth(html)
    action = "生年月日更新"
else:
    print(f"未対応のタイトル形式: {ISSUE_TITLE}")
    sys.exit(1)

save_html(html)
print(f"SUCCESS: {action} 完了 (Issue #{ISSUE_NUM})")

# コメント投稿
if IS_PENDING:
    post_issue_comment(f"✅ **自動審査：承認・反映済み**\n\nエビデンスの内容と出典URLが確認できました。index.htmlに反映されました。")
else:
    post_issue_comment(f"✅ **{action}完了** (Issue #{ISSUE_NUM})\n\nindex.htmlに反映されました。")

close_issue()
