from a2a.types import AgentCard, AgentCapabilities, AgentSkill


def get_agent_card(base_url: str) -> AgentCard:
    capabilities = AgentCapabilities(streaming=True, pushNotifications=False)

    skill_1 = AgentSkill(
        id="example_skill",
        name="Example Skill",
        description="A general-purpose SAP SuccessFactors assistant that helps users query employee data, org structure, HR processes, and other SuccessFactors information",
        tags=["example"],
        examples=["What can you do?"],
    )

    return AgentCard(
        name="sfsf-assistant",
        description="A general-purpose SAP SuccessFactors assistant that helps users query employee data, org structure, HR processes, and other SuccessFactors information",
        url=f"{base_url}/",
        version="1.0.0",
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        capabilities=capabilities,
        skills=[skill_1],
    )
