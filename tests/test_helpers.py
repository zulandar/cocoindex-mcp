"""Tests for pure helper functions in mcp_server.py."""


class TestFormatLocation:
    def test_none_returns_empty(self, mcp_server_module):
        assert mcp_server_module._format_location(None) == ""

    def test_already_readable_uppercase(self, mcp_server_module):
        assert mcp_server_module._format_location("L10-L25") == "L10-L25"

    def test_already_readable_lowercase(self, mcp_server_module):
        assert mcp_server_module._format_location("l5-l10") == "l5-l10"

    def test_offset_pair_tuple_str(self, mcp_server_module):
        result = mcp_server_module._format_location("(0, 500)")
        assert result == "offset 0-500"

    def test_offset_pair_bracket_str(self, mcp_server_module):
        result = mcp_server_module._format_location("[100, 200]")
        assert result == "offset 100-200"

    def test_unexpected_format_passthrough(self, mcp_server_module):
        assert mcp_server_module._format_location("something_else") == "something_else"

    def test_single_number_passthrough(self, mcp_server_module):
        assert mcp_server_module._format_location("42") == "42"


class TestBuildTree:
    def test_empty_list(self, mcp_server_module):
        assert mcp_server_module._build_tree([]) == {}

    def test_flat_files(self, mcp_server_module):
        tree = mcp_server_module._build_tree(["a.py", "b.py"])
        assert tree == {"a.py": {}, "b.py": {}}

    def test_nested_paths(self, mcp_server_module):
        tree = mcp_server_module._build_tree(["src/main.py", "src/utils/helpers.py"])
        assert tree == {
            "src": {
                "main.py": {},
                "utils": {
                    "helpers.py": {},
                },
            },
        }

    def test_duplicated_prefixes(self, mcp_server_module):
        tree = mcp_server_module._build_tree(["src/a.py", "src/b.py"])
        assert tree == {"src": {"a.py": {}, "b.py": {}}}


class TestRenderTree:
    def test_empty_tree(self, mcp_server_module):
        assert mcp_server_module._render_tree({}) == []

    def test_single_file(self, mcp_server_module):
        lines = mcp_server_module._render_tree({"file.py": {}})
        assert lines == ["└── file.py"]

    def test_two_files_sorted(self, mcp_server_module):
        lines = mcp_server_module._render_tree({"b.py": {}, "a.py": {}})
        assert lines == ["├── a.py", "└── b.py"]

    def test_nested_directory(self, mcp_server_module):
        tree = {"src": {"main.py": {}}}
        lines = mcp_server_module._render_tree(tree)
        assert lines == ["└── src", "    └── main.py"]

    def test_box_drawing_with_siblings(self, mcp_server_module):
        tree = {"src": {"a.py": {}, "b.py": {}}, "README.md": {}}
        lines = mcp_server_module._render_tree(tree)
        assert lines == [
            "├── README.md",
            "└── src",
            "    ├── a.py",
            "    └── b.py",
        ]
