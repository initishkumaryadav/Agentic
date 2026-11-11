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
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
    if not GEMINI_API_KEY:
        raise ValueError("Agent_llm=GEMINI but GEMINI_API_KEY is empty in .env")
    print(f"we are using google cloud model for reasoning ({GEMINI_MODEL})...")
    from langchain_google_genai import ChatGoogleGenerativeAI
    llm = ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        temperature=0,
        google_api_key=GEMINI_API_KEY,
        max_output_tokens=2048,
        )

else:  
    
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")
    print(f"we are using a ollama local model for reasoning ({OLLAMA_MODEL})...")
    from langchain_ollama import ChatOllama
    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=0,
        num_predict=1024,
        num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "8192")),  # room for system prompt + tool schemas
    )

tools = [query_telemetry_db, fetch_corridor_conditions, search_compliance_sop]
llmWithTools = llm.bind_tools(tools)

# The folder is "Prompt" (capital P); Linux paths are case-sensitive.
PROMPT_PATH = projectRoot / "src" / "Prompt" / "systemPrompt.txt"
try:
    SYSTEM_PROMPT = SystemMessage(content=PROMPT_PATH.read_text(encoding="utf-8"))
except FileNotFoundError:
    print(f"Warning: system prompt not found at {PROMPT_PATH}; using a generic one.")
    SYSTEM_PROMPT = SystemMessage(content="You are a helpful cold-chain logistics assistant. Use the available tools to answer.")

def message_text(msg) -> str:
    """Plain text of a message. Gemini returns content as a list of parts
    (text blocks plus 'signature'/thinking blocks); keep only the text."""
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
        return "".join(parts)
    return str(content or "")


EMPTY_REPLY_NUDGE = HumanMessage(
    content="Your last reply was empty. Either call one of the tools or answer the question in text."
)


def reasoning_node(state: AgentState):
    # Always prepend the system prompt so every caller (CLI, Streamlit) gets the same instructions.
    messages = state["messages"]
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SYSTEM_PROMPT, *messages]

    response = llmWithTools.invoke(messages)

    # Small local models sometimes return neither text nor a tool call. Retry once with a nudge.
    if not message_text(response).strip() and not getattr(response, "tool_calls", None):
        response = llmWithTools.invoke([*messages, EMPTY_REPLY_NUDGE])

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
    
    thread_config: RunnableConfig = {"configurable": {"thread_id": "production_test_1"}}
    
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
                    reply = message_text(latest_msg)
                    if reply:
                        print(f"\n Agent:\n{reply}")