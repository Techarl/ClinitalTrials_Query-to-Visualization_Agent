"""Compressed artifacts must not bypass release credential or private-env checks."""

import gzip
import io
import json
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from scripts import package_submission
from scripts.package_submission import ArchiveCredentialScanner
from scripts.verify_submission import verify_submission

SECRET = b"synthetic-private-key-4815162342"


def zipped(entries):
    stream = io.BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return stream.getvalue()


def named_gzip(content, name):
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, filename=name, mode="wb", mtime=0) as output:
        output.write(content)
    return stream.getvalue()


@pytest.mark.parametrize("transform,name", [
    (lambda value: zipped([("trace.json", value)]), "results.zip"),
    (lambda value: gzip.compress(value, mtime=0), "results.json.gz"),
    (lambda value: zipped([("record.gz", gzip.compress(value, mtime=0))]), "nested.zip"),
    (lambda value: gzip.compress(zipped([("trace", value)]), mtime=0), "nested.zip.gz"),
    (lambda value: zipped([("inner.bin", zipped([("trace", value)]))]), "renamed.dat"),
    (lambda value: b"synthetic executable prefix" + zipped([("trace", value)]), "sfx.bin"),
])
def test_secrets_hidden_by_compression_are_rejected_without_disclosure(transform, name, capsys):
    content = transform(b"prefix " + SECRET + b" suffix")
    assert SECRET not in content  # Demonstrates the prior literal-only scan gap.
    with pytest.raises(RuntimeError, match="Credential found") as error:
        ArchiveCredentialScanner([SECRET]).scan(content, name)
    assert SECRET.decode() not in str(error.value)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("name", [".env", "nested/.env.local", "nested/.ENV", "nested\\.env", ".env."])
def test_zip_private_env_names_rejected_even_without_known_secrets(name):
    with pytest.raises(RuntimeError, match="environment"):
        ArchiveCredentialScanner([]).scan(zipped([(name, b"unrecognised credentials")]), "source.zip")


def test_gzip_original_filename_checked_in_every_concatenated_member():
    content = named_gzip(b"safe", "notes.txt") + named_gzip(b"private", ".env")
    with pytest.raises(RuntimeError, match="environment"):
        ArchiveCredentialScanner([]).scan(content, "apparently-safe.gz")


def test_concatenated_gzip_outputs_are_scanned_together_and_zero_padding_is_valid():
    cut = len(SECRET) // 2
    content = gzip.compress(SECRET[:cut], mtime=0) + b"\x00" * 10 + gzip.compress(SECRET[cut:], mtime=0)
    assert SECRET not in content
    with pytest.raises(RuntimeError, match="Credential found"):
        ArchiveCredentialScanner([SECRET]).scan(content, "joined.gz")
    scanner = ArchiveCredentialScanner([SECRET])
    scanner.scan(gzip.compress(b"one", mtime=0) + b"\x00" * 4 + gzip.compress(b"two", mtime=0) + b"\x00", "safe.gz")
    assert scanner.members == 2


def test_secret_at_stream_chunk_boundary_is_not_missed():
    scanner = ArchiveCredentialScanner([SECRET])
    content = b"x" * (scanner.CHUNK - 5) + SECRET + b"y" * scanner.CHUNK
    with pytest.raises(RuntimeError, match="Credential found"):
        scanner.scan(zipped([("trace", content)]), "study.zip")


def test_safe_nested_exports_and_env_example_allowed():
    scanner = ArchiveCredentialScanner([SECRET])
    content = zipped([(".env.example", b"OPENAI_API_KEY=\n"),
                      ("payload.json.gz", gzip.compress(b'{"result":"public"}', mtime=0)),
                      ("sources.bin", zipped([("records.json", b"[]")]))])
    scanner.scan(content, "docs/research.zip")
    assert scanner.report()["status"] == "passed"
    assert scanner.report()["archives_checked"] == 3


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/absolute", "C:relative", "a/../../x"])
def test_unsafe_nested_paths_are_inspected_without_extraction(tmp_path, name):
    with pytest.raises(RuntimeError, match="Unsafe"):
        ArchiveCredentialScanner([]).scan(zipped([(name, b"value")]), "source.zip")
    assert list(tmp_path.iterdir()) == []


