import streamlit as st
import os
import json
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

#google sheetの認証アクセス
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
credentials = ServiceAccountCredentials.from_json_keyfile_name(
    "dissintationchatlog-35b4b14b2e1f.json", scope
)
client = gspread.authorize(credentials)

#google sheetの読み込み
chat_sheet = client.open_by_key("1XpB4gzlkOS72uJMADmSIuvqECM5Ud8M-KwwJbXSxJxM").worksheet("Chat")
profile_sheet = client.open_by_key("1XpB4gzlkOS72uJMADmSIuvqECM5Ud8M-KwwJbXSxJxM").worksheet("Personality")


#getting the exisiting usernames from google sheets
existing_users = [row["Username"] for row in profile_sheet.get_all_records()]

#accessing the names of the users who have already signed up.
st.sidebar.title("User Input")
user_name = st.sidebar.text_input("Please enter your username: ", key ="username")

#if they forgot to enter the username
if not user_name:
    st.warning("Please enter the username before you proceed")

#If the username exists move to their chat page
#Else, start with the personality test to see the users personality
if user_name in existing_users:
    page="chat session"
else:
    page="Personality Test"

#First page the personality test
if page=="Personality Test":
    st.title("Big Five Personality Test")
    questions = [
    ("I am the life of the party", "Extraversion", False),
    ("I don't talk a lot", "Extraversion", True),
    ("I sympathize with other's feelings", "Agreeableness", False),
    ("I am not interested in other people's problems", "Agreeableness", True),
    ("I get chores done right away", "Conscientiousness", False),
    ("I often forget to put things back in their proper place", "Conscientiousness", True),
    ("I am relaxed most of the time", "Emotional Stability", False),
    ("I get upset easily", "Emotional Stability", True),
    ("I have a vivid imagination", "Openness", False),
    ("I am not interested in abstract ideas", "Openness", True)
    ]

    #a list to save the response
    responses=[]
    with st.form("personality_form"):
        st.write("Please ratre how you agree with each statement (1 = Strongly Disagree, 5 = Strongly Agree)")
        for question, _, _ in questions:
            #creating a slider to enter the answer
            score= st.slider(question, 1, 5, 3)
            responses.append(score)
        #A button to submit the answer
        submitted = st.form_submit_button("Submit Answer")

    if submitted:
        #reset the score for each persoanlity score per new test
        traits={"Extraversion":0,"Agreeableness":0, "Conscientiousness": 0, "Emotional Stability": 0, "Openness": 0}
        #counting each categories answer 
        trait_counts= {trait: 0 for trait in traits}

        #processing the answer and the questions one by one
        for response, (question, trait, reverse) in zip(responses, questions):
            #processing score for reverse questions
            score = 6- response if reverse else response
            traits[trait] +=score
            trait_counts[trait]+=1

        #showing score and saving the users score
        st.subheader("Your Persoanlity Score is : ")
        profile_row= [user_name]
        for trait in traits:
            #turing the score into out of 100
            avg_score = traits[trait] / trait_counts[trait]*20
            st.write(f"**{trait}**: {round(avg_score)} / 100")
            profile_row.append(round(avg_score))

        
        #saving the result to the google sheet
        profile_sheet.append_row(profile_row)
        st.success("Your persoanlity score has been saved! Now please proceed to the chat session with your AI partner")

elif page=="chat session":
    st.title(f"{user_name}'s chat session")

    # ✅ Get user's personality profile
    def get_profile(user):
        rows = profile_sheet.get_all_records()
        for row in rows:
            if row["Username"] == user:
                return row
        return None

    profile = get_profile(user_name)
    if not profile:
        st.error("No personality profile found. Please take the personality test first.")
        st.stop()


    # ファインチューニング済みモデルの保存場所
    model_path = ""  

    # モデルとトークナイザーを読み込み（デプロイ時もこのまま）
    model = AutoModelForCausalLM.from_pretrained(model_path, device_map="auto")
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    tokenizer.pad_token = tokenizer.eos_token  # pad_token設定（必須）

    #userの性格スコアに対して、promptでAIチャットボットの性格を決定するようにする (まだ未完成)

    def generate_persona_prompt(profile):
        if profile["Emotional Stability"] < 50:
            return "You are a warm, calm, and emotionally supportive AI companion who reassures users gently."
        elif profile["Extraversion"] < 50:
            return "You are a quiet and thoughtful AI who listens attentively and provides gentle encouragement."
        elif profile["Openness"] > 70:
            return "You are a poetic, reflective AI that uses creative and philosophical responses to inspire users."
        else:
            return "You are a logical and dependable AI helper who provides clear and trustworthy guidance."

    # Set persona prompt once per session
    if "persona_prompt" not in st.session_state:
        st.session_state["persona_prompt"] = generate_persona_prompt(profile)

    # ✅ Response generation using the fine-tuned Gemma model
    def generate_response(user_input):
        # モデル未使用の仮応答（テンプレ）
        personality = st.session_state.get("persona_prompt", "")
        return f"{personality}\n\n(This is a placeholder reply for: \"{user_input}\")"



    #create a file to save chat logs for each users
    chat_log_dir="chat_logs"
    os.makedirs(chat_log_dir, exist_ok= True)
    chat_file_path =os.path.join(chat_log_dir, f"{user_name}.json")

    #If there are no past chat log in session load from file
    if "chat_history" not in st.session_state:
        if os.path.exists(chat_file_path):
            with open(chat_file_path, "r", encoding="utf-8") as f:
                st.session_state.chat_history =json.load(f)
        else:
            st.session_state.chat_history = []
    
    #a function to show chatbubbles as a chat session
    def display_message(msg, role):
        style= "user-bubble" if role =="user" else "bot-bubble"
        st.markdown(f'<div class="{style}">{msg}</div>', unsafe_allow_html=True)

    # a css to set the style of chat bubble
    st.markdown("""
    <style>
    .user-bubble {
        background-color: #DCF8C6;
        color: black;
        border-radius: 10px;
        padding: 10px;
        margin: 5px 50px 5px auto;
        text-align: right;
        max-width: 70%;
    }
    .bot-bubble {
        background-color: #E6E6E6;
        color: black;
        border-radius: 10px;
        padding: 10px;
        margin: 5px auto 5px 50px;
        text-align: left;
        max-width: 70%;
    }
    </style>
    """, unsafe_allow_html=True)

    #showing past chat session
    for msg in st.session_state.chat_history:
        display_message(msg["content"], msg["role"])

    #A place for user to enter message
    user_input= st.text_input("Enter your message here")

    #proram to send a message to the AI
    if st.button("Send") and user_input:
        now=datetime.now().strftime("%Y-%m-%d %H:%M")
        st.session_state.chat_history.append({"role": "user", "content": user_input, "timestamp": now})
        ai_reply=generate_response(user_input)
        st.session_state.chat_history.append({"role": "bot", "content": ai_reply, "timestamp": now})

        #saving the chat log locally
        with open(chat_file_path, "w", encoding="utf-8") as f:
            json.dump(st.session_state.chat_history, f, ensure_ascii=False, indent=2)
        
        #Saving the chat logs in Google sheets
        chat_sheet.append_row([user_name, "user", user_input, now])
        chat_sheet.append_row([user_name, "bot", ai_reply, now])

        st.experimental_rerun()

    