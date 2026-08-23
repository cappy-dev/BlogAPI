import logging
import os
import tempfile
from contextlib import asynccontextmanager

import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from scrape import scrape_blogs
from fastapi import FastAPI, HTTPException, Request
import json
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

logger = logging.getLogger(__name__)

BLOG_SOURCE_URL = (
    "https://raw.githubusercontent.com/Project516/project516.github.io/"
    "refs/heads/master/blog.html"
)

_DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".blogapi")
CACHE_FILE = os.environ.get(
    "BLOGAPI_CACHE_FILE", os.path.join(_DEFAULT_CACHE_DIR, "cache.json")
)


def _load_cache() -> list[dict[str, str]]:
    try:
        with open(CACHE_FILE) as file:
            loaded = json.load(file)
        # Valid JSON can still be the wrong shape; treat anything that is not
        # a list of records as an empty cache.
        return loaded if isinstance(loaded, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


cache = _load_cache()


def persist_cache(blogs: list[dict[str, str]]) -> None:
    # Atomic write: write to a temp file in the same directory, then rename.
    # This prevents symlink-following overwrites and partial reads.
    cache_dir = os.path.dirname(CACHE_FILE) or "."
    os.makedirs(cache_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(blogs, file)
        os.replace(tmp_path, CACHE_FILE)
    except BaseException:
        os.unlink(tmp_path)
        raise


def refresh_cache() -> list[dict[str, str]]:
    blogs = scrape_blogs(BLOG_SOURCE_URL)
    persist_cache(blogs)
    return blogs


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A fresh deploy has no cache file, so every endpoint would serve an
    # empty list until someone manually hits POST /blogs/cache. Scrape once
    # at startup instead. Failure must not stop the server from booting.
    if not cache:
        try:
            cache.extend(refresh_cache())
        except Exception:
            logger.warning(
                "Could not populate blog cache at startup; POST /blogs/cache to retry.",
                exc_info=True,
            )
    yield


limiter = Limiter(key_func=get_remote_address)
app = FastAPI(
    title="Blog API",
    description="A simple API to fetch and search blogs from project516's blog.",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/blogs")
@limiter.limit("5/minute")
def get_blogs(request: Request) -> list[dict[str, str]]:
    return cache


@app.get("/blogs/latest")
@limiter.limit("5/minute")
def get_latest_blogs(request: Request) -> dict[str, str] | None:
    return cache[0] if cache else None


@app.get("/blogs/search")
@limiter.limit("5/minute")
def search_blogs(request: Request, query: str) -> list[dict[str, str]] | dict[str, str]:
    results = []
    for blog in cache:
        if query.lower() in blog["title"].lower():
            results.append(blog)
    return results if results else {"message": "Blog not found"}


@app.post("/blogs/cache")
@limiter.limit("1/minute")
def cache_blogs(request: Request) -> dict[str, str]:
    global cache
    try:
        cache = refresh_cache()
    except Exception:
        logger.exception("Failed to refresh blog cache")
        raise HTTPException(
            status_code=500,
            detail="Failed to refresh blog cache",
        )
    return {"message": "Blogs cached successfully"}


@app.get("/", response_class=HTMLResponse)
def read_root():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Blog API</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }

            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', sans-serif;
                background-color: #000000;
                color: #ffffff;
                line-height: 1.6;
                min-height: 100vh;
                display: flex;
                flex-direction: column;
            }

            header {
                border-bottom: 1px solid #333333;
                padding: 0;
            }

            nav {
                max-width: 1200px;
                margin: 0 auto;
                display: flex;
                align-items: center;
                padding: 1rem 2rem;
                gap: 2rem;
            }

            .logo {
                font-size: 1.25rem;
                font-weight: 600;
                color: #ffffff;
                text-decoration: none;
            }

            .nav-links {
                display: flex;
                gap: 1.5rem;
                margin-left: auto;
            }

            .nav-links a {
                text-decoration: none;
                color: #888888;
                padding: 0.5rem 1rem;
                border-radius: 6px;
                transition: all 0.2s ease;
                font-size: 0.875rem;
                font-weight: 500;
            }

            .nav-links a:hover {
                color: #ffffff;
                background-color: #111111;
            }

            main {
                flex: 1;
                max-width: 1200px;
                margin: 0 auto;
                padding: 4rem 2rem;
                display: flex;
                flex-direction: column;
                align-items: center;
                text-align: center;
            }

            .hero {
                margin-bottom: 3rem;
            }

            .hero-code {
                margin-top: 2rem;
                width: 100%;
                max-width: 900px;
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            }

            .hero-code pre {
                background-color: #0a0a0a;
                border: 1px solid #333333;
                border-radius: 8px;
                padding: 1.5rem;
                text-align: left;
                grid-column: 1 / -1;
            }

            h1 {
                font-size: 3rem;
                font-weight: 700;
                margin-bottom: 1rem;
                background: linear-gradient(to right, #ffffff, #888888);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
            }

            .subtitle {
                font-size: 1.25rem;
                color: #888888;
                margin-bottom: 2rem;
                max-width: 600px;
            }

            .cards {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 1.5rem;
                width: 100%;
                max-width: 900px;
            }

            .card {
                background-color: #111111;
                border: 1px solid #333333;
                border-radius: 8px;
                padding: 1.5rem;
                transition: all 0.2s ease;
                text-align: left;
            }

            .card:hover {
                border-color: #555555;
                transform: translateY(-2px);
            }

            .card h3 {
                font-size: 1.125rem;
                font-weight: 600;
                margin-bottom: 0.5rem;
                color: #ffffff;
            }

            .card p {
                color: #888888;
                font-size: 0.875rem;
                margin-bottom: 1rem;
            }

            .card a {
                display: inline-flex;
                align-items: center;
                color: #ffffff;
                text-decoration: none;
                font-size: 0.875rem;
                font-weight: 500;
                padding: 0.5rem 1rem;
                background-color: #222222;
                border-radius: 6px;
                border: 1px solid #333333;
                transition: all 0.2s ease;
            }

            .card a:hover {
                background-color: #333333;
                border-color: #555555;
            }

            .status-badge {
                display: inline-flex;
                align-items: center;
                gap: 0.5rem;
                background-color: #0070f3;
                color: #ffffff;
                padding: 0.25rem 0.75rem;
                border-radius: 20px;
                font-size: 0.75rem;
                font-weight: 500;
                margin-bottom: 2rem;
            }

            .status-dot {
                width: 6px;
                height: 6px;
                background-color: #00ff88;
                border-radius: 50%;
            }

            pre {
                background-color: #0a0a0a;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 1rem;
                overflow-x: auto;
                margin: 0;
            }

            code {
                font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace;
                font-size: 0.85rem;
                line-height: 1.5;
                color: #ffffff;
            }

            /* Syntax highlighting */
            .keyword {
                color: #ff79c6;
            }

            .string {
                color: #f1fa8c;
            }

            .function {
                color: #50fa7b;
            }

            .class {
                color: #8be9fd;
            }

            .module {
                color: #8be9fd;
            }

            .variable {
                color: #f8f8f2;
            }

            .decorator {
                color: #ffb86c;
            }

            @media (max-width: 768px) {
                nav {
                    padding: 1rem;
                    flex-direction: column;
                    gap: 1rem;
                }

                .nav-links {
                    margin-left: 0;
                }

                main {
                    padding: 2rem 1rem;
                }

                h1 {
                    font-size: 2rem;
                }

                .hero-code {
                    grid-template-columns: 1fr;
                }

                .cards {
                    grid-template-columns: 1fr;
                }
            }
        </style>
    </head>
    <body>
        <header>
            <nav>
                <a href="/" class="logo">Blog API</a>
                <div class="nav-links">
                    <a href="/docs">API Docs</a>
                </div>
            </nav>
        </header>
        <main>
            <div class="hero">
                <h1>Blog API</h1>
                <div class="hero-code">
                </div>
            </div>
        </main>
    </body>
    </html>
    """


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