def test_depth_member_and_total_work_budgets_fail_closed():
    nested = zipped([("inner.zip", zipped([("plain", b"safe")]))])
    with pytest.raises(RuntimeError, match="nesting budget"):
        ArchiveCredentialScanner([], max_depth=1).scan(nested, "outer.zip")
    with pytest.raises(RuntimeError, match="member budget"):
        ArchiveCredentialScanner([], max_members=1).scan(zipped([("a", b""), ("b", b"")]), "outer.zip")
    scanner = ArchiveCredentialScanner([], max_total_bytes=10)
    scanner.scan(b"123456", "first.txt")
    with pytest.raises(RuntimeError, match="total byte budget"):
        scanner.scan(b"123456", "second.txt")


@pytest.mark.parametrize("kind", ["zip", "gzip"])
def test_high_compression_payload_stops_at_member_limit(kind):
    content = b"0" * 200000
    encoded = zipped([("huge.txt", content)]) if kind == "zip" else gzip.compress(content, mtime=0)
    assert len(encoded) < 2000
    scanner = ArchiveCredentialScanner([], max_member_bytes=2000)
    with pytest.raises(RuntimeError, match="byte budget"):
        scanner.scan(encoded, "bomb." + ("zip" if kind == "zip" else "gz"))
    assert scanner.total_bytes < 2 * scanner.CHUNK


@pytest.mark.parametrize("content,name", [(b"not a zip", "broken.zip"), (b"not gzip", "broken.gz"),
                                            (gzip.compress(b"content")[:-5], "truncated.gz"),
                                            (b"7z\xbc\xaf\x27\x1cfixture", "renamed.bin")])
def test_unreadable_or_unsupported_archives_are_not_certified(content, name):
    with pytest.raises(RuntimeError):
        ArchiveCredentialScanner([]).scan(content, name)


def test_secret_in_archive_filename_is_not_printed(capsys):
    content = zipped([(SECRET.decode() + ".txt", b"safe")])
    with pytest.raises(RuntimeError) as error:
        ArchiveCredentialScanner([SECRET]).scan(content, "research.zip")
    assert SECRET.decode() not in str(error.value)
    assert capsys.readouterr() == ("", "")


def test_encrypted_member_is_not_silently_skipped():
    content = bytearray(zipped([("data.txt", b"safe")]))
    local = content.index(b"PK\x03\x04")
    central = content.index(b"PK\x01\x02")
    content[local + 6] |= 1
    content[central + 8] |= 1
    with pytest.raises(RuntimeError, match="encrypted"):
        ArchiveCredentialScanner([]).scan(bytes(content), "encrypted.zip")


def test_environment_only_credentials_are_scanned(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(package_submission, "ROOT", tmp_path)
    monkeypatch.setattr(package_submission, "clean_revision", lambda: "a" * 40)
    monkeypatch.setenv("NCBI_API_KEY", SECRET.decode())
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "study.gz").write_bytes(gzip.compress(SECRET, mtime=0))
    with pytest.raises(RuntimeError, match="Credential found"):
        package_submission.main()
    assert SECRET.decode() not in capsys.readouterr().out


def test_package_uses_recursive_scan_and_safe_package_still_passes_independent_verifier(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(package_submission, "ROOT", tmp_path)
    monkeypatch.setattr(package_submission, "clean_revision", lambda: "a" * 40)
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("Synthetic release fixture")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=" + SECRET.decode())
    target = tmp_path / "docs" / "study.zip"
    target.write_bytes(zipped([("nested.gz", gzip.compress(SECRET, mtime=0))]))
    with pytest.raises(RuntimeError, match="Credential found"):
        package_submission.main()
    assert not (tmp_path / "dist" / "cheiron-clinicaltrials-agent.zip").exists()
    target.write_bytes(zipped([(".env.example", b"OPENAI_API_KEY=\n"), ("records.gz", gzip.compress(b"public", mtime=0))]))
    package_submission.main()
    archive = tmp_path / "dist" / "cheiron-clinicaltrials-agent.zip"
    result = verify_submission(archive)
    assert result["status"] == "verified"
    with ZipFile(archive) as release:
        manifest = json.loads(release.read("BUILD-MANIFEST.json"))
        assert manifest["credential_scan"]["archives_checked"] == 2
        assert manifest["credentials_included"] is False
    assert SECRET.decode() not in capsys.readouterr().out
