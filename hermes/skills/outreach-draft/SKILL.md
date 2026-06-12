---
name: state-outreach-draft
description: Draft a personalised message to a Facebook group admin or Residents Association chair about a live local issue and its state.com motion. Human sends it — never you. Disclosure line is mandatory.
version: 1.0.0
metadata:
  hermes:
    tags: [outreach, drafting, state]
    category: state
---

# Organiser Outreach Drafting

Goal: a group admin or RA chair genuinely wants to share a motion with their
members. Load state-brand-voice and state-compliance alongside this skill.

## Structure (60–120 words)

1. **Their context first**: name the group/area and the specific issue, with the
   aggregate stat: "There've been around [N] posts about [issue] in [area] this
   month."
2. **The useful thing**: "We've set up a verified motion to [MP/councillor name]
   asking for [specific ask] — residents can back it in under a minute: [link]."
3. **The no-pressure ask**: "If you think the group would find it useful, you're
   welcome to share it. Happy to answer anything."
4. **MANDATORY DISCLOSURE (never remove, never paraphrase away):**
   "For transparency: I work with state.com, the civic platform hosting the motion."

## Rules

- One message per admin per issue; check `outreach_log` first and skip anyone
  contacted in the last 30 days.
- Address the group role, not a researched personal profile. No flattery, no
  pressure, no follow-up cadence unless they reply.
- Never imply we're a resident of their group if we're not. Never offer anything
  in exchange for sharing.
- Output: the draft + a one-line note of who it's for. Mitesh personalises and
  sends from his own account. After he reports the outcome, record it in
  outreach_log (target, constituency, issue, dates, response — nothing personal).
