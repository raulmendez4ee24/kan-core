---
name: cold-email
description: Write B2B cold emails and follow-up sequences that get replies. Use when the user wants to write cold outreach emails, prospecting emails, cold email campaigns, sales development emails, or SDR emails. Also use when the user mentions "cold outreach," "prospecting email," "outbound email," "email to leads," "reach out to prospects," "sales email," "follow-up email sequence," "nobody's replying to my emails," or "how do I write a cold email." Covers subject lines, opening lines, body copy, CTAs, personalization, and multi-touch follow-up sequences. For warm/lifecycle email sequences, see email-sequence. For sales collateral beyond emails, see sales-enablement.
metadata:
  version: 1.1.0
---

# Cold Email Writing

You are an expert cold email writer. Your goal is to write emails that sound like they came from a sharp, thoughtful human — not a sales machine following a template.

## Before Writing

**Check for product marketing context first:**
If `.agents/product-marketing-context.md` exists (or `.claude/product-marketing-context.md` in older setups), read it before asking questions. Use that context and only ask for information not already covered or specific to this task.

Understand the situation (ask if not provided):

1. **Who are you writing to?** — Role, company, why them specifically
2. **What do you want?** — The outcome (meeting, reply, intro, demo)
3. **What's the value?** — The specific problem you solve for people like them
4. **What's your proof?** — A result, case study, or credibility signal
5. **Any research signals?** — Funding, hiring, LinkedIn posts, company news, tech stack changes

Work with whatever the user gives you. If they have a strong signal and a clear value prop, that's enough to write. Don't block on missing inputs — use what you have and note what would make it stronger.

---

## Writing Principles

### Write like a peer, not a vendor

The email should read like it came from someone who understands their world — not someone trying to sell them something. Use contractions. Read it aloud. If it sounds like marketing copy, rewrite it.

### Every sentence must earn its place

Cold email is ruthlessly short. If a sentence doesn't move the reader toward replying, cut it. The best cold emails feel like they could have been shorter, not longer.

### Personalization must connect to the problem

If you remove the personalized opening and the email still makes sense, the personalization isn't working. The observation should naturally lead into why you're reaching out.

See [personalization.md](references/personalization.md) for the 4-level system and research signals.

### Lead with their world, not yours

The reader should see their own situation reflected back. "You/your" should dominate over "I/we." Don't open with who you are or what your company does.

### One ask, low friction

Interest-based CTAs ("Worth exploring?" / "Would this be useful?") beat meeting requests. One CTA per email. Make it easy to say yes with a one-line reply.

---

## Voice & Tone

**The target voice:** A smart colleague who noticed something relevant and is sharing it. Conversational but not sloppy. Confident but not pushy.

**Calibrate to the audience:**

- C-suite: ultra-brief, peer-level, understated
- Mid-level: more specific value, slightly more detail
- Technical: precise, no fluff, respect their intelligence

**What it should NOT sound like:**

- A template with fields swapped in
- A pitch deck compressed into paragraph form
- A LinkedIn DM from someone you've never met
- An AI-generated email (avoid the telltale patterns: "I hope this email finds you well," "I came across your profile," "leverage," "synergy," "best-in-class")

---

## Structure

There's no single right structure. Choose a framework that fits the situation, or write freeform if the email flows naturally without one.

**Common shapes that work:**

- **Observation → Problem → Proof → Ask** — You noticed X, which usually means Y challenge. We helped Z with that. Interested?
- **Question → Value → Ask** — Struggling with X? We do Y. Company Z saw [result]. Worth a look?
- **Trigger → Insight → Ask** — Congrats on X. That usually creates Y challenge. We've helped similar companies with that. Curious?
- **Story → Bridge → Ask** — [Similar company] had [problem]. They [solved it this way]. Relevant to you?

For the full catalog of frameworks with examples, see [frameworks.md](references/frameworks.md).

---

## Subject Lines

Short, boring, internal-looking. The subject line's only job is to get the email opened — not to sell.

- 2-4 words, lowercase, no punctuation tricks
- Should look like it came from a colleague ("reply rates," "hiring ops," "Q2 forecast")
- No product pitches, no urgency, no emojis, no prospect's first name

See [subject-lines.md](references/subject-lines.md) for the full data.

---

## Follow-Up Sequences

Each follow-up should add something new — a different angle, fresh proof, a useful resource. "Just checking in" gives the reader no reason to respond.

- 3-5 total emails, increasing gaps between them
- Each email should stand alone (they may not have read the previous ones)
- The breakup email is your last touch — honor it

See [follow-up-sequences.md](references/follow-up-sequences.md) for cadence, angle rotation, and breakup email templates.

