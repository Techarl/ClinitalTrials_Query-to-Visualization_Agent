"""Entity-disjoint outer splits. Variants and every dialogue turn stay together."""

from app.sources import content_hash

SPLITS = {
    "melanoma": "development",
    "psoriasis": "calibration",
    "asthma": "heldout",
    "rheumatoid arthritis": "heldout",
}


def opaque_id(group, family):
    return "ep_" + content_hash(["vnext-challenge-v1", group, family])[:14]


def audit(episodes):
    seen = {}
    for episode in episodes:
        prior = seen.setdefault(episode.entity_group, episode.split)
        if prior != episode.split:
            raise ValueError("Entity-group split leakage")
    if len({e.id for e in episodes}) != len(episodes):
        raise ValueError("Duplicate originating episode ID")
    return {
        "entity_groups": seen,
        "episode_count": len(episodes),
        "policy": "Entity-disjoint outer split, all originating dialogue turns/plan mutations grouped. Capability templates deliberately recur; this does not claim template-disjoint generalization.",
    }
