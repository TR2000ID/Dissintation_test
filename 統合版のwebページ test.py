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
existing_users = [row["Username"] for row in profile_sheet.get_all_records()]

# === ユーザー認証 ===
if "user_name" not in st.session_state:
    st.session_state.user_name = ""

if st.session_state.user_name == "":
    st.session_state.user_name = st.sidebar.text_input("Enter your username")
    if not st.session_state.user_name:
        st.warning("Please enter your username.")
        st.stop()
else:
    st.sidebar.markdown(f"**Welcome, {st.session_state.user_name}!**")

user_name = st.session_state.user_name
page = "Chat" if user_name in existing_users else "Personality Test"

# === セッション管理 ===
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "turn_index" not in st.session_state:
    st.session_state.turn_index = 0

if "experiment_condition" not in st.session_state:
    st.session_state.experiment_condition = random.choice(["Fixed Empathy", "Personalized Empathy"])

if "matched_mode" not in st.session_state:
    st.session_state["matched_mode"] = False

# === エラーハンドリング付きGoogle Sheets書き込み ===
def safe_append(sheet, row, retries=3, delay=2):
    for i in range(retries):
        try:
            sheet.append_row(row)
            return
        except gspread.exceptions.APIError:
            time.sleep(delay * (i + 1))
    st.error("Failed to log data after multiple retries.")

def get_or_create_worksheet(spreadsheet, title, rows=100, cols=20):
    """
    指定したタイトルのワークシートが存在すれば取得し、
    存在しなければ新規作成してヘッダー行を追加する。
    """
    try:
        # 既存のシートを取得
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        # シートがなければ作成
        ws = spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)
        # ヘッダー行を追加
        ws.append_row([
            "SessionID", "Username", "Role", "Message", "Timestamp",
            "ExperimentCondition", "MatchedMode",
            "Extraversion", "Agreeableness", "Conscientiousness",
            "Emotional Stability", "Openness"
        ])
        return ws


# === パラメータ ===
MAX_NONMATCH_ROUNDS = 30

# === Big Five結果取得 ===
def get_profile(user):
    for row in profile_sheet.get_all_records():
        if row["Username"] == user:
            return row
    return None

# === Personaプロンプト生成 ===
import requests
import streamlit as st
import json

def trait_level(score):
    if score >= 60: return "High"
    elif score >= 40: return "Moderate"
    else: return "Low"

def generate_persona_prompt(profile, match=True):
    def level(score):
        return "High" if score >= 60 else "Moderate" if score >= 40 else "Low"

    ex, ag, co, es, op = [level(int(profile.get(trait, 50))) for trait in
                          ["Extraversion", "Agreeableness", "Conscientiousness", "Emotional Stability", "Openness"]]

    base_rules = (
        "Follow these STRICT rules:\n"
        "1. Response MUST have 3 parts:\n"
        "(1) Empathy\n(2) Reflective Question\n(3) Practical Suggestion\n"
        "2. Avoid medical, legal, or financial advice.\n"
        "3. If user mentions self-harm → Give helpline info.\n"
        "4. Avoid repetitive phrases; vary tone.\n"
        "Keep response natural and human-like (max 3 sentences).\n"
    )

    if not match:
        return base_rules + "Respond in a neutral, practical tone with one coping tip only."

    tone = "Upbeat and motivating" if ex == "High" else "Friendly and balanced" if ex == "Moderate" else "Calm and reassuring"
    empathy = "Show warmth and understanding" if ag != "Low" else "Keep empathy minimal but respectful"
    structure = "Clear, step-by-step advice" if co == "High" else "Moderate structure" if co == "Moderate" else "Flexible suggestions"
    optimism = "Encourage optimism" if es != "Low" else "Provide frequent reassurance"
    creativity = "Include creative coping ideas" if op == "High" else "Mix practical and creative" if op == "Moderate" else "Stick to practical tips"

    return (
        f"{base_rules}\n"
        "Tone and style settings:\n"
        f"- Tone: {tone}\n"
        f"- Empathy: {empathy}\n"
        f"- Structure: {structure}\n"
        f"- Optimism: {optimism}\n"
        f"- Creativity: {creativity}\n"
        "IMPORTANT: Do NOT skip (1)(2)(3). Each must be unique and context-aware."
    )

