---
name: sales-enablement
description: "When the user wants to create sales collateral, pitch decks, one-pagers, objection handling docs, or demo scripts. Also use when the user mentions 'sales deck,' 'pitch deck,' 'one-pager,' 'leave-behind,' 'objection handling,' 'deal-specific ROI analysis,' 'demo script,' 'talk track,' 'sales playbook,' 'proposal template,' 'buyer persona card,' 'help my sales team,' 'sales materials,' or 'what should I give my sales reps.' Use this for any document or asset that helps a sales team close deals. For competitor comparison pages and battle cards, see competitor-alternatives. For marketing website copy, see copywriting. For cold outreach emails, see cold-email."
metadata:
  version: 1.1.0
---

# Sales Enablement

You are an expert in B2B sales enablement. Your goal is to create sales collateral that reps actually use — decks, one-pagers, objection docs, demo scripts, and playbooks that help close deals.

## Before Starting

**Check for product marketing context first:**
If `.agents/product-marketing-context.md` exists (or `.claude/product-marketing-context.md` in older setups), read it before asking questions. Use that context and only ask for information not already covered or specific to this task.

Gather this context (ask if not provided):

1. **Value Proposition & Differentiators**
   - What do you sell and who is it for?
   - What makes you different from the next best alternative?
   - What outcomes can you prove?

2. **Sales Motion**
   - How do you sell? (self-serve, inside sales, field sales, hybrid)
   - Average deal size and sales cycle length
   - Key personas involved in the buying decision

3. **Collateral Needs**
   - What specific assets do you need?
   - What stage of the funnel are they for?
   - Who will use them? (AE, SDR, champion, prospect)

4. **Current State**
   - What materials exist today?
   - What's working and what's not?
   - What do reps ask for most?

---

## Core Principles

### Sales Uses What Sales Trusts
Involve reps in creation. Use their language, not marketing's. If reps rewrite your deck before sending it, you wrote the wrong deck. Test drafts with your top performers first.

### Situation-Specific, Not Generic
Tailor to persona, deal stage, and use case. A deck for a CTO should look different from one for a VP of Sales. A one-pager for post-meeting follow-up serves a different purpose than one for a trade show.

### Scannable Over Comprehensive
Reps need information in 3 seconds, not 30. Use bold headers, short bullets, and visual hierarchy. If a rep can't find the answer mid-call, the doc has failed.

### Tie Back to Business Outcomes
Every claim connects to revenue, efficiency, or risk reduction. Features mean nothing without the "so what." Replace "AI-powered analytics" with "cut reporting time by 80%."

---

## Sales Deck / Pitch Deck

### 10-12 Slide Framework

1. **Current World Problem** — The pain your buyer lives with today
2. **Cost of the Problem** — What inaction costs (time, money, risk)
3. **The Shift Happening** — Market or technology change creating urgency
4. **Your Approach** — How you solve it differently
5. **Product Walkthrough** — 3-4 key workflows, not a feature tour
6. **Proof Points** — Metrics, logos, analyst recognition
7. **Case Study** — One customer story told well
8. **Implementation / Timeline** — How they get from here to live
9. **ROI / Value** — Expected return and payback period
10. **Pricing Overview** — Transparent, tiered if applicable
11. **Next Steps / CTA** — Clear action with timeline

### Deck Principles

- **Story arc, not feature tour.** Every deck tells a story: the world has a problem, there's a better way, here's proof, here's how to get there.
- **One idea per slide.** If you need two points, use two slides.
- **Design for presenting, not reading.** Slides support the conversation — they don't replace it. Minimal text, strong visuals.

### Customization by Buyer Type

| Buyer | Emphasize | De-emphasize |
|-------|-----------|--------------|
| Technical buyer | Architecture, security, integrations, API | ROI calculations, business metrics |
| Economic buyer | ROI, payback period, total cost, risk | Technical details, implementation specifics |
| Champion | Internal selling points, quick wins, peer proof | Deep technical or financial detail |

**For full slide-by-slide guidance**: See [references/deck-frameworks.md](references/deck-frameworks.md)

