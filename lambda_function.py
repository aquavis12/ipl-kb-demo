"""
IPL Buddy - Bedrock knowledge base vs no knowledge base, side by side.

One Lambda, two answers to every question:
  1. KB path    -> bedrock-agent-runtime:RetrieveAndGenerate  (grounded in IPL docs)
  2. No-KB path -> bedrock-runtime:Converse                   (model memory only)

Both paths run through the same Bedrock guardrail, so the audience can see that
grounding and policy are two independent layers.

The response also carries a `flow` block describing which stage did what, so the
front end can light up the architecture diagram live.

Environment variables:
  KB_ID              required   Bedrock knowledge base ID
  MODEL_ID           optional   default amazon.nova-lite-v1:0
  GUARDRAIL_ID       optional   if empty, guardrail is skipped entirely
  GUARDRAIL_VERSION  optional   default "1"
"""

import concurrent.futures
import json
import os
import time

import boto3
from botocore.config import Config

REGION = os.environ.get("AWS_REGION", "us-east-1")
KB_ID = os.environ["KB_ID"]
MODEL_ID = os.environ.get("MODEL_ID", "amazon.nova-lite-v1:0")
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "").strip()
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "1")

MODEL_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/{MODEL_ID}"

_cfg = Config(read_timeout=55, connect_timeout=10, retries={"max_attempts": 2})
brt = boto3.client("bedrock-runtime", region_name=REGION, config=_cfg)
agent = boto3.client("bedrock-agent-runtime", region_name=REGION, config=_cfg)

PERSONA = (
    "You are IPL Buddy, a cricket commentator with the energy of a man on his fourth "
    "chai of the afternoon. Style: exactly one light cricket joke or bit of commentary "
    "flair per answer, then the actual facts. Never mean about any team or player. "
    "Keep answers under 80 words. Never invent scores, dates, names, or results."
)

KB_TEMPLATE = (
    PERSONA
    + """

Answer using ONLY the search results below. If the answer is not in them, say
"Not in my scorebook - I only know what's in the documents I was given."
Do not guess results, margins, captains, dates, or venues.

Search results:
$search_results$

Question: $query$
"""
)


def _gr_rag():
    if not GUARDRAIL_ID:
        return {}
    return {
        "guardrailConfiguration": {
            "guardrailId": GUARDRAIL_ID,
            "guardrailVersion": GUARDRAIL_VERSION,
        }
    }


def _gr_converse():
    if not GUARDRAIL_ID:
        return {}
    return {
        "guardrailConfig": {
            "guardrailIdentifier": GUARDRAIL_ID,
            "guardrailVersion": GUARDRAIL_VERSION,
            "trace": "enabled",
        }
    }


def ask_with_kb(question, use_guardrail=True):
    """Grounded path: retrieve from the knowledge base, then generate."""
    t0 = time.time()
    gen_cfg = {
        "promptTemplate": {"textPromptTemplate": KB_TEMPLATE},
        "inferenceConfig": {
            "textInferenceConfig": {"temperature": 0.7, "maxTokens": 400}
        },
    }
    if use_guardrail:
        gen_cfg.update(_gr_rag())

    resp = agent.retrieve_and_generate(
        input={"text": question},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": KB_ID,
                "modelArn": MODEL_ARN,
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {"numberOfResults": 4}
                },
                "generationConfiguration": gen_cfg,
            },
        },
    )

    sources = []
    for cit in resp.get("citations", []):
        for ref in cit.get("retrievedReferences", []):
            uri = ref.get("location", {}).get("s3Location", {}).get("uri", "unknown")
            sources.append(
                {
                    "source": uri.split("/")[-1] or uri,
                    "snippet": ref.get("content", {}).get("text", "")[:260],
                }
            )

    blocked = resp.get("guardrailAction") == "INTERVENED"
    return {
        "mode": "with_kb",
        "answer": resp["output"]["text"],
        "blocked": blocked,
        "sources": sources,
        "chunks_retrieved": len(sources),
        "latency_ms": int((time.time() - t0) * 1000),
    }


def ask_without_kb(question, use_guardrail=True):
    """Ungrounded path: no retrieval at all, pure model recall."""
    t0 = time.time()
    kwargs = {
        "modelId": MODEL_ID,
        "system": [{"text": PERSONA}],
        "messages": [{"role": "user", "content": [{"text": question}]}],
        "inferenceConfig": {"temperature": 0.7, "maxTokens": 400},
    }
    if use_guardrail:
        kwargs.update(_gr_converse())

    resp = brt.converse(**kwargs)
    text = "".join(
        b.get("text", "") for b in resp["output"]["message"]["content"]
    ).strip()

    return {
        "mode": "without_kb",
        "answer": text or "(empty response)",
        "blocked": resp.get("stopReason") == "guardrail_intervened",
        "sources": [],
        "chunks_retrieved": 0,
        "latency_ms": int((time.time() - t0) * 1000),
    }


def _failed(name, exc):
    return {
        "mode": name,
        "answer": f"Call failed: {type(exc).__name__}: {exc}",
        "blocked": False,
        "sources": [],
        "chunks_retrieved": 0,
        "latency_ms": 0,
        "error": True,
    }


def lambda_handler(event, context):
    try:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64

            body = base64.b64decode(body).decode()
        payload = json.loads(body) if isinstance(body, str) else body

        question = (payload.get("question") or "").strip()
        use_guardrail = bool(payload.get("use_guardrail", True))
        if not question:
            return _reply(400, {"error": "question is required"})

        t_all = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                "with_kb": pool.submit(ask_with_kb, question, use_guardrail),
                "without_kb": pool.submit(ask_without_kb, question, use_guardrail),
            }
            results = {}
            for name, fut in futures.items():
                try:
                    results[name] = fut.result(timeout=50)
                except Exception as exc:
                    results[name] = _failed(name, exc)

        gr_on = use_guardrail and bool(GUARDRAIL_ID)
        any_blocked = results["with_kb"]["blocked"] or results["without_kb"]["blocked"]

        flow = {
            "client": "ok",
            "lambda": "ok",
            "retrieval": (
                "blocked"
                if results["with_kb"]["blocked"]
                else ("ok" if results["with_kb"]["chunks_retrieved"] else "empty")
            ),
            "chunks_retrieved": results["with_kb"]["chunks_retrieved"],
            "generation": "ok",
            "guardrail": (
                "off" if not gr_on else ("blocked" if any_blocked else "passed")
            ),
            "total_ms": int((time.time() - t_all) * 1000),
        }

        return _reply(
            200,
            {
                "question": question,
                "guardrail_enabled": gr_on,
                "model_id": MODEL_ID,
                "flow": flow,
                **results,
            },
        )

    except Exception as exc:
        return _reply(500, {"error": f"{type(exc).__name__}: {exc}"})


def _reply(status, obj):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(obj),
    }
