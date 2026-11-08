"""Start the local web app: python run.py  ->  http://127.0.0.1:8000 (opens in your browser once it's ready).
Set NO_BROWSER=1 to skip opening the browser."""
import os
import threading
import time
import urllib.request
import webbrowser

import uvicorn

HOST = "127.0.0.1"  # local only; never exposed to the network
PORT = int(os.environ.get("PORT", "8000"))
URL = f"http://{HOST}:{PORT}"


def open_when_ready(timeout: float = 30) -> None:
    """Wait until the server answers, then open the site once."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(URL + "/dashboard", timeout=2)
            webbrowser.open(URL)
            return
        except OSError:
            time.sleep(0.5)


if __name__ == "__main__":
    print(f"jobhunt is starting at {URL}  (Ctrl+C to stop)")
    if os.environ.get("NO_BROWSER") != "1" and os.environ.get("RELOAD") != "1":
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=os.environ.get("RELOAD") == "1")
