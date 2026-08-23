# BlogAPI

Simple API for my [blog website](https://project516.dev/blog)!

## Endpoints:

### GET:

`GET /` - returns an HTML landing page with a quick API overview.

`GET /blogs` - returns json of all blogs, with title, link, and date.

`GET /blogs/latest` - returns json of just the latest blog post.

`GET /blogs/search?query=...` - searches through blog posts by title, returns any matches.

### POST:

`POST /blogs/cache` - updates the blog cache. When the cache loads empty, startup attempts one refresh; if that fails the API starts with an empty cache and this endpoint can retry it.

### Docs:

FastAPI auto-generates interactive API docs (Swagger UI and ReDoc) when the server is running:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
