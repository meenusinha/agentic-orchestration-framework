"""Initialise and close the HTML log file written to by demo_logger.py."""
from pathlib import Path

_HEAD = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="3">
<title>MCP Orchestration Log</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0d1117;color:#e6edf3;font-family:monospace;font-size:13px;padding:20px}
  h2{color:#58a6ff;margin-bottom:4px}
  #meta{color:#8b949e;font-size:11px;margin-bottom:16px}
  details{border:1px solid #30363d;border-radius:6px;margin:4px 0;background:#161b22;overflow:hidden}
  details[open]{border-color:#58a6ff55}
  summary{cursor:pointer;padding:7px 12px;font-weight:bold;display:flex;align-items:center;gap:8px;user-select:none;list-style:none}
  summary::-webkit-details-marker{display:none}
  summary::before{content:"▶";font-size:9px;color:#8b949e;transition:transform .15s}
  details[open]>summary::before{transform:rotate(90deg)}
  .badge{margin-left:auto;background:#21262d;border-radius:10px;padding:1px 8px;font-size:10px;color:#8b949e;font-weight:normal}
  .entries{padding:0 12px 8px 28px;border-top:1px solid #21262d}
  .e{display:flex;gap:8px;padding:3px 0;border-bottom:1px solid #21262d22;font-size:12px;word-break:break-word}
  .e:last-child{border-bottom:none}
  .ts{color:#8b949e;flex-shrink:0;font-size:11px;padding-top:1px}
  .lv{flex-shrink:0;min-width:72px;font-weight:bold}
  .mg{color:#c9d1d9}
  .lvl-INDEX{color:#58a6ff}  .lvl-RAG{color:#3fb950}     .lvl-RESULT{color:#f0883e}
  .lvl-ARCH{color:#bc8cff}   .lvl-ROUTING{color:#58a6ff}  .lvl-TOOL_CALL{color:#58a6ff}
  .lvl-INFO{color:#8b949e}   .lvl-WARN{color:#d29922}     .lvl-ERROR{color:#ff7b72}
</style>
<script>
document.addEventListener("DOMContentLoaded", function () {
  const entries = [...document.querySelectorAll("#raw .e")];

  // Group: repo → level → [entry HTML strings]
  const tree = {};
  entries.forEach(function (e) {
    const r = e.dataset.r, l = e.dataset.l;
    if (!tree[r]) tree[r] = {};
    if (!tree[r][l]) tree[r][l] = [];
    tree[r][l].push(e.outerHTML);
  });

  let html = "";
  for (const [repo, levels] of Object.entries(tree)) {
    const total = Object.values(levels).reduce(function (s, a) { return s + a.length; }, 0);
    html += '<details open><summary>📦 ' + repo
          + '<span class="badge">' + total + ' entries</span></summary>';
    for (const [lvl, items] of Object.entries(levels)) {
      html += '<details open><summary class="lvl-' + lvl + '">' + lvl
            + '<span class="badge">' + items.length + '</span></summary>'
            + '<div class="entries">' + items.join("") + '</div></details>';
    }
    html += '</details>';
  }

  const tree_el = document.getElementById("tree");
  tree_el.innerHTML = html || "<p style='color:#8b949e;padding:8px'>Waiting for first log entry...</p>";
  document.getElementById("meta").textContent =
    "Auto-refreshes every 3 s  ·  Last updated: " + new Date().toLocaleTimeString()
    + "  ·  " + entries.length + " entries";
});
</script>
</head>
<body>
<h2>🤖 MCP Orchestration Log</h2>
<p id="meta"></p>
<div id="tree"></div>
<div id="raw" style="display:none">
"""

_TAIL = "</div>\n</body>\n</html>\n"


def init(html_path: str) -> None:
    """Write the HTML header. Called once at test_mcp.py startup."""
    Path(html_path).write_text(_HEAD, encoding="utf-8")


def close(html_path: str) -> None:
    """Append the closing tags after all log entries have been written."""
    with open(html_path, "a", encoding="utf-8") as f:
        f.write(_TAIL)


def path_from_log(log_path: str) -> str:
    """Derive the HTML file path from the plain-text log file path."""
    return log_path.rsplit(".", 1)[0] + ".html" if "." in log_path else log_path + ".html"