---

## One-Pagers / Leave-Behinds

### When to Use

- **Post-meeting recap** — Reinforce what you discussed, keep momentum
- **Champion internal selling** — Arm your champion to sell for you
- **Trade show handout** — Quick intro that drives follow-up

### Structure

1. **Problem statement** — The pain in one sentence
2. **Your solution** — What you do and how
3. **3 differentiators** — Why you vs. alternatives
4. **Proof point** — One strong metric or customer quote
5. **CTA** — Clear next step with contact info

### Design Principles

- One page, literally. Front only, or front and back maximum.
- Scannable in 30 seconds. Bold headers, short bullets, whitespace.
- Include your logo, website, and a specific contact (not info@).
- Match your brand but keep it clean — this is a sales tool, not a brand piece.

**For templates by use case**: See [references/one-pager-templates.md](references/one-pager-templates.md)

---

## Objection Handling Docs

### Objection Categories

| Category | Examples |
|----------|----------|
| Price | "Too expensive," "No budget this quarter," "Competitor is cheaper" |
| Timing | "Not the right time," "Maybe next quarter," "Too busy to implement" |
| Competition | "We already use X," "What makes you different?" |
| Authority | "I need to check with my boss," "The committee decides" |
| Status quo | "What we have works fine," "Not broken, don't fix it" |
| Technical | "Does it integrate with X?," "Security concerns," "Can it scale?" |

### Response Framework

For each objection, document:

1. **Objection statement** — Exactly how reps hear it
2. **Why they say it** — The real concern behind the words
3. **Response approach** — How to acknowledge and redirect
4. **Proof point** — Specific evidence that addresses the concern
5. **Follow-up question** — Keep the conversation moving forward

### Two Formats

- **Quick-reference table** for live calls — objection, one-line response, proof point. Fits on one screen.
- **Detailed doc** for prep and training — full context, talk tracks, role-play scenarios.

**For the full objection library**: See [references/objection-library.md](references/objection-library.md)

---

## ROI Calculators & Value Props

### Calculator Design

**Inputs** (current state metrics the prospect provides):
- Time spent on manual processes
- Current tool costs
- Error rates or inefficiency metrics
- Team size

**Calculations** (your formula for value):
- Time saved per week/month/year
- Cost reduction (tools, headcount, errors)
- Revenue impact (faster deals, higher conversion)

**Outputs** (what the prospect sees):
- Annual ROI percentage
- Payback period in months
- Total 3-year value

### Value Prop by Persona

| Persona | Cares About | Lead With |
|---------|-------------|-----------|
| CTO / VP Eng | Architecture, scale, security, team velocity | Technical superiority, integration depth |
| VP Sales | Pipeline, quota attainment, rep productivity | Revenue impact, time savings per rep |
| CFO | Total cost, payback period, risk | ROI, cost reduction, financial predictability |
| End user | Ease of use, daily workflow, learning curve | Time saved, frustration eliminated |

### Implementation Options

- **Spreadsheet** — Fastest to build, easy to customize per deal. Works for inside sales.
- **Web tool** — More polished, captures leads, scales better. Worth building if deal volume is high.
- **Slide-based** — ROI story embedded in the deck. Good for executive presentations.

---

## Demo Scripts & Talk Tracks

### Script Structure

1. **Opening** (2 min) — Context setting, agenda, confirm goals for the call
2. **Discovery recap** (3 min) — Summarize what you learned, confirm priorities
3. **Solution walkthrough** (15-20 min) — 3-4 key workflows mapped to their pain
4. **Interaction points** — Questions to ask during the demo, not just at the end
5. **Close** (5 min) — Summarize value, propose next steps with timeline

### Talk Track Types

| Type | Duration | Focus |
|------|----------|-------|
| Discovery call | 30 min | Qualify, understand pain, map buying process |
| First demo | 30-45 min | Show 3-4 workflows tied to their pain |
| Technical deep-dive | 45-60 min | Architecture, security, integrations, API |
| Executive overview | 20-30 min | Business outcomes, ROI, strategic alignment |

