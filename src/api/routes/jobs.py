"""
Public job progress page and SSE stream.

No API key auth — job UUIDs are the access secret.
"""

import asyncio
import html
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from src.api.services.job_service import get_job_service
from src.api.config.redis import get_redis_client

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_class=HTMLResponse)
async def job_progress_page(job_id: UUID):
    """Serve the real-time progress page."""
    job_service = get_job_service()
    job = job_service.get_job(str(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    safe_prompt = html.escape(job.prompt[:300])
    status = job.status.value
    return HTMLResponse(content=_render_page(str(job_id), safe_prompt, status))


@router.get("/{job_id}/stream")
async def job_event_stream(job_id: UUID, request: Request):
    """SSE endpoint: replay backlog then stream live events."""
    job_service = get_job_service()
    job = job_service.get_job(str(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        redis = get_redis_client()
        channel = f"progress:{job_id}"

        # Phase 1: replay backlog
        existing = job_service.get_progress_events(str(job_id))
        has_terminal = False
        for evt in existing:
            yield f"data: {evt}\n\n"
            try:
                parsed = json.loads(evt)
                if parsed.get("event") in ("completed", "failed"):
                    has_terminal = True
            except json.JSONDecodeError:
                pass

        # If terminal event already in backlog, or job is done, stop
        if has_terminal:
            return
        job_fresh = job_service.get_job(str(job_id))
        if job_fresh and job_fresh.status.value in ("completed", "failed", "timeout"):
            return

        # Phase 2: subscribe to live events
        pubsub = redis.pubsub()
        pubsub.subscribe(channel)
        try:
            while True:
                if await request.is_disconnected():
                    break
                msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg["type"] == "message":
                    data = msg["data"]
                    yield f"data: {data}\n\n"
                    # Stop on terminal events
                    try:
                        parsed = json.loads(data)
                        if parsed.get("event") in ("completed", "failed"):
                            break
                    except json.JSONDecodeError:
                        pass
                else:
                    await asyncio.sleep(0.5)
        finally:
            pubsub.unsubscribe(channel)
            pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _render_page(job_id: str, safe_prompt: str, status: str) -> str:
    """Render the terminal-like progress page."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Vibe Agent</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
      background: #0d1117; color: #c9d1d9;
      padding: 24px; max-width: 860px; margin: 0 auto;
      font-size: 13px; line-height: 1.6;
    }}
    a {{ color: #58a6ff; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}

    /* Header */
    .header {{ margin-bottom: 20px; }}
    .header h1 {{
      font-size: 14px; font-weight: 600; color: #58a6ff;
      display: flex; align-items: center; gap: 8px;
    }}
    .prompt-box {{
      background: #161b22; border: 1px solid #30363d; border-radius: 6px;
      padding: 10px 14px; margin-top: 10px; color: #8b949e; font-size: 12px;
      white-space: pre-wrap; word-break: break-word;
    }}
    #status {{
      font-size: 11px; color: #484f58; margin-top: 8px;
    }}

    /* Spinner */
    .spinner {{
      display: inline-block; width: 10px; height: 10px;
      border: 2px solid #30363d; border-top-color: #58a6ff;
      border-radius: 50%; animation: spin 0.8s linear infinite;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}

    /* Event stream */
    #events {{ margin-top: 16px; }}

    /* Tool call block */
    .tool-call {{
      margin: 4px 0; animation: fadein 0.2s ease-in;
    }}
    .tool-call-header {{
      color: #3fb950; cursor: pointer; user-select: none;
      padding: 4px 0; display: flex; align-items: baseline; gap: 6px;
    }}
    .tool-call-header:hover {{ color: #56d364; }}
    .tool-name {{ font-weight: 600; }}
    .tool-args {{
      color: #484f58; font-size: 12px; overflow: hidden;
      text-overflow: ellipsis; white-space: nowrap; max-width: 500px;
    }}
    .tool-args-full {{
      display: none; background: #161b22; border: 1px solid #30363d;
      border-radius: 4px; padding: 8px 12px; margin: 4px 0 4px 20px;
      font-size: 12px; color: #8b949e; white-space: pre-wrap; word-break: break-word;
    }}
    .tool-call.expanded .tool-args-full {{ display: block; }}
    .tool-call.expanded .tool-args {{ display: none; }}

    /* Tool result */
    .tool-result {{
      margin: 2px 0 8px 20px; padding: 6px 12px;
      border-left: 2px solid #30363d; color: #8b949e;
      font-size: 12px; animation: fadein 0.2s ease-in;
    }}
    .tool-result.error {{
      border-left-color: #f85149; color: #f85149;
    }}
    .tool-result-content {{
      white-space: pre-wrap; word-break: break-word;
      max-height: 200px; overflow-y: auto;
    }}
    .tool-result-content.collapsed {{
      max-height: 120px; overflow: hidden;
      position: relative;
    }}
    .tool-result-content.collapsed::after {{
      content: ''; position: absolute; bottom: 0; left: 0; right: 0;
      height: 40px; background: linear-gradient(transparent, #0d1117);
    }}
    .tool-result .expand-btn {{
      color: #58a6ff; cursor: pointer; font-size: 11px;
      margin-top: 2px; display: inline-block;
    }}
    .tool-result .expand-btn:hover {{ text-decoration: underline; }}
    .tool-duration {{ color: #484f58; font-size: 11px; margin-left: 8px; }}

    /* Assistant message */
    .assistant-msg {{
      margin: 10px 0; padding: 8px 0;
      color: #e6edf3; white-space: pre-wrap; word-break: break-word;
      animation: fadein 0.2s ease-in;
    }}

    /* Terminal banners */
    .banner {{
      margin: 16px 0 8px; padding: 10px 14px;
      border-radius: 6px; font-weight: 600; font-size: 13px;
      animation: fadein 0.3s ease-in;
    }}
    .banner.success {{ background: #0d2818; border: 1px solid #238636; color: #3fb950; }}
    .banner.error {{ background: #2d1117; border: 1px solid #da3633; color: #f85149; }}

    /* Final result */
    #final-result {{
      display: none; background: #161b22; border: 1px solid #30363d;
      border-radius: 6px; padding: 14px; margin-top: 12px;
      white-space: pre-wrap; word-break: break-word; font-size: 12px;
      max-height: 500px; overflow-y: auto; color: #c9d1d9;
    }}

    @keyframes fadein {{
      from {{ opacity: 0; transform: translateY(3px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
  </style>
</head>
<body>
  <div class="header">
    <h1><span class="spinner" id="spinner"></span> vibe agent</h1>
    <div class="prompt-box">&gt; {safe_prompt}</div>
    <div id="status">status: {status}</div>
  </div>
  <div id="events"></div>
  <pre id="final-result"></pre>

  <script>
    const eventsEl = document.getElementById('events');
    const finalResult = document.getElementById('final-result');
    const spinner = document.getElementById('spinner');
    const statusEl = document.getElementById('status');

    // Auto-link URLs in text
    function linkify(text) {{
      return text.replace(
        /(https?:\\/\\/[^\\s<>)\\]]+)/g,
        '<a href="$1" target="_blank" rel="noopener">$1</a>'
      );
    }}

    // Escape HTML entities
    function esc(s) {{
      const d = document.createElement('div');
      d.textContent = s;
      return d.innerHTML;
    }}

    // Format tool args as compact string
    function fmtArgs(args) {{
      if (!args || Object.keys(args).length === 0) return '';
      const parts = [];
      for (const [k, v] of Object.entries(args)) {{
        let val = typeof v === 'string' ? v : JSON.stringify(v);
        if (val.length > 60) val = val.slice(0, 57) + '...';
        parts.push(k + '=' + JSON.stringify(val));
      }}
      return parts.join(' ');
    }}

    // Format tool args as full block
    function fmtArgsFull(args) {{
      if (!args || Object.keys(args).length === 0) return '';
      return Object.entries(args)
        .map(([k, v]) => k + ': ' + (typeof v === 'string' ? v : JSON.stringify(v, null, 2)))
        .join('\\n');
    }}

    function renderEvent(data) {{
      switch (data.event) {{
        case 'tool_call': {{
          const div = document.createElement('div');
          div.className = 'tool-call';
          const argsShort = fmtArgs(data.args);
          const argsFull = fmtArgsFull(data.args);
          div.innerHTML =
            '<div class="tool-call-header" onclick="this.parentElement.classList.toggle(\\'expanded\\')">' +
            '  <span>\\u25b6</span>' +
            '  <span class="tool-name">' + esc(data.tool) + '</span>' +
            '  <span class="tool-args">' + esc(argsShort) + '</span>' +
            '</div>' +
            '<div class="tool-args-full">' + linkify(esc(argsFull)) + '</div>';
          eventsEl.appendChild(div);
          break;
        }}
        case 'tool_result': {{
          const div = document.createElement('div');
          const isErr = !!data.error;
          div.className = 'tool-result' + (isErr ? ' error' : '');
          const content = data.error || data.result || '(no output)';
          const lines = content.split('\\n');
          const needsCollapse = lines.length > 8;
          const durStr = data.duration ? ' <span class="tool-duration">' + data.duration.toFixed(1) + 's</span>' : '';
          div.innerHTML =
            '<div class="tool-result-content' + (needsCollapse ? ' collapsed' : '') + '">' +
            linkify(esc(content)) +
            '</div>' +
            (needsCollapse ? '<span class="expand-btn" onclick="const c=this.previousElementSibling;c.classList.toggle(\\'collapsed\\');this.textContent=c.classList.contains(\\'collapsed\\')?\\'show more\\':\\'show less\\'">show more</span>' : '') +
            durStr;
          eventsEl.appendChild(div);
          break;
        }}
        case 'assistant': {{
          // Merge consecutive assistant chunks into one element
          const last = eventsEl.lastElementChild;
          if (last && last.classList.contains('assistant-msg')) {{
            last.innerHTML += linkify(esc(data.content));
          }} else {{
            const div = document.createElement('div');
            div.className = 'assistant-msg';
            div.innerHTML = linkify(esc(data.content));
            eventsEl.appendChild(div);
          }}
          break;
        }}
        case 'completed': {{
          const div = document.createElement('div');
          div.className = 'banner success';
          div.textContent = '\\u2705 Done!';
          eventsEl.appendChild(div);
          statusEl.textContent = 'status: completed';
          spinner.style.display = 'none';
          if (data.content) {{
            finalResult.style.display = 'block';
            finalResult.innerHTML = linkify(esc(data.content));
          }}
          break;
        }}
        case 'failed': {{
          const div = document.createElement('div');
          div.className = 'banner error';
          div.innerHTML = '\\u274c ' + linkify(esc(data.content || 'Task failed'));
          eventsEl.appendChild(div);
          statusEl.textContent = 'status: failed';
          spinner.style.display = 'none';
          break;
        }}
      }}
      // Auto-scroll
      window.scrollTo({{ top: document.body.scrollHeight, behavior: 'smooth' }});
    }}

    // Connect SSE
    let finished = false;
    const es = new EventSource('/jobs/{job_id}/stream');
    es.onmessage = function(e) {{
      const data = JSON.parse(e.data);
      renderEvent(data);
      if (data.event === 'completed' || data.event === 'failed') {{
        finished = true;
        es.close();
      }}
    }};
    es.onerror = function() {{
      if (!finished) {{
        statusEl.textContent += ' (connection lost, refresh to retry)';
        spinner.style.display = 'none';
      }}
    }};
  </script>
</body>
</html>"""
