#!/usr/bin/env python3
"""The unified "Ludodex score": one 0-100 number rolled up from every rating
source we have (IGDB critic/user, Steam % positive, Metacritic, ScreenScraper,
GOG), while each source's original rating is retained as-is elsewhere.

Two ideas combine:

  * Critic-vs-user lean. Critic-type sources share a base weight `w_critic`
    (default 0.6); user-type sources share `1 - w_critic`. So critics lead but
    players still count.

  * Confidence from vote count (Bayesian). A user score backed by few reviews is
    both down-weighted AND shrunk toward the prior mean, so a single 5-star vote
    can't crown a game. A source reaches full effect once it clears ~`n_full`
    reviews; below that it "decreasingly affects" the result. Critic aggregates
    (curated, inherently low-count) are trusted with a small `n_full_critic`.

  universal = Σ Wᵢ·adjᵢ / Σ Wᵢ
    Wᵢ   = base(typeᵢ) · cᵢ
    adjᵢ = cᵢ·scoreᵢ + (1-cᵢ)·M          # shrink toward prior mean M
    cᵢ   = min(1, votesᵢ / n_full)        # or a fixed value when votes unknown
"""

DEFAULTS = {
    "w_critic": 0.6,          # critic pool base weight (user pool = 1 - this)
    "prior_mean": 70.0,       # M: where low-confidence scores are pulled toward
    "n_full_user": 400.0,     # community reviews for full confidence — high on purpose:
                              # Steam %-positive only reads as quality with a big sample
    "n_full_critic": 5.0,     # critic outlets for full confidence (few, but trusted)
    "unknown_user_conf": 0.5,   # confidence for a user score with no vote count
    "unknown_critic_conf": 1.0,  # critic scores are trusted even without a count
    # Critic validation: a game with NO critic rating (purely community-scored) is
    # pulled toward the prior by this factor, so an un-reviewed niche darling can't
    # top a critically-acclaimed classic. 1.0 disables the penalty.
    "no_critic_factor": 0.6,
}


def _confidence(kind, votes, p):
    if votes is None:
        return p["unknown_critic_conf"] if kind == "critic" else p["unknown_user_conf"]
    n = p["n_full_critic"] if kind == "critic" else p["n_full_user"]
    return max(0.0, min(1.0, votes / n)) if n > 0 else 1.0


def compute(ratings, params=None):
    """ratings: iterable of dicts {kind: 'critic'|'user', score: 0-100, votes: int|None}.
    Returns (universal:int|None, breakdown:dict) — breakdown carries the pooled
    critic/user values actually used, for display/debugging."""
    p = dict(DEFAULTS, **(params or {}))
    M, wc = p["prior_mean"], p["w_critic"]
    num = den = 0.0
    pools = {"critic": [0.0, 0.0], "user": [0.0, 0.0]}   # kind -> [wsum, wsum*adj]
    for r in ratings:
        s = r.get("score")
        if s is None:
            continue
        kind = "critic" if r.get("kind") == "critic" else "user"
        c = _confidence(kind, r.get("votes"), p)
        adj = c * s + (1 - c) * M
        base = wc if kind == "critic" else (1 - wc)
        w = base * c
        num += w * adj
        den += w
        pools[kind][0] += w
        pools[kind][1] += w * adj
    if den <= 0:
        return None, {}
    result = num / den
    # Critic-validation penalty: no critic source -> shrink toward the prior mean.
    has_critic = pools["critic"][0] > 0
    if not has_critic:
        f = p["no_critic_factor"]
        result = f * result + (1 - f) * M
    def pooled(k):
        w, wa = pools[k]
        return round(wa / w) if w > 0 else None
    return round(result), {"critic": pooled("critic"), "user": pooled("user"),
                           "has_critic": has_critic}


if __name__ == "__main__":       # sanity demo
    cases = {
        "Bubsy (1 user vote, 100)": [{"kind": "user", "score": 100, "votes": 1}],
        "Well-reviewed (user 92 / 40k, critic 88 / 30)":
            [{"kind": "user", "score": 92, "votes": 40000},
             {"kind": "critic", "score": 88, "votes": 30}],
        "Critic 60 + 1-vote user 100":
            [{"kind": "critic", "score": 60, "votes": 12},
             {"kind": "user", "score": 100, "votes": 1}],
        "IGDB only, no counts (critic 90, user 84)":
            [{"kind": "critic", "score": 90, "votes": None},
             {"kind": "user", "score": 84, "votes": None}],
    }
    for name, rs in cases.items():
        u, b = compute(rs)
        print("%-45s -> %s   pools=%s" % (name, u, b))
