#!/usr/bin/env python3
"""
週次自動更新スクリプト
毎週月曜の午前2時 JST（UTC日曜17時）に GitHub Actions で実行される
1. pending-review ラベルの Issue を Claude API で審査し index.html を更新する
2. 国会議事録API（kokkai.ndl.go.jp）から各議員の新規発言を取得・分類し蓄積する
3. 新規発言があった議員のスコア（s/x/y）を全蓄積発言から再計算する
"""

import os
import re
import json
import time
import requests
import anthropic
from datetime import datetime, date, timedelta

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


# ══════════════════════════════════════════════════════════
# 国会議事録API連携
# ══════════════════════════════════════════════════════════

POLICY_CATS = [
    ('security', '日米安保・防衛'),
    ('economy',  '経済政策'),
    ('china',    '対中政策'),
    ('russia',   '対ロ政策'),
    ('okinawa',  '沖縄基地問題'),
    ('nuclear',  '原発政策'),
    ('imperial', '皇室制度'),
    ('surname',  '夫婦別姓'),
    ('gender',   'ジェンダー政策'),
]
CAT_LABELS  = {k: v for k, v in POLICY_CATS}
SCORE_KEYS  = [k for k, _ in POLICY_CATS]

# x/y 計算の重み（index.html の calcQuizResult と同一）
X_WEIGHTS = {'okinawa':0.15, 'nuclear':0.10, 'economy':0.15,
             'imperial':0.40, 'surname':0.30, 'gender':0.25}
Y_WEIGHTS = {'security':0.55, 'china':0.25, 'russia':0.15}


def s_to_catavg(s):
    """s スコア (1〜10) → catAvg (-10〜+10)"""
    return (s - 1) / 9 * 20 - 10


def compute_xy_from_s(s_dict):
    """s スコア辞書から x/y 座標を計算（-10〜+10, 小数第1位）"""
    x_sum = sum(s_to_catavg(s_dict[k]) * w for k, w in X_WEIGHTS.items() if k in s_dict)
    x_w   = sum(w for k, w in X_WEIGHTS.items() if k in s_dict)
    y_sum = sum(s_to_catavg(s_dict[k]) * w for k, w in Y_WEIGHTS.items() if k in s_dict)
    y_w   = sum(w for k, w in Y_WEIGHTS.items() if k in s_dict)
    x = round(x_sum / x_w * 10) / 10 if x_w else 0.0
    y = round(y_sum / y_w * 10) / 10 if y_w else 0.0
    return max(-10.0, min(10.0, x)), max(-10.0, min(10.0, y))


# ── メタデータ管理 ──────────────────────────────────────

def get_kokkai_last_update(content):
    """HTML コメントから前回更新日を取得。なければ直近 180 日（約 1 国会会期）前。"""
    m = re.search(r'<!-- KOKKAI_LAST_UPDATE: (\d{4}-\d{2}-\d{2}) -->', content)
    if m:
        return m.group(1)
    return (date.today() - timedelta(days=180)).strftime('%Y-%m-%d')


def set_kokkai_last_update(content, date_str):
    """KOKKAI_LAST_UPDATE コメントを更新または挿入する。"""
    tag = f'<!-- KOKKAI_LAST_UPDATE: {date_str} -->'
    if '<!-- KOKKAI_LAST_UPDATE:' in content:
        return re.sub(r'<!-- KOKKAI_LAST_UPDATE: \d{4}-\d{2}-\d{2} -->', tag, content)
    if '</body>' in content:
        return content.replace('</body>', f'{tag}\n</body>', 1)
    return content + '\n' + tag


def get_existing_speech_ids(content):
    """ev 配列に既に格納された speechID を集合で返す（重複取得防止）。"""
    return set(re.findall(r'"speechID"\s*:\s*"([^"]+)"', content))


def get_all_politicians(content):
    """DATA 配列から全議員の id・name・house を抽出する。"""
    politicians = []
    for m in re.finditer(r"id:'(p_[^']+)'[^{]*?name:'([^']+)'[^{]*?house:'([^']*)'", content):
        politicians.append({'id': m.group(1), 'name': m.group(2), 'house': m.group(3)})
    return politicians


# ── 国会議事録 API 取得 ──────────────────────────────────

KOKKAI_API = 'https://kokkai.ndl.go.jp/api/speech'


