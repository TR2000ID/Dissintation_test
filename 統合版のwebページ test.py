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
    # Convert Big Five scores to High/Moderate/Low
    def trait_level(score):
        if score >= 60: return "High"
        elif score >= 40: return "Moderate"
        return "Low"

    ex_level = trait_level(int(profile.get("Extraversion", 50)))
    ag_level = trait_level(int(profile.get("Agreeableness", 50)))
    co_level = trait_level(int(profile.get("Conscientiousness", 50)))
    es_level = trait_level(int(profile.get("Emotional Stability", 50)))
    op_level = trait_level(int(profile.get("Openness", 50)))

    # Global Safety & Ethical Rules
    safety_instructions = (
        "IMPORTANT SAFETY RULES:\n"
        "- Do NOT provide medical diagnosis or medication advice.\n"
        "- Do NOT include medication names or suggest treatment changes.\n"
        "- Do NOT make assumptions about user's trauma or abuse history.\n"
        "- Do NOT provide legal or financial advice.\n"
        "- If user mentions self-harm or suicide, do NOT provide coping tips. "
        "Instead, respond with empathy and refer to crisis helplines.\n"
    )

    # Fixed Empathy Mode
    if st.session_state.experiment_condition == "Fixed Empathy":
        return (
            f"{safety_instructions}\n"
            "You are a professional mental health counselor.\n"
            "Your response MUST strictly follow this format:\n"
            "(1) Empathy: [One short empathetic sentence]\n"
            "(2) Question: [One reflective question]\n"
            "(3) Suggestion: [One practical coping tip]\n"
            "Keep it concise (max 3 short sentences). Avoid medical terms."
        )

    # Personalized Mode
    if match:
        # Tone
        if ex_level == "High":
            tone_instruction = "Speak in an upbeat, lively, and motivating manner."
        elif ex_level == "Moderate":
            tone_instruction = "Speak in a friendly, balanced, and reassuring tone."
        else:
            tone_instruction = "Speak in a calm, steady, and soothing manner."

        # Empathy
        if ag_level == "High":
            empathy_instruction = "Show strong warmth and emotional understanding."
        elif ag_level == "Moderate":
            empathy_instruction = "Show moderate empathy with supportive tone."
        else:
            empathy_instruction = "Show minimal empathy, keep it factual but kind."

        # Structure
        if co_level == "High":
            structure_instruction = "Give clear, structured, step-by-step advice."
        elif co_level == "Moderate":
            structure_instruction = "Give moderately structured guidance."
        else:
            structure_instruction = "Give flexible, open-ended advice."

        # Optimism
        if es_level == "High":
            optimism_instruction = "Include light optimism, avoid overpromising."
        elif es_level == "Moderate":
            optimism_instruction = "Encourage optimism in a balanced manner."
        else:
            optimism_instruction = "Provide frequent reassurance and comfort."

        # Creativity
        if op_level == "High":
            creativity_instruction = "Include creative and imaginative coping ideas."
        elif op_level == "Moderate":
            creativity_instruction = "Mix practical tips with small creative suggestions."
        else:
            creativity_instruction = "Stick to practical, evidence-based advice."

        return (
            f"{safety_instructions}\n"
            "You are an AI mental health support assistant.\n"
            "Adapt your response based on the user's personality traits:\n"
            f"- {tone_instruction}\n"
            f"- {empathy_instruction}\n"
            f"- {structure_instruction}\n"
            f"- {optimism_instruction}\n"
            f"- {creativity_instruction}\n\n"
            "Your response MUST strictly follow this format:\n"
            "(1) Empathy: [Short empathetic sentence]\n"
            "(2) Question: [One reflective question]\n"
            "(3) Suggestion: [One practical coping tip]\n"
            "Keep answers concise (max 3 short sentences). Avoid medical terms."
        )

    # Non-Match Mode
    return (
        f"{safety_instructions}\n"
        "Respond in a direct and practical tone. No empathy or emotional language.\n"
        "Give one simple coping tip only.\n"
        "Example: 'Focus on one task at a time and take short breaks.'"
    )


