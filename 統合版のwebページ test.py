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

# === セッション管理 ===
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "turn_index" not in st.session_state:
    st.session_state.turn_index = 0

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

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

# === Big Fiveトーン ===
def determine_tone(profile, match=True):
    def flip(value):
        if value >= 60: return 20
        if value <= 40: return 80
        return 50

    def adjusted(trait):
        val = int(profile.get(trait, 50))
        return val if match else flip(val)

    ex, ag, co, es, op = [adjusted(t) for t in ["Extraversion", "Agreeableness", "Conscientiousness", "Emotional Stability", "Openness"]]

    tone = "cheerful and engaging" if ex >= 60 else "calm and measured"
    empathy = "warm and supportive" if ag >= 60 else "matter-of-fact and minimal empathy"
    style = "clear and structured" if co >= 60 else "casual and flexible"
    emotional = "steady and reassuring" if es >= 60 else "slightly anxious"
    creativity = "curious and imaginative" if op >= 60 else "practical and focused"

    return tone, empathy, style, emotional, creativity

def generate_personalized_suggestion(profile):
    ex = int(profile.get("Extraversion", 50))
    ag = int(profile.get("Agreeableness", 50))
    co = int(profile.get("Conscientiousness", 50))
    es = int(profile.get("Emotional Stability", 50))
    op = int(profile.get("Openness", 50))

    if ex >= 60:
        return "Call or meet a friend for a quick chat to feel connected."
    elif op >= 60:
        return "Try journaling or a creative activity to express yourself."
    elif co >= 60:
        return "Make a simple to-do list to regain control and focus."
    elif ag >= 60:
        return "Reach out to someone you trust for emotional support."
    else:
        return "Start with a calming breathing exercise to ease your mind."


# === 危機対応 ===
def handle_crisis(user_input):
    keywords = ["suicide", "kill myself", "end my life", "self-harm"]
    if any(kw in user_input.lower() for kw in keywords):
        return "I'm really sorry you're feeling this way. You're not alone. Please contact someone you trust or a hotline."
    return None

def build_prompt(user_input, context, tone_instruction, suggestion):
    return f"""
You are a supportive assistant for mental well-being.
STRICTLY follow this personality style: {tone_instruction}
Keep it warm and natural. Avoid generic empathy like "I understand".
Write exactly 2–3 sentences: acknowledge concern, ask a question, and suggest this action: {suggestion}.
Explain briefly why it helps. Do NOT repeat these instructions.

Conversation so far:
{context}
User's latest message: {user_input}
Assistant:
""".strip()


def call_api(prompt):
    API_URL = "https://royalmilktea103986368-dissintation.hf.space/generate"
    payload = {"prompt": prompt, "max_tokens": 150, "temperature": 0.7, "top_p": 0.9}
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

# ✅ experiment_condition 初期化
if "experiment_condition" not in st.session_state:
    # 新規ユーザーを交互に分ける（固定ロジック）
    all_profiles = profile_sheet.get_all_records()
    st.session_state.experiment_condition = "Fixed Empathy" if len(all_profiles) % 2 == 0 else "Personalized Empathy"

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
            tone, empathy, style, emotional, creativity = determine_tone(profile, match=(st.session_state.turn_index >= 30))
            tone_instruction = f"Respond in a {tone}, {empathy} way. Keep tone {emotional} and include {creativity} ideas."

        suggestion = generate_personalized_suggestion(profile)

        crisis_msg = handle_crisis(user_input)
        if crisis_msg:
            ai_reply = crisis_msg
        else:
            prompt = build_prompt(user_input, context, tone_instruction, suggestion)
            ai_reply = call_api(prompt) or "The system could not generate a response. Try again later."

        st.session_state.chat_history.append({"role": "User", "content": user_input})
        st.session_state.chat_history.append({"role": "AI", "content": ai_reply})


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