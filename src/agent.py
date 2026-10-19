import src.tools as tl
from src.prompts import system_agent_prompt
from src.tools import collecting_info
from src.objects import Answer
from src import config

from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.messages import HumanMessage


llm = ChatOpenAI(
    model=config.MODEL,
    base_url=config.BASE_URL,
    api_key=config.API_KEY,   
    temperature=0,
)

agent = create_agent(
    model=llm,
    tools=[collecting_info],
    system_prompt=system_agent_prompt,
    response_format=Answer
)

def ask(question) -> dict:
    """ Answer the user's question about the pdf"""

    tl.last_retrieved_context = []

    result = agent.invoke(
        {"messages": [HumanMessage(content=question)]}
    )

    structured_response = result["structured_response"]

    return {
        "answer" : structured_response.answer,
        "context": structured_response.context
    } 


if __name__ == "__main__":
    print(ask("what is the diet of Penguins"))