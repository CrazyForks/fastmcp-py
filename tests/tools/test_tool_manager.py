import json
import logging

import pytest
from pydantic import BaseModel

from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.tools import ToolManager
from fastmcp.tools.tool import Tool


class TestAddTools:
    def test_basic_function(self):
        """Test registering and running a basic function."""

        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        manager = ToolManager()
        manager.add_tool_from_fn(add)

        tool = manager.get_tool("add")
        assert tool is not None
        assert tool.name == "add"
        assert tool.description == "Add two numbers."
        assert tool.is_async is False
        assert tool.parameters["properties"]["a"]["type"] == "integer"
        assert tool.parameters["properties"]["b"]["type"] == "integer"

    async def test_async_function(self):
        """Test registering and running an async function."""

        async def fetch_data(url: str) -> str:
            """Fetch data from URL."""
            return f"Data from {url}"

        manager = ToolManager()
        manager.add_tool_from_fn(fetch_data)

        tool = manager.get_tool("fetch_data")
        assert tool is not None
        assert tool.name == "fetch_data"
        assert tool.description == "Fetch data from URL."
        assert tool.is_async is True
        assert tool.parameters["properties"]["url"]["type"] == "string"

    def test_pydantic_model_function(self):
        """Test registering a function that takes a Pydantic model."""

        class UserInput(BaseModel):
            name: str
            age: int

        def create_user(user: UserInput, flag: bool) -> dict:
            """Create a new user."""
            return {"id": 1, **user.model_dump()}

        manager = ToolManager()
        manager.add_tool_from_fn(create_user)

        tool = manager.get_tool("create_user")
        assert tool is not None
        assert tool.name == "create_user"
        assert tool.description == "Create a new user."
        assert tool.is_async is False
        assert "name" in tool.parameters["$defs"]["UserInput"]["properties"]
        assert "age" in tool.parameters["$defs"]["UserInput"]["properties"]
        assert "flag" in tool.parameters["properties"]

    def test_add_invalid_tool(self):
        manager = ToolManager()
        with pytest.raises(AttributeError):
            manager.add_tool_from_fn(1)  # type: ignore

    def test_add_lambda(self):
        manager = ToolManager()
        tool = manager.add_tool_from_fn(lambda x: x, name="my_tool")
        assert tool.name == "my_tool"

    def test_add_lambda_with_no_name(self):
        manager = ToolManager()
        with pytest.raises(
            ValueError, match="You must provide a name for lambda functions"
        ):
            manager.add_tool_from_fn(lambda x: x)

    def test_warn_on_duplicate_tools(self, caplog):
        """Test warning on duplicate tools."""
        manager = ToolManager(duplicate_behavior="warn")

        def test_fn(x: int) -> int:
            return x

        manager.add_tool_from_fn(test_fn, name="test_tool")
        manager.add_tool_from_fn(test_fn, name="test_tool")

        assert "Tool already exists: test_tool" in caplog.text
        # Should have the tool
        assert manager.get_tool("test_tool") is not None

    def test_disable_warn_on_duplicate_tools(self, caplog):
        """Test disabling warning on duplicate tools."""

        def f(x: int) -> int:
            return x

        manager = ToolManager(duplicate_behavior="ignore")
        manager.add_tool_from_fn(f)
        with caplog.at_level(logging.WARNING):
            manager.add_tool_from_fn(f)
            assert "Tool already exists: f" not in caplog.text

    def test_error_on_duplicate_tools(self):
        """Test error on duplicate tools."""
        manager = ToolManager(duplicate_behavior="error")

        def test_fn(x: int) -> int:
            return x

        manager.add_tool_from_fn(test_fn, name="test_tool")

        with pytest.raises(ValueError, match="Tool already exists: test_tool"):
            manager.add_tool_from_fn(test_fn, name="test_tool")

    def test_replace_duplicate_tools(self):
        """Test replacing duplicate tools."""
        manager = ToolManager(duplicate_behavior="replace")

        def original_fn(x: int) -> int:
            return x

        def replacement_fn(x: int) -> int:
            return x * 2

        manager.add_tool_from_fn(original_fn, name="test_tool")
        manager.add_tool_from_fn(replacement_fn, name="test_tool")

        # Should have replaced with the new function
        tool = manager.get_tool("test_tool")
        assert tool is not None
        assert tool.fn.__name__ == "replacement_fn"

    def test_ignore_duplicate_tools(self):
        """Test ignoring duplicate tools."""
        manager = ToolManager(duplicate_behavior="ignore")

        def original_fn(x: int) -> int:
            return x

        def replacement_fn(x: int) -> int:
            return x * 2

        manager.add_tool_from_fn(original_fn, name="test_tool")
        result = manager.add_tool_from_fn(replacement_fn, name="test_tool")

        # Should keep the original
        tool = manager.get_tool("test_tool")
        assert tool is not None
        assert tool.fn.__name__ == "original_fn"
        # Result should be the original tool
        assert result.fn.__name__ == "original_fn"


