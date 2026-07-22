# fastmcp-tasks

`fastmcp-tasks` provides background task execution for FastMCP servers via the `io.modelcontextprotocol/tasks` extension ([SEP-2663](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2663)).

It bundles the task-queue dependencies (powered by [docket](https://github.com/chrisguidry/docket)) that FastMCP's `tasks` extra requires. Install it alongside [FastMCP](https://gofastmcp.com) to run long-lived tools as background tasks instead of blocking a request for their full duration.

```bash
uv pip install "fastmcp[tasks]"
```
