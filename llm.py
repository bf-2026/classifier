import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)

endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT")
api_key = os.getenv("AZURE_OPENAI_API_KEY")

client = OpenAI(
    base_url=endpoint,
    api_key=api_key,
)
response = client.responses.create(
    model=deployment_name,
    input="What is the capital of France?",
)

print(f"answer: {response.output[0]}")
print(response.output_text)
