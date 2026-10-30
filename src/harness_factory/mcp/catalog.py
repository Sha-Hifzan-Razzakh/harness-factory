"""ToolCatalog (B11): the run's discovered tools, checked against the declared catalog."""

from __future__ import annotations

from pydantic import BaseModel

from harness_factory.env.mcp.servers import ToolCatalogFile, ToolSpec
from harness_factory.hashing import content_hash
from harness_factory.mcp.client import DiscoveredTool


class DriftIssue(BaseModel):
    tool_ref: str
    problem: str                 # undeclared | missing | schema_changed | description_changed


class ToolCatalog(BaseModel):
    tools: dict[str, DiscoveredTool]
    specs: dict[str, ToolSpec]

    @staticmethod
    def build(discovered: list[DiscoveredTool], declared: ToolCatalogFile, servers: list[str]) -> ToolCatalog:
        tools = {t.ref: t for t in discovered}
        specs = {ref: declared.spec(ref) for ref in declared.all_refs() if ref.split(".")[0] in servers}
        return ToolCatalog(tools=tools, specs=specs)

    def schema_hash(self, ref: str) -> str:
        return content_hash(self.tools[ref].input_schema)

    def description_hash(self, ref: str) -> str:
        """The description is what the model reads to decide what a tool does: changing it changes behaviour."""
        return content_hash(self.tools[ref].description)

    def catalog_hash(self) -> str:
        return content_hash({ref: {"schema": self.schema_hash(ref), "description": self.description_hash(ref)}
                             for ref in sorted(self.tools)})

    def drift(self) -> list[DriftIssue]:
        issues = [DriftIssue(tool_ref=r, problem="undeclared") for r in self.tools if r not in self.specs]
        issues += [DriftIssue(tool_ref=r, problem="missing") for r in self.specs if r not in self.tools]
        for ref, spec in self.specs.items():
            if ref in self.tools and spec.schema_hash and spec.schema_hash != self.schema_hash(ref):
                issues.append(DriftIssue(tool_ref=ref, problem="schema_changed"))
            if ref in self.tools and spec.description_hash and spec.description_hash != self.description_hash(ref):
                issues.append(DriftIssue(tool_ref=ref, problem="description_changed"))
        return issues
