from openai import OpenAI

endpoint = ""
deployment_name = "mygpt"
api_key = ""

client = OpenAI(
    base_url=endpoint,
    api_key=api_key
)
response = client.responses.create(
    model=deployment_name,
    input="What is the capital of France?",
)

print(f"answer: {response.output[0]}")
