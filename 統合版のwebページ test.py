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
def generate_persona_prompt(profile, match=True):
    ex = int(profile.get("Extraversion", 50))
    ag = int(profile.get("Agreeableness", 50))
    es = int(profile.get("Emotional Stability", 50))
    op = int(profile.get("Openness", 50))
    co = int(profile.get("Conscientiousness", 50))

    # Fixed Empathy condition
    if st.session_state.experiment_condition == "Fixed Empathy":
        style = random.choice(["Counselor", "Casual"])
        if style == "Counselor":
            return ("You are a professional counselor. Always structure your answer exactly as: "
                    "(1) Empathy, (2) Reflective Question?, (3) Practical Suggestion. "
                    "Example: 'I understand how hard this feels. How do you usually cope with stress? "
                    "One idea is to break tasks into smaller steps.' "
                    "Keep responses concise (max 2 sentences unless user requests more).")

        else:
            return ("You are a friendly and casual friend. Use this flow: "
                    "Empathy. Light humor. Suggestion. "
                    "Keep it short (max 2 sentences).")

    # Personalized (Match or Non-Match)
    if match:
        empathy = "Show deep empathy warmly." if ag >= 60 else "Show light empathy with practicality."
        reassurance = "Offer reassurance often." if es < 40 else "Encourage optimism gently."
        tone = "Be highly energetic and positive." if ex >= 70 else "Be calm and steady."
        creativity = "Use creative, imaginative examples." if op >= 60 else "Stay practical and simple."
        structure = "Give structured advice." if co >= 60 else "Keep advice flexible and simple."
    else:
        empathy = "Respond with minimal empathy, blunt and factual."
        reassurance = "Do not offer emotional reassurance."
        tone = "Keep tone cold and detached. Do not ask questions."
        creativity = "Avoid creativity; stay rigid."
        structure = "Avoid giving structured advice."

    return (f"You are an AI assistant. {empathy} {reassurance} {tone} {creativity} {structure} "
            f"Respond in 2 short sentences unless asked for details.")


# === 応答生成 ===
def generate_response(user_input):
    profile = get_profile(user_name)
    history_len = len(st.session_state.chat_history) // 2
    if not st.session_state["matched_mode"] and history_len >= MAX_NONMATCH_ROUNDS:
        st.session_state["matched_mode"] = True

    persona = generate_persona_prompt(profile, match=st.session_state["matched_mode"])
    prompt = (f"EXPERIMENT CONDITION: {st.session_state.experiment_condition}, "
              f"MATCH: {st.session_state['matched_mode']}\n"
              f"{persona}\nUser: {user_input}\nAssistant:")

    try:
        detail_flag = any(kw in user_input.lower() for kw in ["tell me more", "explain", "more detail"])
        max_tokens = 150 if detail_flag else 80
        
        response = requests.post(
            "https://royalmilktea103986368-dissintation.hf.space/generate",
            json={"prompt": prompt, "max_tokens": max_tokens, "temperature": 0.7},
            timeout=60
        )
        result = response.json().get("response", "")
        # 応答を2文に制御
        sentences = result.replace("[END]", "").strip().split('.')
        return '. '.join(s.strip() for s in sentences[:2] if s.strip()) + '.'
    except Exception as e:
        return "Sorry, the assistant is currently unavailable."

