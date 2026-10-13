"""
The tools the chat model can call. Each tool is a class in its own module with the
same small interface (see base.Tool). To add one, write the class and list it here.
"""
from typing import List

from src.tools.base import Tool, ToolResult, Turn
from src.tools.courses import CourseAssignments, SearchCourse
from src.tools.documents import CreateDiagram, CreateDocument
from src.tools.web import OpenUrl, WebSearch

__all__ = ["Tool", "ToolResult", "Turn", "default_tools"]


def default_tools() -> List[Tool]:
    """Every tool the bot has, in the order their prompt sections are added to the system prompt."""
    return [CreateDocument(), CreateDiagram(), OpenUrl(), WebSearch(), SearchCourse(), CourseAssignments()]
