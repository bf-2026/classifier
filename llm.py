from openai import OpenAI

endpoint = ""
deployment_name = "mygpt"
api_key = ""

client = OpenAI(
    base_url=endpoint,
    api_key=api_key
)

print(client.models.retrieve(deployment_name))
