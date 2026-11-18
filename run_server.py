"""同步服务端启动入口：python run_server.py"""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "server.main:app",
        host=os.environ.get("DINGFENGBO_HOST", "0.0.0.0"),
        port=int(os.environ.get("DINGFENGBO_PORT", "8000")),
        reload=False,
    )
