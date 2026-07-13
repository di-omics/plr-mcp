# Evaluations

`plr_mcp_eval.xml` is a small agent-usability suite: can an MCP client actually
drive this server to answer realistic questions?

Every question is answerable on the default **chatterbox** (simulation) backend,
with no hardware, no external starlab scripts, and no network. Answers are
deterministic and stable, so an eval harness can score the agent's answer
against `<answer>` by string comparison.

Format (per the MCP server-building guide):

```xml
<evaluation>
  <qa_pair>
    <question>...</question>
    <answer>...</answer>
  </qa_pair>
</evaluation>
```

To run: start the server (`plr-mcp`), connect your eval harness or an MCP client
(for example the [MCP Inspector](https://github.com/modelcontextprotocol/inspector)),
and for each pair let the agent answer by calling tools, then compare to
`<answer>`. All pairs are independent and non-destructive.