---

## Quality Check

Before presenting, gut-check:

- Does it sound like a human wrote it? (Read it aloud)
- Would YOU reply to this if you received it?
- Does every sentence serve the reader, not the sender?
- Is the personalization connected to the problem?
- Is there one clear, low-friction ask?

---

## What to Avoid

- Opening with "I hope this email finds you well" or "My name is X and I work at Y"
- Jargon: "synergy," "leverage," "circle back," "best-in-class," "leading provider"
- Feature dumps — one proof point beats ten features
- HTML, images, or multiple links
- Fake "Re:" or "Fwd:" subject lines
- Identical templates with only {{FirstName}} swapped
- Asking for 30-minute calls in first touch
- "Just checking in" follow-ups

---

## Data & Benchmarks

The references contain performance data if you need to make informed choices:

- [benchmarks.md](references/benchmarks.md) — Reply rates, conversion funnels, expert methods, common mistakes
- [personalization.md](references/personalization.md) — 4-level personalization system, research signals
- [subject-lines.md](references/subject-lines.md) — Subject line data and optimization
- [follow-up-sequences.md](references/follow-up-sequences.md) — Cadence, angles, breakup emails
- [frameworks.md](references/frameworks.md) — All copywriting frameworks with examples

Use this data to inform your writing — not as a checklist to satisfy.

---

## Related Skills

- **copywriting**: For landing pages and web copy
- **email-sequence**: For lifecycle/nurture email sequences (not cold outreach)
- **social-content**: For LinkedIn and social posts
- **product-marketing-context**: For establishing foundational positioning
- **revops**: For lead scoring, routing, and pipeline management
# Cold Email Copywriting Frameworks

Frameworks beat templates — they teach thinking patterns, not copy-paste shortcuts.

## PAS — Problem, Agitate, Solution (default)

**Structure:** Identify pain → Amplify consequences → Present solution + soft CTA.
**Best for:** Problem-aware but not solution-aware prospects. The workhorse framework.

> Most VP Sales at companies your size spend 5+ hours/week on manual CRM reporting. That's 250+ hours/year not spent coaching reps — and often means inaccurate forecasts reaching leadership. We built a tool that auto-generates CRM reports in real time. Teams like Datadog reduced reporting time by 80%. Would it make sense to see how?

## BAB — Before, After, Bridge

**Structure:** Current painful situation → Ideal future → Your product as the bridge.
**Best for:** Transformation-driven offers with clear before/after. Emotional decision-makers.

> Right now, your team is likely spending hours manually sourcing leads — feast or famine each quarter. Imagine qualified leads arriving daily on autopilot, reps spending 100% of their time selling. That's what our platform does. Companies like HubSpot saw a 40% pipeline increase within 90 days. Can I show you how?

## QVC — Question, Value, CTA

**Structure:** Targeted pain question → Brief value → Direct next step.
**Best for:** C-suite prospects who prefer brevity. Qualify interest immediately.

> Are your SDRs spending more time researching than selling? We help sales teams automate prospect research so reps focus on conversations. Clients see 3x more meetings per rep per week. Worth a 10-minute demo?

## AIDA — Attention, Interest, Desire, Action

**Structure:** Hook/stat → Address specific challenge → Social proof/outcome → Clear CTA.
**Best for:** Data-driven prospects, high-ticket pitches with strong stats.

> Companies in pharma lose 30% of leads due to manual outreach. Given {{Company}}'s growth this quarter, pipeline velocity is likely top of mind. Customers like Pfizer use our platform to automate lead qualification — cutting time-to-contact by 60%. Worth a 15-minute call?

## PPP — Praise, Picture, Push

**Structure:** Genuine compliment → How things could be better → Gentle push to action.
**Best for:** Senior prospects who respond to relationship-building. Requires genuine trigger.

> Your keynote on scaling SDR teams was spot-on — especially on ramp time as the hidden cost. What if you could cut that in half? Our in-inbox coach helps new reps write effective emails from day one with real-time scoring. Open to a quick chat about how this could support your growth?

## Star-Story-Solution

**Structure:** Introduce character (customer) → Tell challenge narrative → Reveal results.
**Best for:** Strong customer success stories. Humanizes the pitch.

> Last year, Sarah — VP Sales at a Series B startup — had 5 SDRs competing against a rival with 20. Her team was getting crushed on volume. They adopted our AI prospecting tool and sent hyper-personalized emails at 3x pace without losing quality. Within 90 days, they booked more meetings than their competitor's entire team. Happy to share how this could work for {{Company}}.

## SCQ — Situation, Complication, Question

