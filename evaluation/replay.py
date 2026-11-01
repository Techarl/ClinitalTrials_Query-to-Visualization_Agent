"""Exact-query replay. Missing coverage is an error, never an empty population."""

from app.sources import SnapshotStore, canonical_json


class ReplayCoverageMissing(ValueError):
    pass


class FrozenSource:
    def __init__(self, root):
        self.store = SnapshotStore(root)
        self.index = {}
        for manifest in sorted(self.store.root.glob("ds_*/manifest.json")):
            snapshot, _ = self.store.load(manifest.parent.name)
            if snapshot.complete and snapshot.schema_version == "snapshot-v1":
                key = canonical_json(snapshot.query)
                prior = self.index.get(key)
                if prior is None or snapshot.finished_at > prior.finished_at:
                    self.index[key] = snapshot
        self.calls = []

    def fetch(self, query, deadline):
        key = canonical_json(query)
        self.calls.append(key)
        if key not in self.index:
            raise ReplayCoverageMissing(
                "replay_coverage_missing: no frozen response for this exact source query"
            )
        return self.store.load(self.index[key].dataset_id)
