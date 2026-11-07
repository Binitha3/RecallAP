# Member 1 content drafts

These are drafts for the Member 1 technical angle in the team playbook. The official Content Guide was not included with the supplied materials, so confirm its word counts, tags, hashtags, platform, and submission format before publishing. The figures below are from the latest full 60-invoice run on 2026-11-07.

## Article draft

# We Gave an AP Agent a Memory, Then Made Sure It Could Not Be Reckless

Every accounts-payable team has a Priya: the person who remembers why a freight vendor’s invoice is four percent above its purchase order, or which small renewal can be processed without a new PO. When Priya is away, that knowledge often disappears into old email threads and spreadsheets. A new clerk has to ask the same questions all over again.

That is the problem behind RecallAP, our invoice-exception agent built for HackwithHyderabad 3.0. It checks structured invoices against purchase orders, goods-receipt notes, and vendor records. When something does not match, it looks for relevant past decisions, proposes an action, and sends the result through deterministic safety checks. It can resolve a supported case, ask for vendor information, or send the case to a person.

The hard part is deciding when the agent has learned enough. A language model can sound confident even when it has weak evidence. We kept detection and safety in Python, and used the model only to propose a structured decision. RecallAP’s Hindsight adapter stores human resolutions, recalls relevant cases before a decision, and can reflect on a vendor’s history for a briefing. The memories have three contexts: vendor profiles, resolution cases, and approver preferences.

Each resolution case is stored as a compact record with a case ID, vendor, exception type, decision, reason, and any relevant limit. Hindsight may return an extracted fact that omits the original case number or exact wording. To keep citations verifiable, RecallAP requests source chunks and carries the case ID in metadata. The guardrail layer then checks that cited IDs were actually recalled and that the case applies to this vendor and exception.

Memory does not get the final say. Changed bank details always require human review. Other automatic actions need at least two distinct, cited, human-approved and reusable precedents for every exception type, must remain within the precedent’s stated range, and must meet the confidence and invoice-amount limits. A one-off manual approval can still be retained for audit, but is marked manual-only so the agent does not generalize it into a standing approval rule. Agent-made decisions do not count as human precedents.

We ran the verified 60-invoice sequence with memory enabled and disabled. Memory-on produced a **41.67% correct touchless rate (25 of 60)**: four clean invoices and 21 correct automatic exception resolutions. All 25 automatic decisions were correct in this run; it sent 35 invoices to human review, with zero unsafe touchless decisions. Memory-off produced a **13.33% correct touchless rate (8 of 60)**: four clean invoices and four exact-duplicate rejections. It sent 52 invoices to human review, also with zero unsafe touchless decisions. The all-invoice denominator counts a human review as not touchless; the sample precision is not a guarantee of 100% accuracy beyond these cases. Memory improved correct touchless coverage by 28.34 percentage points. These measured results use synthetic invoices and a simulated approver.

The result also changed how we think about learning. Retaining a human’s final action is not always enough: some approvals are exceptional, one-time decisions that should not become reusable policy. We added an explicit manual-only marker and tightened the guardrails to require multiple matching precedents. Groq rate limits can also reduce automation by sending more proposals to human review. A safe fallback is useful, but the next iteration should improve throughput without weakening the evidence checks.

RecallAP is still a prototype. The current demo uses structured JSON, synthetic vendor data, and a simulated approver. OCR intake, ERP connections, real approval roles, and a pilot with an AP team are future work. Our next step is to improve repeat-case coverage and make the learning curve visible in the interface, then test the system with real users and approval policies.

**Project:** [RecallAP on GitHub](https://github.com/Binitha3/RecallAP)

**Built for:** HackwithHyderabad 3.0

**Memory system:** [Hindsight by Vectorize](https://hindsight.vectorize.io/)

## Social post draft

Most AP teams have someone who remembers how every unusual invoice was handled. We built RecallAP to make those decisions searchable and reusable, with Hindsight memory and safety checks in Python.

On our 60-invoice run, memory-on achieved a 41.67% correct touchless rate (25/60), compared with 13.33% (8/60) memory-off. Precision was 100% across the 25 automatic memory-on decisions in this sample, and both runs had zero unsafe touchless decisions. Human-reviewed invoices are not counted as automatic successes, so the metric reflects our conservative policy as well as the model. These measured results use synthetic invoices and a simulated approver.

The agent cites prior cases, checks that each precedent applies, and escalates when evidence is weak. A manual-only approval stays in the audit trail without becoming a rule for future automation.

Code and results: https://github.com/Binitha3/RecallAP

Demo video: [add link after recording]

Team: [add teammates’ handles]

#HackwithHyderabad #AIagents #Hindsight #AccountsPayable #ResponsibleAI

## Video script draft (60–90 seconds)

**0:00–0:10 — On camera**
“Hi, I’m Binitha, Member 1 on RecallAP. We built an invoice-exception agent that can remember how similar cases were resolved.”

**0:10–0:30 — Show the backend flow or API response**
“A structured invoice is checked against its PO, goods receipt, and vendor data. Hindsight recalls relevant cases, Groq proposes an action, and Python validates the evidence before anything is marked resolved.”

**0:30–0:50 — Show a cited case and the next similar invoice**
“Each resolution has a case ID and its original source text is checked before a citation is accepted. One case is not enough for automation: we require two matching, reusable human precedents for each exception.”

**0:50–1:10 — Show changed bank details or manual-only case**
“Safety stays in code. Changed bank details always go to a person. One-time manual approvals are kept for audit, but they cannot teach the agent to auto-approve later cases.”

**1:10–1:25 — Show benchmark numbers and repo**
"On our 60-invoice run, memory-on reached 41.67% correct touchless decisions versus 13.33% memory-off. All 25 automatic memory-on decisions were correct in this sample, with zero unsafe touchless decisions. These results use synthetic invoices and a simulated approver. Here are the code and run artifacts. Thanks to the Hindsight team and HackwithHyderabad."

Before publishing, replace the demo link and team handles, and align the article/post tags and video format with the official Content Guide.
