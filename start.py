# -*- coding: utf-8 -*-
import os
import sys
import logging
import locale

os.environ["PYTHONIOENCODING"] = "utf-8"
locale.setlocale(locale.LC_ALL, "zh_CN.UTF-8")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

from api.server import app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="debug",
    )