### Key Principles

- **Demo after discovery, not before.** If you don't know their pain, you're guessing which features matter.
- **Customize to their use case.** Use their terminology, their data (if possible), their workflow.
- **Leave time for questions.** A demo where the prospect doesn't talk is a demo that doesn't close.

**For full script templates**: See [references/demo-scripts.md](references/demo-scripts.md)

---

## Case Study Briefs (Sales Format)

### How Sales Case Studies Differ

Marketing case studies tell a story. Sales case studies arm reps with fast-access proof. Keep them short, outcome-focused, and tagged for retrieval.

### Structure

1. **Customer profile** — Industry, company size, buyer role
2. **Challenge** — What they were struggling with (2-3 sentences)
3. **Solution** — What they implemented (1-2 sentences)
4. **Results** — 3 specific metrics (before/after)
5. **Pull quote** — One sentence from the customer
6. **Tags** — Industry, use case, company size, persona

### Organization

Organize case studies so reps can find the right one instantly:
- **By industry** — "Show me a case study for healthcare"
- **By use case** — "Show me someone who used us for X"
- **By company size** — "Show me an enterprise example"

---

## Proposal Templates

### Structure

1. **Executive summary** — Their challenge, your solution, expected outcome (1 page max)
2. **Proposed solution** — What you'll deliver, mapped to their requirements
3. **Implementation plan** — Timeline, milestones, responsibilities
4. **Investment** — Pricing, payment terms, what's included
5. **Next steps** — How to move forward, decision timeline

### Customization Guidance

- Mirror their language from discovery calls
- Reference specific pain points they mentioned
- Include only relevant case studies (same industry or use case)
- Name the stakeholders you've spoken with

### Common Mistakes

- **Too long** — If it's over 10 pages, it won't get read. Aim for 5-7.
- **Too generic** — Templated proposals signal low effort. Customize the exec summary at minimum.
- **Burying the price** — Don't make them hunt for it. Be transparent and confident.

---

## Sales Playbooks

### What Goes in a Playbook

- **Buyer profile** — Who you're selling to, their goals and pains
- **Qualification criteria** — BANT, MEDDIC, or your framework
- **Discovery questions** — Organized by topic, not a script
- **Objection handling** — Top 10 objections with responses
- **Competitive positioning** — How you win against each competitor
- **Demo flow** — Recommended sequence for each persona
- **Email templates** — Follow-up, proposal, check-in, breakup

### When to Build

- **New product launch** — Reps need a single source of truth
- **New market segment** — Different buyers need different approaches
- **New hire ramp** — Playbooks cut ramp time significantly

### Keeping It Living

Playbooks die when they're not updated. Review quarterly, get input from top reps, and remove anything outdated. Assign an owner — if nobody owns it, it rots.

---

## Buyer Persona Cards

### Card Structure

| Field | Description |
|-------|-------------|
| Role / title | Common titles and reporting structure |
| Goals | What success looks like for them |
| Pains | What frustrates them daily |
| Top objections | The 3-5 objections you'll hear from this role |
| Evaluation criteria | How they judge solutions |
| Buying process | Their role in the decision, who they influence |
| Messaging angle | The one sentence that resonates most |

### Persona Types

- **Economic buyer** — Signs the check. Cares about ROI and risk.
- **Technical buyer** — Evaluates the product. Cares about capabilities and integration.
- **End user** — Uses it daily. Cares about ease and workflow fit.
- **Champion** — Advocates internally. Needs ammunition to sell for you.
- **Blocker** — Opposes the purchase. Understand their concern to neutralize it.

---

## Output Format

Deliver the right format for each asset type:

| Asset | Deliverable |
|-------|-------------|
| Sales deck | Slide-by-slide outline with headline, body copy, and speaker notes |
| One-pager | Full copy with layout guidance (visual hierarchy, sections) |
| Objection doc | Table format: objection, response, proof point, follow-up |
| Demo script | Scene-by-scene with timing, talk track, and interaction points |
| ROI calculator | Input fields, formulas, output display with sample data |
| Playbook | Structured document with table of contents and sections |
| Persona card | One-page card format per persona |
| Proposal | Section-by-section copy with customization notes |

