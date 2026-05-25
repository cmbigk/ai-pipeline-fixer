import os
from dotenv import load_dotenv
load_dotenv()
from google import genai
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
models = ["gemini-2.5-flash-lite", "gemini-flash-lite-latest", "gemini-flash-latest"]
for m in models:
    try:
        res = client.models.generate_content(model=m, contents="hello")
        print(f"{m}: Success")
    except Exception as e:
        print(f"{m}: Error - {e}")