def fetch_kokkai_speeches(name, house, from_date, until_date):
    """
    kokkai.ndl.go.jp API で議員の発言を取得する。
    100 件ずつページングし、実質的な発言（100 文字以上）のみ返す。
    """
    all_speeches = []
    start = 1
    while True:
        params = {
            'speaker':        name,
            'from':           from_date,
            'until':          until_date,
            'maximumRecords': 100,
            'startRecord':    start,
            'recordPacking':  'json',
        }
        try:
            r = requests.get(KOKKAI_API, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f'    国会API エラー ({name}): {e}')
            break

        records = data.get('speechRecord', [])
        if not records:
            break

        for rec in records:
            text = rec.get('speech', '')
            if len(text) < 100:          # 短い発言（挨拶等）を除外
                continue
            all_speeches.append({
                'speechID': rec.get('speechID', ''),
                'date':     rec.get('date', '')[:10],   # YYYY-MM-DD
                'speech':   text[:800],                 # Claude に渡す最大文字数
                'meeting':  rec.get('nameOfMeeting', ''),
                'house':    rec.get('nameOfHouse', ''),
                'session':  rec.get('session', ''),
            })

        total = int(data.get('numberOfRecords', 0))
        if start + 99 >= total:
            break
        start += 100
        time.sleep(1)

    return all_speeches


# ── Claude による発言分類 ────────────────────────────────

def classify_speeches_with_claude(politician_name, speeches):
    """
    最大 20 件の発言を一括で Claude に渡し、政策カテゴリ分類と要旨を取得する。
    返値: 追加する ev アイテムのリスト
    """
    if not speeches:
        return []

    cat_list = '\n'.join(f'- {k}: {v}' for k, v in POLICY_CATS)
    speeches_text = '\n\n'.join(
        f'[{i+1}] 日付:{s["date"]} 委員会:{s["meeting"]}\n発言:{s["speech"][:500]}'
        for i, s in enumerate(speeches)
    )

    prompt = f"""政治家「{politician_name}」の国会発言を分析してください。

## 発言一覧
{speeches_text}

## 政策カテゴリ（9種）
{cat_list}

## 指示
各発言について：
1. 上記9カテゴリのうち最も関連するもの1つを選ぶ（無関係・手続き的発言は "none"）
2. 政策的主張が読み取れる場合のみ、発言要旨を100字以内で要約

JSON配列のみ返答（noneは含めない）:
[{{"index": 1, "category": "security", "summary": "要旨..."}}, ...]"""

    try:
        message = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=2048,
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        results = json.loads(raw)
    except Exception as e:
        print(f'    発言分類エラー: {e}')
        return []

    ev_items = []
    for r in results:
        idx     = r.get('index', 0) - 1
        cat_key = r.get('category', '')
        summary = r.get('summary', '').strip()
        if cat_key not in CAT_LABELS or not summary:
            continue
        if not (0 <= idx < len(speeches)):
            continue
        s = speeches[idx]
        ev_items.append({
            'c':        CAT_LABELS[cat_key],
            'q':        summary,
            'src':      f'第{s["session"]}回国会 {s["house"]} {s["meeting"]}（kokkai.ndl.go.jp）',
            'd':        s['date'],           # ISO 形式 YYYY-MM-DD（日付ソート用）
            'kokkai':   True,
            'speechID': s['speechID'],
        })
    return ev_items


# ── HTML への ev 追加 ────────────────────────────────────

def add_ev_to_html(content, pid, ev_item):
    """
    議員の ev 配列の先頭に ev_item を追加する。
    get_politician_block() でブロックを特定してから ev:[ を探す。
    """
    start, end, block = get_politician_block(content, pid)
    if block is None:
        return content, False

    ev_pos = block.find('ev:[')
    if ev_pos == -1:
        return content, False

    insert_pos = start + ev_pos + 4  # 'ev:[' の直後

    ev_js = json.dumps(ev_item, ensure_ascii=False)
    # JSON 文字列内の制御文字をエスケープ
    ev_js = re.sub(
        r'"[^"]*"',
        lambda m: m.group(0).replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t'),
        ev_js
    )

    return content[:insert_pos] + ev_js + ',' + content[insert_pos:], True


# ── スコア再計算 ─────────────────────────────────────────

