"""Create labeled acceptance runs via the real API and verify their stored evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

import httpx

from app.core.config import settings
from app.db.models.domain import Achievement
from app.db.models.knowledge import AchievementChunk, Document
from app.db.session import AsyncSessionFactory


async def verify(args):
    report = {"mode": "real-http-api-and-configured-llm", "runs": [], "checks": []}
    if args.resume and args.output.exists():
        report = json.loads(args.output.read_text(encoding="utf-8"))
        assert len(report["runs"]) == len(report["checks"])
    verified_runs = iter(list(report["runs"]))
    async with httpx.AsyncClient(base_url=args.api, timeout=60, trust_env=False) as client:

        async def call(method, path, **kwargs):
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        def save():
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        auth = await call(
            "POST",
            "/auth/login",
            json={
                "email": settings.bootstrap_admin_email,
                "password": settings.bootstrap_admin_password,
            },
        )
        client.headers["Authorization"] = "Bearer " + auth["access_token"]
        report["readiness"] = await call("GET", "/readiness")
        assert report["readiness"]["status"] == "ready"
        catalog = await call("GET", "/achievements", params={"query": "超长顶管", "page_size": 100})
        chosen = next(
            item
            for item in catalog["items"]
            if item["title"] == "超长顶管施工输水岩石隧洞关键技术及应用"
        )

        async def run(message, context=None, conversation_id=None):
            previous_run = next(verified_runs, None)
            if previous_run is not None:
                assert previous_run["status"] == "completed"
                assert previous_run["input_payload"]["message"] == message
                return previous_run
            payload = {"message": message, "task_type": "qa", "context": context or {}}
            if conversation_id:
                payload["conversation_id"] = conversation_id
            created = await call("POST", "/runs", json=payload)
            deadline = time.monotonic() + 900
            previous = None
            while time.monotonic() < deadline:
                state = await call("GET", "/runs/" + created["id"])
                progress = (state["status"], state.get("current_step"))
                if progress != previous:
                    print(json.dumps({"run": state["id"], "progress": progress}), flush=True)
                    previous = progress
                if state["status"] in {"completed", "failed", "cancelled", "waiting_review"}:
                    break
                await asyncio.sleep(2)
            report["runs"].append(state)
            report.setdefault("traces", []).append(
                await call("GET", "/runs/" + state["id"] + "/trace")
            )
            save()
            assert state["status"] == "completed", (state["status"], state.get("error_message"))
            assert state["total_input_tokens"] > 0, "Actual LLM must be invoked"
            output = state["output_payload"]
            if output.get("rag"):
                assert output["rag"]["mode"] == "grounded_llm"
                assert 1 <= output["rag"]["rounds"] <= 2
            checks = {"run_id": state["id"], "citations_checked": 0, "originals_downloaded": []}
            async with AsyncSessionFactory() as session:
                downloaded = set()
                for citation in output.get("citations", []):
                    chunk = await session.get(AchievementChunk, citation["chunk_id"])
                    assert chunk is not None
                    assert citation["quote"] in chunk.content
                    assert chunk.achievement_id == citation["achievement_id"]
                    assert chunk.version == citation["version"]
                    assert hashlib.sha256(chunk.content.encode()).hexdigest() == chunk.content_hash
                    checks["citations_checked"] += 1
                    doc_id = citation.get("document_id")
                    if doc_id:
                        owner = await session.get(Achievement, chunk.achievement_id)
                        assert any(
                            citation["quote"] in segment["text"]
                            and citation["locator"] == segment["locator"]
                            for document in owner.structured_data.get("source_documents", [])
                            if document["document_id"] == doc_id
                            for segment in document["segments"]
                        ), "Cited original must contain the quote at its stated locator"
                    if doc_id and doc_id not in downloaded:
                        document = await session.get(Document, doc_id)
                        response = await client.get(f"/documents/{doc_id}/download")
                        response.raise_for_status()
                        assert hashlib.sha256(response.content).hexdigest() == document.sha256
                        checks["originals_downloaded"].append(
                            {
                                "document_id": doc_id,
                                "filename": document.filename,
                                "sha256": document.sha256,
                                "locator": citation.get("locator"),
                            }
                        )
                        downloaded.add(doc_id)
            artifact = await call("GET", "/artifacts/" + output["artifact_id"])
            response = await client.get("/artifacts/" + artifact["id"] + "/download")
            response.raise_for_status()
            assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
            checks["report_sha256_verified"] = True
            report["checks"].append(checks)
            save()
            return state

        first = await run(
            "【全库验收】请仅依据所选成果原始材料回答：超长顶管施工创造的单洞施工纪录是多少米？"
            "遇到岩石隧洞卡管时有哪些脱困措施？请逐字引用证据，并标明来源。",
            {"achievement_ids": [chosen["id"]]},
        )
        assert first["output_payload"]["citations"]
        assert {c["achievement_id"] for c in first["output_payload"]["citations"]} == {chosen["id"]}
        assert "3224" in json.dumps(first["output_payload"], ensure_ascii=False)
        assert report["checks"][0]["originals_downloaded"]
        second = await run(
            "【全库验收追问】这些原件有没有明确承诺这项成果在2030年的采购单价和年收入？"
            "如无明确证据，请说明未知，不要推算或虚构数字。",
            conversation_id=first["conversation_id"],
        )
        assert second["conversation_id"] == first["conversation_id"]
        assert second["run_sequence"] == first["run_sequence"] + 1
        assert second["output_payload"]["selected_achievement_ids"] == [chosen["id"]]
        assert second["output_payload"]["rag"]["sufficient"] is False
        report["unknown_answer_for_manual_review"] = second["output_payload"].get("answer", "")
        third = await run(
            "【全库验收】请在文档库检索分布式光纤输水管道渗漏监测的成果，"
            "说明原文给出的单机最大监测距离是多少，以及怎样定位渗漏。请逐字引用原文。"
        )
        assert third["output_payload"]["citations"]
        assert "35km" in third["output_payload"]["answer"].replace(" ", "")
        assert report["checks"][2]["originals_downloaded"]
        report["passed"] = True
        save()
        print(
            json.dumps(
                {"passed": True, "run_ids": [r["id"] for r in report["runs"]]},
                ensure_ascii=False,
            ),
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    main()
