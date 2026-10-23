from langchain_core.runnables import RunnableConfig
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel
from typing import Any, AsyncIterable, Literal
import os
import json
import httpx

from app.tools import get_tools


class ResponseFormat(BaseModel):
    """Structured response format for A2A compatibility."""
    status: Literal["input_required", "completed", "error"] = "input_required"
    message: str


def _get_aicore_creds() -> dict:
    """Resolve AI Core credentials: AICORE_SERVICE_KEY > VCAP aicore binding > Destination Service."""
    aicore_key = os.getenv("AICORE_SERVICE_KEY")
    if aicore_key:
        return json.loads(aicore_key)

    vcap = json.loads(os.getenv("VCAP_SERVICES", "{}"))
    aicore_bindings = vcap.get("aicore", [])
    if aicore_bindings:
        return aicore_bindings[0]["credentials"]

    # CF: resolve via aaulcm-destination (Destination Service binding)
    from app.sfsf_client import _get_dest_service_creds
    creds = _get_dest_service_creds()
    ds_token = httpx.post(
        creds["url"] + "/oauth/token",
        data={"grant_type": "client_credentials"},
        auth=(creds["clientid"], creds["clientsecret"]),
        timeout=15,
    ).json()["access_token"]

    dest = httpx.get(
        f"{creds['uri']}/destination-configuration/v1/destinations/aicore-destination",
        headers={"Authorization": f"Bearer {ds_token}"},
        timeout=15,
    ).json()
    cfg = dest["destinationConfiguration"]
    return {
        "url":         cfg["tokenServiceURL"].replace("/oauth/token", ""),
        "clientid":    cfg["clientId"],
        "clientsecret": cfg["clientSecret"],
        "serviceurls": {"AI_API_URL": cfg["URL"]},
    }


def get_llm():
    """Initialize LLM using ai-core-sdk for deployment lookup + langchain-openai for inference."""
    from ai_core_sdk.ai_core_v2_client import AICoreV2Client
    from langchain_openai import ChatOpenAI

    creds = _get_aicore_creds()
    model = os.getenv("MODEL_NAME", "gpt-4.1")
    resource_group = os.getenv("AICORE_RESOURCE_GROUP", "default")

    client = AICoreV2Client(
        base_url=creds["serviceurls"]["AI_API_URL"] + "/v2",
        auth_url=creds["url"] + "/oauth/token",
        client_id=creds["clientid"],
        client_secret=creds["clientsecret"],
        resource_group=resource_group,
    )

    deployments = client.deployment.query()
    deployment = next(
        (d for d in deployments.resources
         if d.status.value == "RUNNING"
         and (d.details or {}).get("resources", {}).get("backend_details", {})
             .get("model", {}).get("name") == model),
        None,
    )
    if not deployment:
        raise RuntimeError(f"No running deployment for model '{model}' in resource group '{resource_group}'")

    access_token = httpx.post(
        creds["url"] + "/oauth/token",
        data={"grant_type": "client_credentials"},
        auth=(creds["clientid"], creds["clientsecret"]),
        timeout=30,
    ).json()["access_token"]

    return ChatOpenAI(
        model=model,
        base_url=f"{deployment.deployment_url}/v1",
        api_key=access_token,
        max_tokens=4096,
        default_headers={"AI-Resource-Group": resource_group},
    )


SYSTEM_INSTRUCTION = """You are a SAP SuccessFactors assistant. You help users look up employee information, org charts, job details, leave balances, and contract renewal cases by querying the SuccessFactors OData API.

## How to answer
- Use the available tools to fetch data — never guess employee details, IDs, or org structures.
- When searching for a person, use search_employees first to resolve their userId, then use more specific tools.
- When the user asks for contract renewal cases without specifying filters, call the tool with default parameters to show the most recent cases. Do not ask for clarification unless the request is genuinely ambiguous.
- Present results in a clear, readable format (tables or bullet lists).
- If a userId is not found, tell the user and suggest they check the spelling.
- If a tool returns an "error" field in its response, communicate that error clearly and politely to the user. Do not retry unless the user explicitly asks. Common errors include authorization issues (user not allowed to access certain data) or external user restrictions.

## Response status rules
- Set status to "completed" when you have called a tool and returned results to the user (even if no data was found or the result is empty).
- Set status to "input_required" ONLY when you genuinely cannot proceed without user input BEFORE calling any tool (e.g. user asked about an employee but there are multiple matches and you need them to pick one).
- Set status to "error" if an unrecoverable error occurs.
"""

RESPONSE_FORMAT_INSTRUCTION = """Always respond using the ResponseFormat schema."""

memory = MemorySaver()


class MyAgent:
    SUPPORTED_CONTENT_TYPES = ["text/plain"]

    def __init__(self):
        self.tools = get_tools()

    def _get_graph(self):
        """Build graph with a fresh AI Core token on every call."""
        return create_react_agent(
            get_llm(),
            tools=self.tools,
            checkpointer=memory,
            prompt=SYSTEM_INSTRUCTION,
            response_format=(RESPONSE_FORMAT_INSTRUCTION, ResponseFormat),
        )

    def invoke(self, query: str, session_id: str) -> dict[str, Any]:
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        self._get_graph().invoke({"messages": [("user", query)]}, config)
        return self._get_response(config)

    async def stream(self, query: str, session_id: str) -> AsyncIterable[dict[str, Any]]:
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        inputs = {"messages": [("user", query)]}
        for item in self._get_graph().stream(inputs, config, stream_mode="values"):
            message = item["messages"][-1]
            if isinstance(message, AIMessage) and message.tool_calls:
                yield {"is_task_complete": False, "require_user_input": False, "content": "Processing..."}
            elif isinstance(message, ToolMessage):
                yield {"is_task_complete": False, "require_user_input": False, "content": "Analyzing results..."}
        yield self._get_response(config)

    def _get_response(self, config: RunnableConfig) -> dict[str, Any]:
        state = self._get_graph().get_state(config)
        messages = state.values.get("messages", [])
        
        # Check if any tool was called in this conversation turn
        # If tools were called, the task should be completed regardless of LLM's status
        tool_was_called = any(isinstance(m, ToolMessage) for m in messages[-5:])  # Check recent messages
        
        structured = state.values.get("structured_response")
        if structured and isinstance(structured, ResponseFormat):
            # Override LLM's status if tools were called - task is complete
            if tool_was_called and structured.status == "input_required":
                return {
                    "is_task_complete": True,
                    "require_user_input": False,
                    "content": structured.message,
                }
            return {
                "is_task_complete": structured.status == "completed",
                "require_user_input": structured.status == "input_required",
                "content": structured.message,
            }
        if messages and hasattr(messages[-1], "content"):
            return {"is_task_complete": True, "require_user_input": False, "content": str(messages[-1].content)}
        return {"is_task_complete": False, "require_user_input": True, "content": "Could you rephrase your request?"}
