import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8010)),
        reload=os.getenv("RELOAD", "false").lower() == "true",
        log_level="info",
    )
