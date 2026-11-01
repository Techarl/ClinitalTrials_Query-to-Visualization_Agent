"""A release cannot claim a clean commit when tracked or new source files differ."""

import subprocess

import pytest

from scripts import package_submission


@pytest.fixture
def release_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(package_submission, "ROOT", tmp_path)

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, stderr=subprocess.PIPE, text=True)

    git("init")
    git("config", "user.name", "Release fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / "README.md").write_text("Recorded release\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-m", "fixture")
    return tmp_path, git


def test_release_identity_matches_clean_commit(release_tree):
    _, git = release_tree
    assert package_submission.clean_revision() == git("rev-parse", "HEAD").strip()


@pytest.mark.parametrize("filename", ["README.md", "new-source.py"])
def test_release_rejects_uncommitted_or_untracked_source(release_tree, filename):
    root, _ = release_tree
    (root / filename).write_text("Uncommitted source\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="working tree is dirty"):
        package_submission.clean_revision()


def test_extracted_tree_does_not_claim_a_git_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(package_submission, "ROOT", tmp_path)
    assert package_submission.clean_revision() is None