class TestToolTags:
    """Test functionality related to tool tags."""

    def test_add_tool_with_tags(self):
        """Test adding tags to a tool."""

        def example_tool(x: int) -> int:
            """An example tool with tags."""
            return x * 2

        manager = ToolManager()
        tool = manager.add_tool_from_fn(example_tool, tags={"math", "utility"})

        assert tool.tags == {"math", "utility"}
        tool = manager.get_tool("example_tool")
        assert tool is not None
        assert tool.tags == {"math", "utility"}

    def test_add_tool_with_empty_tags(self):
        """Test adding a tool with empty tags set."""

        def example_tool(x: int) -> int:
            """An example tool with empty tags."""
            return x * 2

        manager = ToolManager()
        tool = manager.add_tool_from_fn(example_tool, tags=set())

        assert tool.tags == set()

    def test_add_tool_with_none_tags(self):
        """Test adding a tool with None tags."""

        def example_tool(x: int) -> int:
            """An example tool with None tags."""
            return x * 2

        manager = ToolManager()
        tool = manager.add_tool_from_fn(example_tool, tags=None)

        assert tool.tags == set()

    def test_list_tools_with_tags(self):
        """Test listing tools with specific tags."""

        def math_tool(x: int) -> int:
            """A math tool."""
            return x * 2

        def string_tool(x: str) -> str:
            """A string tool."""
            return x.upper()

        def mixed_tool(x: int) -> str:
            """A tool with multiple tags."""
            return str(x)

        manager = ToolManager()
        manager.add_tool_from_fn(math_tool, tags={"math"})
        manager.add_tool_from_fn(string_tool, tags={"string", "utility"})
        manager.add_tool_from_fn(mixed_tool, tags={"math", "utility", "string"})

        # Check if we can filter by tags when listing tools
        math_tools = [tool for tool in manager.list_tools() if "math" in tool.tags]
        assert len(math_tools) == 2
        assert {tool.name for tool in math_tools} == {"math_tool", "mixed_tool"}

        utility_tools = [
            tool for tool in manager.list_tools() if "utility" in tool.tags
        ]
        assert len(utility_tools) == 2
        assert {tool.name for tool in utility_tools} == {"string_tool", "mixed_tool"}


