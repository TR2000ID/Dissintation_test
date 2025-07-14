import streamlit as st
import gspread
import json
import tempfile
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel
import torch
import requests

def load_model():
    model_name = "mistralai/Mistral-7B-Instruct-v0.3"
    adapter_path = "/content/drive/MyDrive/nous-hermes-mental-lora_2" 

    base_model = AutoModelForCausalLM.from_pretrained(model_name, device_map="auto")
    tokenizer = AutoTokenizer.from_pretrained(model_name)

    tokenizer.pad_token = tokenizer.pad_token or tokenizer.eos_token
    tokenizer.eos_token = tokenizer.eos_token or tokenizer.pad_token
    tokenizer.bos_token = tokenizer.bos_token or tokenizer.eos_token

    model = PeftModel.from_pretrained(base_model, adapter_path)
    model.eval()

    return model, tokenizer

model, tokenizer = load_model()

# === Google Sheets 認証 ===
creds_dict = st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"].to_dict()
creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".json") as tmp:
    json.dump(creds_dict, tmp)
    tmp_path = tmp.name

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials = ServiceAccountCredentials.from_json_keyfile_name(tmp_path, scope)
client = gspread.authorize(credentials)

# === Google Sheets 接続 ===
chat_sheet = client.open_by_key("1XpB4gzlkOS72uJMADmSIuvqECM5Ud8M-KwwJbXSxJxM").worksheet("Chat")
profile_sheet = client.open_by_key("1XpB4gzlkOS72uJMADmSIuvqECM5Ud8M-KwwJbXSxJxM").worksheet("Personality")
existing_users = [row["Username"] for row in profile_sheet.get_all_records()]


# === ユーザー認証（サイドバー）===
st.sidebar.title("User Login")

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

# === ユーザー専用のチャットシートを取得 or 作成 ===
spreadsheet = client.open_by_key("1XpB4gzlkOS72uJMADmSIuvqECM5Ud8M-KwwJbXSxJxM")

# === ユーザー専用のログシート：不一致・一致 で分離 ===
try:
    mismatch_sheet = spreadsheet.worksheet(f"{user_name}_nonmatch")
except gspread.exceptions.WorksheetNotFound:
    mismatch_sheet = spreadsheet.add_worksheet(title=f"{user_name}_nonmatch", rows="1000", cols="4")
    mismatch_sheet.append_row(["Username", "Role", "Message", "Timestamp"])

try:
    match_sheet = spreadsheet.worksheet(f"{user_name}_match")
except gspread.exceptions.WorksheetNotFound:
    match_sheet = spreadsheet.add_worksheet(title=f"{user_name}_match", rows="1000", cols="4")
    match_sheet.append_row(["Username", "Role", "Message", "Timestamp"])



# === 質問リスト20問版 ===
questions = [
    ("I make friends easily", "Extraversion", False),
    ("I am the life of the party", "Extraversion", False),
    ("I don't talk a lot", "Extraversion", True),
    ("I keep in the background", "Extraversion", True),

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

    ("I have a vivid imagination", "Openness", False),
    ("I am full of ideas", "Openness", False),
    ("I am not interested in abstract ideas", "Openness", True),
    ("I do not have a good imagination", "Openness", True)
]

# === ユーザー認証 ===
st.sidebar.title("User Login")
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

# === パーソナリティテスト ===
MAX_NONMATCH_ROUNDS = 30

def get_profile(user):
    for row in profile_sheet.get_all_records():
        if row["Username"] == user:
            return row
    return None

def generate_persona_prompt(profile, match=True):
    ex = int(profile["Extraversion"])
    if match:
        if ex >= 70:
            return "You are an outgoing and encouraging AI."
        elif ex >= 50:
            return "You are a friendly and engaging AI."
        elif ex >= 30:
            return "You are a calm and reflective AI."
        else:
            return "You are a gentle and listening AI."
    else:
        # わざとズレたプロンプト
        if ex >= 70:
            return "You are a quiet and reserved AI."
        elif ex >= 50:
            return "You are a minimalistic and blunt AI."
        elif ex >= 30:
            return "You are an energetic and humorous AI."
        else:
            return "You are a highly talkative and loud AI."

