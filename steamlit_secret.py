import json

# Step 1: JSONを読み込み
with open("dissintationchatlog-35b4b14b2e1f.json", "r", encoding="utf-8") as f:
    creds = json.load(f)

# Step 2: TOML形式に変換（キーごとに記述）
print("[GOOGLE_SERVICE_ACCOUNT_JSON]")
for key, value in creds.items():
    if isinstance(value, str):
        # """...""" で囲う（改行を含むものだけ三重クォート）
        if "\n" in value:
            print(f'{key} = """{value}"""')
        else:
            print(f'{key} = "{value}"')
    else:
        # 数値など
        print(f'{key} = {value}')
