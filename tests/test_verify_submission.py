import hashlib
import json
import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from scripts.verify_submission import MANIFEST, VerificationError, main, verify_submission

COMMIT = "a" * 40


def build(tmp_path, *, files=None, entries=None, manifest_update=None, manifest_bytes=None):
    files = files if files is not None else {"README.md": b"Synthetic release", ".env.example": b"KEY=\n", "app/main.py": b"pass\n"}
    manifest = {"git_commit": COMMIT, "clean_working_tree": True, "credentials_included": False,
                "file_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    manifest.update(manifest_update or {})
    path = tmp_path / "submission.zip"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in (entries if entries is not None else files.items()):
            archive.writestr(name, data)
        archive.writestr(MANIFEST, manifest_bytes if manifest_bytes is not None else json.dumps(manifest))
    return path, manifest


def test_valid_archive_receipt_and_new_workspace_extraction(tmp_path):
    archive, manifest = build(tmp_path)
    workspace = tmp_path / "fresh"
    result = verify_submission(archive, extract=workspace, expected_commit=COMMIT)
    assert result["status"] == "verified" and result["git_commit"] == COMMIT
    assert result["manifest_files_verified"] == 3
    assert result["zip_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert json.loads(archive.with_name(archive.name + ".verification.json").read_text()) == result
    assert (workspace / "README.md").read_bytes() == b"Synthetic release"
    assert json.loads((workspace / MANIFEST).read_text()) == manifest
    assert sorted(p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if p.is_file()) == sorted([MANIFEST, *manifest["file_sha256"]])


@pytest.mark.parametrize("change", ["tampered", "missing", "unlisted", "wrong_hash", "self_listed"])
def test_complete_manifest_set_and_all_bytes_are_verified_before_extract(tmp_path, change):
    original = {"README.md": b"original"}
    entries, update = list(original.items()), {}
    if change == "tampered":
        entries = [("README.md", b"edited")]
    elif change == "missing":
        entries = []
    elif change == "unlisted":
        entries.append(("extra.txt", b"extra"))
    elif change == "wrong_hash":
        update = {"file_sha256": {"README.md": "b" * 64}}
    else:
        update = {"file_sha256": {"README.md": hashlib.sha256(b"original").hexdigest(), MANIFEST: "b" * 64}}
    archive, _ = build(tmp_path, files=original, entries=entries, manifest_update=update)
    workspace = tmp_path / "fresh"
    with pytest.raises(VerificationError):
        verify_submission(archive, extract=workspace)
    assert not workspace.exists()
    assert not archive.with_name(archive.name + ".verification.json").exists()


@pytest.mark.parametrize("name", [
    "../escape", "a/../../escape", "/absolute", "C:/absolute", "C:relative", "\\\\server\\share\\x",
    "//server/share/x", "a\\..\\escape", "a//b", "a/./b", "a/../b", "file:stream",
    "file.", "dir /file", "CON", "nul.txt", "COM1.py", "A/LPT9.txt", "CON .txt", "COM¹.log",
    "question?", "file\x01.txt",
])
def test_windows_and_posix_unsafe_paths_rejected_before_workspace_creation(tmp_path, name):
    archive, _ = build(tmp_path, files={name: b"payload"})
    with pytest.raises(VerificationError):
        verify_submission(archive, extract=tmp_path / "fresh")
    assert not (tmp_path / "fresh").exists()


@pytest.mark.parametrize("name", [".env", "app/.env.local", "APP/.ENV", "frontend/node_modules/x.js",
                                   ".venv/bin/python", "app/.git/config", "app/__pycache__/x.pyc"])
def test_private_environment_and_local_dependency_paths_rejected(tmp_path, name):
    archive, _ = build(tmp_path, files={name: b"fixture"})
    with pytest.raises(VerificationError, match="Excluded"):
        verify_submission(archive)


@pytest.mark.parametrize("names", [["app/A.py", "app/a.py"], ["App/a.py", "app/b.py"],
                                    ["app", "app/file.py"], ["café.txt", "cafe\u0301.txt"]])
def test_case_unicode_and_file_parent_collisions_rejected(tmp_path, names):
    archive, _ = build(tmp_path, files={name: b"fixture" for name in names})
    with pytest.raises(VerificationError):
        verify_submission(archive)


def test_duplicate_members_and_duplicate_manifest_keys_rejected(tmp_path):
    with pytest.warns(UserWarning, match="Duplicate name"):
        archive, _ = build(tmp_path, files={"a.txt": b"a"}, entries=[("a.txt", b"a"), ("a.txt", b"a")])
    with pytest.raises(VerificationError, match="Duplicate ZIP"):
        verify_submission(archive)
    archive, _ = build(tmp_path, manifest_bytes='{"git_commit":"' + COMMIT + '","git_commit":"' + "b" * 40 + '"}')
    with pytest.raises(VerificationError, match="Duplicate manifest"):
        verify_submission(archive)


def test_symlink_and_nonregular_members_rejected(tmp_path):
    for kind in (stat.S_IFLNK, stat.S_IFIFO):
        member = ZipInfo("payload")
        member.create_system = 3
        member.external_attr = (kind | 0o777) << 16
        archive, _ = build(tmp_path, files={"payload": b"../outside"}, entries=[(member, b"../outside")])
        with pytest.raises(VerificationError, match="Non-regular"):
            verify_submission(archive)


def test_nul_name_and_corrupted_zip_payload_rejected_without_receipt(tmp_path):
    archive, _ = build(tmp_path, files={"bad_name": b"fixture"})
    archive.write_bytes(archive.read_bytes().replace(b"bad_name", b"bad\x00name"))
    with pytest.raises(VerificationError, match="NUL"):
        verify_submission(archive)
    member = ZipInfo("plain.txt")  # Stored, so a payload bit flip forces a CRC mismatch.
    archive, _ = build(tmp_path, files={"plain.txt": b"unique_payload"}, entries=[(member, b"unique_payload")])
    archive.write_bytes(archive.read_bytes().replace(b"unique_payload", b"Unique_payload"))
    with pytest.raises(VerificationError, match="ZIP integrity"):
        verify_submission(archive)
    assert not archive.with_name(archive.name + ".verification.json").exists()


@pytest.mark.parametrize("commit", [None, "", " ", "72d95bd", "not-a-commit", 123])
def test_explicit_full_commit_required(tmp_path, commit):
    archive, _ = build(tmp_path, manifest_update={"git_commit": commit})
    with pytest.raises(VerificationError, match="commit"):
        verify_submission(archive)


def test_wrong_expected_revision_and_unclean_or_credentials_marker_rejected(tmp_path):
    archive, _ = build(tmp_path)
    with pytest.raises(VerificationError, match="expected-commit"):
        verify_submission(archive, expected_commit="b" * 40)
    for update in ({"clean_working_tree": False}, {"credentials_included": True}):
        archive, _ = build(tmp_path, manifest_update=update)
        with pytest.raises(VerificationError, match="explicitly declare"):
            verify_submission(archive)


def test_existing_workspace_and_receipt_never_overwritten(tmp_path):
    archive, _ = build(tmp_path)
    workspace = tmp_path / "existing"
    workspace.mkdir()
    marker = workspace / "user.txt"
    marker.write_text("preserve")
    with pytest.raises(VerificationError, match="already exists"):
        verify_submission(archive, extract=workspace)
    assert marker.read_text() == "preserve"
    receipt = tmp_path / "receipt.json"
    receipt.write_text("preserve receipt")
    with pytest.raises(VerificationError, match="Receipt already"):
        verify_submission(archive, receipt=receipt)
    assert receipt.read_text() == "preserve receipt"


def test_declared_uncompressed_limit_and_missing_manifest(tmp_path):
    archive, _ = build(tmp_path, files={"large.txt": b"0" * 4096})
    with pytest.raises(VerificationError, match="byte limit"):
        verify_submission(archive, max_uncompressed_bytes=1000)
    with ZipFile(archive, "w") as output:
        output.writestr("only.txt", b"x")
    with pytest.raises(VerificationError, match="Missing"):
        verify_submission(archive)


def test_explicit_directory_entries_allowed_but_not_file_directory_aliases(tmp_path):
    archive, _ = build(tmp_path, files={"app/main.py": b"pass"}, entries=[("app/", b""), ("app/main.py", b"pass")])
    result = verify_submission(archive)
    assert result["manifest_files_verified"] == 1


def test_cli_emits_receipt_without_installing_or_running_release(tmp_path, monkeypatch, capsys):
    archive, _ = build(tmp_path)
    receipt = tmp_path / "acceptance.json"
    monkeypatch.setattr("sys.argv", ["verify_submission", str(archive), "--receipt", str(receipt), "--expected-commit", COMMIT])
    main()
    assert json.loads(capsys.readouterr().out)["git_commit"] == COMMIT
    assert receipt.exists()
