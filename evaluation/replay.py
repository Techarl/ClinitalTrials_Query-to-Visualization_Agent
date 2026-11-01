"""Exact-query replay. Missing coverage is an error, never an empty population."""

from app.sources import SnapshotStore, canonical_json


class ReplayCoverageMissing(ValueError):
    pass


class FrozenSource:
    def __init__(self, root):
        roots = root if isinstance(root, (list, tuple)) else [root]
        self.stores = [SnapshotStore(path) for path in roots]
        self.store = self.stores[0]
        self.index = {}
        self.owners = {}
        for source in self.stores:
            for manifest in sorted(source.root.glob("ds_*/manifest.json")):
                snapshot, _ = source.load(manifest.parent.name)
                self.owners[snapshot.dataset_id] = source
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
        snapshot = self.index[key]
        return self.owners[snapshot.dataset_id].load(snapshot.dataset_id)
