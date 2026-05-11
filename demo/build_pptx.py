"""
Generates demo/slides.pptx with fully editable text using python-pptx.
Run from repo root: python demo/build_pptx.py
"""
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

# ── Colours ───────────────────────────────────────────────────────────────────
BG        = RGBColor(0x0d, 0x11, 0x17)   # slide background
BLUE      = RGBColor(0x58, 0xa6, 0xff)   # headings / accent
WHITE     = RGBColor(0xf0, 0xf6, 0xfc)   # body text
GREY      = RGBColor(0x8b, 0x94, 0x9e)   # subdued text
GREEN     = RGBColor(0x3f, 0xb9, 0x50)   # positive / code
ORANGE    = RGBColor(0xf0, 0x88, 0x3e)   # warning
RED       = RGBColor(0xff, 0x7b, 0x72)   # error / problem
PURPLE    = RGBColor(0xbc, 0x8c, 0xff)   # copilot
CARD_BG   = RGBColor(0x16, 0x1b, 0x22)   # card background
BORDER    = RGBColor(0x30, 0x36, 0x3d)   # borders

W = Inches(13.33)   # widescreen width
H = Inches(7.5)     # widescreen height

# ── Helpers ───────────────────────────────────────────────────────────────────
def new_slide(prs):
    layout = prs.slide_layouts[6]   # blank
    slide  = prs.slides.add_slide(layout)
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = BG
    return slide

def add_text(slide, text, x, y, w, h, size=18, bold=False, color=WHITE,
             align=PP_ALIGN.LEFT, wrap=True):
    txb = slide.shapes.add_textbox(x, y, w, h)
    txb.word_wrap = wrap
    tf  = txb.text_frame
    tf.word_wrap = wrap
    p   = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size  = Pt(size)
    run.font.bold  = bold
    run.font.color.rgb = color
    return txb

def add_box(slide, x, y, w, h, fill=CARD_BG, line=BORDER):
    shape = slide.shapes.add_shape(1, x, y, w, h)   # MSO_SHAPE_TYPE.RECTANGLE = 1
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.color.rgb = line
    shape.line.width = Emu(9525)   # 0.75pt
    return shape

def add_box_with_text(slide, text, x, y, w, h, size=12, bold=False,
                      color=WHITE, fill=CARD_BG, line=BORDER,
                      align=PP_ALIGN.LEFT):
    add_box(slide, x, y, w, h, fill=fill, line=line)
    pad = Inches(0.12)
    add_text(slide, text, x+pad, y+pad, w-pad*2, h-pad*2,
             size=size, bold=bold, color=color, align=align)

def label(slide, text, x, y, w=Inches(10)):
    add_text(slide, text, x, y, w, Inches(0.3),
             size=10, bold=True, color=BLUE, align=PP_ALIGN.CENTER)

def hline(slide, y):
    line = slide.shapes.add_connector(1, Inches(0.5), y, Inches(12.83), y)
    line.line.color.rgb = BORDER
    line.line.width = Emu(9525)

