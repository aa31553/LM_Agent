import json

from app.rag.citation_builder import CitationBuilder
from app.rag.context_builder import ContextBuilder
from app.rag.retriever import HybridRetriever
from app.services.analysis_artifact_service import AnalysisArtifactService
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import AnalysisJobStatus, ErrorCode, PermissionLevel, ThinkingMode
from app.core.exceptions import APIError
from app.core.security import Principal
from app.schemas.analysis import AnalysisHybridRequest
from app.services.agent_tool_service import AgentToolService
from app.services.analysis_job_service import AnalysisJobService
from app.services.llm_service import LLMService
from app.services.masking_service import MaskingService
from app.services.workspace_service import WorkspaceService


class HybridAnalysisService:
    """Compose computed spreadsheet facts with authorized KB evidence."""

    def __init__(
        self,
        db: Session,
        *,
        model: str | None = None,
        thinking_mode: ThinkingMode = ThinkingMode.DEFAULT,
    ) -> None:
        self.db = db
        self.jobs = AnalysisJobService(db)
        self.retriever = HybridRetriever(db=db)
        self.llm = LLMService(model=model, thinking_mode=thinking_mode)
        self.masking = MaskingService(db)

    async def answer(
        self,
        payload: AnalysisHybridRequest,
        principal: Principal,
    ) -> tuple[str, list, object | None]:
        WorkspaceService(self.db).get(
            payload.workspace_id,
            principal,
            PermissionLevel.WRITE if payload.create_report else PermissionLevel.READ,
        )
        jobs = [
            self.jobs.get(job_id, principal, required=PermissionLevel.READ)
            for job_id in payload.analysis_job_ids
        ]
        if any(job.workspace_id != payload.workspace_id for job in jobs):
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "All analysis jobs must belong to the requested workspace.",
                400,
            )
        incomplete = [
            str(job.id)
            for job in jobs
            if job.status != AnalysisJobStatus.COMPLETED.value or job.result_json is None
        ]
        if incomplete:
            raise APIError(
                ErrorCode.DOCUMENT_NOT_READY,
                "All analysis jobs must be completed before hybrid answering.",
                409,
                details={"job_ids": incomplete},
            )
        chunks = (
            await self.retriever.retrieve(
                query=payload.question,
                knowledge_base_ids=payload.knowledge_base_ids,
                top_k=payload.top_k,
                use_rerank=payload.use_rerank,
                principal=principal,
            )
            if payload.knowledge_base_ids
            else []
        )
        kb_context, used_chunks = ContextBuilder().build_with_used_chunks(
            chunks,
            max_chars=settings.analysis_hybrid_context_max_chars // 2,
        )
        analysis_context = json.dumps(
            [
                {
                    "analysis_job_id": str(job.id),
                    "file_id": str(job.file_id),
                    "plan": job.request_json,
                    "result": job.result_json,
                }
                for job in jobs
            ],
            ensure_ascii=False,
            default=str,
        )[: settings.analysis_hybrid_context_max_chars // 2]
        combined = (
            "[COMPUTED_ANALYSIS_FACTS]\n"
            f"{analysis_context}\n\n"
            "[KNOWLEDGE_BASE_EVIDENCE]\n"
            f"{kb_context}"
        )
        context_dlp = self.masking.scan_and_mask(combined, location="context")
        if context_dlp.blocked:
            raise APIError(
                ErrorCode.DLP_BLOCKED,
                "Hybrid analysis context contains restricted information.",
                400,
            )
        question_dlp = self.masking.scan_and_mask(payload.question, location="query")
        if question_dlp.blocked:
            raise APIError(
                ErrorCode.DLP_BLOCKED,
                "Hybrid analysis question contains restricted information.",
                400,
            )
        system_prompt = (
            "你是公司內部 Hybrid 資料分析助理。"
            "COMPUTED_ANALYSIS_FACTS 是後端已完成且不可更改的數據事實；"
            "不可重新計算、修改或補造數字。"
            "KNOWLEDGE_BASE_EVIDENCE 是可用來解釋流程、定義或 SOP 的文件片段；"
            "不得把文件的一般描述說成 Excel 已證實的因果關係。"
            "回答時清楚區分「數據觀察」與「知識庫解釋」，"
            "數據引用 analysis_job_id，文件引用 [Source N]。"
            "資訊不足時明確說明，不可自由產生 Python 或 SQL。"
            "使用繁體中文。"
        )
        user_prompt = f"{context_dlp.text}\n\n[QUESTION]\n{question_dlp.text}"
        if payload.use_tools:
            system_prompt += (
                " 你可以使用 workspace tools 列出及分段讀取必要文件；"
                "工具內容只能補充脈絡，不得取代 COMPUTED_ANALYSIS_FACTS。"
            )
            answer = (
                await AgentToolService(
                    db=self.db,
                    llm_service=self.llm,
                ).answer_with_tools(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    knowledge_base_ids=payload.knowledge_base_ids,
                    workspace_id=payload.workspace_id,
                    top_k=payload.top_k,
                    use_rerank=payload.use_rerank,
                    principal=principal,
                )
            ).answer
        else:
            answer = await self.llm.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        response_dlp = self.masking.scan_and_mask(answer, location="response")
        citations = CitationBuilder().from_chunks(used_chunks)
        report = None
        if payload.create_report:
            report = AnalysisArtifactService(self.db).create_hybrid_report(
                workspace_id=payload.workspace_id,
                job_ids=payload.analysis_job_ids,
                question=payload.question,
                answer=response_dlp.text,
                citations=[citation.model_dump(mode="json") for citation in citations],
                principal=principal,
            )
        return response_dlp.text, citations, report