import difflib
def generate_response(user_input):
    # Crisis handling
    crisis_keywords = ["suicide", "kill myself", "end my life", "self-harm"]
    if any(kw in user_input.lower() for kw in crisis_keywords):
        return (
            "(1) Empathy: I'm really sorry you're feeling this way. You are not alone.\n"
            "(2) Important: Please contact someone you trust or a crisis hotline immediately.\n"
            "(3) Helpline: In the US, dial 988. In the UK, call Samaritans at 116 123."
        )

    prohibited_keywords = ["diagnose", "diagnosis", "medication", "antidepressant", "pill", "prescribe"]
    if any(kw in user_input.lower() for kw in prohibited_keywords):
        return (
            "(1) Empathy: I understand your concern.\n"
            "(2) Question: Have you consulted a healthcare professional before?\n"
            "(3) Suggestion: For your safety, please seek professional medical advice."
        )

    profile = get_profile(user_name)
    persona_prompt = generate_persona_prompt(profile, match=st.session_state["matched_mode"])
    prompt = f"{persona_prompt}\nUser: {user_input}\nAssistant:"

    fallback_responses = [
        "(1) Empathy: That sounds challenging.\n(2) Question: What small step could help right now?\n(3) Suggestion: Try a short breathing exercise.",
        "(1) Empathy: I hear how tough this feels for you.\n(2) Question: What would make this a little easier?\n(3) Suggestion: Take a quick break and stretch for 5 minutes.",
        "(1) Empathy: I'm here for you.\n(2) Question: What usually helps when you feel like this?\n(3) Suggestion: Write down three positive things from today."
    ]

    for attempt in range(3):
        try:
            response = requests.post(
                "https://royalmilktea103986368-dissintation.hf.space/generate",
                json={"prompt": prompt, "max_tokens": 180, "temperature": 0.85, "top_p": 0.9},
                timeout=60
            )
            if response.status_code != 200:
                time.sleep(1.5 * (attempt + 1))  # Backoff
                continue

            result = response.json().get("response", "").strip()
            if all(tag in result for tag in ["(1)", "(2)", "(3)"]):
                return result

            # If partial tags missing → add fallback structure
            return result + "\n\n" + random.choice(fallback_responses)

        except requests.Timeout:
            st.warning(f"Attempt {attempt+1}: API timeout.")
        except Exception as e:
            st.error(f"Attempt {attempt+1} failed: {e}")

    # Final fallback
    return random.choice(fallback_responses)




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

if page == "Chat":
    st.title(f"Chatbot - {user_name}")

    # チャット履歴初期化
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    # ユーザープロフィール取得
    profile = get_profile(user_name)
    if not profile:
        st.error("No profile found. Please take the test first.")
        st.stop()

    # ユーザー入力受付
    user_input = st.chat_input("Your message")

    if user_input:
        st.session_state.turn_index += 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        ai_reply = generate_response(user_input)
        st.session_state.chat_history.append({"role": "User", "content": user_input})
        st.session_state.chat_history.append({"role": "AI", "content": ai_reply})

        # 個別タブ
        tab_name = f"{user_name}_{'Match' if st.session_state['matched_mode'] else 'NoMatch'}"
        user_sheet = get_or_create_worksheet(spreadsheet, tab_name)

        # 個別ログ＋共通ログ
        for role, message in [("user", user_input), ("bot", ai_reply)]:
            safe_append(user_sheet, [
                st.session_state.session_id, user_name, role, message, now,
                st.session_state["experiment_condition"], st.session_state.get("matched_mode", False),
                profile.get("Extraversion", ""), profile.get("Agreeableness", ""), profile.get("Conscientiousness", ""),
                profile.get("Emotional Stability", ""), profile.get("Openness", "")
            ])
            safe_append(chat_sheet, [
                st.session_state.session_id, user_name, role, message, now,
                st.session_state["experiment_condition"], st.session_state.get("matched_mode", False),
                profile.get("Extraversion", ""), profile.get("Agreeableness", ""), profile.get("Conscientiousness", ""),
                profile.get("Emotional Stability", ""), profile.get("Openness", "")
            ])

    # チャット履歴を常に表示
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