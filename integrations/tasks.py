"""Compatibility imports; canonical task code is integrations.juridiq.tasks."""

from .juridiq.tasks import TASK_TEMPLATE_TITLES, TaskDraftError, build_task_drafts

__all__ = ["TASK_TEMPLATE_TITLES", "TaskDraftError", "build_task_drafts"]