class TestCallTools:
    async def test_call_tool(self):
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        manager = ToolManager()
        manager.add_tool_from_fn(add)
        result = await manager.call_tool("add", {"a": 1, "b": 2})
        assert isinstance(result, list)
        assert len(result) == 1
        from mcp.types import TextContent

        assert isinstance(result[0], TextContent)
        assert result[0].text == "3"
        assert json.loads(result[0].text) == 3

    async def test_call_async_tool(self):
        async def double(n: int) -> int:
            """Double a number."""
            return n * 2

        manager = ToolManager()
        manager.add_tool_from_fn(double)
        result = await manager.call_tool("double", {"n": 5})
        assert isinstance(result, list)
        assert len(result) == 1
        from mcp.types import TextContent

        assert isinstance(result[0], TextContent)
        assert result[0].text == "10"
        assert json.loads(result[0].text) == 10

    async def test_call_tool_with_default_args(self):
        def add(a: int, b: int = 1) -> int:
            """Add two numbers."""
            return a + b

        manager = ToolManager()
        manager.add_tool_from_fn(add)
        result = await manager.call_tool("add", {"a": 1})
        assert isinstance(result, list)
        assert len(result) == 1
        from mcp.types import TextContent

        assert isinstance(result[0], TextContent)
        assert result[0].text == "2"
        assert json.loads(result[0].text) == 2

    async def test_call_tool_with_missing_args(self):
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        manager = ToolManager()
        manager.add_tool_from_fn(add)
        with pytest.raises(ToolError):
            await manager.call_tool("add", {"a": 1})

    async def test_call_unknown_tool(self):
        manager = ToolManager()
        with pytest.raises(NotFoundError, match="Unknown tool: unknown"):
            await manager.call_tool("unknown", {"a": 1})

    async def test_call_tool_with_list_int_input(self):
        def sum_vals(vals: list[int]) -> int:
            return sum(vals)

        manager = ToolManager()
        manager.add_tool_from_fn(sum_vals)
        # Try both with plain list and with JSON list
        from mcp.types import TextContent

        result = await manager.call_tool("sum_vals", {"vals": "[1, 2, 3]"})
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "6"
        assert json.loads(result[0].text) == 6

        result = await manager.call_tool("sum_vals", {"vals": [1, 2, 3]})
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "6"
        assert json.loads(result[0].text) == 6

    async def test_call_tool_with_list_str_or_str_input(self):
        def concat_strs(vals: list[str] | str) -> str:
            return vals if isinstance(vals, str) else "".join(vals)

        manager = ToolManager()
        manager.add_tool_from_fn(concat_strs)
        from mcp.types import TextContent

        # Try both with plain python object and with JSON list
        result = await manager.call_tool("concat_strs", {"vals": ["a", "b", "c"]})
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "abc"

        result = await manager.call_tool("concat_strs", {"vals": '["a", "b", "c"]'})
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "abc"

        result = await manager.call_tool("concat_strs", {"vals": "a"})
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "a"

        result = await manager.call_tool("concat_strs", {"vals": '"a"'})
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == '"a"'

    async def test_call_tool_with_complex_model(self):
        from mcp.types import TextContent

        from fastmcp import Context

        class MyShrimpTank(BaseModel):
            class Shrimp(BaseModel):
                name: str

            shrimp: list[Shrimp]
            x: None

        def name_shrimp(tank: MyShrimpTank, ctx: Context) -> list[str]:
            return [x.name for x in tank.shrimp]

        manager = ToolManager()
        manager.add_tool_from_fn(name_shrimp)

        result = await manager.call_tool(
            "name_shrimp",
            {"tank": {"x": None, "shrimp": [{"name": "rex"}, {"name": "gertrude"}]}},
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == '["rex", "gertrude"]'
        assert json.loads(result[0].text) == ["rex", "gertrude"]

        result = await manager.call_tool(
            "name_shrimp",
            {"tank": '{"x": null, "shrimp": [{"name": "rex"}, {"name": "gertrude"}]}'},
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == '["rex", "gertrude"]'
        assert json.loads(result[0].text) == ["rex", "gertrude"]


class TestToolSchema:
    async def test_context_arg_excluded_from_schema(self):
        from fastmcp import Context

        def something(a: int, ctx: Context) -> int:
            return a

        manager = ToolManager()
        tool = manager.add_tool_from_fn(something)
        assert "ctx" not in json.dumps(tool.parameters)
        assert "Context" not in json.dumps(tool.parameters)
        assert "ctx" not in tool.fn_metadata.arg_model.model_fields


class TestContextHandling:
    """Test context handling in the tool manager."""

    def test_context_parameter_detection(self):
        """Test that context parameters are properly detected in
        Tool.from_function()."""
        from fastmcp import Context

        def tool_with_context(x: int, ctx: Context) -> str:
            return str(x)

        manager = ToolManager()
        tool = manager.add_tool_from_fn(tool_with_context)
        assert tool.context_kwarg == "ctx"

        def tool_without_context(x: int) -> str:
            return str(x)

        tool = manager.add_tool_from_fn(tool_without_context)
        assert tool.context_kwarg is None

    async def test_context_injection(self):
        """Test that context is properly injected during tool execution."""
        from mcp.types import TextContent

        from fastmcp import Context, FastMCP

        def tool_with_context(x: int, ctx: Context) -> str:
            assert isinstance(ctx, Context)
            return str(x)

        manager = ToolManager()
        manager.add_tool_from_fn(tool_with_context)

        mcp = FastMCP()
        ctx = mcp.get_context()
        result = await manager.call_tool("tool_with_context", {"x": 42}, context=ctx)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "42"

    async def test_context_injection_async(self):
        """Test that context is properly injected in async tools."""
        from mcp.types import TextContent

        from fastmcp import Context, FastMCP

        async def async_tool(x: int, ctx: Context) -> str:
            assert isinstance(ctx, Context)
            return str(x)

        manager = ToolManager()
        manager.add_tool_from_fn(async_tool)

        mcp = FastMCP()
        ctx = mcp.get_context()
        result = await manager.call_tool("async_tool", {"x": 42}, context=ctx)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "42"

    async def test_context_optional(self):
        """Test that context is optional when calling tools."""
        from mcp.types import TextContent

        from fastmcp import Context

        def tool_with_context(x: int, ctx: Context | None = None) -> str:
            return str(x)

        manager = ToolManager()
        manager.add_tool_from_fn(tool_with_context)
        # Should not raise an error when context is not provided
        result = await manager.call_tool("tool_with_context", {"x": 42})
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "42"

    async def test_context_error_handling(self):
        """Test error handling when context injection fails."""
        from fastmcp import Context, FastMCP

        def tool_with_context(x: int, ctx: Context) -> str:
            raise ValueError("Test error")

        manager = ToolManager()
        manager.add_tool_from_fn(tool_with_context)

        mcp = FastMCP()
        ctx = mcp.get_context()
        with pytest.raises(ToolError, match="Error executing tool tool_with_context"):
            await manager.call_tool("tool_with_context", {"x": 42}, context=ctx)


class TestCustomToolNames:
    """Test adding tools with custom names that differ from their function names."""

    def test_add_tool_with_custom_name(self):
        """Test adding a tool with a custom name parameter using add_tool_from_fn."""

        def original_fn(x: int) -> int:
            return x * 2

        manager = ToolManager()
        tool = manager.add_tool_from_fn(original_fn, name="custom_name")

        # The tool is stored under the custom name and its .name is also set to custom_name
        assert manager.get_tool("custom_name") is not None
        assert tool.name == "custom_name"
        assert tool.fn.__name__ == "original_fn"
        # The tool should not be accessible via its original function name
        assert manager.get_tool("original_fn") is None

    def test_add_tool_object_with_custom_key(self):
        """Test adding a Tool object with a custom key using add_tool()."""

        def fn(x: int) -> int:
            return x + 1

        # Create a tool with a specific name
        tool = Tool.from_function(fn, name="my_tool")
        manager = ToolManager()
        # Store it under a different name
        manager.add_tool(tool, key="proxy_tool")
        # The tool is accessible under the key
        stored = manager.get_tool("proxy_tool")
        assert stored is not None
        # But the tool's .name is unchanged
        assert stored.name == "my_tool"
        # The tool is not accessible under its original name
        assert manager.get_tool("my_tool") is None

    async def test_call_tool_with_custom_name(self):
        """Test calling a tool added with a custom name."""
        from mcp.types import TextContent

        def multiply(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b

        manager = ToolManager()
        manager.add_tool_from_fn(multiply, name="custom_multiply")

        # Tool should be callable by its custom name
        result = await manager.call_tool("custom_multiply", {"a": 5, "b": 3})
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].text == "15"
        assert json.loads(result[0].text) == 15

        # Original name should not be registered
        with pytest.raises(NotFoundError, match="Unknown tool: multiply"):
            await manager.call_tool("multiply", {"a": 5, "b": 3})

    def test_replace_tool_keeps_original_name(self):
        """Test that replacing a tool with "replace" keeps the original name."""

        def original_fn(x: int) -> int:
            return x

        def replacement_fn(x: int) -> int:
            return x * 2

        # Create a manager with REPLACE behavior
        manager = ToolManager(duplicate_behavior="replace")

        # Add the original tool
        original_tool = manager.add_tool_from_fn(original_fn, name="test_tool")
        assert original_tool.name == "test_tool"

        # Replace with a new function but keep the same registered name
        replacement_tool = manager.add_tool_from_fn(replacement_fn, name="test_tool")

        # The tool object should have been replaced
        stored_tool = manager.get_tool("test_tool")
        assert stored_tool is not None
        assert stored_tool == replacement_tool

        # The name should still be the same
        assert stored_tool.name == "test_tool"

        # But the function is different
        assert stored_tool.fn.__name__ == "replacement_fn"
