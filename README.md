# IPL Buddy - Bedrock knowledge base vs no knowledge base, live

A small demo that answers every question **twice**: once grounded in an Amazon Bedrock
knowledge base of IPL documents, once from raw model memory with no retrieval. Both
answers pass through the same Bedrock guardrail, so an audience can see that grounding
and policy are two independent layers rather than one blurry "AI safety" thing.

Built for a live AWS Student Builder Group speaker session. Console-built, no CDK,
no SAM, no build step.

```
Streamlit  ->  Lambda function URL  ->  RetrieveAndGenerate (KB)  ->  Guardrail  ->  both answers
                                    ->  Converse (no KB)          ->  Guardrail       side by side
```

The UI renders that pipeline as a live strip across the top. Each stage turns green,
amber, or red per request, and the retrieval stage shows how many chunks came back.

## Repo contents

| Path | What it is |
| --- | --- |
| `lambda_function.py` | The whole backend. Paste into the Lambda console editor. |
| `app.py` | Streamlit UI: flow strip plus before/after KB panels. |
| `kb-context/` | Two IPL documents. Upload both to the S3 bucket root. |
| `requirements.txt` | `streamlit`, `requests`. |

## The demo in one paragraph

IPL results up to 2024 are common knowledge, so a model answers those correctly from
memory and the comparison looks pointless. The 2025 and 2026 seasons are recent enough
to sit past most model training cutoffs. Ask about IPL 2026 and the ungrounded side
either refuses or confidently states that RCB's 2025 title was their only one. The
grounded side cites the document. The audience can verify the real answer on their
phones in about four seconds, which is what makes it land.

## Part 1 - build it (about 30 minutes, all console)

### 1. S3 bucket

- S3 -> Create bucket -> `ipl-kb-demo-<yourname>`
- Must be a General Purpose bucket, in the same Region as the knowledge base
- Upload both files from `kb-context/` to the **bucket root**, not a subfolder

Nested folders make retrieval less reliable. Keep it flat. Markdown, not PDF: a
designed PDF chunks badly and the retrieved chunks look like garbage when you open
them on stage, and opening them is part of the demo.

### 2. Bedrock model access

Bedrock -> Model access -> enable `Amazon Nova Lite` and `Titan Text Embeddings V2`.

### 3. Knowledge base

Bedrock -> Knowledge Bases -> Create -> Knowledge Base with vector store

| Field | Value |
| --- | --- |
| Data source | Amazon S3, your bucket |
| Chunking | Default, fixed size 300 tokens |
| Embeddings model | Titan Text Embeddings V2 |
| Vector store | Quick create -> **Amazon S3 Vectors** |

Create, select the data source, **Sync**, wait for `Available`. Copy the knowledge base ID.

> Do not pick OpenSearch Serverless. It bills for idle capacity and keeps charging long
> after the talk. S3 Vectors has no idle charge, which is why this whole stack costs
> under 1 USD for the day.

### 4. Guardrail - three blocks, three different mechanisms

Bedrock -> Guardrails -> Create guardrail.

**Denied topic 1 - financial figures**

- Name: `FinancialFigures`
- Definition: `Requests for revenue, earnings, broadcast rights values, franchise valuations, player salaries, auction purse amounts, prize money, sponsorship values, or any other monetary figure.`
- Sample phrase: `What was the total IPL revenue in 2026?`

**Denied topic 2 - code generation**

- Name: `CodeGeneration`
- Definition: `Requests to write, generate, complete, debug, refactor, review, or explain source code in any programming language.`
- Sample phrase: `Write me a Python function to calculate strike rate.`

**Sensitive information filters**

- PII type `EMAIL` -> Anonymize
- PII type `PHONE` -> Anonymize

**Content filters**

- Hate, Insults, Violence, Sexual, Misconduct -> High
- Prompt attack -> High

**Contextual grounding check**

- Grounding threshold `0.75`, Relevance threshold `0.75`

Create, then **Create version**. Copy the guardrail ID and version.

Write a blocked message in the bot's voice. The room sees it three times, so the
default string gets old fast. Something like: *"That one's outside my scorebook -
I only do cricket facts, not money and not code."*

### 5. Lambda

Lambda -> Create function. Name `ipl-buddy`, runtime **Python 3.13**, arch **arm64**.

Paste `lambda_function.py`, Deploy. Then:

- **General configuration**: timeout `1 min 0 sec`, memory `512 MB`
- **Environment variables**:

| Key | Value |
| --- | --- |
| `KB_ID` | your knowledge base ID |
| `MODEL_ID` | `amazon.nova-lite-v1:0` |
| `GUARDRAIL_ID` | your guardrail ID |
| `GUARDRAIL_VERSION` | `1` |

- **Permissions** -> execution role -> Add permissions -> Create inline policy -> JSON:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:Converse",
        "bedrock:Retrieve",
        "bedrock:RetrieveAndGenerate",
        "bedrock:ApplyGuardrail"
      ],
      "Resource": "*"
    }
  ]
}
```

Scope `Resource` to your specific model, KB, and guardrail ARNs before this pattern
goes near a customer account. Wildcard is a demo shortcut, and saying that out loud
on stage is worth more than pretending it is fine.

- **Function URL** -> Create -> Auth type `NONE`, CORS enabled, origin `*`,
  header `content-type`, method `POST`

An unauthenticated function URL is a public, billable Bedrock endpoint. Fine for a
demo you delete the same day. Not fine otherwise.

### 6. Run the UI

```bash
pip install -r requirements.txt
export FUNCTION_URL="https://xxxx.lambda-url.us-east-1.on.aws/"
streamlit run app.py
```

Test the backend on its own first:

```bash
curl -s -X POST "$FUNCTION_URL" -H 'content-type: application/json' \
  -d '{"question":"Who won IPL 2026?","use_guardrail":true}' | python3 -m json.tool