def extract_ev_items_for_scoring(block):
    """
    議員ブロックの ev 配列から {c, q, d} を抽出してリストで返す。
    ev アイテムはネストなしの flat オブジェクトなので [^{}]+ で安全にマッチ可能。
    """
    ev_pos = block.find('ev:[')
    if ev_pos == -1:
        return []
    ev_content = block[ev_pos + 4:]

    items = []
    for m in re.finditer(r'\{([^{}]{10,})\}', ev_content):
        s = m.group(1)
        c = re.search(r"['\"]?c['\"]?\s*:\s*['\"]([^'\"]+)['\"]", s)
        q = re.search(r"['\"]?q['\"]?\s*:\s*['\"]([^'\"]+)['\"]", s)
        d = re.search(r"['\"]?d['\"]?\s*:\s*['\"]([^'\"]+)['\"]", s)
        if c and q:
            items.append({'c': c.group(1), 'q': q.group(1), 'd': d.group(1) if d else '不明'})
    return items


def recalculate_scores_with_claude(politician_name, all_evs, current_s):
    """
    全蓄積発言からカテゴリ別に集計し、Claude で 9 カテゴリスコア（1〜10）を再計算する。
    返値: (new_s dict, new_x float, new_y float) または失敗時 (current_s, None, None)
    """
    # カテゴリ別に集計（代表例 3 件 + 件数を提示）
    by_cat = {}
    for ev in all_evs:
        label = ev.get('c', '不明')
        by_cat.setdefault(label, []).append(ev)

    cat_summary_lines = []
    for label, evs in by_cat.items():
        recent = sorted(evs, key=lambda e: e.get('d', ''), reverse=True)[:3]
        examples = '\n'.join(f'  ・{e["q"][:80]}（{e["d"]}）' for e in recent)
        cat_summary_lines.append(f'【{label}】全{len(evs)}件\n{examples}')
    ev_summary = '\n\n'.join(cat_summary_lines)

    cat_desc_lines = '\n'.join([
        'security: 日米安保・防衛（1=専守防衛・軍縮, 10=積極防衛・強化）',
        'economy:  経済政策（1=再分配・規制強化, 10=市場優先・成長重視）',
        'china:    対中政策（1=対話・融和, 10=対抗・強硬）',
        'russia:   対ロ政策（1=対話・協調, 10=制裁・強硬）',
        'okinawa:  沖縄基地問題（1=基地撤廃・移設反対, 10=現状維持・賛成）',
        'nuclear:  原発政策（1=脱原発推進, 10=積極活用）',
        'imperial: 皇室制度（1=女系・女性天皇容認, 10=男系厳守）',
        'surname:  夫婦別姓（1=選択的別姓賛成, 10=現行維持・反対）',
        'gender:   ジェンダー政策（1=積極推進, 10=慎重・反対）',
    ])

    prompt = f"""政治家「{politician_name}」の全発言記録（カテゴリ別集計）を分析し、
9カテゴリのスタンス指数を 1〜10 で算出してください。

## カテゴリ別発言記録
{ev_summary}

## スコア基準
{cat_desc_lines}

発言が少ないカテゴリは現在値を参考にしてください（現在値: {json.dumps(current_s, ensure_ascii=False)}）

JSONのみ返答:
{{"security":N,"economy":N,"china":N,"russia":N,"okinawa":N,"nuclear":N,"imperial":N,"surname":N,"gender":N}}"""

    try:
        message = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=256,
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        new_s = json.loads(raw)
        # バリデーション・クランプ
        for k in SCORE_KEYS:
            new_s[k] = max(1, min(10, int(new_s.get(k, current_s.get(k, 5)))))
    except Exception as e:
        print(f'    スコア再計算エラー: {e}')
        return current_s, None, None

    new_x, new_y = compute_xy_from_s(new_s)
    return new_s, new_x, new_y


def update_all_scores_in_html(content, pid, new_s, new_x, new_y):
    """議員ブロックの s/x/y を一括更新する。"""
    start, end, block = get_politician_block(content, pid)
    if block is None:
        return content, False

    s_str   = ','.join(f'{k}:{new_s[k]}' for k in SCORE_KEYS)
    new_block = re.sub(r's:\{[^}]+\}', f's:{{{s_str}}}', block)
    new_block = re.sub(r'x:(-?[\d.]+)', f'x:{new_x}', new_block)
    new_block = re.sub(r'y:(-?[\d.]+)', f'y:{new_y}', new_block)

    return content[:start] + new_block + content[end:], True


