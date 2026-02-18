"""
AlfredCore — Unified LLM brain.

Single LLM call that both decides and responds, replacing the old
router + QA handler two-call architecture.
"""
from .core import AlfredCore, CoreDecision

__all__ = ["AlfredCore", "CoreDecision"]