def generate_response(user_input):
    profile = get_profile(user_name)
    history_len = len(st.session_state.chat_history) // 2  # 1往復＝2行
    persona = get_chatbot_style(profile, history_len)
    prompt = f"{persona}\n{user_input}"

    try:
        response = requests.post(
            "https://huggingface.co/spaces/RoyalMilkTea103986368/Dissintation/api/predict/",
            json={"data": [prompt]},
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        return result["data"][0]  # Spaces標準構造 {"data": [返答]}
    except Exception as e:
        print("Error:", e)
        return "Sorry, the assistant is currently unavailable."


def get_chatbot_style(profile, history_len):
    # 明示的にマッチモードがONなら一致型を返す
    if st.session_state.get("matched_mode", False):
        return generate_persona_prompt(profile, match=True)
    
    # 非一致期間中（最初の30ターン）
    if history_len < MAX_NONMATCH_ROUNDS:
        return generate_persona_prompt(profile, match=False)
    
    # 30ターンを超えても未切替（案内済みで保留中）
    return generate_persona_prompt(profile, match=False)

if page == "Personality Test":
    st.title("Big Five Personality Test")
    responses = []

    with st.form("personality_form"):
        st.write("Rate from 1 (Disagree) to 5 (Agree)")
        for q, _, _ in questions:
            responses.append(st.slider(q, 1, 5, 3))
        submitted = st.form_submit_button("Submit")

    if submitted:
        traits = {t: 0 for _, t, _ in questions}
        trait_counts = {t: 0 for t in traits}
        for r, (q, t, rev) in zip(responses, questions):
            traits[t] += 6 - r if rev else r
            trait_counts[t] += 1

        st.subheader("Your Personality Results")
        row = [user_name]
        for trait in traits:
            avg = traits[trait] / trait_counts[trait] * 20
            st.write(f"{trait}: {round(avg)} / 100")
            row.append(round(avg))

        profile_sheet.append_row(row)
        st.success("Saved. You can now proceed to chat.")
        st.session_state["completed_test"] = True

    if st.session_state.get("completed_test", False):
        if st.button("Go to Chat"):
            st.rerun()


if page == "Chat":
    if "matched_mode" not in st.session_state:
        st.session_state["matched_mode"] = False

    st.title(f"Chatbot - {user_name}")

    profile = get_profile(user_name)
    if not profile:
        st.error("No profile found. Please take the test first.")
        st.stop()

    if "persona_prompt" not in st.session_state:
        st.session_state.persona_prompt = generate_persona_prompt(profile)

    if "chat_history" not in st.session_state:
        # === 初回のみ：Google Sheets からチャット履歴をロード ===
        chat_history = []
        rows = chat_sheet.get_all_values()[1:]  # ヘッダー除外
        for row in rows:
            name, role, message, _ = row
            if name == user_name:
                chat_history.append({"role": role, "content": message})
        st.session_state.chat_history = chat_history

    history_len = len(st.session_state.chat_history) // 2  # 1往復＝2行

    for msg in st.session_state.chat_history:
        role = msg["role"].lower()
        bubble_color = "#DCF8C6" if role == "user" else "#E8E8E8"
    
        with st.chat_message(role):
            st.markdown(
                f"""
                <div style="background-color: {bubble_color}; color:black; padding: 10px; border-radius: 10px; max-width: 90%; word-wrap: break-word;">
                    {msg['content']}
                </div>
                """,
                unsafe_allow_html=True
            )


# --- ① 30ターン後の切り替え案内 ---
if (
    history_len >= MAX_NONMATCH_ROUNDS and 
    "matched_mode" not in st.session_state
):
    st.info("We've now learned your personality. Would you like to switch to a chatbot that better matches your traits?")
    if st.button("Switch to matched chatbot"):
        st.session_state["matched_mode"] = True
        st.success("Switched to matched chatbot personality!")
        st.rerun()

# --- ② 通常のチャット入力処理（案内の有無に関わらず実行） ---
user_input = st.chat_input("Your message")
if user_input:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    st.session_state.chat_history.append({"role": "User", "content": user_input})
    ai_reply = generate_response(user_input)
    st.session_state.chat_history.append({"role": "AI", "content": ai_reply})

    log_sheet = match_sheet if st.session_state.get("matched_mode", False) else mismatch_sheet
    log_sheet.append_row([user_name, "user", user_input, now])
    log_sheet.append_row([user_name, "bot", ai_reply, now])

    st.rerun()

