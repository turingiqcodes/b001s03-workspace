"""Repository tools: file-content edge cases, listings, recursive truncation."""

import base64

from .conftest import call, error_text

B64 = lambda b: base64.b64encode(b).decode()  # noqa: E731


def file_entry(content: bytes, size=None, **kw):
    return {"type": "file", "path": "src/app.py", "name": "app.py", "sha": "abc", "size": len(content) if size is None else size,
            "encoding": "base64", "content": B64(content), **kw}


async def test_get_file_contents_utf8(gh):
    gh.add("GET", "/repos/o/r/contents/src/app.py", file_entry("print('héllo')\n".encode()))
    r = await call(gh, "get_file_contents", {"owner": "o", "repo": "r", "path": "src/app.py", "ref": "dev"})
    assert r.structured_content["content"] == "print('héllo')\n"
    assert r.structured_content["truncated"] is False and r.structured_content["reason"] is None
    assert gh.requests[0].url.params["ref"] == "dev"


async def test_get_file_contents_large_file_returns_null(gh):
    gh.add("GET", "/repos/o/r/contents/big.bin", {**file_entry(b""), "size": 5_000_000, "encoding": "none", "content": ""})
    r = await call(gh, "get_file_contents", {"owner": "o", "repo": "r", "path": "big.bin"})
    assert r.structured_content["content"] is None
    assert "larger than the 1 MB limit" in r.structured_content["reason"]


async def test_get_file_contents_just_over_1mb_returns_null(gh):
    gh.add("GET", "/repos/o/r/contents/f.txt", file_entry(b"a", size=1024 * 1024 + 1))
    r = await call(gh, "get_file_contents", {"owner": "o", "repo": "r", "path": "f.txt"})
    assert r.structured_content["content"] is None


async def test_get_file_contents_binary_returns_null(gh):
    gh.add("GET", "/repos/o/r/contents/logo.png", file_entry(b"\x89PNG\r\n\x1a\n\x00\x00\xff\xfe"))
    r = await call(gh, "get_file_contents", {"owner": "o", "repo": "r", "path": "logo.png"})
    assert r.structured_content["content"] is None and "binary" in r.structured_content["reason"]


async def test_get_file_contents_invalid_utf8_returns_null(gh):
    gh.add("GET", "/repos/o/r/contents/latin1.txt", file_entry("café".encode("latin-1")))
    r = await call(gh, "get_file_contents", {"owner": "o", "repo": "r", "path": "latin1.txt"})
    assert r.structured_content["content"] is None


async def test_get_file_contents_directory_points_to_listing_tool(gh):
    gh.add("GET", "/repos/o/r/contents/src", [{"type": "file", "path": "src/a.py"}])
    r = await call(gh, "get_file_contents", {"owner": "o", "repo": "r", "path": "src"})
    assert "is a directory" in error_text(r) and "list_repository_files" in error_text(r)


async def test_get_file_contents_truncates_long_text(gh):
    gh.add("GET", "/repos/o/r/contents/long.txt", file_entry(b"x" * 600))
    r = await call(gh, "get_file_contents", {"owner": "o", "repo": "r", "path": "long.txt"}, max_text_chars=500)
    assert len(r.structured_content["content"]) == 500 and r.structured_content["truncated"] is True


async def test_get_file_contents_rejects_path_traversal(gh):
    r = await call(gh, "get_file_contents", {"owner": "o", "repo": "r", "path": "../../user"})
    assert "Invalid input" in error_text(r) and gh.requests == []


async def test_get_file_contents_encodes_path(gh):
    gh.add("GET", "/repos/o/r/contents/docs/my file#1.md", file_entry(b"hi"))
    r = await call(gh, "get_file_contents", {"owner": "o", "repo": "r", "path": "docs/my file#1.md"})
    assert not r.is_error
    assert gh.requests[0].url.raw_path.startswith(b"/repos/o/r/contents/docs/my%20file%231.md")


