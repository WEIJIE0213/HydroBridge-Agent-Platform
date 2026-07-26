from __future__ import annotations

import time
from typing import TypeVar
from urllib.parse import urlparse

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.runtime import AgentRun, AgentStep, ModelUsage

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class ModelNotConfiguredError(RuntimeError):
    pass


class ModelGateway:
    def __init__(self) -> None:
        self._model: ChatOpenAI | None = None
        if settings.model_is_configured:
            self._model = ChatOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url or None,
                model=settings.llm_model or "",
                temperature=settings.llm_temperature,
                timeout=settings.llm_timeout_seconds,
                max_retries=2,
                streaming=True,
            )

    @property
    def configured(self) -> bool:
        return self._model is not None

    async def structured(
        self,
        schema: type[SchemaT],
        system_prompt: str,
        user_prompt: str,
        *,
        session: AsyncSession,
        run_id: str,
        agent_name: str,
    ) -> SchemaT:
        if self._model is None:
            raise ModelNotConfiguredError("The model endpoint is not configured")
        runnable = self._model.with_structured_output(
            schema,
            method="json_schema",
            include_raw=True,
        )
        started = time.perf_counter()
        result = await runnable.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        raw = result.get("raw") if isinstance(result, dict) else None
        if raw is not None:
            await self._record_usage(
                session,
                run_id,
                agent_name,
                raw,
                latency_ms,
            )
        parsed = result.get("parsed") if isinstance(result, dict) else result
        parsing_error = result.get("parsing_error") if isinstance(result, dict) else None
        if parsing_error is not None:
            raise parsing_error
        if isinstance(parsed, schema):
            return parsed
        return schema.model_validate(parsed)

    async def text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        session: AsyncSession,
        run_id: str,
        agent_name: str,
    ) -> str:
        if self._model is None:
            raise ModelNotConfiguredError("The model endpoint is not configured")
        started = time.perf_counter()
        result = await self._model.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )
        await self._record_usage(
            session,
            run_id,
            agent_name,
            result,
            int((time.perf_counter() - started) * 1000),
        )
        return str(result.content)

    async def _record_usage(
        self,
        session: AsyncSession,
        run_id: str,
        agent_name: str,
        message: object,
        latency_ms: int,
    ) -> None:
        usage = dict(getattr(message, "usage_metadata", None) or {})
        response_metadata = dict(getattr(message, "response_metadata", None) or {})
        token_usage = dict(response_metadata.get("token_usage") or {})
        input_tokens = int(
            usage.get("input_tokens", token_usage.get("prompt_tokens", 0)) or 0
        )
        output_tokens = int(
            usage.get("output_tokens", token_usage.get("completion_tokens", 0)) or 0
        )
        input_details = dict(usage.get("input_token_details") or {})
        prompt_details = dict(token_usage.get("prompt_tokens_details") or {})
        cache_read_tokens = int(
            input_details.get(
                "cache_read",
                input_details.get(
                    "cache_read_tokens",
                    prompt_details.get("cached_tokens", 0),
                ),
            )
            or 0
        )
        provider = urlparse(settings.llm_base_url).netloc or "openai-compatible"
        model_name = str(
            response_metadata.get("model_name")
            or response_metadata.get("model")
            or settings.llm_model
        )
        step_id = await session.scalar(
            select(AgentStep.id)
            .where(
                AgentStep.run_id == run_id,
                AgentStep.agent_name == agent_name,
                AgentStep.status == "running",
            )
            .order_by(AgentStep.sequence.desc())
            .limit(1)
        )
        session.add(
            ModelUsage(
                run_id=run_id,
                step_id=step_id,
                provider=provider,
                model_name=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cost=0,
                latency_ms=latency_ms,
            )
        )
        await session.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .values(
                total_input_tokens=AgentRun.total_input_tokens + input_tokens,
                total_output_tokens=AgentRun.total_output_tokens + output_tokens,
            )
        )
        await session.commit()
