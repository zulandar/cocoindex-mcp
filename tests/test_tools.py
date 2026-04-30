"""Tests for MCP tool functions (search_code, get_project_structure)."""


class TestSearchCode:
    async def test_returns_snippets_by_default(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        mock_conn.fetch.return_value = [
            {"filename": "src/main.py", "code": "def main():\n    pass\n",
             "distance": 0.1, "start_line": 1, "end_line": 10},
        ]

        results = await mcp_server_module.search_code("main function")

        assert len(results) == 1
        assert results[0]["filename"] == "src/main.py"
        assert results[0]["snippet"] == "def main():\n    pass\n"
        assert results[0]["score"] == 0.9
        assert results[0]["location"] == "L1-L10"
        assert "code" not in results[0]

    async def test_include_code_flag(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        mock_conn.fetch.return_value = [
            {"filename": "src/main.py", "code": "def main():\n    pass\n",
             "distance": 0.1, "start_line": 1, "end_line": 10},
        ]

        results = await mcp_server_module.search_code("main", include_code=True)

        assert len(results) == 1
        assert results[0]["code"] == "def main():\n    pass\n"
        assert "snippet" in results[0]

    async def test_min_score_filters(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        mock_conn.fetch.return_value = [
            {"filename": "a.py", "code": "low relevance",
             "distance": 0.85, "start_line": 1, "end_line": 5},
            {"filename": "b.py", "code": "high relevance",
             "distance": 0.05, "start_line": 1, "end_line": 5},
        ]

        results = await mcp_server_module.search_code("query", min_score=0.3)

        assert len(results) == 1
        assert results[0]["filename"] == "b.py"

    async def test_empty_results(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        mock_conn.fetch.return_value = []

        results = await mcp_server_module.search_code("nonexistent")

        assert results == []

    async def test_snippet_truncated_to_200(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        long_code = "x" * 500
        mock_conn.fetch.return_value = [
            {"filename": "a.py", "code": long_code,
             "distance": 0.05, "start_line": 1, "end_line": 100},
        ]

        results = await mcp_server_module.search_code("query")

        assert len(results[0]["snippet"]) == 200


class TestGetProjectStructure:
    async def test_returns_tree_string(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        mock_conn.fetch.return_value = [
            {"filename": "src/main.py"},
            {"filename": "src/utils.py"},
        ]

        result = await mcp_server_module.get_project_structure()

        assert "src" in result
        assert "main.py" in result
        assert "utils.py" in result

    async def test_empty_table(self, mcp_server_module, mock_pool_and_embedder):
        mock_conn, _ = mock_pool_and_embedder
        mock_conn.fetch.return_value = []

        result = await mcp_server_module.get_project_structure()

        assert result == "(no files indexed)"
