from google.adk import Agent

root_agent = Agent(
    name="hello_agent",
    model="gemini-2.5-flash",
    description="Simple greeting agent",
    instruction="You are a helpful assistant. Greet the user warmly and answer questions.",
)
