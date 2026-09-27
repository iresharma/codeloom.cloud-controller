from __future__ import annotations

import uvicorn

from codeloom_cloud.app import create_app

app = create_app()


def main() -> None:
    uvicorn.run("codeloom_cloud.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
