import argparse
import asyncio
from uuid import UUID

from app.core.constants import ConfidentialLevel, ThinkingMode
from app.core.security import Principal
from app.db.session import SessionLocal
from app.services.llmwiki_service import LLMWikiService


def _admin_principal() -> Principal:
    return Principal(
        external_user_id="llmwiki-cli",
        username="llmwiki-cli",
        department=None,
        roles={"admin"},
        clearance_level=ConfidentialLevel.RESTRICTED,
    )


def reset_main() -> None:
    parser = argparse.ArgumentParser(description="Reset LLMWiki data for one knowledge base.")
    parser.add_argument("--knowledge-base-id", required=True, type=UUID)
    args = parser.parse_args()
    with SessionLocal() as db:
        LLMWikiService(db).reset_knowledge_base(args.knowledge_base_id)
    print(f"Reset LLMWiki data for knowledge base {args.knowledge_base_id}.")


async def _discover(args: argparse.Namespace) -> None:
    with SessionLocal() as db:
        items = await LLMWikiService(
            db,
            model=args.model,
            thinking_mode=args.thinking_mode,
        ).discover_and_review_topics(
            knowledge_base_id=args.knowledge_base_id,
            limit=args.limit,
            principal=_admin_principal(),
            query=args.query,
        )
    print(f"Reviewed {len(items)} LLMWiki topics for knowledge base {args.knowledge_base_id}.")
    for item in items:
        print(f"- {item.topic} [{item.status}] quality={item.quality_score:.2f}")


def discover_main() -> None:
    parser = argparse.ArgumentParser(description="Discover and review LLMWiki topics.")
    parser.add_argument("--knowledge-base-id", required=True, type=UUID)
    parser.add_argument("--limit", default=20, type=int)
    parser.add_argument("--query", default="")
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--thinking-mode",
        default=ThinkingMode.DEFAULT,
        type=ThinkingMode,
        choices=list(ThinkingMode),
    )
    args = parser.parse_args()
    asyncio.run(_discover(args))