async def test_list_files_non_recursive(gh):
    gh.add("GET", "/repos/o/r/contents", [
        {"type": "dir", "path": "src", "size": 0, "sha": "1"}, {"type": "file", "path": "README.md", "size": 10, "sha": "2"}])
    r = await call(gh, "list_repository_files", {"owner": "o", "repo": "r"})
    assert [e["path"] for e in r.structured_content["entries"]] == ["src", "README.md"]
    assert r.structured_content["truncated"] is False


async def test_list_files_on_a_file_is_helpful_error(gh):
    gh.add("GET", "/repos/o/r/contents/README.md", file_entry(b"x"))
    r = await call(gh, "list_repository_files", {"owner": "o", "repo": "r", "path": "README.md"})
    assert "get_file_contents" in error_text(r)


def tree(n):
    return {"sha": "t", "truncated": False,
            "tree": [{"path": "src", "type": "tree", "sha": "d"}]
            + [{"path": f"src/f{i}.py", "type": "blob", "size": i, "sha": str(i)} for i in range(n)]}


async def test_recursive_listing_defaults_to_1000_with_truncation_flag(gh):
    gh.add("GET", "/repos/o/r", {"default_branch": "main"})
    gh.add("GET", "/repos/o/r/git/trees/main", tree(1500))
    r = await call(gh, "list_repository_files", {"owner": "o", "repo": "r", "recursive": True})
    sc = r.structured_content
    assert sc["count"] == 1000 and len(sc["entries"]) == 1000
    assert sc["truncated"] is True and sc["total_found"] == 1501 and sc["ref"] == "main"
    assert gh.requests[1].url.params["recursive"] == "1"


async def test_recursive_listing_custom_cap_and_prefix(gh):
    t = tree(5)
    t["tree"].append({"path": "docs/x.md", "type": "blob", "size": 1, "sha": "z"})
    gh.add("GET", "/repos/o/r/git/trees/dev", t)
    r = await call(gh, "list_repository_files",
                   {"owner": "o", "repo": "r", "path": "src", "ref": "dev", "recursive": True, "max_entries": 10})
    sc = r.structured_content
    assert sc["count"] == 5 and sc["truncated"] is False
    assert all(e["path"].startswith("src/") for e in sc["entries"]) and sc["entries"][0]["type"] == "file"


async def test_recursive_listing_reports_github_truncation(gh):
    t = tree(3)
    t["truncated"] = True
    gh.add("GET", "/repos/o/r/git/trees/main", t)
    r = await call(gh, "list_repository_files", {"owner": "o", "repo": "r", "ref": "main", "recursive": True})
    assert r.structured_content["truncated"] is True and r.structured_content["github_tree_truncated"] is True


async def test_max_entries_bounds(gh):
    for bad in (0, 10_001):
        r = await call(gh, "list_repository_files", {"owner": "o", "repo": "r", "recursive": True, "max_entries": bad})
        assert r.is_error
    gh.add("GET", "/repos/o/r/git/trees/main", tree(1))
    r = await call(gh, "list_repository_files",
                   {"owner": "o", "repo": "r", "ref": "main", "recursive": True, "max_entries": 10_000})
    assert not r.is_error


async def test_branches_and_commits(gh):
    gh.add("GET", "/repos/o/r/branches", [{"name": "main"}])
    gh.add("GET", "/repos/o/r/commits", [{"sha": "1"}, {"sha": "2"}])
    r = await call(gh, "list_repository_branches", {"owner": "o", "repo": "r", "protected": True})
    assert r.structured_content["items"] == [{"name": "main"}]
    assert gh.requests[-1].url.params["protected"] == "true"
    r = await call(gh, "get_repository_commits", {"owner": "o", "repo": "r", "sha": "dev", "path": "src", "per_page": 2})
    assert r.structured_content["count"] == 2
    params = gh.requests[-1].url.params
    assert (params["sha"], params["path"], params["per_page"]) == ("dev", "src", "2")
    assert "author" not in params
