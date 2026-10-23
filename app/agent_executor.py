import asyncio
import sys
from uuid import uuid4
from datetime import datetime, timezone

from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from app.agent import MyAgent


class MyAgentExecutor(AgentExecutor):

    def __init__(self):
        self.agent = MyAgent()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input()
        task = context.current_task
        task_id = context.task_id or str(uuid4())
        context_id = context.context_id or str(uuid4())
        
        print(f"[AGENT] Received request - context_id={context_id}, user_input={user_input[:100] if user_input else 'None'}", file=sys.stderr)

        if not task:
            task = Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.submitted,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ),
            )
            await event_queue.enqueue_event(task)
        else:
            context_id = task.context_id or context_id

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.agent.invoke(user_input, context_id),
        )
        
        print(f"[AGENT] Response - is_task_complete={response.get('is_task_complete')}, require_user_input={response.get('require_user_input')}, content={response['content'][:200] if response.get('content') else 'None'}", file=sys.stderr)

        parts = [Part(root=TextPart(text=response["content"]))]
        task.status.timestamp = datetime.now(timezone.utc).isoformat()

        if response["require_user_input"]:
            task.status.state = TaskState.input_required
            task.status.message = Message(
                role=Role.agent,
                parts=[Part(root=TextPart(text=response["content"]))],
                messageId=str(uuid4()),
            )
        else:
            task.status.state = TaskState.completed
            task.status.message = None
        
        # Always set artifacts so Joule can read the response
        task.artifacts = (task.artifacts or []) + [
            Artifact(parts=parts, artifact_id=str(uuid4()))
        ]

        await event_queue.enqueue_event(task)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task:
            task.status = TaskStatus(
                state=TaskState.canceled,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            await event_queue.enqueue_event(task)
