import logging

import uvicorn

from . import config

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run("needle_server.app:app", host=config.HOST, port=config.PORT, log_level="info")
