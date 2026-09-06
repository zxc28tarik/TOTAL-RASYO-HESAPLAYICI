from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile
import scripts.search_original_catalog_artifacts as search


def zipped(name, raw):
    out = BytesIO()
    with ZipFile(out, "w") as archive:
        archive.writestr(name, raw)
    return out.getvalue()


def test_nested_catalog_restored_only_on_original_byte_hash(tmp_path, monkeypatch):
    raw = b"original gzip identity bytes"
    monkeypatch.setattr(search, "ROOT", tmp_path)
    monkeypatch.setattr(search, "TARGETS", {sha256(raw).hexdigest(): "original.csv.gz"})
    row = {"members": [], "unhashed_members": [], "hashed_members": 0, "matches": []}
    with ZipFile(BytesIO(zipped("inner.zip", zipped("renamed.bin", raw)))) as archive:
        search.scan_zip(archive, "", row)
    assert (tmp_path / "original.csv.gz").read_bytes() == raw
    assert row["matches"][0]["member"] == "inner.zip!/renamed.bin"
    assert row["hashed_members"] == 2


def test_same_filename_with_wrong_hash_is_not_recovered(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "ROOT", tmp_path)
    monkeypatch.setattr(search, "TARGETS", {sha256(b"original").hexdigest(): "original.csv.gz"})
    row = {"members": [], "unhashed_members": [], "hashed_members": 0, "matches": []}
    with ZipFile(BytesIO(zipped("original.csv.gz", b"new snapshot"))) as archive:
        search.scan_zip(archive, "", row)
    assert not (tmp_path / "original.csv.gz").exists()
    assert row["matches"] == []