```

## API contract

Request:

```json
{ "question": "string, required", "use_guardrail": true }
```

Response:

```json
{
  "question": "...",
  "guardrail_enabled": true,
  "model_id": "amazon.nova-lite-v1:0",
  "flow": {
    "client": "ok", "lambda": "ok", "retrieval": "ok",
    "chunks_retrieved": 4, "generation": "ok",
    "guardrail": "passed", "total_ms": 2140
  },
  "with_kb":    { "answer": "...", "blocked": false, "chunks_retrieved": 4,
                  "sources": [{"source":"01-ipl-seasons-2008-2026.md","snippet":"..."}],
                  "latency_ms": 1840 },
  "without_kb": { "answer": "...", "blocked": false, "chunks_retrieved": 0,
                  "sources": [], "latency_ms": 910 }
}
```

The `flow` block is what drives the live pipeline strip in the UI. `guardrail` is one
of `passed`, `blocked`, or `off`; `retrieval` is `ok`, `empty`, or `blocked`.

## Part 2 - running it on stage

### Pre-flight, 10 minutes before

- [ ] KB status `Available`, last sync succeeded
- [ ] Streamlit running, tab open, no terminal visible
- [ ] Click **Warm up backend**. A cold Lambda plus a first Bedrock call is the most
      likely thing to leave you standing in silence
- [ ] Browser zoom 125 to 150 percent. Two columns of 15px text is invisible from row 10
- [ ] Guardrail toggle on
- [ ] Run question 1 once privately, confirm the split is dramatic, then reload
- [ ] Screenshots of all six answers saved locally as a fallback
- [ ] Phone hotspot ready. Venue Wi-Fi is the biggest single demo risk

### The six questions, in this order

| # | Question | What the room sees | Mechanism |
| --- | --- | --- | --- |
| 1 | Who won IPL 2026 and who did they beat in the final? | Grounded: RCB beat Gujarat Titans by five wickets at Narendra Modi Stadium. Ungrounded: refuses, or gets the year wrong. This is the hook - run it before you explain anything. | Retrieval |
| 2 | How many IPL titles do RCB have and in which years? | Grounded says two, 2025 and 2026, and cites the file. Open the retrieved chunks - the citation is the whole point. | Retrieval + citation |
| 3 | Which teams have never won an IPL title? | Both sound plausible. Only one is complete and current. Plausible is not accurate. | Retrieval |
| 4 | What was the total IPL revenue and broadcast rights value in 2026? | Blocked on both paths. | Denied topic |
| 5 | Write me a Python function to calculate a batting strike rate. | Blocked on both paths. Nothing to do with cricket facts or grounding - purely policy. | Denied topic |
| 6 | Who won IPL 2027 and what was the final score? | 2027 has not happened. Grounded refuses. Ungrounded may invent a champion and a scoreline. Refusing is the correct answer. | Grounding |

Then flip the guardrail toggle **off** and re-run questions 4 and 5. The revenue
question gets answered and the Python function gets written. That before-and-after is
more persuasive than any slide about guardrails.

### Lines worth having ready

- On question 1: "Same model, same prompt, same guardrail. One of them read the
  documents and one of them is guessing. Which one would you put on a website?"
- On question 5: "Notice this has nothing to do with cricket. Retrieval is about
  *what it knows*. Guardrails are about *what it is allowed to do*. Two different
  problems, two different controls."
- On question 6: "This is the answer I care about most. It said no. Most of the work
  in a production RAG system is teaching it to say no."
- On the grounding check: it only fires on the KB path, because it needs a retrieved
  source to compare the answer against. That asymmetry is a real teaching moment,
  not a bug. Say it before someone in row three does.

### If it breaks

Backend errors come back with an `error` flag and render as a red badge rather than
crashing the UI. Read the error out loud and move on - an honest failure beats dead
air. If the network drops, switch to the saved screenshots and say the demo is live
and the Wi-Fi is not.

## Why the KB documents look the way they do

Two files, split by topic, so a question about franchises pulls the teams file and a
question about results pulls the seasons file rather than a mixed bag.

Flat declarative sentences, not question-and-answer pairs. Q&A format wastes half of
every chunk on the question, and retrieves worse when someone phrases their question
differently from how you wrote it.

Each file opens with a title, a date, and an authority line, so the retrieved chunks
have visible provenance when you open them on stage.

The seasons file states explicitly that financial questions and code requests are out
of scope. That is belt and braces: the guardrail does the actual blocking, but having
it written down means the grounded path has something to cite if a question slips past
the denied topic classifier.

Note the deliberate absence - nothing anywhere mentions IPL 2027, because it has not
happened. That is what makes question 6 work.

## Costs and cleanup

Under 1 USD for a full day at roughly 300 questions. S3 Vectors has no idle charge,
Lambda stays inside the free tier, Nova Lite is roughly 0.00006 USD per thousand
input tokens.

Delete the same day, in this order: Lambda function and function URL, guardrail,
knowledge base, S3 vector index, S3 bucket.

## Data note

IPL results in `kb-context/` are real and current as of August 2026. Verify the 2026
season details against a live source before you present, since anything this recent is
worth a second look.

## License

MIT. See `LICENSE`.