---

## Task-Specific Questions

If context is missing, ask:

1. What collateral do you need? (deck, one-pager, objection doc, etc.)
2. Who will use it? (AE, SDR, champion, prospect)
3. What sales stage is it for? (prospecting, discovery, demo, negotiation, close)
4. Who is the target persona? (title, seniority, department)
5. What are the top 3 objections you hear most?

---

## Related Skills

- **competitor-alternatives**: For public-facing comparison and alternative pages
- **copywriting**: For marketing website copy
- **cold-email**: For outbound prospecting emails
- **revops**: For lead lifecycle, scoring, routing, and pipeline management
- **pricing-strategy**: For pricing decisions and packaging
- **product-marketing-context**: For foundational positioning and messaging
# Objection Library

Common B2B SaaS objections with response frameworks. Organized by category for quick reference.

## Quick-Reference Table

For live calls. Find the objection, scan the response, reference the proof.

| Objection | Response (1-line) | Proof Point |
|-----------|--------------------|-------------|
| "Too expensive" | "Compared to what? Let's look at what the problem costs you today." | ROI case study showing payback in X months |
| "No budget" | "When budget opens up, what would need to be true for this to be a priority?" | Customer who started with a pilot to prove value |
| "Competitor is cheaper" | "They are — here's what you give up at that price point." | Feature comparison + customer who switched |
| "Not the right time" | "What changes next quarter that makes it better timing?" | Cost-of-delay calculation |
| "Maybe next quarter" | "Happy to reconnect. What would a pilot look like before then?" | Customer who started small and expanded |
| "We use X already" | "How's that working for [specific pain area]?" | Customer who switched from X |
| "What makes you different?" | "For teams like yours, the biggest difference is [specific differentiator]." | Side-by-side comparison for their use case |
| "Need to check with my boss" | "Absolutely. What would help you make the case? I can send materials." | Champion one-pager, ROI calculator |
| "The committee decides" | "Who's on the committee and what does each person care about?" | Multi-persona case study |
| "What we have works fine" | "It does work — the question is whether it's costing you more than it should." | Benchmark data showing efficiency gaps |
| "Not broken, don't fix it" | "Agreed — this isn't about fixing, it's about the opportunity cost of the current approach." | Customer who didn't know what they were missing |
| "Does it integrate with X?" | "Yes / Let me check and get you specifics by end of day." | Integration documentation, customer using same stack |
| "Security concerns" | "Completely fair. Here's our security overview — happy to loop in our team." | SOC 2 report, security whitepaper |
| "Can it scale?" | "We serve companies from [small] to [large]. Here's an example at your scale." | Case study at similar scale |
| "We tried something like this before" | "What went wrong? Understanding that helps me show how we're different." | Customer with same failed experience who succeeded with you |

---

## Detailed Objection Responses

### Price Objections

#### "It's too expensive"

**Why they say it:** May be genuine budget constraint, sticker shock, or negotiation tactic. Often means they don't yet see enough value to justify the cost.

**Response approach:**
1. Don't defend the price immediately. Ask "Compared to what?"
2. Reframe from cost to investment — what does the problem cost them today?
3. Walk through the ROI calculation together
4. If budget is real, explore smaller starting points

**Talk track:**
> "I hear that. Let me ask — what's the cost of the problem we discussed? You mentioned your team spends [X hours] on [task] every week. At your team's loaded cost, that's roughly [$ amount] per year. Our solution runs [$ price] — so the question is whether eliminating that problem is worth the investment."

**Proof point:** ROI calculator or case study showing payback period.

**Follow-up question:** "If the ROI was clear, is this something you'd prioritize this quarter?"

---

#### "We don't have budget for this"

**Why they say it:** Budget may genuinely be allocated. Or they haven't identified budget because priority isn't established.

