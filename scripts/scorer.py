"""Group classified items into issues, score, dedupe against prior weeks,
persist aggregates (and ONLY aggregates), return the top items for the brief."""
import hashlib
import logging
from difflib import SequenceMatcher

log = logging.getLogger("scorer")
TOP_N = 15
SIMILARITY = 0.75      # summary similarity above this = same issue as a prior one
LOOKBACK_DAYS = 21


def _issue_hash(category: str, constituency: str, summary: str) -> str:
    return hashlib.sha256(f"{category}|{constituency}|{summary[:60].lower()}".encode()).hexdigest()[:16]


def _similar(a: str, b: str) -> float:
    """Max of character similarity and word-set overlap, so the same issue
    phrased with reordered words ('Potholes on Brighton Road' vs 'Brighton
    Road potholes') still merges."""
    char = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    wa, wb = set(a.lower().split()), set(b.lower().split())
    jaccard = len(wa & wb) / len(wa | wb) if wa | wb else 0.0
    return max(char, jaccard)


def group_and_score(conn, items: list[dict]) -> list[dict]:
    # 1. merge near-duplicate items from this run into single issues
    issues: list[dict] = []
    for it in items:
        merged = False
        for iss in issues:
            if iss["category"] == it["category"] and iss["constituency"] == it["constituency"] \
                    and _similar(iss["summary"], it["summary"]) >= SIMILARITY:
                iss["volume"] += 1
                iss["engagement"] += it["score"] + it["num_comments"]
                iss["urgency"] = max(iss["urgency"], it["urgency"])
                merged = True
                break
        if not merged:
            issues.append({
                "category": it["category"], "area": it["area"] or it["city"],
                "constituency": it["constituency"], "mp_name": it["mp_name"],
                "summary": it["summary"], "urgency": it["urgency"],
                "specificity": it["specificity"], "volume": 1,
                "engagement": it["score"] + it["num_comments"],
                "source_link": it["permalink"],
                "source_platform": it.get("platform", "reddit"),
            })

    # 2. uniqueness: check against issues already briefed in the last 3 weeks
    prior = conn.execute(
        f"SELECT hash, summary, constituency FROM issues WHERE last_seen > datetime('now','-{LOOKBACK_DAYS} days')"
    ).fetchall()
    for iss in issues:
        iss["hash"] = _issue_hash(iss["category"], iss["constituency"], iss["summary"])
        iss["repeat"] = any(
            p["constituency"] == iss["constituency"] and _similar(p["summary"], iss["summary"]) >= SIMILARITY
            for p in prior
        )

    # 3. score & trending
    avg_eng = (sum(i["engagement"] for i in issues) / len(issues)) if issues else 0
    for iss in issues:
        eng_norm = min(iss["engagement"] / max(avg_eng, 1), 3.0)
        freshness = 1.0
        uniqueness = 0.2 if iss["repeat"] else 1.0
        iss["score"] = round(
            eng_norm * 2 + iss["urgency"] + iss["specificity"] + freshness + uniqueness * 2
            + min(iss["volume"], 5), 2)
        iss["trending"] = int(avg_eng > 0 and iss["engagement"] >= 2 * avg_eng)
        iss["suggested_action"] = (
            "seed_motion" if iss["specificity"] >= 4 and iss["urgency"] >= 3 and not iss["repeat"]
            else "outreach" if iss["volume"] >= 3 or iss["trending"]
            else "watch")

    issues.sort(key=lambda i: i["score"], reverse=True)
    top = issues[:TOP_N]

    # 4. persist aggregates (update volume/engagement if issue already known)
    for iss in top:
        conn.execute("""
            INSERT INTO issues (hash, category, area, constituency, mp_name, summary,
                urgency, specificity, volume, engagement, source_link, source_platform,
                trending, suggested_action, status, first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'briefed', datetime('now'), datetime('now'))
            ON CONFLICT(hash) DO UPDATE SET
                volume = volume + excluded.volume,
                engagement = engagement + excluded.engagement,
                trending = excluded.trending,
                last_seen = datetime('now')
        """, (iss["hash"], iss["category"], iss["area"], iss["constituency"], iss["mp_name"],
              iss["summary"], iss["urgency"], iss["specificity"], iss["volume"], iss["engagement"],
              iss["source_link"], iss["source_platform"], iss["trending"], iss["suggested_action"]))
    conn.commit()
    log.info("Scored %d issues, kept top %d", len(issues), len(top))
    return top
