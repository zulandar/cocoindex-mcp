"""Tests for MCP tool functions (search_code, get_project_structure)."""

from unittest.mock import MagicMock


class TestSearchCode:
    def test_returns_snippets_by_default(self, mcp_server_module, mock_pool):
        # Row: (filename, location, code, distance)
        mock_pool.fetchall.return_value = [
            ("src/main.py", "L1-L10", "def main():\n    pass\n", 0.1),
        ]
        mcp_server_module.code_to_embedding.eval.return_value = [0.0] * 768

        results = mcp_server_module.search_code("main function")

        assert len(results) == 1
        assert results[0]["filename"] == "src/main.py"
        assert results[0]["snippet"] == "def main():\n    pass\n"
        assert results[0]["score"] == 0.9
        assert "code" not in results[0]

    def test_include_code_flag(self, mcp_server_module, mock_pool):
        mock_pool.fetchall.return_value = [
            ("src/main.py", "L1-L10", "def main():\n    pass\n", 0.1),
        ]
        mcp_server_module.code_to_embedding.eval.return_value = [0.0] * 768

        results = mcp_server_module.search_code("main", include_code=True)

        assert len(results) == 1
        assert results[0]["code"] == "def main():\n    pass\n"

    def test_min_score_filters(self, mcp_server_module, mock_pool):
        mock_pool.fetchall.return_value = [
            ("a.py", "L1-L5", "low relevance", 0.85),  # score = 0.15
            ("b.py", "L1-L5", "high relevance", 0.05),  # score = 0.95
        ]
        mcp_server_module.code_to_embedding.eval.return_value = [0.0] * 768

        results = mcp_server_module.search_code("query", min_score=0.3)

        assert len(results) == 1
        assert results[0]["filename"] == "b.py"

    def test_empty_results(self, mcp_server_module, mock_pool):
        mock_pool.fetchall.return_value = []
        mcp_server_module.code_to_embedding.eval.return_value = [0.0] * 768

        results = mcp_server_module.search_code("nonexistent")

        assert results == []

    def test_snippet_truncated_to_200(self, mcp_server_module, mock_pool):
        long_code = "x" * 500
        mock_pool.fetchall.return_value = [
            ("a.py", None, long_code, 0.05),
        ]
        mcp_server_module.code_to_embedding.eval.return_value = [0.0] * 768

        results = mcp_server_module.search_code("query")

        assert len(results[0]["snippet"]) == 200


class TestGetProjectStructure:
    def test_returns_tree_string(self, mcp_server_module, mock_pool):
        mock_pool.fetchall.return_value = [
            ("src/main.py",),
            ("src/utils.py",),
        ]

        result = mcp_server_module.get_project_structure()

        assert "src" in result
        assert "main.py" in result
        assert "utils.py" in result

    def test_empty_table(self, mcp_server_module, mock_pool):
        mock_pool.fetchall.return_value = []

        result = mcp_server_module.get_project_structure()

        assert result == "(no files indexed)"
