import streamlit as st
import gspread
import json
import tempfile
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
import requests
import uuid
import time
import random
import math
import pandas as pd
# === Google Sheets 認証 ===
creds_dict = st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"].to_dict()
creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".json") as tmp:
    json.dump(creds_dict, tmp)
    tmp_path = tmp.name

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials = ServiceAccountCredentials.from_json_keyfile_name(tmp_path, scope)
client = gspread.authorize(credentials)

spreadsheet = client.open_by_key("1XpB4gzlkOS72uJMADmSIuvqECM5Ud8M-KwwJbXSxJxM")
chat_sheet = spreadsheet.worksheet("Chat")
profile_sheet = spreadsheet.worksheet("Personality")

def log_chat_to_sheet(user, session_id, turn_index, user_msg, ai_msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    experiment = st.session_state.get("experiment_condition", "Unknown")
    matched = "Matched" if st.session_state.get("matched_mode", False) else "NoMatch"
    
    sheet_name = f"{user}_{matched}"
    try:
        user_sheet = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        user_sheet = spreadsheet.add_worksheet(title=sheet_name, rows="1000", cols="7")
        user_sheet.append_row(["SessionID", "Username", "Role", "Message", "Timestamp", "ExperimentCondition", "MatchedMode"])
    
    user_sheet.append_row([session_id, user, "User", user_msg, timestamp, experiment, matched])
    user_sheet.append_row([session_id, user, "AI", ai_msg, timestamp, experiment, matched])



# === セッション管理 ===
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

# アンケート表示状態の初期化
if "survey_prompts_shown" not in st.session_state:
    st.session_state.survey_prompts_shown = {
        "initial": False,
        "30": False,
        "60": False,
        "90": False
    }


if "turn_index" not in st.session_state:
    st.session_state.turn_index = 0

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# 初回アンケートのお願い（チャット開始前）
if not st.session_state.survey_prompts_shown["initial"]:
    with st.form("initial_survey_form"):
        st.info("Before starting the chat, would you be willing to complete a short survey?")
        answer = st.radio("Survey Consent", ["Yes", "No"])
        submit = st.form_submit_button("Submit")
        if submit:
            st.session_state.survey_prompts_shown["initial"] = True
            if answer == "Yes":
                st.success("Thank you! Please fill out the form: [Survey Link](https://example.com/survey_initial)")
            else:
                st.info("No problem, you can continue to the chat.")
            st.stop()  # ユーザーが回答するまで進ませない


# === Google Sheets 安全書き込み ===
def safe_append(sheet, row, retries=3, delay=2):
    for i in range(retries):
        try:
            sheet.append_row(row)
            return
        except gspread.exceptions.APIError:
            time.sleep(delay * (i + 1))
    st.error("Failed to log data after multiple retries.")

def get_profile(user):
    for row in profile_sheet.get_all_records():
        if row["Username"] == user:
            return row
    return None


#Penley & Tomaka 2002, Carver & Connor-Smith 2010, Stewart 2000, Frontiers 2023
# === Big Fiveトーン + 複合パターン対応 ===
import random

def determine_tone(profile, match=True):
    def flip(value): return 20 if value >= 60 else 80 if value <= 40 else 50
    def adjusted(trait): return int(profile.get(trait, 50)) if match else flip(int(profile.get(trait, 50)))

    ex, ag, co, es, op = [adjusted(t) for t in ["Extraversion","Agreeableness","Conscientiousness","Emotional Stability","Openness"]]

    tone = "cheerful and engaging" if ex >= 60 else "calm and measured"
    empathy = "warm and supportive" if ag >= 60 else "matter-of-fact but polite"
    style = "clear and structured" if co >= 60 else "casual and flexible"
    emotional = "steady and reassuring" if es >= 60 else "gentle and calming"
    creativity = "curious and imaginative" if op >= 60 else "practical and simple"

    # Personality-based coping instructions
    suggestions_map = []
    if es <= 40 and co <= 40:  # 高N＋低C
        suggestions_map.append("Try breaking big tasks into small steps and reframe stress as a challenge.")
    if ex >= 60 and co <= 40:  # 高E＋低C
        suggestions_map.append("Plan a fun social activity that gives you energy but adds a little structure.")
    if es <= 40 and ex <= 40:  # 高N＋低E（Type D）
        suggestions_map.append("Express your feelings safely, like journaling, or try a mindfulness break.")
    if ex >= 60 and co >= 60:
        suggestions_map.append("Set a short-term goal and tackle it with a friend to stay motivated.")
    if ag >= 60:
        suggestions_map.append("Reach out to a supportive friend or help someone else—it can lift your mood.")
    if op >= 60:
        suggestions_map.append("Try a creative outlet like art or music, or explore a new hobby.")

    # Combine 2 suggestions randomly
    if not suggestions_map:
        suggestions_map.append("Offer practical coping ideas based on their personality.")
    special_instruction = " ".join(random.sample(suggestions_map, min(2, len(suggestions_map))))

    return {
        "tone": tone,
        "empathy": empathy,
        "style": style,
        "emotional": emotional,
        "creativity": creativity,
        "special_instruction": special_instruction
    }

# ==== ここから Big5Chat ベースの擬似ユーザー生成 & 自動会話シミュレーション ==== #

BIG5_PATH = "data/big5_chat/big5_chat_dataset.csv"  # アップロード済みのパスに合わせて

def load_big5chat():
    """
    Big5Chat を読み込む。想定カラム:
      - 'text'（ユーザー発話）
      - 'Extraversion','Agreeableness','Conscientiousness','Emotional Stability','Openness'
    ※ 列名が違う場合はここで rename してください。
    """
    df = pd.read_csv(BIG5_PATH)
    # 列名の正規化（必要なら調整）
    rename_map = {
        'E':'Extraversion','A':'Agreeableness','C':'Conscientiousness','N':'Emotional Stability','O':'Openness',
        'utterance':'text','message':'text'
    }
    for k, v in rename_map.items():
        if k in df.columns and v not in df.columns:
            df = df.rename(columns={k: v})

    needed = ['text','Extraversion','Agreeableness','Conscientiousness','Emotional Stability','Openness']
    missing = [c for c in needed if c not in df.columns]
    if missing:
        st.warning(f"Big5Chat columns missing: {missing}. Please adjust load_big5chat().")
    return df.dropna(subset=['text']).reset_index(drop=True)

def to_bins(score, step=10):
    """0–100 のスコアを step 幅（±10 など）でビン化（例: step=10 なら 0,10,20,...）"""
    try:
        s = float(score)
    except:
        s = 50.0
    s = max(0, min(100, s))
    return int(round(s/step)*step)

def group_by_trait_window(df, center, window=10):
    """
    center: {'Extraversion': 70,...} のような中心値
    window: ±10 の範囲で類似サンプルを抽出
    """
    m = pd.Series(center)
    cond = (
        (df['Extraversion'].between(m['Extraversion']-window, m['Extraversion']+window)) &
        (df['Agreeableness'].between(m['Agreeableness']-window, m['Agreeableness']+window)) &
        (df['Conscientiousness'].between(m['Conscientiousness']-window, m['Conscientiousness']+window)) &
        (df['Emotional Stability'].between(m['Emotional Stability']-window, m['Emotional Stability']+window)) &
        (df['Openness'].between(m['Openness']-window, m['Openness']+window))
    )
    return df[cond].copy()

def build_profile_from_center(center):
    """
    center（各特性 0–100）から、シートの Personality と同形式の dict を作る
    """
    return {
        "Extraversion": int(center.get("Extraversion", 50)),
        "Agreeableness": int(center.get("Agreeableness", 50)),
        "Conscientiousness": int(center.get("Conscientiousness", 50)),
        "Emotional Stability": int(center.get("Emotional Stability", 50)),
        "Openness": int(center.get("Openness", 50)),
        # 以降は Chat 時に参照されないが、関数互換のためキー揃え
        "Username": "",
        "ExperimentCondition": "Personalized Empathy",
    }

def make_user_inputs_from_group(gdf, min_count=60, seed=0):
    """
    類似スコア群 (gdf) からユーザー入力（text）を少なくとも min_count 個用意。
    足りない場合はリサンプリングで補う。
    """
    rs = gdf.sample(frac=1, random_state=seed)['text'].tolist()
    if len(rs) >= min_count:
        return rs[:min_count]
    # 足りなければループして補完
    out = []
    i = 0
    while len(out) < min_count:
        out.append(rs[i % len(rs)])
        i += 1
    return out

def run_simulation_for_user(username, profile_dict, user_inputs, flip_after=30):
    """
    1ユーザーについて 60 入力程度を投入:
      - 前半: Non-match（flip ロジック適用）
      - 後半: Match（通常）
    既存の call_api / build プロンプトに合わせて API 叩き、Google Sheet にもログする。
    """
    # 会話履歴はここでは短めに（直近 4ターン）だけ参照
    chat_history = []
    turn_index = 0
    for ux in user_inputs:
        turn_index += 1
        # match 切替
        match = (turn_index > flip_after)

        # Chat ページと同じトーン構築
        tone_data = determine_tone(profile_dict, match=match)
        tone_instruction = (
            f"Respond in a {tone_data['tone']}, {tone_data['empathy']} way. "
            f"Keep tone {tone_data['emotional']} and include {tone_data['creativity']} ideas. "
            f"{tone_data['special_instruction']}"
        )

        profile_summary = ", ".join([
            f"Extraversion={profile_dict.get('Extraversion', 'N/A')}",
            f"Agreeableness={profile_dict.get('Agreeableness', 'N/A')}",
            f"Conscientiousness={profile_dict.get('Conscientiousness', 'N/A')}",
            f"Emotional Stability={profile_dict.get('Emotional Stability', 'N/A')}",
            f"Openness={profile_dict.get('Openness', 'N/A')}"
        ])

        context = "\n".join([f"{m['role']}: {m['content']}" for m in chat_history[-4:]])

        prompt = f"""
You are a warm, supportive mental health assistant.
Reflect this personality style: {tone_instruction}.
Write a natural, conversational response in 2–3 sentences:
- Acknowledge the user's concern using their own words.
- Ask ONE relevant question to keep the conversation going.
Avoid sounding like a list. Make it flow like a real chat.
-Suggest ONE practical coping tip based on their personality ({profile_summary}) and briefly explain why it helps.
Avoid phrases like "I understand" or "That sounds tough".
Keep it empathetic, practical, and conversational.
Conversation so far:
{context}
User: {ux}
Assistant:
""".strip()

        crisis_msg = handle_crisis(ux)
        if crisis_msg:
            ai_reply = crisis_msg
        else:
            ai_reply = call_api(prompt) or "[Simulation] No response."

        # 履歴更新とログ保存
        chat_history.append({"role": "User", "content": ux})
        chat_history.append({"role": "AI", "content": ai_reply})

        # ⚠️ シミュレーションでもログ形式は実ユーザーと同じ
        # 実装の log_chat_to_sheet は st.session_state の matched_mode を参照しているので一時的に設定
        st.session_state['matched_mode'] = match
        log_chat_to_sheet(
            user=username,
            session_id=st.session_state.get("session_id", str(uuid.uuid4())),
            turn_index=turn_index,
            user_msg=ux,
            ai_msg=ai_reply
        )

def simulate_many_users(n_users=3, step=10, min_turns=60, seed=42):
    """
    Big5Chat の実データから、±step で近いスコアをまとめた擬似ユーザーを複数作る。
      1) ランダムな中心スコアを n_users 個サンプリング
      2) その中心スコア ±step の範囲に入る発話群から少なくとも min_turns を集める
      3) 30ターン Non-match → 以降 Match で応答生成 + ログ保存
    """
    df = load_big5chat()
    if df.empty:
        st.error("Big5Chat could not be loaded or has no rows.")
        return

    rng = random.Random(seed)
    centers = []
    # Big5Chat の各スコアを step でビン化した候補からランダム選択
    for _ in range(n_users):
        ridx = rng.randrange(0, len(df))
        row = df.iloc[ridx]
        center = {
            "Extraversion": to_bins(row['Extraversion'], step=step),
            "Agreeableness": to_bins(row['Agreeableness'], step=step),
            "Conscientiousness": to_bins(row['Conscientiousness'], step=step),
            "Emotional Stability": to_bins(row['Emotional Stability'], step=step),
            "Openness": to_bins(row['Openness'], step=step),
        }
        centers.append(center)

    for i, center in enumerate(centers, start=1):
        gdf = group_by_trait_window(df, center=center, window=step)
        if gdf.empty:
            st.warning(f"[SimUser{i}] No samples in ±{step} for center={center}. Skipped.")
            continue

        inputs = make_user_inputs_from_group(gdf, min_count=min_turns, seed=seed+i)
        profile_dict = build_profile_from_center(center)
        username = f"Simulated_user{i}"
        st.info(f"Running simulation for {username} with center={center} using {len(inputs)} inputs...")
        run_simulation_for_user(username, profile_dict, inputs, flip_after=30)

    st.success("Simulation finished. Check Google Sheets for logs.")

# ==== ここまで シミュレーション関数 ==== #



# === 危機対応 ===
def handle_crisis(user_input):
    keywords = ["suicide", "kill myself", "end my life", "self-harm"]
    if any(kw in user_input.lower() for kw in keywords):
        return "I'm really sorry you're feeling this way. You're not alone. Please contact someone you trust or a hotline."
    return None

def build_prompt(user_input, context, tone_instruction, profile_summary):
    return f"""
You are a mental well-being assistant.
Reflect these traits strongly: {tone_instruction}.
Respond in 2–3 sentences:
1. Acknowledge the user's concern using their words.
2. Ask a relevant question.
3. Suggest ONE action tailored to their personality ({profile_summary}) and explain why it helps.
Avoid phrases like "I understand". Use a warm, natural tone.
Conversation so far:
{context}
User's message: {user_input}
Assistant:
""".strip()


def call_api(prompt):
    API_URL = "https://royalmilktea103986368-dissintation.hf.space/generate"
    payload = {"prompt": prompt, "max_tokens": 180, "temperature": 0.7, "top_p": 0.95}
    for attempt in range(3):
        try:
            r = requests.post(API_URL, json=payload, timeout=30)
            if r.status_code == 200:
                text = r.json().get("response", "").strip()
                if text:
                    return text.split("Assistant:")[-1].replace("\n\n", "\n").strip()
        except:
            time.sleep(2)
    return None

# === ユーザーとページ管理 ===
user_name = st.sidebar.text_input("Enter your username")
if not user_name:
    st.warning("Please enter your username.")
    st.stop()

st.session_state.user_name = user_name


# すでに登録されているユーザーか確認
existing_users = [row["Username"] for row in profile_sheet.get_all_records()]
if st.session_state.user_name in existing_users:
    user_row = next(row for row in profile_sheet.get_all_records() if row["Username"] == st.session_state.user_name)
    st.session_state.experiment_condition = user_row["ExperimentCondition"]
else:
    # 新規ユーザー：偶数/奇数で割り当て
    st.session_state.experiment_condition = "Fixed Empathy" if len(existing_users) % 2 == 0 else "Personalized Empathy"


# ✅ ページ自動選択
profile = get_profile(user_name)
page = "Chat Session" if profile else "Personality Test"

# === Personality Test ===
if page == "Personality Test":
    st.title("Big Five Personality Test (BFI-44)")
    total_pages = 5
    if "page" not in st.session_state: st.session_state.page = 1
    if "responses" not in st.session_state: st.session_state.responses = []

    def interpret_trait(trait, score):
        if trait == "Extraversion":
            if score >= 60: 
                return "High → Very outgoing and energetic"
            elif score >= 40: 
                return "Moderate → Balanced between sociable and reserved"
            else: 
                return "Low → Quiet and reserved"
        if trait == "Agreeableness":
            if score >= 60: return "High → Cooperative and empathetic"
            elif score >= 40: return "Moderate → Balanced between friendly and assertive"
            else: 
                return "Low → Independent and critical"
        if trait == "Conscientiousness":
            if score >= 60: 
                return "High → Organized and responsible"
            elif score >= 40: 
                return "Moderate → Sometimes structured, sometimes flexible"
            else: 
                return "Low → Spontaneous and less structured"
        if trait == "Emotional Stability":
            if score >= 60: 
                return "High → Calm and resilient"
            elif score >= 40: 
                return "Moderate → Occasionally stressed but generally balanced"
            else: 
                return "Low → Sensitive to stress and emotions"
        if trait == "Openness":
            if score >= 60: 
                return "High → Creative and open to new ideas"
            elif score >= 40: 
                return "Moderate → Appreciates some novelty but prefers familiarity"
            else: 
                return "Low → Prefers routine and familiarity"
        return ""


    # 質問セット（BFI-44）
    bfi_questions = [
    # Extraversion (8 items)
    ("I see myself as someone who is talkative.", "Extraversion", False),
    ("I see myself as someone who is reserved.", "Extraversion", True),
    ("I see myself as someone who is full of energy.", "Extraversion", False),
    ("I see myself as someone who generates a lot of enthusiasm.", "Extraversion", False),
    ("I see myself as someone who tends to be quiet.", "Extraversion", True),
    ("I see myself as someone who has an assertive personality.", "Extraversion", False),
    ("I see myself as someone who is sometimes shy, inhibited.", "Extraversion", True),
    ("I see myself as someone who is outgoing, sociable.", "Extraversion", False),

    # Agreeableness (9 items)
    ("I see myself as someone who is helpful and unselfish with others.", "Agreeableness", False),
    ("I see myself as someone who starts quarrels with others.", "Agreeableness", True),
    ("I see myself as someone who has a forgiving nature.", "Agreeableness", False),
    ("I see myself as someone who is generally trusting.", "Agreeableness", False),
    ("I see myself as someone who can be cold and aloof.", "Agreeableness", True),
    ("I see myself as someone who is considerate and kind to almost everyone.", "Agreeableness", False),
    ("I see myself as someone who is sometimes rude to others.", "Agreeableness", True),
    ("I see myself as someone who likes to cooperate with others.", "Agreeableness", False),
    ("I see myself as someone who tends to find fault with others.", "Agreeableness", True),

    # Conscientiousness (9 items)
    ("I see myself as someone who does a thorough job.", "Conscientiousness", False),
    ("I see myself as someone who tends to be lazy.", "Conscientiousness", True),
    ("I see myself as someone who is a reliable worker.", "Conscientiousness", False),
    ("I see myself as someone who does things efficiently.", "Conscientiousness", False),
    ("I see myself as someone who makes plans and follows through with them.", "Conscientiousness", False),
    ("I see myself as someone who tends to be disorganized.", "Conscientiousness", True),
    ("I see myself as someone who is easily distracted.", "Conscientiousness", True),
    ("I see myself as someone who is persistent and works until the task is finished.", "Conscientiousness", False),
    ("I see myself as someone who is careful and pays attention to details.", "Conscientiousness", False),

    # Neuroticism / Emotional Stability (8 items)
    ("I see myself as someone who is depressed, blue.", "Emotional Stability", True),
    ("I see myself as someone who can be tense.", "Emotional Stability", True),
    ("I see myself as someone who worries a lot.", "Emotional Stability", True),
    ("I see myself as someone who remains calm in tense situations.", "Emotional Stability", False),
    ("I see myself as someone who is emotionally stable, not easily upset.", "Emotional Stability", False),
    ("I see myself as someone who gets nervous easily.", "Emotional Stability", True),
    ("I see myself as someone who can be moody.", "Emotional Stability", True),
    ("I see myself as someone who handles stress well.", "Emotional Stability", False),

    # Openness to Experience (10 items)
    ("I see myself as someone who is original, comes up with new ideas.", "Openness", False),
    ("I see myself as someone who is curious about many different things.", "Openness", False),
    ("I see myself as someone who is ingenious, a deep thinker.", "Openness", False),
    ("I see myself as someone who has an active imagination.", "Openness", False),
    ("I see myself as someone who is inventive.", "Openness", False),
    ("I see myself as someone who values artistic, aesthetic experiences.", "Openness", False),
    ("I see myself as someone who prefers work that is routine.", "Openness", True),
    ("I see myself as someone who likes to reflect and play with ideas.", "Openness", False),
    ("I see myself as someone who has few artistic interests.", "Openness", True),
    ("I see myself as someone who is sophisticated in art, music, or literature.", "Openness", False)
]

    per_page = math.ceil(len(bfi_questions) / total_pages)
    start = (st.session_state.page - 1) * per_page
    end = start + per_page

    with st.form(f"personality_form_{st.session_state.page}"):
        page_responses = []
        for q, _, _ in bfi_questions[start:end]:
            page_responses.append(st.slider(q, 1, 5, 3))
        submitted = st.form_submit_button("Next" if st.session_state.page < total_pages else "Submit")

    if submitted:
        st.session_state.responses.extend(page_responses)
        if st.session_state.page < total_pages:
            st.session_state.page += 1
        else:
            # スコア計算
            traits = {t: 0 for _, t, _ in bfi_questions}
            trait_counts = {t: 0 for t in traits}
            for r, (q, t, rev) in zip(st.session_state.responses, bfi_questions):
                score = 6 - r if rev else r
                traits[t] += score
                trait_counts[t] += 1

            scores = {t: round((traits[t] / trait_counts[t]) * 20) for t in traits}
            responses_json = json.dumps(dict(zip([q for q, _, _ in bfi_questions], st.session_state.responses)))

            row = [
                user_name,
                st.session_state.session_id,
                st.session_state.experiment_condition,
                scores["Extraversion"], scores["Agreeableness"], scores["Conscientiousness"],
                scores["Emotional Stability"], scores["Openness"],
                responses_json
            ]
            safe_append(profile_sheet, row)

            # 結果表示
            st.success("Profile saved!")
            st.write("Your Personality Scores:", scores)
            st.write("Interpretation:")
            for trait, score in scores.items():
                st.write(f"{trait}: {interpret_trait(trait, score)}")

            #チャット画面に進む用のボタン
            if st.button("Proceed to Chat"):
                page = "Chat"
                st.experimental_rerun()


# === Chatページ ===
if page == "Chat Session":
    st.title(f"Chatbot - {user_name}")
    profile = get_profile(user_name)
    if not profile:
        st.error("No personality profile found. Please take the personality test first.")
        st.stop()

    user_input = st.chat_input("Your message...")
    if user_input:
        st.session_state.turn_index += 1
        context = "\n".join([f"{msg['role']}: {msg['content']}" for msg in st.session_state.chat_history[-4:]])


        # モード設定
        if st.session_state.get("experiment_condition") == "Fixed Empathy":
            tone_instruction = "Respond in a calm, supportive tone, like a counselor."
        else:
            tone_data = determine_tone(profile, match=(st.session_state.turn_index >= 30))
            tone_instruction = (
                f"Respond in a {tone_data['tone']}, {tone_data['empathy']} way. "
                f"Keep tone {tone_data['emotional']} and include {tone_data['creativity']} ideas. "
                f"{tone_data['special_instruction']}"
            )


        crisis_msg = handle_crisis(user_input)
        if crisis_msg:
            ai_reply = crisis_msg
        else:
            profile_summary = ", ".join([
                f"Extraversion={profile.get('Extraversion', 'N/A')}",
                f"Agreeableness={profile.get('Agreeableness', 'N/A')}",
                f"Conscientiousness={profile.get('Conscientiousness', 'N/A')}",
                f"Emotional Stability={profile.get('Emotional Stability', 'N/A')}",
                f"Openness={profile.get('Openness', 'N/A')}"
            ])


            prompt = f"""
You are a warm, supportive mental health assistant.
Reflect this personality style: {tone_instruction}.
Write a natural, conversational response in 2–3 sentences:
- Acknowledge the user's concern using their own words.
- Ask ONE relevant question to keep the conversation going.
Avoid sounding like a list. Make it flow like a real chat.
-Suggest ONE practical coping tip based on their personality ({profile_summary}) and briefly explain why it helps.
Avoid phrases like "I understand" or "That sounds tough".
Keep it empathetic, practical, and conversational.
Conversation so far:
{context}
User: {user_input}
Assistant:
"""

        ai_reply = call_api(prompt) or "The system could not generate a response. Try again. If that doesn't work conatact Ryosuke Komatsu"

        st.session_state.chat_history.append({"role": "User", "content": user_input})
        st.session_state.chat_history.append({"role": "AI", "content": ai_reply})

        log_chat_to_sheet(
            user=user_name,
            session_id=st.session_state.session_id,
            turn_index=st.session_state.turn_index,
            user_msg=user_input,
            ai_msg=ai_reply
        )

        # アンケートリンクを一元管理
        survey_links = {
        "initial": "https://forms.gle/PtfRCrwwVfrGuxEQ9",
        "30": "https://forms.gle/aDpHpj15gxWfu24s6",
        "60": "https://forms.gle/8byChpdXQS4azgXH6",
        "90": "https://forms.gle/PB9JVdD5jmytwxTJA"
        }

        # 回数ベースでアンケートを案内（30, 60, 90ターン）
        turn = st.session_state.turn_index
        for milestone in [30, 60, 90]:
            key = str(milestone)
            if turn == milestone and not st.session_state.survey_prompts_shown.get(key, False):
                st.session_state.survey_prompts_shown[key] = True
                st.warning(f"You've reached {milestone} messages! We’d appreciate it if you could fill out a quick follow-up survey.")
                st.markdown(f"[Click here for the {milestone}th message survey]({survey_links[key]})")


    for msg in st.session_state.chat_history:
        st.chat_message(msg["role"].lower()).write(msg["content"])

# === Admin Debug Panel ===
if user_name.lower() == "admin":
    st.sidebar.markdown("### Debug Panel")
    st.sidebar.write(f"Your Condition: {st.session_state['experiment_condition']}")
    st.sidebar.write(f"Match Mode: {st.session_state.get('matched_mode', False)}")
    
    # 追加: 全ユーザー一覧表示
    st.sidebar.subheader("All Users")
    all_profiles = profile_sheet.get_all_records()  # ←これでOK
    for p in all_profiles:
        st.sidebar.write(f"{p['Username']} | Condition: {p.get('ExperimentCondition', 'N/A')} | Match: {p.get('MatchMode', 'N/A')}")

    # （既存の Admin Debug Panel の最後に 追記）
    st.sidebar.subheader("Simulation (Big5Chat)")
    sim_users = st.sidebar.number_input("Number of simulated users", min_value=1, max_value=50, value=3, step=1)
    sim_step  = st.sidebar.slider("Trait match window (±)", min_value=5, max_value=20, value=10, step=1)
    sim_turns = st.sidebar.slider("Turns per user", min_value=30, max_value=120, value=60, step=10)
    if st.sidebar.button("Run Big5Chat Simulation"):
        with st.spinner("Simulating conversations from Big5Chat..."):
            simulate_many_users(n_users=int(sim_users), step=int(sim_step), min_turns=int(sim_turns))