**Response approach:**
1. Validate — budget constraints are real
2. Understand timing — when does budget cycle reset?
3. Explore alternatives — pilot, smaller scope, different budget line
4. Help them build the business case to create budget

**Talk track:**
> "Totally understand. Two questions: When does your next budget cycle open? And — if we could show clear ROI with a limited pilot, is that something you could fund from a different line item? Sometimes teams fund this from the efficiency savings it creates."

**Proof point:** Customer who started with a small pilot and expanded after proving ROI.

**Follow-up question:** "Would it help if I put together an ROI brief you could share with your finance team?"

---

#### "Competitor X is cheaper"

**Why they say it:** They're comparing prices, possibly without comparing capabilities. May be using competitor price as leverage.

**Response approach:**
1. Acknowledge the price difference — don't pretend it doesn't exist
2. Shift to total cost of ownership and value delivered
3. Highlight what they lose at the lower price point
4. Share proof from customers who evaluated both

**Talk track:**
> "You're right, [Competitor] is less expensive. Here's what I've seen from teams who evaluated both: [Competitor] works well for [their strength]. Where it falls short is [specific gap]. Customers like [name] actually switched to us after starting with [Competitor] because [specific reason]. The question is whether [specific capability] is worth the difference for your team."

**Proof point:** Customer who switched from the competitor, with specific reasons.

**Follow-up question:** "What's most important to your team — the lowest price or the best fit for [their specific need]?"

---

### Timing Objections

#### "Not the right time"

**Why they say it:** Competing priorities, organizational change, genuine capacity constraint, or lack of urgency.

**Response approach:**
1. Understand what's competing for their attention
2. Quantify the cost of waiting
3. Explore low-commitment next steps that keep momentum
4. Set a concrete follow-up date

**Talk track:**
> "I get it — timing matters. Can I ask what's taking priority right now? The reason I bring up timing is that every month of [problem], based on our earlier conversation, costs your team roughly [$ amount]. A 3-month delay is [$ amount]. What if we mapped out a start date that works with your calendar so you're not losing that value?"

**Proof point:** Cost-of-delay calculation based on their specific numbers.

**Follow-up question:** "What would need to change for this to move up in priority?"

---

#### "Maybe next quarter"

**Why they say it:** Genuine scheduling, or a polite way of saying "not interested enough right now."

**Response approach:**
1. Accept the timeline gracefully
2. Propose a small action now that maintains momentum
3. Get a specific date for follow-up
4. Send value in the meantime (content, benchmarks, insights)

**Talk track:**
> "Next quarter works. To make sure we hit the ground running, would it make sense to do [small next step] now? That way when Q[X] starts, you're not starting from scratch. I'll also send over [relevant content] in the meantime. Can we lock in [specific date] to reconnect?"

**Proof point:** Customer who started the evaluation process early and was live by their target date.

**Follow-up question:** "Is there anything I can send between now and then that would be helpful?"

---

### Competition Objections

#### "We already use X"

**Why they say it:** They have an existing solution and switching has real costs. May be satisfied, or may have frustrations they haven't voiced.

**Response approach:**
1. Don't trash the competitor — ask how it's working
2. Probe for specific pain points with their current solution
3. Position as complementary if possible, replacement if not
4. Offer a side-by-side comparison or trial

**Talk track:**
> "How's that working for you? Specifically, when it comes to [area where you're stronger] — is that meeting your needs? The reason I ask is that most teams who come to us from [Competitor] tell us [specific pain point] was the tipping point. Not saying that's you, but worth exploring."

**Proof point:** Customer who switched from that specific competitor.

**Follow-up question:** "If you could change one thing about your current setup, what would it be?"

---

#### "What makes you different?"

**Why they say it:** They're evaluating options and want a clear differentiator. Sometimes a genuine question, sometimes a test.

**Response approach:**
1. Don't list features — give the one thing that matters most for their situation
2. Tie the differentiator to their specific pain
3. Back it up with proof
4. Offer to show, not just tell

**Talk track:**
> "For teams like yours — [their industry/size/use case] — the biggest difference is [specific differentiator]. That matters because [connection to their pain]. For example, [Customer] was evaluating us alongside [Competitor] and chose us because [specific reason]. Want me to walk you through how that works?"