def generate_response(user_input):
    # Crisis Detection
    crisis_keywords = [
        "suicide", "kill myself", "end my life", "self-harm",
        "can't go on", "hopeless", "worthless", "life is meaningless", "give up"
    ]
    if any(kw in user_input.lower() for kw in crisis_keywords):
        st.error("⚠ Crisis detected: Providing emergency response information.")
        return (
            "(1) Empathy: I'm really sorry you're feeling this way. You are not alone.\n"
            "(2) Important: If you are in danger or thinking about self-harm, please reach out immediately.\n"
            "(3) Helplines: In the US, call 988. In the UK, call Samaritans at 116 123. "
            "If elsewhere, search for your local crisis hotline."
        )

    # Medication or diagnosis request detection
    prohibited_keywords = ["diagnose", "diagnosis", "medication", "antidepressant", "pill", "prescribe"]
    if any(kw in user_input.lower() for kw in prohibited_keywords):
        return (
            "(1) Empathy: I understand your concern.\n"
            "(2) Note: I cannot provide medical diagnosis or medication advice.\n"
            "(3) Suggestion: Please consult a licensed healthcare professional for these matters."
        )

    # Randomized, evidence-based coping suggestions (WHO & CBT)
    coping_strategies = [
        "Try slow, deep breathing for 2 minutes.",
        "Write down three things you are grateful for.",
        "Take a short walk or stretch for 5 minutes.",
        "Use the 5-4-3-2-1 grounding technique: Name 5 things you see, 4 you feel, 3 you hear, 2 you smell, 1 you taste.",
        "Break a big task into smaller steps and start with one easy task.",
        "Listen to a calming playlist or nature sounds for 5 minutes."
    ]
    selected_tip = random.choice(coping_strategies)

    # Generate response with API
    with st.spinner("Generating response... Please wait."):
        profile = get_profile(user_name)
        history_len = len(st.session_state.chat_history) // 2
        if not st.session_state["matched_mode"] and history_len >= MAX_NONMATCH_ROUNDS:
            st.session_state["matched_mode"] = True

        persona_prompt = generate_persona_prompt(profile, match=st.session_state["matched_mode"])
        prompt = (
            f"EXPERIMENT CONDITION: {st.session_state.experiment_condition}, "
            f"MATCH: {st.session_state['matched_mode']}\n"
            f"{persona_prompt}\nUser: {user_input}\nAssistant:"
        )

        detail_flag = any(kw in user_input.lower() for kw in ["tell me more", "explain", "more detail"])
        max_tokens = 150 if detail_flag else 120

        for attempt in range(3):  # Retry loop
            try:
                response = requests.post(
                    "https://royalmilktea103986368-dissintation.hf.space/generate",
                    json={"prompt": prompt, "max_tokens": max_tokens, "temperature": 0.4},
                    timeout=90
                )

                if response.status_code != 200:
                    st.warning(f"API Error: {response.status_code} on attempt {attempt+1}")
                    continue

                data = response.json()
                result = data.get("response", "").strip()

                # Validate format
                lines = [l.strip() for l in result.splitlines() if l.strip()]
                if (
                    len(lines) == 3 and
                    all(tag in lines[i] for i, tag in enumerate(["(1)", "(2)", "(3)"])) and
                    all(10 < len(line) < 120 for line in lines)
                ):
                    return result

                # Prompt refinement if invalid
                prompt += (
                    "\nYour previous response did NOT follow the required format."
                    "Retry and include ALL of these: (1), (2), (3)."
                    "Do NOT repeat invalid or incomplete answers."
                )

            except Exception as e:
                st.error(f"Exception on attempt {attempt+1}: {e}")
                continue

        # Final fallback response
        return (
            f"(1) Empathy: I understand this is difficult.\n"
            f"(2) Question: What usually helps you feel a bit better?\n"
            f"(3) Suggestion: {selected_tip}"
        )


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