# ── 週次 kokkai 更新メイン ────────────────────────────────

def run_kokkai_update(content):
    """
    1. 前回更新日以降の発言を kokkai API から取得
    2. Claude で分類・要旨化して ev 配列に追加（重複は speechID で排除）
    3. 新規発言があった議員のスコアを全蓄積発言から再計算
    4. KOKKAI_LAST_UPDATE を今日の日付に更新
    返値: (updated_content, changed: bool)
    """
    from_date = get_kokkai_last_update(content)
    until_date = date.today().strftime('%Y-%m-%d')
    existing_ids = get_existing_speech_ids(content)

    print(f'[kokkai] 対象期間: {from_date} 〜 {until_date}')
    print(f'[kokkai] 既存 speechID 数: {len(existing_ids)}')

    politicians = get_all_politicians(content)
    print(f'[kokkai] 対象議員数: {len(politicians)}人')

    updated_pids = []

    for pol in politicians:
        pid, name, house = pol['id'], pol['name'], pol['house']
        print(f'  [{name}] 発言取得中...')

        speeches = fetch_kokkai_speeches(name, house, from_date, until_date)
        new_speeches = [s for s in speeches if s['speechID'] not in existing_ids]

        if not new_speeches:
            print(f'  [{name}] 新規発言なし')
            continue

        print(f'  [{name}] 新規発言 {len(new_speeches)} 件を分類中...')

        # 最大 20 件ずつバッチ分類
        new_ev_items = []
        for i in range(0, len(new_speeches), 20):
            batch = new_speeches[i:i + 20]
            new_ev_items.extend(classify_speeches_with_claude(name, batch))
            time.sleep(0.5)

        if not new_ev_items:
            print(f'  [{name}] 政策関連発言なし（分類スキップ）')
            continue

        print(f'  [{name}] {len(new_ev_items)} 件を ev 配列に追加')
        for ev in new_ev_items:
            content, ok = add_ev_to_html(content, pid, ev)
            if ok:
                existing_ids.add(ev['speechID'])

        updated_pids.append(pid)
        time.sleep(1)

    # スコア再計算（新規発言があった議員のみ）
    print(f'[kokkai] スコア再計算: {len(updated_pids)} 人')
    for pid in updated_pids:
        _, _, block = get_politician_block(content, pid)
        if block is None:
            continue

        # 現在の s 値を抽出
        current_s = {k: 5 for k in SCORE_KEYS}
        s_match = re.search(r's:\{([^}]+)\}', block)
        if s_match:
            for km in re.finditer(r'(\w+):(\d+)', s_match.group(1)):
                if km.group(1) in SCORE_KEYS:
                    current_s[km.group(1)] = int(km.group(2))

        name_m = re.search(r"name:'([^']+)'", block)
        name   = name_m.group(1) if name_m else pid

        all_evs = extract_ev_items_for_scoring(block)
        if not all_evs:
            continue

        new_s, new_x, new_y = recalculate_scores_with_claude(name, all_evs, current_s)
        if new_x is not None:
            content, ok = update_all_scores_in_html(content, pid, new_s, new_x, new_y)
            if ok:
                print(f'  [{name}] スコア更新: x={new_x}, y={new_y}')

        time.sleep(0.5)

    # 最終更新日を今日に更新
    content = set_kokkai_last_update(content, until_date)

    changed = bool(updated_pids)
    return content, changed


# ══════════════════════════════════════════════════════════
# メイン
# ══════════════════════════════════════════════════════════

def main():
    html_content = load_html()
    original = html_content

    # ── Issue 審査処理 ──────────────────────────────────
    issues = get_pending_issues()
    if issues:
        for issue in issues:
            print(f'処理中: Issue #{issue["number"]} - {issue["title"]}')
            html_content = process_issue(issue, html_content)
    else:
        print('処理するIssueはありません。')

    # ── 週次 国会議事録更新 ─────────────────────────────
    print('週次 国会議事録更新を開始...')
    html_content, kokkai_changed = run_kokkai_update(html_content)
    if kokkai_changed:
        print('国会議事録データを更新しました。')
    else:
        print('国会議事録: 新規データなし。')

    if html_content != original:
        save_html(html_content)
        print('index.htmlを更新しました。')
    else:
        print('index.htmlの変更はありません。')


if __name__ == '__main__':
    main()