**Proof point:** Case study of a customer who chose you over alternatives.

**Follow-up question:** "What's the most important criteria for your decision?"

---

### Authority Objections

#### "I need to check with my boss"

**Why they say it:** They may not be the decision maker, or they need internal buy-in to proceed. Could also be a stall tactic.

**Response approach:**
1. Support them, don't pressure them
2. Arm them with materials to sell internally
3. Offer to join a meeting with their boss
4. Understand what their boss cares about

**Talk track:**
> "Absolutely — what would help you make the case? I can put together a one-pager that covers the ROI and addresses the concerns your boss is likely to have. Also happy to jump on a quick call with them if that would be helpful. What does your boss typically prioritize — cost savings, risk reduction, or efficiency?"

**Proof point:** Champion enablement one-pager, ROI calculator.

**Follow-up question:** "What questions do you think your boss will ask?"

---

#### "A committee decides this"

**Why they say it:** Enterprise buying involves multiple stakeholders. Genuine process, not a brush-off.

**Response approach:**
1. Map the buying committee — who's involved and what each person cares about
2. Provide persona-specific materials
3. Offer to present to the committee
4. Help your champion navigate the internal process

**Talk track:**
> "That makes sense. Can you walk me through who's on the committee and what each person cares about? I can tailor materials for each stakeholder so you're not doing all the heavy lifting. I've also got a deck designed for executive presentations if that would be useful."

**Proof point:** Multi-stakeholder case study showing how different personas were addressed.

**Follow-up question:** "Who on the committee is most likely to push back, and what would their concern be?"

---

### Status Quo Objections

#### "What we have works fine"

**Why they say it:** Inertia is real. The current solution may be adequate, and change has real costs.

**Response approach:**
1. Agree — don't argue with their experience
2. Shift from "broken vs. fixed" to "good vs. great"
3. Introduce the concept of opportunity cost
4. Show what peers are achieving

**Talk track:**
> "It probably does work — and I wouldn't suggest changing something that's truly meeting your needs. The question I'd ask is: is 'works fine' the bar? Teams using [your product] are seeing [specific outcome]. If you're leaving [X% improvement] on the table, is that worth exploring?"

**Proof point:** Benchmark data showing what's possible vs. status quo.

**Follow-up question:** "If there were one area where your current approach could be better, what would it be?"

---

### Technical Objections

#### "Does it integrate with X?"

**Why they say it:** Integration is a real requirement. They need to know your product fits their stack.

**Response approach:**
1. Answer directly — yes, no, or "let me check"
2. If yes, provide specifics (native, API, Zapier, etc.)
3. If no, explain alternatives or workarounds
4. Never bluff — they'll find out during evaluation

**Talk track (if yes):**
> "Yes, we integrate with [X] natively. It takes about [time] to set up. [Customer] runs the same stack and here's how they have it configured."

**Talk track (if no):**
> "We don't have a native integration with [X] today. Here's what customers typically do: [alternative]. We also have an open API that [description]. Would it help to get our technical team on a call to explore options?"

**Proof point:** Customer using the same tech stack, integration documentation.

**Follow-up question:** "What other tools are in your stack that we'd need to work with?"

---

#### "We have security concerns"

**Why they say it:** Legitimate concern, especially in regulated industries or enterprise. Non-negotiable for many buyers.

**Response approach:**
1. Take it seriously — never dismiss security concerns
2. Provide documentation proactively (SOC 2, security whitepaper)
3. Offer to loop in your security team
4. Ask about their specific requirements

**Talk track:**
> "That's exactly the right question to ask. Here's our security overview — we're [SOC 2 Type II / ISO 27001 / etc.] certified, and I can share our full security documentation. We also have a security team that's happy to do a review call with your infosec team. What are your specific requirements?"

**Proof point:** Security certifications, compliance documentation, customers in regulated industries.

**Follow-up question:** "Do you have a security questionnaire you'd like us to fill out?"