**Structure:** Current reality → Complicating challenge → Question that speaks to need → Optional answer.
**Best for:** Consultative selling. Mirrors how professionals present to leadership.

> Your team doubled this year. That usually means onboarding is eating into selling time. How are you handling ramp for new hires?

## ACCA — Awareness, Comprehension, Conviction, Action

**Structure:** Contrarian hook → Explain benefit simply → Provide proof → Strong CTA.
**Best for:** Analytical buyers who need evidence (engineers, CFOs, ops leaders).

> Most sales teams measure rep activity. The top 5% measure rep efficiency instead. When Acme switched, they booked 40% more meetings with fewer emails. Worth seeing how?

## 3C's (Alex Berman)

**Structure:** Compliment → Case Study → CTA.
**Best for:** Agency/services cold outreach. Case study does the heavy lifting.

> Big fan of [Company]. We just built an app for [Competitor] that does XYZ. I have a few more ideas. Interested?

## Mouse Trap (Lavender/Will Allred)

**Structure:** Observation + Binary value-prop question. 1–2 sentences total.
**Best for:** Maximum brevity. Impulsive reply based on curiosity.

> Looks like you're hiring reps. Would it be helpful to get a more granular look at how they're ramping on email?

## Justin Michael Method

**Structure:** Trigger/Pain → Solution hint → Binary CTA. 1–3 sentences, no intro.
**Best for:** High-velocity SDR teams. Mobile-optimized. Deliberately polarizing.

Spend max 1 minute on personalization. Use industry/persona-level signals. For top-tier prospects, quote their own words from interviews — they almost always respond.

## Vanilla Ice Cream (Lavender)

**Structure:** Observation → Problem/Insight → Credibility → Solution → Call-to-Conversation.
**Best for:** Universal "base" framework that works everywhere. Five parts.

## PASTOR (Ray Edwards)

**Structure:** Problem → Amplify → Story → Testimony → Offer → Response.
**Best for:** Longer-form or multi-email sequences. Consulting, education, complex B2B services. Each element can be developed across separate touches.
# Subject Line Optimization

The subject line determines whether the email gets read. The data is counterintuitive: **short, boring, internal-looking subject lines win decisively.**

## Length: 2–4 words

- 2-word subject lines get **60% more opens** than 5-word (Lavender).
- Going from 2 to 4 words reduces replies by **17.5%**.
- 2–4 words yield **46% open rates** vs 34% for 10 words (Belkins, 5.5M emails).
- Mobile truncates at 30–35 characters — brevity is practical necessity.

## Internal Camouflage Principle

Subject lines that look like they came from a colleague, not a vendor, double open rates (Gong). Buyers mentally categorize before opening — if it looks like sales, it's filtered.

**High-performing examples:** "reply rates" · "trial delays" · "hiring ops" · "employee turnover" · "Q2 forecast" · "new patients" · "personalization issue" · "second page"

## Capitalization: lowercase wins

All-lowercase has highest open rates (Gong, 85M+ emails). Lowercase looks more personal/internal. For cold outreach specifically, lowercase beats title case.

## Personalization: context over name

Personalized subject lines boost opens **26–50%**, but type matters:

- **First name in subject line → 12% fewer replies.** Signals automation.
- **Contextual personalization works:** pain points, competitors, trigger events, industry challenges.
- Use {{painPoint}}, {{competitor}}, {{commonGround}} — not {{firstName}}.

## Questions: only when highly specific

Data conflicts: Belkins says questions perform well (46% open rate). Lavender says questions lower opens by **56%**. Resolution: **specific pain questions work** ("Need help with {{challenge}}?"), **generic questions fail** ("Quick question?" / "Have 15 minutes?"). Default to statements.

## What to Avoid

| Anti-pattern                                   | Impact                      |
| ---------------------------------------------- | --------------------------- |
| Salesy language ("increase," "boost," "ROI")   | -17.9% opens                |
| Urgency words ("ASAP," "urgent")               | Below 36% opens             |
| Excessive punctuation ("!!!" or "??")          | -36% opens                  |
| Numbers and percentages                        | -46% opens                  |
| Emojis                                         | Hurt B2B professionalism    |
| Pitching product in subject                    | -57% replies                |
| Empty/no subject line                          | +30% opens but -12% replies |
| Spam triggers ("free," "guarantee," "act now") | Deliverability risk         |

## C-Suite Subject Lines

Executives receive 300–400 emails daily, decide in seconds. They respond **23% more often** than non-C-suite when emails pass their filter (6.4% reply rate).

What works: ultra-concise, human, understated. "{{companyInitiative}}" · "thank you" · "an update" · "a question" · reference to a specific project or trigger event.

Anything "salesy" is immediately rejected.
