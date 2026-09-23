"""Local LangGraph Studio entrypoint for the MIA workflow."""

from typing import Any

from mia_dpp.mia import Mia
from mia_dpp.workflow.graph import create_graph


def graph() -> Any:
    """Build the same workflow graph with local MIA services for Studio runs."""
    mia = Mia()
    return create_graph(context=mia.context)
