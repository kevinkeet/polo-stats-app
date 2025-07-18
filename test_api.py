import os
import google.generativeai as genai
from dotenv import load_dotenv

print("--- Starting API test ---")

# 1. Load environment variables from .env file
load_dotenv()

# 2. Get the API Key
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("!!! FATAL ERROR: Could not find GEMINI_API_KEY in your .env file.")
else:
    print("--- API Key loaded successfully. ---")
    try:
        # 3. Configure the AI model
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        print("--- Configuring AI model... ---")

        # 4. Send a simple test prompt
        print("--- Sending a test prompt to Google AI... ---")
        response = model.generate_content("Give me a two-word motto for a water polo team.")
        
        # 5. Print the response
        print("\n\n--- SUCCESS! AI responded: ---")
        print(response.text)

    except Exception as e:
        print("\n\n!!! TEST FAILED. An error occurred: ---")
        print(e)

print("\n--- Test finished ---")
