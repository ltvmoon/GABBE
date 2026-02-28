# Guide: Visual AI vs Text AI Diagramming

When agents operate inside of a project, determining how to communicate architectural designs is critical. The GABBE Kit supports both traditional **Text-based rendering** (Mermaid, PlantUML via markdown) and **Visual Canvas rendering** (Draw.io, Miro via MCP servers).

This guide dictates when an agent should use which method.

## 1. The Default: Text-Based Diagramming (Mermaid)
By default, the agent **MUST** prioritize text-based diagramming. It is deterministic, version-controllable, easily refactored by other LLMs, and does not require complex spatial reasoning.

**Use Mermaid/Markdown when:**
*   Generating initial PRDs (`PRD_TEMPLATE.md`).
*   Writing out standard architectural patterns.
*   Documenting sequential workflows or logic inside a README.
*   The system complexity is low-to-medium (Under 15 nodes).
*   The primary consumer is another developer or agent reading markdown.

## 2. The Exception: Visual Canvas Integrations (MCP Servers)
Visual canvases are exceptional tools for high-level human collaboration, non-technical stakeholders, or massive ecosystem maps that become unreadable in Mermaid text blocks. To use these, the corresponding [Miro, Draw.io, Figma] MCP server must be enabled.

**Use Visual Canvases when:**
*   **Explicit Human Request**: The user specifically says "Draw this in Miro" or "Generate a Draw.io XML file."
*   **Extreme Complexity**: The system has \>15 interconnected microservices, rendering a Mermaid graph unreadable (the "spaghetti" effect).
*   **Spatial Requirements**: The problem requires physical grouping, swimlanes, or free-form whiteboarding (e.g., Event storming sessions).
*   **Cross-Functional Output**: The result must be consumed by Product Managers, Designers, or Executives who do not use Markdown renderers.

## 3. Operations Guardrails for Visual Tools
If the agent triggers a Visual tool (Draw.io, Miro):
1.  **Coordinate Mapping**: The agent must pre-map the geometry. Use the `WHITEBOARD_DESIGN_TEMPLATE.md` to establish an X/Y grid. LLMs are poor at spatial arrangement; pre-calculating coordinates mathematically guarantees a legible graph. 
2.  **State Syncing**: If an agent updates an architecture in Miro, it MUST also reflect the structural changes back into the text-based `PLAN.md` or `SYSTEM_ANALYSIS_TEMPLATE.md` to ensure the project's internal semantic memory is updated. A picture is not searchable by default RAG tools; the metadata must stay in text.
3.  **Authentication Constraints**: Visual MCP servers require OAuth or Bearer Tokens. The agent must verify connectivity by calling a read operation (e.g., `get_board`) before attempting to dump 50 nodes into a massive write operation.
