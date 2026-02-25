import sys
import json
import logging
from .context import RunContext
from .gateway import ToolDefinition

logger = logging.getLogger("gabbe.mcp")

def run_command_handler(command: str):
    import subprocess
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}

def serve():
    """Zero-dependency JSON-RPC server implementing the MCP Protocol endpoints."""
    with RunContext(command="serve-mcp", initiator="mcp", agent_persona="external_agent") as ctx:
        ctx.gateway.register(ToolDefinition(
            name="run_command",
            description="Run a shell command on the host.",
            parameters={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            handler=run_command_handler,
            allowed_roles={"external_agent"}
        ))
        
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                method = req.get("method")
                req_id = req.get("id")
                
                if method == "initialize":
                    res = {
                        "jsonrpc": "2.0", 
                        "id": req_id, 
                        "result": {
                            "capabilities": {"tools": {}}, 
                            "serverInfo": {"name": "gabbe-mcp", "version": "1.0.0"}
                        }
                    }
                elif method == "notifications/initialized":
                    continue # No response needed
                elif method == "tools/list":
                    res = {
                        "jsonrpc": "2.0", 
                        "id": req_id, 
                        "result": {
                            "tools": [{
                                "name": "run_command", 
                                "description": "Run a shell command", 
                                "inputSchema": {
                                    "type": "object", 
                                    "properties": {"command": {"type": "string"}}, 
                                    "required": ["command"]
                                }
                            }]
                        }
                    }
                elif method == "tools/call":
                    params = req.get("params", {})
                    name = params.get("name")
                    args = params.get("arguments", {})
                    try:
                        tool_res = ctx.gateway.execute(name, args, role="external_agent", run_context=ctx)
                        res = {
                            "jsonrpc": "2.0", 
                            "id": req_id, 
                            "result": {
                                "content": [{"type": "text", "text": json.dumps(tool_res)}]
                            }
                        }
                    except Exception as e:
                        res = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}}
                else:
                    res = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}
                
                print(json.dumps(res), flush=True)
            except Exception as e:
                logger.error(f"MCP Server error processing line: {e}")
                res = {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}
                print(json.dumps(res), flush=True)
