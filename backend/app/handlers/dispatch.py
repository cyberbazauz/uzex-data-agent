from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.engine import Connection

from app.agent.query_plan import ExecutionPlan
from app.domain.enums import Intent
from app.handlers import (
    average,
    broker_lookup,
    compare,
    contract_lookup,
    count,
    deal_lookup,
    filter_list,
    min_max,
    participant_lookup,
    sum_amount,
    sum_volume,
    top_n,
    trend,
)
from app.handlers.base import HandlerResult
from app.handlers.common import dataset_context

Handler = Callable[[Connection, ExecutionPlan], HandlerResult]

HANDLERS: dict[Intent, Handler] = {
    Intent.DEAL_LOOKUP: deal_lookup.handle,
    Intent.CONTRACT_LOOKUP: contract_lookup.handle,
    Intent.PARTICIPANT_LOOKUP: participant_lookup.handle,
    Intent.BROKER_LOOKUP: broker_lookup.handle,
    Intent.COUNT: count.handle,
    Intent.SUM_AMOUNT: sum_amount.handle,
    Intent.SUM_VOLUME: sum_volume.handle,
    Intent.AVERAGE: average.handle,
    Intent.MIN_MAX: min_max.handle,
    Intent.TOP_N: top_n.handle,
    Intent.COMPARE: compare.handle,
    Intent.TREND: trend.handle,
    Intent.FILTER_LIST: filter_list.handle,
}


def execute(
    conn: Connection, execution: ExecutionPlan, execution_id: str
) -> HandlerResult:
    handler = HANDLERS.get(execution.plan.intent)
    if handler is None:
        raise ValueError(f"no handler registered for intent {execution.plan.intent}")
    result = handler(conn, execution)
    dataset_version, dataset_cutoff = dataset_context(conn)
    return result.with_dataset(dataset_version, dataset_cutoff, execution_id)
