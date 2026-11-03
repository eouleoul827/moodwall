import asyncio
import sys


def main():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    import uvicorn

    from app.security_events import configure_security_logging

    configure_security_logging()
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, workers=1, loop="asyncio", access_log=False)


if __name__ == "__main__":
    main()
