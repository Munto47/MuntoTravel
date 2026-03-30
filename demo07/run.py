import uvicorn

if __name__ == "__main__":
    # reload=True 时日志来自子进程，终端看不到自定义日志
    # 生产或调试时建议 reload=False，日志全部汇聚在当前进程
    import os
    reload = os.getenv("RELOAD", "false").lower() == "true"
    uvicorn.run("app.main:app", host="0.0.0.0", port=8006, reload=reload)
