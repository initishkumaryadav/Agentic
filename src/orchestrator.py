import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

scriptDir = Path(__file__).resolve().parent
projectRoot = scriptDir.parents[0]

if str(projectRoot) not in sys.path:
    sys.path.insert(0, str(projectRoot))

from src.tools import query_telemetry_db, fetch_corridor_conditions, search_compliance_sop

load_dotenv(projectRoot / ".env")

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]



AGENT_LLM_SETTING = os.getenv("Agent_llm", "OLLAMA").strip().upper()

if AGENT_LLM_SETTING == "OPENAI":
    print("We are using openai cloud model for reasoning...")
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(
        model="gpt-4o",
        temperature=0,
    )

elif AGENT_LLM_SETTING == "GEMINI":
    print("we are using google cloud model for reasoning.....")
    from langchain_google_genai import ChatGoogleGenerativeAI
    llm = ChatGoogleGenerativeAI(
        model="gemini-3.6-flash",
        temperature=0,
        google_api_key=os.getenv("GEMINI_API_KEY"),
        max_output_tokens=2048,
        )

else:  
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
    print(f"we are using a ollama local model for reasoning ({OLLAMA_MODEL})...")
    from langchain_ollama import ChatOllama
    llm = ChatOllama(model=OLLAMA_MODEL, temperature=0, num_predict=1024)

tools = [query_telemetry_db, fetch_corridor_conditions, search_compliance_sop]
llmWithTools = llm.bind_tools(tools)


def reasoning_node(state: AgentState):
    response = llmWithTools.invoke(state["messages"])
    return {"messages": [response]}

print("Compiling LangGraph Orchestrator...")
graph_builder = StateGraph(AgentState)
graph_builder.add_node("reasoner", reasoning_node)
graph_builder.add_node("tools", ToolNode(tools))

graph_builder.add_edge(START, "reasoner")
graph_builder.add_conditional_edges("reasoner", tools_condition)
graph_builder.add_edge("tools", "reasoner")

agent = graph_builder.compile(checkpointer=MemorySaver())


if __name__ == "__main__":
    print("\n" + "="*10)
    print("Supply Chain Orchestrator State Machine Online")
    print(f"Configured Execution: [LLM: {AGENT_LLM_SETTING}] -> [Embeddings: {os.getenv('Embeddings_model', 'LOCAL')}]")
    print("="*10 + "\n")
    
    prompt_path = projectRoot / "src" / "prompt" / "systemPrompt.txt"
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_instructions = f.read()
    except FileNotFoundError:
        print(f"Error: Could not find {prompt_path}")
        system_instructions = "You are a helpful AI assistant."
    system_prompt = SystemMessage(content=system_instructions)
    
    thread_config: RunnableConfig = {"configurable": {"thread_id": "production_test_1"}}
    agent.invoke({"messages": [system_prompt]}, config=thread_config)
    
    while True:
        user_input = input("\nDispatcher > ")
        if user_input.lower() in ['exit', 'quit']:
            break
            
        events = agent.stream({"messages": [HumanMessage(content=user_input)]},config=thread_config, stream_mode="updates")
        for event in events:
            for node_name, node_state in event.items():
                if node_name == "tools":
                    print("Retrieving external data elements via ToolNode...")
                elif node_name == "reasoner":
                    latest_msg = node_state["messages"][-1]
                    if latest_msg.content:
                        print(f"\n Agent:\n{latest_msg.content}")