def conn(slide, x1, y1, x2, y2, color=BLUE, lw=Emu(15876), ctype=1):
    """Draw a connector (ctype 1=straight, 2=elbow/angular) with arrowhead at (x2,y2)."""
    from lxml import etree
    c = slide.shapes.add_connector(ctype, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = color
    c.line.width = lw
    # Arrowhead via XML on the connector's spPr/ln element
    try:
        from pptx.oxml.ns import qn
        sp_pr = c._element.find(qn('p:spPr'))
        ans   = 'http://schemas.openxmlformats.org/drawingml/2006/main'
        ln    = sp_pr.find(f'{{{ans}}}ln')
        if ln is None:
            ln = etree.SubElement(sp_pr, f'{{{ans}}}ln')
        tail = etree.SubElement(ln, f'{{{ans}}}tailEnd')
        tail.set('type', 'arrow')
        tail.set('w', 'med')
        tail.set('len', 'med')
    except Exception:
        pass   # arrowhead failed gracefully — line still visible
    return c

def conn_label(slide, x1, y1, x2, y2, text, color=BLUE):
    """Small label centred on a connector."""
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    add_text(slide, text, Inches(mx - 0.9), Inches(my - 0.17),
             Inches(1.8), Inches(0.32), size=8, color=color, align=PP_ALIGN.CENTER)

def seq_badge(slide, n, x, y, color=BLUE):
    """Small filled square with a sequence number, placed at (x, y)."""
    add_box(slide, Inches(x - 0.14), Inches(y - 0.14), Inches(0.28), Inches(0.28),
            fill=color, line=color)
    add_text(slide, str(n), Inches(x - 0.14), Inches(y - 0.16), Inches(0.28), Inches(0.28),
             size=8, bold=True, color=BG, align=PP_ALIGN.CENTER)

# ── Slide builder functions ───────────────────────────────────────────────────

def slide_title(prs):
    s = new_slide(prs)
    label(s, "AGENTIC AI", Inches(0.5), Inches(1.5))
    add_text(s, "Multi-Repo Agentic\nOrchestration Framework",
             Inches(0.5), Inches(1.9), Inches(12.3), Inches(1.8),
             size=40, bold=True, color=BLUE, align=PP_ALIGN.CENTER)
    add_text(s, "Cross-repo AI awareness for lithography software development",
             Inches(0.5), Inches(3.8), Inches(12.3), Inches(0.5),
             size=18, color=GREY, align=PP_ALIGN.CENTER)

    tech = ["MCP Protocol", "RAG", "ChromaDB", "sentence-transformers", "FastMCP", "GitHub Copilot", "Python 3.11+"]
    x = Inches(1.2)
    for t in tech:
        bw = Inches(1.5)
        add_box_with_text(s, t, x, Inches(4.8), bw, Inches(0.35),
                          size=10, bold=True, color=BLUE,
                          fill=RGBColor(0x1f,0x3a,0x5f), line=RGBColor(0x2d,0x59,0x86),
                          align=PP_ALIGN.CENTER)
        x += bw + Inches(0.08)


def slide_problem(prs):
    s = new_slide(prs)
    label(s, "THE PROBLEM", Inches(0.5), Inches(0.3))
    add_text(s, "Copilot sees only one repo at a time",
             Inches(0.5), Inches(0.6), Inches(12.3), Inches(0.5),
             size=28, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    add_text(s, "Real features span multiple lithography subsystems — but your AI assistant is blind to them",
             Inches(0.5), Inches(1.2), Inches(12.3), Inches(0.4),
             size=14, color=GREY, align=PP_ALIGN.CENTER)

    repos = ["scan_manager", "illumination", "expose_sequence", "wafer_handler"]
    positions = [Inches(0.4), Inches(3.4), Inches(6.4), Inches(9.4)]
    for name, x in zip(repos, positions):
        add_box_with_text(s, f"📦  {name}", x, Inches(1.9), Inches(2.8), Inches(0.7),
                          size=13, bold=True, color=WHITE)

    add_box_with_text(s, "🤖  GitHub Copilot\n     sees only scan_manager",
                      Inches(3.4), Inches(2.9), Inches(2.8), Inches(0.8),
                      size=12, color=PURPLE,
                      fill=RGBColor(0x2d,0x1f,0x47), line=PURPLE)

    problems = [
        "🔴   A dose control change in expose_sequence must align with scan_manager timing — Copilot cannot see this",
        "🔴   Illumination parameter updates affect exposure logic across repos — completely invisible to the AI",
        "🔴   Developer must manually gather context from all subsystem repos before every cross-repo question",
        "🔴   AI suggestions are incomplete — only one subsystem's perspective, missing critical dependencies",
    ]
    y = Inches(4.0)
    for p in problems:
        add_box_with_text(s, p, Inches(0.4), y, Inches(12.5), Inches(0.5),
                          size=12, color=WHITE,
                          fill=RGBColor(0x1a,0x0a,0x0a), line=RED)
        y += Inches(0.6)


def slide_solution(prs):
    s = new_slide(prs)
    label(s, "THE SOLUTION", Inches(0.5), Inches(0.3))
    add_text(s, "An orchestra of specialised agents",
             Inches(0.5), Inches(0.6), Inches(12.3), Inches(0.5),
             size=28, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    add_text(s, "Each subsystem repo has its own agent. An orchestrator routes intelligently. Copilot synthesises.",
             Inches(0.5), Inches(1.2), Inches(12.3), Inches(0.4),
             size=14, color=GREY, align=PP_ALIGN.CENTER)

    # Copilot box at top
    add_box_with_text(s, "🤖  GitHub Copilot  (Agent mode)",
                      Inches(4.5), Inches(1.8), Inches(4.3), Inches(0.6),
                      size=14, bold=True, color=PURPLE,
                      fill=RGBColor(0x2d,0x1f,0x47), line=PURPLE, align=PP_ALIGN.CENTER)

    add_text(s, "↕", Inches(6.4), Inches(2.5), Inches(0.5), Inches(0.4),
             size=20, color=BLUE, align=PP_ALIGN.CENTER)

    # Orchestrator
    add_box_with_text(s, "🎯  Orchestrator\n     Routes by RAG relevance score\n     Returns top 2 relevant repos",
                      Inches(4.2), Inches(3.0), Inches(4.9), Inches(0.95),
                      size=12, bold=False, color=GREEN,
                      fill=RGBColor(0x0d,0x1f,0x12), line=GREEN, align=PP_ALIGN.CENTER)

    add_text(s, "↙                    ↓                    ↘",
             Inches(2.5), Inches(4.05), Inches(8.0), Inches(0.4),
             size=16, color=BLUE, align=PP_ALIGN.CENTER)

    # Repo agents
    agents = ["📦  scan_manager\n     RAG index", "📦  illumination\n     RAG index", "📦  expose_sequence\n     RAG index"]
    positions = [Inches(0.8), Inches(4.9), Inches(9.0)]
    for name, x in zip(agents, positions):
        add_box_with_text(s, name, x, Inches(4.55), Inches(3.3), Inches(0.75),
                          size=12, color=BLUE,
                          fill=RGBColor(0x0d,0x1f,0x35), line=BLUE, align=PP_ALIGN.CENTER)

    add_box_with_text(s, "✅  Feature Analysis Document — cross-subsystem, complete, grounded in real knowledge",
                      Inches(0.8), Inches(5.6), Inches(11.7), Inches(0.5),
                      size=13, bold=True, color=GREEN,
                      fill=RGBColor(0x0d,0x1f,0x12), line=GREEN, align=PP_ALIGN.CENTER)


def slide_architecture(prs):
    s = new_slide(prs)
    label(s, "ARCHITECTURE", Inches(0.5), Inches(0.3))
    add_text(s, "How it all fits together",
             Inches(0.5), Inches(0.6), Inches(12.3), Inches(0.5),
             size=28, bold=True, color=WHITE, align=PP_ALIGN.CENTER)

    headers = ["Layer", "Component", "Role"]
    col_x   = [Inches(0.4), Inches(2.8), Inches(7.3)]
    col_w   = [Inches(2.2), Inches(4.3), Inches(5.6)]

    rows = [
        ("AI Interface",   "GitHub Copilot + Agent mode",          "Developer's entry point — types feature request"),
        ("Instructions",   "copilot-instructions.md",              "Tells Copilot to call MCP tools in order"),
        ("Tool Config",    ".vscode/mcp.json  (auto-generated)",   "Wires up all MCP servers with absolute paths"),
        ("Orchestrator",   "router_mcp_server.py",                 "Routes query to most relevant subsystem repos"),
        ("Repo Agents",    "mcp_server.py  (per repo)",            "Exposes query_repo tool, runs RAG search"),
        ("Knowledge",      "repo_rag.py + ChromaDB",               "Indexes docs + source code, serves queries"),
        ("Embedding",      "all-MiniLM-L6-v2  (local, ~90 MB)",   "Converts text to semantic vectors, fully offline"),
    ]

    # Header row
    y = Inches(1.4)
    for i, (hdr, cx, cw) in enumerate(zip(headers, col_x, col_w)):
        add_box_with_text(s, hdr, cx, y, cw, Inches(0.38),
                          size=12, bold=True, color=BLUE,
                          fill=RGBColor(0x16,0x1b,0x22), line=BORDER, align=PP_ALIGN.CENTER)

    # Data rows
    y += Inches(0.38)
    for row in rows:
        for i, (val, cx, cw) in enumerate(zip(row, col_x, col_w)):
            c = BLUE if i == 0 else (GREEN if i == 1 else GREY)
            add_box_with_text(s, val, cx, y, cw, Inches(0.55),
                              size=11, color=c,
                              fill=RGBColor(0x0d,0x11,0x17), line=BORDER)
        y += Inches(0.55)

    add_text(s, "★  One setup.py generates all mcp.json and copilot-instructions.md files automatically",
             Inches(0.4), Inches(7.0), Inches(12.5), Inches(0.35),
             size=12, bold=True, color=ORANGE, align=PP_ALIGN.CENTER)


def slide_tech_stack(prs):
    s = new_slide(prs)
    label(s, "TECHNOLOGY STACK", Inches(0.5), Inches(0.3))
    add_text(s, "Purpose-built from proven components",
             Inches(0.5), Inches(0.6), Inches(12.3), Inches(0.5),
             size=28, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    add_text(s, "Every layer is local — no cloud calls, no data leaves your machine",
             Inches(0.5), Inches(1.2), Inches(12.3), Inches(0.35),
             size=14, color=GREY, align=PP_ALIGN.CENTER)

    layers = [
        (PURPLE, "AI Interface",     "GitHub Copilot  ·  Agent Mode  ·  copilot-instructions.md"),
        (BLUE,   "Communication",    "MCP — Model Context Protocol  ·  stdio transport  ·  JSON-RPC 2.0  ·  FastMCP"),
        (GREEN,  "Knowledge / RAG",  "ChromaDB (vector store)  ·  sentence-transformers  ·  all-MiniLM-L6-v2 (local model)"),
        (ORANGE, "Config / Infra",   "config.yaml  ·  mcp.json (auto-generated)  ·  Python 3.11+  ·  pysqlite3-binary"),
        (RED,    "IDE",              "VS Code  ·  .vscode/mcp.json  ·  .github/copilot-instructions.md"),
    ]

    y = Inches(1.75)
    for color, layer_name, items in layers:
        add_box_with_text(s, layer_name, Inches(0.4), y, Inches(2.2), Inches(0.55),
                          size=12, bold=True, color=color,
                          fill=RGBColor(0x16,0x1b,0x22), line=color, align=PP_ALIGN.CENTER)
        add_box_with_text(s, items, Inches(2.7), y, Inches(10.2), Inches(0.55),
                          size=12, color=WHITE,
                          fill=RGBColor(0x0d,0x11,0x17), line=BORDER)
        y += Inches(0.65)


def slide_workflow(prs):
    s = new_slide(prs)
    label(s, "WORKFLOW", Inches(0.5), Inches(0.3))
    add_text(s, "4 steps, fully automatic",
             Inches(0.5), Inches(0.6), Inches(12.3), Inches(0.5),
             size=28, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    add_text(s, 'Developer: "Add real-time dose control feedback during wafer exposure"',
             Inches(0.5), Inches(1.2), Inches(12.3), Inches(0.4),
             size=14, color=ORANGE, align=PP_ALIGN.CENTER)

    steps = [
        ("1", "Route",          "orchestrator_router\n.get_relevant_repos()",
               "Queries each repo RAG\nScores by content length\nReturns top 2 repos",
               "expose_sequence: 0.91\nillumination: 0.74"),
        ("2", "Own Repo",       "scan_manager\n.query_repo()",
               "Searches own docs\n+ source code via RAG",
               "Returns scan timing,\nstage control knowledge"),
        ("3", "Query Peers",    "expose_sequence\n.query_repo()\nillumination\n.query_repo()",
               "Retrieves dose calc,\npulse control knowledge\nfrom selected peers",
               "Returns combined\nknowledge chunks"),
        ("4", "Synthesise",     "Feature Analysis\nDocument",
               "Current State per repo\n+ cross-subsystem\nSolution Design",
               "Complete cross-repo\nanswer for Copilot"),
    ]

    x = Inches(0.3)
    for num, title, tool, body, result in steps:
        # Step number circle (simulated with box)
        add_box_with_text(s, num, x+Inches(0.85), Inches(1.85), Inches(0.45), Inches(0.45),
                          size=14, bold=True, color=BLUE,
                          fill=RGBColor(0x1f,0x3a,0x5f), line=BLUE, align=PP_ALIGN.CENTER)
        # Main card
        add_box_with_text(s, title, x, Inches(2.4), Inches(2.9), Inches(0.45),
                          size=13, bold=True, color=WHITE,
                          fill=RGBColor(0x16,0x1b,0x22), line=BLUE, align=PP_ALIGN.CENTER)
        add_box_with_text(s, tool, x, Inches(2.95), Inches(2.9), Inches(0.7),
                          size=10, bold=True, color=GREEN,
                          fill=RGBColor(0x0d,0x1f,0x12), line=GREEN, align=PP_ALIGN.CENTER)
        add_box_with_text(s, body, x, Inches(3.75), Inches(2.9), Inches(1.1),
                          size=11, color=GREY,
                          fill=RGBColor(0x0d,0x11,0x17), line=BORDER)
        add_box_with_text(s, result, x, Inches(4.95), Inches(2.9), Inches(0.7),
                          size=11, color=ORANGE,
                          fill=RGBColor(0x1f,0x12,0x00), line=ORANGE)
        if num != "4":
            add_text(s, "→", x+Inches(2.93), Inches(3.5), Inches(0.35), Inches(0.4),
                     size=20, bold=True, color=BLUE)
        x += Inches(3.1) + Inches(0.17)


def slide_rag(prs):
    s = new_slide(prs)
    label(s, "UNDER THE HOOD — RAG", Inches(0.5), Inches(0.3))
    add_text(s, "How each agent knows its subsystem",
             Inches(0.5), Inches(0.6), Inches(12.3), Inches(0.5),
             size=28, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    add_text(s, "Retrieval-Augmented Generation — semantic search over your own docs and source code",
             Inches(0.5), Inches(1.2), Inches(12.3), Inches(0.35),
             size=14, color=GREY, align=PP_ALIGN.CENTER)

    # Phase 1
    add_box_with_text(s, "PHASE 1 — Indexing  (runs once on first start)",
                      Inches(0.4), Inches(1.75), Inches(5.9), Inches(0.4),
                      size=12, bold=True, color=BLUE,
                      fill=RGBColor(0x1f,0x3a,0x5f), line=BLUE)
    phase1 = [
        "📄  .md docs (knowledge/ + BB-*/*/docs)  →  split into chunks  →  embed  →  ChromaDB",
        "💻  Source code (BB-*/*/com)              →  split into chunks  →  embed  →  ChromaDB",
        "💾  Stored in .chroma_db/  —  persistent cache, rebuilt on each test run",
    ]
    y = Inches(2.25)
    for line in phase1:
        add_box_with_text(s, line, Inches(0.4), y, Inches(12.5), Inches(0.42),
                          size=11, color=WHITE,
                          fill=RGBColor(0x0d,0x11,0x17), line=BORDER)
        y += Inches(0.47)

    # Phase 2
    add_box_with_text(s, "PHASE 2 — Query  (every call to query_repo)",
                      Inches(0.4), Inches(3.7), Inches(5.9), Inches(0.4),
                      size=12, bold=True, color=GREEN,
                      fill=RGBColor(0x0d,0x1f,0x12), line=GREEN)
    phase2 = [
        '❓  Feature request: "dose control feedback wafer exposure scan timing"',
        "     →  embed query  →  search docs collection  (top 3 semantically similar chunks)",
        "     →  search code collection  (top 3 chunks) — always, not just when docs are thin",
        "     →  return:  [From documentation] ...  +  [From source code] ...",
        "     →  orchestrator scores:  min(content_length / 800,  1.0)",
    ]
    y = Inches(4.2)
    for line in phase2:
        add_box_with_text(s, line, Inches(0.4), y, Inches(12.5), Inches(0.42),
                          size=11, color=WHITE if not line.startswith("❓") else ORANGE,
                          fill=RGBColor(0x0d,0x11,0x17), line=BORDER)
        y += Inches(0.47)


def slide_mcp(prs):
    s = new_slide(prs)
    label(s, "UNDER THE HOOD — MCP", Inches(0.5), Inches(0.3))
    add_text(s, "How agents communicate",
             Inches(0.5), Inches(0.6), Inches(12.3), Inches(0.5),
             size=28, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    add_text(s, "Model Context Protocol — stdin/stdout only, no network ports, no cloud",
             Inches(0.5), Inches(1.2), Inches(12.3), Inches(0.35),
             size=14, color=GREY, align=PP_ALIGN.CENTER)

    protocol = [
        ('msg 1', 'initialize',                    'Handshake — agree on protocol version'),
        ('msg 2', 'notifications/initialized',     'Client confirms ready'),
        ('msg 3', 'tools/call  →  query_repo',     'Actual RAG query — returns relevant knowledge'),
        ('reply', 'result.content[0].text',        'RELEVANT KNOWLEDGE: [docs] ... [source code] ...'),
    ]
    y = Inches(1.75)
    for tag, method, desc in protocol:
        add_box_with_text(s, tag, Inches(0.4), y, Inches(0.9), Inches(0.45),
                          size=10, bold=True, color=GREY,
                          fill=RGBColor(0x16,0x1b,0x22), line=BORDER, align=PP_ALIGN.CENTER)
        add_box_with_text(s, method, Inches(1.4), y, Inches(3.8), Inches(0.45),
                          size=11, bold=True, color=GREEN,
                          fill=RGBColor(0x0d,0x1f,0x12), line=GREEN)
        add_box_with_text(s, desc, Inches(5.3), y, Inches(7.6), Inches(0.45),
                          size=11, color=GREY,
                          fill=RGBColor(0x0d,0x11,0x17), line=BORDER)
        y += Inches(0.52)

    # Two server cards
    add_box_with_text(s,
        "router_mcp_server.py\n\nTool: get_relevant_repos\n\nCalled by Copilot in step 1\nSpawns each repo agent as subprocess\nScores responses, returns top 2",
        Inches(0.4), Inches(4.3), Inches(6.1), Inches(2.8),
        size=12, color=WHITE, fill=RGBColor(0x0d,0x1f,0x12), line=GREEN)

    add_box_with_text(s,
        "mcp_server.py  (per subsystem repo)\n\nTool: query_repo\n\nCalled by Copilot + orchestrator\nSearches ChromaDB for relevant chunks\nReturns docs + source code knowledge",
        Inches(6.8), Inches(4.3), Inches(6.1), Inches(2.8),
        size=12, color=WHITE, fill=RGBColor(0x0d,0x1f,0x35), line=BLUE)


def slide_setup(prs):
    s = new_slide(prs)
    label(s, "SETUP", Inches(0.5), Inches(0.3))
    add_text(s, "What the user does — once",
             Inches(0.5), Inches(0.6), Inches(12.3), Inches(0.5),
             size=28, bold=True, color=WHITE, align=PP_ALIGN.CENTER)

    steps = [
        ("1", "Clone framework",       "git clone <this repo>"),
        ("2", "Copy embedding model",  "Copy models/all-MiniLM-L6-v2/ into repo root  (offline, ~90 MB)"),
        ("3", "Install dependencies",  "pip install -r requirements.txt"),
        ("4", "Configure orchestrator","Edit orchestrator/mcp/config.yaml — list all subsystem repos + paths"),
        ("5", "Add agent files",       "cp repo-agent/mcp/ knowledge/ .github/  into each subsystem repo"),
        ("6", "Configure each repo",   "Edit each repo's mcp/config.yaml — set repo_name and src_path (glob ok)"),
        ("7", "Run setup",             "python orchestrator/setup.py — generates mcp.json + copilot-instructions.md"),
        ("8", "Open in VS Code",       "source .venv/bin/activate  &&  code /path/to/repo"),
    ]

    y = Inches(1.4)
    for num, title, detail in steps:
        add_box_with_text(s, num, Inches(0.4), y, Inches(0.38), Inches(0.48),
                          size=12, bold=True, color=BLUE,
                          fill=RGBColor(0x1f,0x3a,0x5f), line=BLUE, align=PP_ALIGN.CENTER)
        add_box_with_text(s, title, Inches(0.88), y, Inches(2.6), Inches(0.48),
                          size=12, bold=True, color=WHITE,
                          fill=RGBColor(0x16,0x1b,0x22), line=BORDER)
        add_box_with_text(s, detail, Inches(3.58), y, Inches(9.7), Inches(0.48),
                          size=11, color=GREY,
                          fill=RGBColor(0x0d,0x11,0x17), line=BORDER)
        y += Inches(0.55)

    add_text(s, "★  Only config.yaml files are ever edited by the user — everything else is generated",
             Inches(0.4), Inches(7.05), Inches(12.5), Inches(0.35),
             size=12, bold=True, color=ORANGE, align=PP_ALIGN.CENTER)


def slide_demo(prs):
    s = new_slide(prs)
    label(s, "LIVE DEMO", Inches(0.5), Inches(0.5))
    add_text(s, "Let's see it in action",
             Inches(0.5), Inches(0.9), Inches(12.3), Inches(0.6),
             size=36, bold=True, color=BLUE, align=PP_ALIGN.CENTER)

    cmds = [
        ("Terminal 1 — watch live logs",
         "tail -f /tmp/mcp-orchestration.log"),
        ("Terminal 2 — run the simulation",
         'python test_mcp.py "dose control feedback wafer exposure scan timing"'),
    ]
    y = Inches(2.0)
    for title, cmd in cmds:
        add_text(s, title, Inches(1.0), y, Inches(11.3), Inches(0.35),
                 size=13, bold=True, color=GREY)
        add_box_with_text(s, cmd, Inches(1.0), y+Inches(0.38), Inches(11.3), Inches(0.5),
                          size=13, bold=True, color=GREEN,
                          fill=RGBColor(0x0d,0x1f,0x12), line=GREEN)
        y += Inches(1.1)

    add_text(s, "Output", Inches(1.0), Inches(4.3), Inches(11.3), Inches(0.35),
             size=13, bold=True, color=GREY)
    outputs = [
        "LOG  |  [scan_manager]  [RAG]   Searching docs (top_k=3)...",
        "LOG  |  [scan_manager]  [RAG]   Docs: 3 results, 480 chars",
        "LOG  |  [scan_manager]  [RAG]   Searching source code (top_k=3)...",
        "LOG  |  [scan_manager]  [RAG]   Code: 3 results, 620 chars",
        "LOG  |  [orchestrator]  [RESULT] Selected: ['expose_sequence', 'illumination']",
    ]
    y = Inches(4.7)
    for line in outputs:
        add_box_with_text(s, line, Inches(1.0), y, Inches(11.3), Inches(0.37),
                          size=10, color=GREEN,
                          fill=RGBColor(0x0d,0x11,0x17), line=BORDER)
        y += Inches(0.4)

    add_box_with_text(s, "🎯  Output saved to feature_analysis.md — paste into Copilot chat for cross-subsystem Solution Design",
                      Inches(1.0), Inches(7.05), Inches(11.3), Inches(0.38),
                      size=13, bold=True, color=GREEN,
                      fill=RGBColor(0x0d,0x1f,0x12), line=GREEN, align=PP_ALIGN.CENTER)


def slide_component_diagram(prs):
    s = new_slide(prs)

    # ── Header ─────────────────────────────────────────────────────────────────
    add_text(s, "DATA FLOW DIAGRAM", Inches(0.4), Inches(0.10), Inches(12.5), Inches(0.25),
             size=10, bold=True, color=BLUE, align=PP_ALIGN.CENTER)
    add_text(s, "Agentic Orchestration — Components, Interfaces & Data Flow",
             Inches(0.4), Inches(0.37), Inches(12.5), Inches(0.40),
             size=19, bold=True, color=WHITE, align=PP_ALIGN.CENTER)

    # ── Box geometry constants ─────────────────────────────────────────────────
    COP_X, COP_Y, COP_W, COP_H     = 4.3,  0.83, 3.6,  0.62   # Copilot
    ORC_X, ORC_Y, ORC_W, ORC_H     = 0.2,  2.15, 2.9,  0.90   # Orchestrator
    AGT_X, AGT_W, AGT_H            = 3.5,  2.8,  0.65   # Repo Agents (stacked)
    RAG_X, RAG_Y, RAG_W, RAG_H     = 7.1,  2.0,  2.7,  1.10   # RAG Engine
    EMB_X, EMB_Y, EMB_W, EMB_H     = 10.3, 2.1,  2.8,  1.00   # Embedding Model
    CHR_X, CHR_Y, CHR_W, CHR_H     = 7.1,  4.05, 2.7,  0.72   # ChromaDB
    DOC_X, DOC_Y, DOC_W, DOC_H     = 6.65, 5.15, 1.9,  0.62   # .md Docs
    SRC_X, SRC_Y, SRC_W, SRC_H     = 8.85, 5.15, 1.9,  0.62   # Source Code
    LLM_X, LLM_Y, LLM_W, LLM_H     = 10.3, 3.95, 2.8,  1.60   # LLM not-used

    # Derived midpoints / edges used for connectors
    COP_BOT = COP_Y + COP_H                        # 1.45
    COP_CX  = COP_X + COP_W / 2                    # 6.1
    ORC_CX  = ORC_X + ORC_W / 2                    # 1.65
    ORC_CY  = ORC_Y + ORC_H / 2                    # 2.60
    ORC_R   = ORC_X + ORC_W                        # 3.10
    RAG_CY  = RAG_Y + RAG_H / 2                    # 2.55
    RAG_R   = RAG_X + RAG_W                        # 9.80
    RAG_BOT = RAG_Y + RAG_H                        # 3.10
    EMB_CY  = EMB_Y + EMB_H / 2                    # 2.60
    CHR_CX  = CHR_X + CHR_W / 2                    # 8.45
    CHR_BOT = CHR_Y + CHR_H                        # 4.77
    DOC_CX  = DOC_X + DOC_W / 2                    # 7.60
    SRC_CX  = SRC_X + SRC_W / 2                    # 9.80

    agent_y  = [1.52 + i * 1.03 for i in range(3)]          # top y of each agent
    agent_cy = [y + AGT_H / 2   for y in agent_y]           # centre y
    AGT_R    = AGT_X + AGT_W                                 # 6.30

    # ── Component boxes ────────────────────────────────────────────────────────

    # GitHub Copilot — the LLM that synthesises the final answer
    add_box(s, Inches(COP_X), Inches(COP_Y), Inches(COP_W), Inches(COP_H),
            fill=RGBColor(0x2d,0x1f,0x47), line=PURPLE)
    add_text(s, "GitHub Copilot  (Agent Mode  ·  LLM)",
             Inches(COP_X+0.05), Inches(COP_Y+0.08), Inches(COP_W-0.10), Inches(COP_H-0.16),
             size=12, bold=True, color=PURPLE, align=PP_ALIGN.CENTER)

    # Orchestrator
    add_box(s, Inches(ORC_X), Inches(ORC_Y), Inches(ORC_W), Inches(ORC_H),
            fill=RGBColor(0x0d,0x1f,0x12), line=GREEN)
    add_text(s, "Orchestrator\nrouter_mcp_server.py\nget_relevant_repos",
             Inches(ORC_X+0.05), Inches(ORC_Y+0.08), Inches(ORC_W-0.10), Inches(ORC_H-0.16),
             size=10, color=GREEN, align=PP_ALIGN.CENTER)

    # Repo Agents × 3
    for i, name in enumerate(["scan_manager", "illumination", "expose_sequence"]):
        ay = agent_y[i]
        add_box(s, Inches(AGT_X), Inches(ay), Inches(AGT_W), Inches(AGT_H),
                fill=RGBColor(0x0d,0x1f,0x35), line=BLUE)
        add_text(s, name,
                 Inches(AGT_X+0.05), Inches(ay+0.04), Inches(AGT_W-0.10), Inches(0.28),
                 size=11, bold=True, color=BLUE, align=PP_ALIGN.CENTER)
        add_text(s, "mcp_server.py  ·  query_repo",
                 Inches(AGT_X+0.05), Inches(ay+0.37), Inches(AGT_W-0.10), Inches(0.20),
                 size=8, color=BLUE, align=PP_ALIGN.CENTER)

    # RAG Engine
    add_box(s, Inches(RAG_X), Inches(RAG_Y), Inches(RAG_W), Inches(RAG_H),
            fill=RGBColor(0x2a,0x16,0x00), line=ORANGE)
    add_text(s, "RAG Engine\nrepo_rag.py\nhybrid: semantic + keyword",
             Inches(RAG_X+0.05), Inches(RAG_Y+0.10), Inches(RAG_W-0.10), Inches(RAG_H-0.20),
             size=11, color=ORANGE, align=PP_ALIGN.CENTER)

    # Embedding Model
    add_box(s, Inches(EMB_X), Inches(EMB_Y), Inches(EMB_W), Inches(EMB_H),
            fill=RGBColor(0x10,0x18,0x25), line=BLUE)
    add_text(s, "Embedding Model\nall-MiniLM-L6-v2\nlocal  ·  offline  ·  ~90 MB",
             Inches(EMB_X+0.05), Inches(EMB_Y+0.10), Inches(EMB_W-0.10), Inches(EMB_H-0.20),
             size=10, color=WHITE, align=PP_ALIGN.CENTER)

    # ChromaDB
    add_box(s, Inches(CHR_X), Inches(CHR_Y), Inches(CHR_W), Inches(CHR_H),
            fill=RGBColor(0x0d,0x1f,0x12), line=GREEN)
    add_text(s, "ChromaDB  ·  .chroma_db/\nvector store  ·  L2 distance",
             Inches(CHR_X+0.05), Inches(CHR_Y+0.08), Inches(CHR_W-0.10), Inches(CHR_H-0.16),
             size=11, color=GREEN, align=PP_ALIGN.CENTER)

    # Data sources
    add_box(s, Inches(DOC_X), Inches(DOC_Y), Inches(DOC_W), Inches(DOC_H),
            fill=RGBColor(0x16,0x1b,0x22), line=GREY)
    add_text(s, ".md Docs\nknowledge/",
             Inches(DOC_X+0.03), Inches(DOC_Y+0.06), Inches(DOC_W-0.06), Inches(DOC_H-0.12),
             size=10, color=GREY, align=PP_ALIGN.CENTER)

    add_box(s, Inches(SRC_X), Inches(SRC_Y), Inches(SRC_W), Inches(SRC_H),
            fill=RGBColor(0x16,0x1b,0x22), line=GREY)
    add_text(s, "Source Code\n./src  (12 languages)",
             Inches(SRC_X+0.03), Inches(SRC_Y+0.06), Inches(SRC_W-0.06), Inches(SRC_H-0.12),
             size=10, color=GREY, align=PP_ALIGN.CENTER)

    # ── LLM "not used here" annotation ────────────────────────────────────────
    add_box(s, Inches(LLM_X), Inches(LLM_Y), Inches(LLM_W), Inches(LLM_H),
            fill=RGBColor(0x18,0x07,0x07), line=RED)
    add_text(s, "⚡  Normally: LLM API calls",
             Inches(LLM_X+0.08), Inches(LLM_Y+0.08), Inches(LLM_W-0.16), Inches(0.25),
             size=9, bold=True, color=RED)
    add_text(s,
             "✗  Embedding API  (text-embedding-ada-002)\n"
             "   → replaced by all-MiniLM-L6-v2\n"
             "      runs locally, no API key, no cloud\n\n"
             "✗  Answer generation  (GPT-4 / Claude API)\n"
             "   → not needed: Copilot IS the LLM\n"
             "      receives chunks, synthesises answer",
             Inches(LLM_X+0.08), Inches(LLM_Y+0.38), Inches(LLM_W-0.16), Inches(LLM_H-0.50),
             size=8, color=RED)

    # ── Angular (elbow) connectors + sequence badges ───────────────────────────

    # ① Copilot → Orchestrator  :  get_relevant_repos(query)  →  ["repo1","repo2"]
    conn(s, COP_CX-0.8, COP_BOT, ORC_CX, ORC_Y, color=PURPLE, ctype=2)
    seq_badge(s, 1, (COP_CX-0.8+ORC_CX)/2, (COP_BOT+ORC_Y)/2, color=PURPLE)

    # ② Copilot → Repo Agents  :  query_repo(feature_request)  →  RELEVANT KNOWLEDGE
    #    Arrow drawn once to scan_manager; represents all 3 calls (labelled ×3)
    conn(s, COP_CX+0.6, COP_BOT, AGT_X+AGT_W/2, agent_y[0], color=BLUE, ctype=2)
    seq_badge(s, 2, (COP_CX+0.6+AGT_X+AGT_W/2)/2, (COP_BOT+agent_y[0])/2, color=BLUE)
    add_text(s, "×3", Inches((COP_CX+0.6+AGT_X+AGT_W/2)/2 + 0.12),
             Inches((COP_BOT+agent_y[0])/2 - 0.10), Inches(0.35), Inches(0.22),
             size=8, bold=True, color=BLUE)

    # ③ Orchestrator → each Repo Agent  :  query_repo(q)  →  content length score
    for cy in agent_cy:
        conn(s, ORC_R, ORC_CY, AGT_X, cy, color=GREEN, ctype=2)
    seq_badge(s, 3, (ORC_R+AGT_X)/2, (ORC_CY+agent_cy[1])/2, color=GREEN)

    # ④ Repo Agents → RAG Engine  :  query(feature_request)  →  chunks + L2 distances
    for cy in agent_cy:
        conn(s, AGT_R, cy, RAG_X, RAG_CY, color=BLUE, ctype=2)
    seq_badge(s, 4, (AGT_R+RAG_X)/2, (agent_cy[1]+RAG_CY)/2, color=BLUE)

    # ⑤ RAG Engine ↔ Embedding Model  :  encode(text)  ↔  384-dim float[]
    conn(s, RAG_R, RAG_CY, EMB_X, EMB_CY, color=WHITE, ctype=2)
    seq_badge(s, 5, (RAG_R+EMB_X)/2, (RAG_CY+EMB_CY)/2, color=WHITE)

    # ⑥ RAG Engine → ChromaDB  :  vector similarity query  →  top-k chunks + L2 dist
    conn(s, CHR_CX, RAG_BOT, CHR_CX, CHR_Y, color=ORANGE, ctype=2)
    seq_badge(s, 6, CHR_CX, (RAG_BOT+CHR_Y)/2, color=ORANGE)

    # ⑦ Docs + Source → ChromaDB  :  indexed at startup  →  embeddings stored
    conn(s, DOC_CX, DOC_Y, CHR_CX-0.45, CHR_BOT, color=GREY, ctype=2)
    conn(s, SRC_CX, SRC_Y, CHR_CX+0.45, CHR_BOT, color=GREY, ctype=2)
    seq_badge(s, 7, (DOC_CX+CHR_CX-0.45)/2, (DOC_Y+CHR_BOT)/2, color=GREY)

    # ── Sequence legend (2-column footer) ─────────────────────────────────────
    leg_left = [
        (1, PURPLE, "get_relevant_repos(query_text)  →  [repo1, repo2]  (MCP/stdio, JSON-RPC 2.0)"),
        (2, BLUE,   "query_repo(feature_request)  →  RELEVANT KNOWLEDGE:...  (MCP/stdio, ×3 calls)"),
        (3, GREEN,  "route  →  query_repo on all repos  →  min(len/800, 1.0) score, return top-2"),
        (4, BLUE,   "query(feature_request)  →  chunks + L2 distances  (internal, per-agent)"),
    ]
    leg_right = [
        (5, WHITE,  "encode(text)  →  384-dim float[]  (sentence-transformers, local CPU/GPU)"),
        (6, ORANGE, "vector similarity search  →  top-k chunks + L2 dist  (ChromaDB)"),
        (7, GREY,   "indexed at startup  →  text split → embed → stored in .chroma_db/"),
    ]
    row_h = Inches(0.185)
    for i, (n, col, text) in enumerate(leg_left):
        y = Inches(6.18) + i * row_h
        add_box(s, Inches(0.25), y, Inches(0.22), Inches(0.155), fill=col, line=col)
        add_text(s, f"{n}  {text}", Inches(0.52), y - Inches(0.01), Inches(6.0), Inches(0.18),
                 size=7.5, color=GREY)
    for i, (n, col, text) in enumerate(leg_right):
        y = Inches(6.18) + i * row_h
        add_box(s, Inches(6.8), y, Inches(0.22), Inches(0.155), fill=col, line=col)
        add_text(s, f"{n}  {text}", Inches(7.07), y - Inches(0.01), Inches(6.0), Inches(0.18),
                 size=7.5, color=GREY)


# ── Build presentation ────────────────────────────────────────────────────────
prs = Presentation()
prs.slide_width  = W
prs.slide_height = H

slide_title(prs)
slide_problem(prs)
slide_solution(prs)
slide_architecture(prs)
slide_tech_stack(prs)
slide_workflow(prs)
slide_rag(prs)
slide_mcp(prs)
slide_component_diagram(prs)
slide_setup(prs)
slide_demo(prs)

out = Path(__file__).parent / "slides.pptx"
prs.save(str(out))
print(f"Saved: {out}  ({out.stat().st_size // 1024} KB,  {len(prs.slides)} slides)")