# === Personality Test ===
if page == "Personality Test":
    st.title("Big Five Personality Test")
    questions = [("I make friends easily", "Extraversion", False), ("I am the life of the party", "Extraversion", False),
                 ("I don't talk a lot", "Extraversion", True), ("I keep in the background", "Extraversion", True),
                 ("I sympathize with others' feelings", "Agreeableness", False),
                 ("I feel others’ emotions", "Agreeableness", False),
                 ("I am not interested in other people's problems", "Agreeableness", True),
                 ("I insult people", "Agreeableness", True),
                 ("I get chores done right away", "Conscientiousness", False),
                 ("I follow a schedule", "Conscientiousness", False),
                 ("I often forget to put things back in their proper place", "Conscientiousness", True),
                 ("I make a mess of things", "Conscientiousness", True),
                 ("I am relaxed most of the time", "Emotional Stability", False),
                 ("I seldom feel blue", "Emotional Stability", False),
                 ("I get upset easily", "Emotional Stability", True),
                 ("I worry about things", "Emotional Stability", True),
                 ("I have a vivid imagination", "Openness", False), ("I am full of ideas", "Openness", False),
                 ("I am not interested in abstract ideas", "Openness", True),
                 ("I do not have a good imagination", "Openness", True)]

    responses = []
    with st.form("personality_form"):
        st.write("Rate each statement from 1 (Disagree) to 5 (Agree):")
        for q, _, _ in questions:
            responses.append(st.slider(q, 1, 5, 3))
        submitted = st.form_submit_button("Submit")

    if submitted:
        traits = {t: 0 for _, t, _ in questions}
        trait_counts = {t: 0 for t in traits}
        for r, (q, t, rev) in zip(responses, questions):
            traits[t] += 6 - r if rev else r
            trait_counts[t] += 1
        row = [user_name, st.session_state.session_id, st.session_state.experiment_condition, 
               round(traits)["Extraversion"]/trait_counts["Extraversion"] *20 ,
               round(traits["Agreeableness"]/trait_counts["Agreeableness"]*20),
               round(traits["Conscientiousness"]/trait_counts["Conscientiousness"]*20),
               round(traits["Emotional Stability"]/trait_counts["Emotional Stability"]*20),
               round(traits["Openn  ess"]/trait_counts["Openness"]*20),]
        safe_append(profile_sheet, row)
        st.success("Profile saved! You can now proceed to chat.")
        st.session_state["completed_test"] = True

if page == "Chat":
    st.title(f"Chatbot - {user_name}")
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    profile = get_profile(user_name)
    if not profile:
        st.error("No profile found. Please take the test first.")
        st.stop()

    

    user_input = st.chat_input("Your message")
    if user_input:
        st.session_state.turn_index += 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        ai_reply = generate_response(user_input)
        st.session_state.chat_history.append({"role": "User", "content": user_input})
        st.session_state.chat_history.append({"role": "AI", "content": ai_reply})

    # --- シート取得または作成 ---
    tab_name = f"{user_name}_{'Match' if st.session_state['matched_mode'] else 'NoMatch'}"
    user_sheet = get_or_create_worksheet(spreadsheet, tab_name)

    # === ユーザー発話ログ ===
    safe_append(user_sheet, [
        st.session_state.session_id,
        user_name, "user", user_input, now,
        st.session_state["experiment_condition"],
        st.session_state.get("matched_mode", False),
        profile.get("Extraversion"),
        profile.get("Agreeableness"),
        profile.get("Conscientiousness"),
        profile.get("Emotional Stability"),
        profile.get("Openness")
    ])

    #   === AI応答ログ ===
    safe_append(user_sheet, [
        st.session_state.session_id,
        user_name, "bot", ai_reply, now,
        st.session_state["experiment_condition"],
        st.session_state.get("matched_mode", False),
        profile.get("Extraversion"),
        profile.get("Agreeableness"),
        profile.get("Conscientiousness"),
        profile.get("Emotional Stability"),
        profile.get("Openness")
    ])




    for msg in st.session_state.chat_history:
        st.chat_message(msg["role"].lower()).write(msg["content"])

# === Admin Debug Panel ===
if user_name.lower() == "admin":
    st.sidebar.markdown("### Debug Panel")
    st.sidebar.write(f"Your Condition: {st.session_state['experiment_condition']}")
    st.sidebar.write(f"Match Mode: {st.session_state.get('matched_mode', False)}")
    
    # 追加: 全ユーザー一覧表示
    st.sidebar.subheader("All Users")
    all_profiles = profile_sheet.get_all_records(expected_headers=[
    "Username", "SessionID", "ExperimentCondition",
    "Extraversion", "Agreeableness", "Conscientiousness",
    "Emotional Stability", "Openness"
    ])
    for p in all_profiles:
        st.sidebar.write(f"{p['Username']} | Condition: {p.get('ExperimentCondition', 'N/A')} | Match: {p.get('MatchMode', 'N/A')}")
