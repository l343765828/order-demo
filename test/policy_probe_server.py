"""使用独立进程启动本票政策 MCP 验收服务。"""
import sys
from pathlib import Path
import uvicorn
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcp_service.server import make_app
from config import use_config
from order_runtime import read_token

if __name__ == '__main__':
    use_config(sys.argv[2])
    uvicorn.run(make_app(None, read_token()), host='127.0.0.1', port=int(sys.argv[1]), access_log=False, log_level='warning')
