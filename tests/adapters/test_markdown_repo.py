"""Frontmatter round-trip fidelity and atomic writes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nodify.adapters.markdown_repo import AtomicMarkdownRepository, MarkdownCodec
from nodify.adapters.vault_paths import FileSystemVaultPathResolver
from nodify.domain.ports import DocumentFormatError


@pytest.fixture
def codec() -> MarkdownCodec:
    return MarkdownCodec()


@pytest.fixture
def repo(tmp_path: Path) -> AtomicMarkdownRepository:
    root = tmp_path / "vault"
    root.mkdir()
    return AtomicMarkdownRepository(FileSystemVaultPathResolver(root))


class TestParsing:
    def test_reads_frontmatter_and_body(self, codec: MarkdownCodec) -> None:
        text = "---\nid: n1\ntype: note\n---\n# Title\n\nBody.\n"
        frontmatter, body = codec.parse_document(text)
        assert frontmatter == {"id": "n1", "type": "note"}
        assert body == "# Title\n\nBody.\n"

    def test_document_without_frontmatter(self, codec: MarkdownCodec) -> None:
        frontmatter, body = codec.parse_document("# Just markdown\n")
        assert frontmatter == {}
        assert body == "# Just markdown\n"

    def test_empty_frontmatter_block(self, codec: MarkdownCodec) -> None:
        frontmatter, body = codec.parse_document("---\n---\nBody\n")
        assert frontmatter == {}
        assert body == "Body\n"

    def test_tolerates_bom(self, codec: MarkdownCodec) -> None:
        frontmatter, body = codec.parse_document("\ufeff---\nid: n1\n---\nBody\n")
        assert frontmatter == {"id": "n1"}
        assert body == "Body\n"

    def test_unclosed_frontmatter_is_reported(self, codec: MarkdownCodec) -> None:
        with pytest.raises(DocumentFormatError, match="not closed"):
            codec.parse_document("---\nid: n1\ntype: note\n\nBody\n")

    def test_invalid_yaml_is_reported(self, codec: MarkdownCodec) -> None:
        with pytest.raises(DocumentFormatError, match="invalid YAML"):
            codec.parse_document("---\nid: [unclosed\n---\nBody\n")

    def test_non_mapping_frontmatter_is_reported(self, codec: MarkdownCodec) -> None:
        with pytest.raises(DocumentFormatError, match="must be a mapping"):
            codec.parse_document("---\n- a\n- b\n---\nBody\n")

    def test_body_containing_fence_is_preserved(self, codec: MarkdownCodec) -> None:
        body = "before\n---\nafter\n"
        text = f"---\nid: n1\n---\n{body}"
        parsed_frontmatter, parsed_body = codec.parse_document(text)
        assert parsed_frontmatter == {"id": "n1"}
        assert parsed_body == body


class TestRoundTrip:
    def test_preserves_unknown_keys(self, codec: MarkdownCodec) -> None:
        text = (
            "---\nid: n1\ntype: note\nobsidian:\n  cssclass: wide\ncustom_key: keep me\n---\nBody\n"
        )
        frontmatter, body = codec.parse_document(text)
        rendered = codec.serialise(frontmatter, body)
        assert "custom_key: keep me" in rendered
        assert "cssclass: wide" in rendered

        reparsed, _ = codec.parse_document(rendered)
        assert reparsed["custom_key"] == "keep me"

    def test_preserves_comments(self, codec: MarkdownCodec) -> None:
        text = "---\nid: n1\n# a human comment\nkey: value\n---\nBody\n"
        frontmatter, body = codec.parse_document(text)
        assert "# a human comment" in codec.serialise(frontmatter, body)

    def test_preserves_nested_structures(self, codec: MarkdownCodec) -> None:
        text = "---\nid: n1\nlinks:\n  - name: Home\n    url: https://example.com\n---\nBody\n"
        frontmatter, body = codec.parse_document(text)
        rendered = codec.serialise(frontmatter, body)
        assert "name: Home" in rendered
        assert "url: https://example.com" in rendered

    def test_preserves_unicode(self, codec: MarkdownCodec) -> None:
        text = "---\nid: n1\ntitle: Ünïcode 日本語\n---\nBody\n"
        frontmatter, body = codec.parse_document(text)
        assert "日本語" in codec.serialise(frontmatter, body)

    def test_preserves_quoting_style(self, codec: MarkdownCodec) -> None:
        text = "---\nid: n1\ntitle: 'Quoted: value'\n---\nBody\n"
        frontmatter, body = codec.parse_document(text)
        assert "'Quoted: value'" in codec.serialise(frontmatter, body)

    def test_empty_mapping_omits_fence(self, codec: MarkdownCodec) -> None:
        assert codec.serialise({}, "Body only\n") == "Body only\n"

    def test_body_is_not_escaped(self, codec: MarkdownCodec) -> None:
        body = "# Heading\n\n- item\n\n```py\nprint(1)\n```\n"
        rendered = codec.serialise({"id": "n1"}, body)
        _, parsed_body = codec.parse_document(rendered)
        assert parsed_body == body


class TestYamlInstanceIsNotShared:
    """A failed serialise must not break later ones.

    ruamel's ``YAML`` object is stateful, and a representer that raises leaves the
    instance emitting empty documents. Sharing one instance process-wide would
    mean a single malformed save silently blanks the frontmatter of every write
    that follows. These tests use one codec across a failure to prove independence.
    """

    def test_serialise_works_after_a_failure(self, codec: MarkdownCodec) -> None:
        class Unrepresentable:
            pass

        with pytest.raises(DocumentFormatError):
            codec.serialise({"id": "n1", "bad": Unrepresentable()}, "Body\n")

        assert codec.serialise({"id": "n1", "type": "note"}, "Body\n") == (
            "---\nid: n1\ntype: note\n---\nBody\n"
        )

    def test_parse_works_after_a_failure(self, codec: MarkdownCodec) -> None:
        class Unrepresentable:
            pass

        with pytest.raises(DocumentFormatError):
            codec.serialise({"bad": Unrepresentable()}, "")

        assert codec.parse_document("---\nid: n1\n---\nBody\n") == ({"id": "n1"}, "Body\n")

    def test_repository_writes_survive_an_earlier_failure(
        self, repo: AtomicMarkdownRepository
    ) -> None:
        class Unrepresentable:
            pass

        with pytest.raises(DocumentFormatError):
            repo.write(repo.root / "notes" / "Bad.md", {"bad": Unrepresentable()}, "")

        good = repo.root / "notes" / "Good.md"
        repo.write(good, {"id": "n1", "type": "note"}, "Body\n")
        assert repo.read(good) == ({"id": "n1", "type": "note"}, "Body\n")


class TestAtomicRepository:
    def test_writes_and_reads_back(self, repo: AtomicMarkdownRepository) -> None:
        path = repo.root / "notes" / "A.md"
        repo.write(path, {"id": "n1", "type": "note"}, "Body\n")
        assert repo.read(path) == ({"id": "n1", "type": "note"}, "Body\n")

    def test_creates_missing_parent_directories(self, repo: AtomicMarkdownRepository) -> None:
        path = repo.root / "notes" / "Deep" / "Nested" / "A.md"
        repo.write(path, {"id": "n1"}, "Body\n")
        assert path.is_file()

    def test_replaces_existing_file(self, repo: AtomicMarkdownRepository) -> None:
        path = repo.root / "notes" / "A.md"
        repo.write(path, {"id": "n1"}, "first\n")
        repo.write(path, {"id": "n1"}, "second\n")
        _, body = repo.read(path)
        assert body == "second\n"

    def test_leaves_no_temporary_files(self, repo: AtomicMarkdownRepository) -> None:
        path = repo.root / "notes" / "A.md"
        repo.write(path, {"id": "n1"}, "Body\n")
        assert sorted(p.name for p in (repo.root / "notes").iterdir()) == ["A.md"]

    def test_failed_serialise_leaves_original_intact(self, repo: AtomicMarkdownRepository) -> None:
        path = repo.root / "notes" / "A.md"
        repo.write(path, {"id": "n1"}, "original\n")

        class Unrepresentable:
            """YAML has no representation for an arbitrary object."""

        unserialisable = {"id": "n1", "bad": Unrepresentable()}
        with pytest.raises(DocumentFormatError):
            repo.write(path, unserialisable, "replacement\n")

        _, body = repo.read(path)
        assert body == "original\n"

    def test_missing_file_raises_recoverable_error(self, repo: AtomicMarkdownRepository) -> None:
        with pytest.raises(DocumentFormatError, match="no such document"):
            repo.read(repo.root / "notes" / "Missing.md")

    def test_invalid_utf8_raises_recoverable_error(self, repo: AtomicMarkdownRepository) -> None:
        path = repo.root / "notes" / "Bad.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\xff\xfe\x00invalid")
        with pytest.raises(DocumentFormatError, match="not valid UTF-8"):
            repo.read(path)

    def test_exists(self, repo: AtomicMarkdownRepository) -> None:
        path = repo.root / "notes" / "A.md"
        assert not repo.exists(path)
        repo.write(path, {"id": "n1"}, "Body\n")
        assert repo.exists(path)

    def test_list_files_is_case_insensitive_sorted(self, repo: AtomicMarkdownRepository) -> None:
        directory = repo.root / "notes"
        directory.mkdir(parents=True)
        for name in ("beta.md", "Alpha.md", "gamma.md"):
            (directory / name).write_text("---\nid: x\n---\n", encoding="utf-8")
        names = [p.name for p in repo.list_files(directory)]
        assert names == ["Alpha.md", "beta.md", "gamma.md"]

    def test_list_files_ignores_non_markdown(self, repo: AtomicMarkdownRepository) -> None:
        directory = repo.root / "notes"
        directory.mkdir(parents=True)
        (directory / "a.md").write_text("x", encoding="utf-8")
        (directory / "b.txt").write_text("x", encoding="utf-8")
        assert [p.name for p in repo.list_files(directory)] == ["a.md"]

    def test_list_files_on_missing_directory(self, repo: AtomicMarkdownRepository) -> None:
        assert repo.list_files(repo.root / "nope") == []

    def test_write_is_atomic_via_replace(self, repo: AtomicMarkdownRepository) -> None:
        path = repo.root / "notes" / "A.md"
        created: list[str] = []
        original_replace = os.replace

        def spy(src: object, dst: object) -> None:
            created.append(str(src))
            original_replace(src, dst)  # type: ignore[arg-type]

        os.replace = spy  # type: ignore[assignment]
        try:
            repo.write(path, {"id": "n1"}, "Body\n")
        finally:
            os.replace = original_replace  # type: ignore[assignment]

        assert created, "os.replace was not used"
        assert Path(created[0]).parent == path.parent
