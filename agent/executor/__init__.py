"""The ONLY module in ``agent`` that calls Ads API methods changing state (I1, I7)."""

from agent.executor.executor import ExecutionResult, Executor

__all__ = ["ExecutionResult", "Executor"]
