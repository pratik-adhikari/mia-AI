"""Run MIA's website extraction stage without starting the API or mapping workflow."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from types import SimpleNamespace

from mia_dpp.agents.source_exploration import PydanticSourceExplorationPlanner
from mia_dpp.config import Settings
from mia_dpp.integrations.crawl4ai import Crawl4AIPageLoader
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.storage.local import LocalArtifactStore
from mia_dpp.tools.web.tool import WebExtractionTool
from mia_dpp.workflow.nodes_product import extract_evidence
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider


async def extract(url: str, output: Path, *, use_llm: bool) -> None:
    """Run the real extraction node in isolation and retain its normal catalogue/artifacts."""

    output.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    # LLM use is explicit so raw Crawl4AI/HTML extraction can be evaluated independently.
    token = settings.openrouter_api_key if use_llm else None
    if use_llm and token is None:
        raise RuntimeError("OPENROUTER_API_KEY is required with --llm")
    model = (
        OpenRouterModel(
            settings.agent_model,
            provider=OpenRouterProvider(api_key=token.get_secret_value()),
        )
        if token is not None
        else None
    )

    # Reuse production services rather than maintaining a second diagnostic parser or saver.
    catalogue = ProductCatalogue(output / "catalogue.sqlite3")
    artifacts = LocalArtifactStore(output)
    web_tool = WebExtractionTool(
        loader=Crawl4AIPageLoader(
            model=settings.agent_model if use_llm else None,
            api_token=token.get_secret_value() if token is not None else None,
        ),
        source_planner=PydanticSourceExplorationPlanner(model) if model is not None else None,
    )
    product, _ = catalogue.get_or_create_product(url)
    run = catalogue.start_run(product.id, f"extract-{product.id}")
    context = SimpleNamespace(catalogue=catalogue, artifacts=artifacts, web_tool=web_tool)
    state = {
        "product_id": product.id,
        "run_id": run.id,
        "product_url": url,
        "known_source_urls": (),
    }
    # Calling the workflow node ensures this command produces the same artifacts as the backend.
    result = await extract_evidence(state, SimpleNamespace(context=context))  # type: ignore[arg-type]

    print(f"mode={'crawl4ai+llm' if use_llm else 'crawl4ai-only'}")
    print(f"run={run.id}")
    print(f"files={output / run.id}")
    print(f"product={result['product_name']}")
    print(f"evidence_artifact={result['evidence_artifact_id']}")


def main() -> None:
    """Parse the stable command-line interface exposed by ``make extract``."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Public HTTP(S) product URL")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("test-output/extraction"),
        help="Directory for the catalogue and run folders",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Enable structured extraction and selection of Crawl4AI-discovered sources",
    )
    args = parser.parse_args()
    asyncio.run(extract(args.url, args.output.resolve(), use_llm=args.llm))


if __name__ == "__main__":
    main()
