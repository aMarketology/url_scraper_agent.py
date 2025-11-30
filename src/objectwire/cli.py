#!/usr/bin/env python3
"""
ObjectWire CLI - AI-Powered RSS/URL Scraper Agent
==================================================
Scrape URLs & RSS feeds → Generate prediction events → Post to blockchain

Usage:
  objectwire                           # Launch interactive mode
  objectwire scrape <url>              # Scrape URL
  objectwire scrape <url> --post       # Scrape and post to blockchain
  objectwire scrape <url> --json       # Output as JSON
  objectwire scrape <url> --xml        # Output as XML
  objectwire rss <feed_url>            # Parse RSS feed
  objectwire post <url>                # Scrape and post in one step
  objectwire test                      # Test blockchain connectivity
  objectwire status                    # Check system status
"""

import os
import sys
import json
import time
import subprocess
import xml.etree.ElementTree as ET
from xml.dom import minidom
from urllib.parse import urlparse
from typing import Optional, Any, Dict
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.columns import Columns
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.keys import Keys
from prompt_toolkit.key_binding import KeyBindings
import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel
import feedparser
from dotenv import load_dotenv

load_dotenv()

console = Console()

# Configuration
BLOCKCHAIN_URL = os.getenv("BLOCKCHAIN_API_URL", "http://localhost:8080")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Try importing OpenAI
try:
    import openai
    if OPENAI_API_KEY:
        openai.api_key = OPENAI_API_KEY
    else:
        openai = None
except ImportError:
    openai = None


# ─────────────────────────────────────────────────────────────
# Dev Mode - File Watcher for Auto-Reload
# ─────────────────────────────────────────────────────────────

def run_dev_mode():
    """Run in development mode with auto-reload on file changes."""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        console.print("[red]Error:[/] watchdog not installed. Run: pip install watchdog")
        sys.exit(1)
    
    src_path = Path(__file__).parent
    
    class ReloadHandler(FileSystemEventHandler):
        def __init__(self):
            self.process = None
            self.start_app()
        
        def start_app(self):
            """Start the objectwire interactive mode as a subprocess."""
            if self.process:
                self.process.terminate()
                self.process.wait()
            
            console.print("\n[dim]─" * 50 + "[/]")
            console.print("[green]✓[/] [bold]Starting ObjectWire...[/]")
            console.print("[dim]─" * 50 + "[/]\n")
            
            # Run python -m objectwire (without --dev to avoid recursion)
            self.process = subprocess.Popen(
                [sys.executable, "-m", "objectwire"],
                cwd=src_path.parent.parent,
            )
        
        def on_modified(self, event):
            if event.src_path.endswith('.py'):
                rel_path = Path(event.src_path).relative_to(src_path.parent.parent)
                console.print(f"\n[yellow]⟳[/] File changed: [cyan]{rel_path}[/]")
                console.print("[yellow]  Reloading...[/]")
                self.start_app()
    
    console.print(Panel(
        "[bold orange1]🔧 DEV MODE[/]\n\n"
        "Watching for file changes in [cyan]src/objectwire/[/]\n"
        "Press [bold]Ctrl+C[/] to stop",
        border_style="orange1",
        padding=(1, 2)
    ))
    
    handler = ReloadHandler()
    observer = Observer()
    observer.schedule(handler, str(src_path), recursive=True)
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping dev mode...[/]")
        if handler.process:
            handler.process.terminate()
        observer.stop()
    observer.join()


# ─────────────────────────────────────────────────────────────
# XML Utilities
# ─────────────────────────────────────────────────────────────

def dict_to_xml(data: Dict[str, Any], root_name: str = "data") -> str:
    """Convert a dictionary to pretty-printed XML string."""
    
    def build_element(parent: ET.Element, key: str, value: Any):
        """Recursively build XML elements."""
        # Clean key name for valid XML tag
        tag = str(key).replace(" ", "_").replace("-", "_")
        
        if isinstance(value, dict):
            child = ET.SubElement(parent, tag)
            for k, v in value.items():
                build_element(child, k, v)
        elif isinstance(value, list):
            container = ET.SubElement(parent, tag)
            for item in value:
                if isinstance(item, dict):
                    item_elem = ET.SubElement(container, "item")
                    for k, v in item.items():
                        build_element(item_elem, k, v)
                else:
                    item_elem = ET.SubElement(container, "item")
                    item_elem.text = str(item)
        else:
            child = ET.SubElement(parent, tag)
            child.text = str(value) if value is not None else ""
    
    root = ET.Element(root_name)
    for key, value in data.items():
        build_element(root, key, value)
    
    # Pretty print
    xml_str = ET.tostring(root, encoding='unicode')
    parsed = minidom.parseString(xml_str)
    return parsed.toprettyxml(indent="  ")


def print_xml(data: Dict[str, Any], root_name: str = "data"):
    """Print data as formatted XML to console."""
    xml_output = dict_to_xml(data, root_name)
    console.print(xml_output)


# ─────────────────────────────────────────────────────────────
# Clipboard Functions
# ─────────────────────────────────────────────────────────────

def copy_to_clipboard(text: str) -> bool:
    """Copy text to Windows clipboard."""
    try:
        process = subprocess.Popen(
            ['clip'],
            stdin=subprocess.PIPE,
            shell=True
        )
        process.communicate(input=text.encode('utf-8'))
        return process.returncode == 0
    except Exception:
        return False


def paste_from_clipboard() -> Optional[str]:
    """Paste text from Windows clipboard."""
    try:
        result = subprocess.run(
            ['powershell', '-command', 'Get-Clipboard'],
            capture_output=True,
            text=True,
            shell=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────────────────────

class PredictionEvent(BaseModel):
    title: str
    description: str
    category: str
    options: list[str]
    confidence: float
    source_url: str


# ─────────────────────────────────────────────────────────────
# Core Functions
# ─────────────────────────────────────────────────────────────

def scrape_url(url: str) -> Optional[dict]:
    """Scrape content from URL with retry logic."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            break
        except requests.exceptions.RequestException as e:
            if attempt == 2:
                return None
            time.sleep(1)
    
    soup = BeautifulSoup(r.content, "html.parser")
    
    # Remove noise
    for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
        tag.decompose()
    
    title = soup.title.get_text(strip=True) if soup.title else "Untitled"
    
    # Get main content
    main = soup.find('article') or soup.find('main') or soup.body
    content = main.get_text("\n", strip=True)[:5000] if main else ""
    
    if len(content) < 100:
        return None
    
    return {"title": title, "content": content, "url": url, "domain": urlparse(url).netloc}


def analyze(scraped: dict) -> PredictionEvent:
    """Generate prediction event from scraped content."""
    
    # Use OpenAI if available
    if openai and OPENAI_API_KEY:
        try:
            resp = openai.ChatCompletion.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Extract a prediction market question from this article. Return JSON with: title (question), description, category, options (list of 2-3 choices), confidence (0-1), resolution_date (ISO format)."},
                    {"role": "user", "content": f"Title: {scraped['title']}\n\nContent: {scraped['content'][:2000]}"}
                ],
                temperature=0.3,
                max_tokens=400,
            )
            text = resp.choices[0].message.content
            # Parse JSON from response
            start = text.find('{')
            end = text.rfind('}') + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                return PredictionEvent(
                    title=data.get('title', f"Prediction: {scraped['title'][:60]}?"),
                    description=data.get('description', scraped['content'][:200]),
                    category=data.get('category', 'general'),
                    options=data.get('options', ['Yes', 'No']),
                    confidence=float(data.get('confidence', 0.7)),
                    source_url=scraped['url']
                )
        except Exception as e:
            pass  # Fall through to fallback
    
    # Fallback: Simple extraction
    return PredictionEvent(
        title=f"Will '{scraped['title'][:50]}' predictions come true?",
        description=f"Based on: {scraped['title']}",
        category="general",
        options=["Yes", "No", "Partially"],
        confidence=0.5,
        source_url=scraped['url']
    )


def post_to_blockchain(event: PredictionEvent) -> Optional[dict]:
    """Post event to blockchain API."""
    payload = {
        "source": {
            "domain": "objectwire-agent",
            "url": event.source_url
        },
        "event": {
            "title": event.title,
            "description": event.description,
            "category": event.category,
            "options": event.options,
            "confidence": event.confidence,
            "source_url": event.source_url
        }
    }
    
    try:
        # Post event directly (no health check needed)
        resp = requests.post(
            f"{BLOCKCHAIN_URL}/ai/events",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()
    
    except requests.exceptions.RequestException as e:
        console.print(f"[red]❌ Failed to post to blockchain:[/] {e}")
        return None


def parse_rss(url: str) -> Optional[dict]:
    """Parse RSS/Atom feed from URL."""
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            return None
        
        return {
            "title": feed.feed.get("title", "Unknown Feed"),
            "link": feed.feed.get("link", url),
            "items": [
                {
                    "title": entry.get("title", "Untitled"),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", entry.get("description", ""))[:200],
                    "published": entry.get("published", "")
                }
                for entry in feed.entries
            ]
        }
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# Interactive Mode
# ─────────────────────────────────────────────────────────────

def show_banner():
    """Display welcome banner with Claude-style layout."""
    import os
    from datetime import datetime
    
    # Get current directory
    cwd = os.getcwd()
    if len(cwd) > 35:
        cwd = "..." + cwd[-32:]
    
    # Build the welcome panel content with ASCII shamrock
    left_side = f"""
[bold orange3]ObjectWire[/] v0.1.0
─────────────────────────────

[bold orange1]      Welcome![/]

[orange3]           ,@@@,[/]
[orange3]          @@@@@@@[/]
[orange3]   ,@@@, '@@@@@@@' ,@@@,[/]
[orange3]  @@@@@@@ '@@@@@@' @@@@@@@[/]
[orange3]  '@@@@@@'  @@@@  '@@@@@@'[/]
[orange3]    '@@@' ,@@@@@@, '@@@'[/]
[orange3]         @@@@@@@@@@[/]
[orange3]          '@@@@@@'[/]
[orange3]            @@@@[/]
[orange3]            @@[/]
[orange3]            @@[/]
[orange3]            @@[/]

[dim]Prediction Markets[/]
[dim]{cwd}[/]
"""

    right_side = f"""[bold]Tips for getting started[/]
[dim]─────────────────────────────────────────────────────[/]
[orange3]scrape <url>[/]     Scrape a URL and generate prediction
[orange3]rss <feed>[/]       Parse an RSS feed for articles
[orange3]post[/]             Post last event to blockchain
[orange3]test[/]             Test blockchain connectivity
[orange3]status[/]           Check system status
[orange3]help[/]             Show all available commands
[orange3]exit[/]             Quit ObjectWire

[bold]Recent Commands[/]
[dim]─────────────────────────────────────────────────────[/]
[dim]Type a command to get started...[/]
"""

    # Create a two-column layout
    console.print()
    console.print(Panel(
        Columns([left_side, right_side], expand=True, equal=False),
        border_style="orange3",
        title="[bold orange3]ObjectWire CLI v0.1.0[/]",
        subtitle="[dim]AI-Powered Prediction Market Agent[/]"
    ))
    console.print()


def show_help():
    """Display help information."""
    help_md = """
## Commands

| Command | Description |
|---------|-------------|
| `<rss_feed_url>` | Just paste an RSS feed to see 3 latest posts |
| `<url>` | Just paste any URL to scrape and analyze it |
| `1`, `2`, `3`... | Select article from RSS feed |
| `1 json` | Get article 1 as JSON |
| `2 xml` | Get article 2 as XML |
| `3 json xml` | Get article 3 as both JSON and XML |
| `scrape <url>` | Scrape URL and generate prediction event |
| `rss <feed_url>` | Parse and display RSS feed items (15 max) |
| `post` | Post last event to blockchain |
| `copy` or `c` | Copy last event to clipboard (JSON) |
| `copy xml` | Copy last event to clipboard (XML) |
| `paste` or `v` | Paste URL from clipboard and process it |
| `test` | Test blockchain connectivity |
| `status` | Check system status |
| `help` | Show this help |
| `exit` or `q` | Quit ObjectWire |

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+V` | Paste from clipboard into prompt |
| `Ctrl+C` | Cancel current operation |
| `↑` / `↓` | Browse command history |

## Examples

```
v                                    # Paste & process URL from clipboard
https://feeds.bbci.co.uk/news/rss.xml
copy
post
```
"""
    console.print(Markdown(help_md))


def show_status():
    """Display system status."""
    table = Table(title="System Status")
    table.add_column("Component", style="cyan")
    table.add_column("Status")
    
    # OpenAI
    if OPENAI_API_KEY:
        table.add_row("OpenAI API", "[green]✓ Configured[/]")
    else:
        table.add_row("OpenAI API", "[yellow]✗ Not set (using fallback)[/]")
    
    # Blockchain
    table.add_row("Blockchain URL", BLOCKCHAIN_URL)
    try:
        r = requests.get(f"{BLOCKCHAIN_URL}/health", timeout=3)
        if r.status_code == 200:
            table.add_row("Blockchain Status", "[green]✓ Online[/]")
        else:
            table.add_row("Blockchain Status", f"[red]✗ Error ({r.status_code})[/]")
    except:
        table.add_row("Blockchain Status", "[red]✗ Offline[/]")
    
    console.print(table)


def interactive_mode():
    """Launch interactive REPL mode."""
    show_banner()
    
    # Set up key bindings for Ctrl+V paste
    bindings = KeyBindings()
    
    @bindings.add(Keys.ControlV)
    def _(event):
        """Handle Ctrl+V to paste from clipboard."""
        clipboard_text = paste_from_clipboard()
        if clipboard_text:
            event.current_buffer.insert_text(clipboard_text)
    
    session = PromptSession(
        history=FileHistory(os.path.expanduser("~/.objectwire_history")),
        key_bindings=bindings,
        mouse_support=False  # Disable to allow normal terminal mouse behavior (select, scroll)
    )
    last_event: Optional[PredictionEvent] = None
    last_rss_items: list = []  # Store last RSS feed items for number selection
    
    while True:
        try:
            cmd = session.prompt("\n[objectwire]> ").strip()
            if not cmd:
                continue
            
            parts = cmd.split()
            action = parts[0].lower()
            args = " ".join(parts[1:]) if len(parts) > 1 else ""
            
            # Check if first part is a number (article selection from RSS)
            if action.isdigit() and last_rss_items:
                article_num = int(action)
                if 1 <= article_num <= len(last_rss_items):
                    item = last_rss_items[article_num - 1]
                    article_url = item["link"]
                    
                    # Parse output format options
                    show_json = "json" in args.lower()
                    show_xml = "xml" in args.lower()
                    
                    # Default to showing the prediction panel if no format specified
                    if not show_json and not show_xml:
                        show_json = False
                        show_xml = False
                    
                    console.print(f"\n[dim]Scraping article {article_num}: {item['title'][:50]}...[/]")
                    
                    with console.status("[green]Scraping article..."):
                        data = scrape_url(article_url)
                    
                    if not data:
                        console.print("[red]❌ Failed to scrape article[/]")
                        continue
                    
                    last_event = analyze(data)
                    output = last_event.model_dump()
                    
                    if show_json or show_xml:
                        if show_json:
                            console.print("\n[bold orange3]═══ JSON ═══[/]")
                            console.print_json(data=output)
                        if show_xml:
                            console.print("\n[bold orange3]═══ XML ═══[/]")
                            print_xml(output, root_name="prediction_event")
                    else:
                        # Show prediction panel
                        console.print(Panel(
                            f"[bold cyan]{last_event.title}[/]\n\n"
                            f"{last_event.description}\n\n"
                            f"[dim]Options:[/] {', '.join(last_event.options)}\n"
                            f"[dim]Confidence:[/] {last_event.confidence:.0%}\n"
                            f"[dim]Resolves:[/] {last_event.resolution_date}",
                            title="🎯 Prediction Event",
                            border_style="green"
                        ))
                    
                    console.print(f"\n[dim]Tip: Type 'post' to send to blockchain, 'copy' to copy, or '{article_num} json xml' for both formats[/]")
                    continue
                else:
                    console.print(f"[red]Invalid article number. Choose 1-{len(last_rss_items)}[/]")
                    continue
            
            # Exit
            if action in ("exit", "quit", "q"):
                console.print("[yellow]👋 Goodbye![/]")
                break
            
            # Help
            elif action == "help":
                show_help()
            
            # Status
            elif action == "status":
                show_status()
            
            # Scrape
            elif action == "scrape":
                if not args:
                    console.print("[red]Usage: scrape <url>[/]")
                    continue
                
                with console.status("[green]Scraping URL..."):
                    data = scrape_url(args)
                
                if not data:
                    console.print("[red]❌ Failed to scrape URL[/]")
                    continue
                
                last_event = analyze(data)
                console.print(Panel(
                    f"[bold cyan]{last_event.title}[/]\n\n"
                    f"{last_event.description}\n\n"
                    f"[dim]Options:[/] {', '.join(last_event.options)}\n"
                    f"[dim]Confidence:[/] {last_event.confidence:.0%}",
                    title="🎯 Prediction Event",
                    border_style="green"
                ))
                
                # Auto-post to blockchain
                with console.status("[green]Posting to blockchain..."):
                    result = post_to_blockchain(last_event)
                
                if result:
                    console.print(f"[green]✓ Posted to blockchain![/]")
                    if isinstance(result, dict):
                        event_id = result.get('id') or result.get('market_id') or result.get('event_id')
                        if event_id:
                            console.print(f"[dim]Event ID: {event_id}[/]")
                else:
                    console.print("[yellow]⚠ Could not post to blockchain (service may be offline)[/]")
            
            # RSS
            elif action == "rss":
                if not args:
                    console.print("[red]Usage: rss <feed_url>[/]")
                    continue
                
                with console.status("[green]Fetching RSS feed..."):
                    feed = parse_rss(args)
                
                if not feed:
                    console.print("[red]❌ Failed to parse RSS feed[/]")
                    continue
                
                # Store items for number selection
                last_rss_items = feed["items"][:15]
                
                table = Table(title=feed["title"])
                table.add_column("#", style="dim", width=3)
                table.add_column("Title", style="cyan")
                table.add_column("Published", style="dim", width=20)
                
                for i, item in enumerate(feed["items"][:15], 1):
                    table.add_row(str(i), item["title"][:60], item["published"][:20] if item["published"] else "")
                
                console.print(table)
                console.print(f"\n[dim]Type a number (1-{len(last_rss_items)}) to scrape that article. Add 'json' or 'xml' for formatted output.[/]")
                console.print(f"[dim]Example: '1 json' or '2 xml' or '3 json xml'[/]")
            
            # Post
            elif action == "post":
                if not last_event:
                    console.print("[red]No event to post. Scrape a URL first.[/]")
                    continue
                
                # Show preview data
                payload = {
                    "source": {
                        "domain": "objectwire-agent",
                        "url": last_event.source_url
                    },
                    "event": {
                        "title": last_event.title,
                        "description": last_event.description,
                        "category": last_event.category,
                        "options": last_event.options,
                        "confidence": last_event.confidence,
                        "source_url": last_event.source_url
                    }
                }
                
                console.print("\n[bold cyan]═══ JSON Preview ═══[/]")
                console.print_json(data=payload)
                
                console.print("\n[bold cyan]═══ XML Preview ═══[/]")
                print_xml(payload, root_name="blockchain_payload")
                
                # Confirm before posting
                confirm = session.prompt("\n[yellow]Post to blockchain? (y/n):[/] ").strip().lower()
                if confirm not in ("y", "yes"):
                    console.print("[dim]Cancelled.[/]")
                    continue
                
                with console.status("[green]Posting to blockchain..."):
                    result = post_to_blockchain(last_event)
                
                if result:
                    event_id = result.get('id') or result.get('market_id') or result.get('event_id') if isinstance(result, dict) else result
                    console.print(f"[green]✓ Posted! Event ID:[/] [bold]{event_id}[/]")
                else:
                    console.print("[red]❌ Failed to post to blockchain[/]")
            
            # Copy - copy last event to clipboard
            elif action in ("copy", "c"):
                if not last_event:
                    console.print("[red]No event to copy. Scrape a URL first.[/]")
                    continue
                
                # Determine format (default JSON, or xml if specified)
                fmt = args.lower() if args else "json"
                output = last_event.model_dump()
                
                if fmt == "xml":
                    text = dict_to_xml(output, root_name="prediction_event")
                else:
                    text = json.dumps(output, indent=2)
                
                if copy_to_clipboard(text):
                    console.print(f"[green]✓ Copied to clipboard as {fmt.upper()}![/]")
                else:
                    console.print("[red]❌ Failed to copy to clipboard[/]")
            
            # Paste - paste URL from clipboard and process it
            elif action in ("paste", "v", "pv"):
                clipboard_text = paste_from_clipboard()
                if not clipboard_text:
                    console.print("[red]❌ Clipboard is empty or couldn't read[/]")
                    continue
                
                # Check if it's a URL
                if clipboard_text.startswith(("http://", "https://")):
                    console.print(f"[dim]Pasted: {clipboard_text[:60]}...[/]" if len(clipboard_text) > 60 else f"[dim]Pasted: {clipboard_text}[/]")
                    
                    # Try RSS first
                    with console.status("[green]Detecting content type..."):
                        feed = parse_rss(clipboard_text)
                    
                    if feed and feed.get("items"):
                        # It's an RSS feed
                        console.print(f"\n[bold orange3]📡 RSS Feed Detected:[/] {feed['title']}")
                        console.print("[dim]Showing 3 most recent posts:[/]\n")
                        
                        table = Table(title=feed["title"], border_style="orange3")
                        table.add_column("#", style="dim", width=3)
                        table.add_column("Title", style="orange3")
                        table.add_column("Link", style="dim")
                        
                        for i, item in enumerate(feed["items"][:3], 1):
                            link = item["link"][:45] + "..." if len(item["link"]) > 45 else item["link"]
                            table.add_row(str(i), item["title"][:55], link)
                        
                        console.print(table)
                    else:
                        # Scrape as webpage
                        with console.status("[green]Scraping URL..."):
                            data = scrape_url(clipboard_text)
                        
                        if data:
                            last_event = analyze(data)
                            console.print(Panel(
                                f"[bold cyan]{last_event.title}[/]\n\n"
                                f"{last_event.description}\n\n"
                                f"[dim]Options:[/] {', '.join(last_event.options)}\n"
                                f"[dim]Confidence:[/] {last_event.confidence:.0%}",
                                title="🎯 Prediction Event",
                                border_style="green"
                            ))
                            
                            # Auto-post to blockchain
                            with console.status("[green]Posting to blockchain..."):
                                result = post_to_blockchain(last_event)
                            
                            if result:
                                console.print(f"[green]✓ Posted to blockchain![/]")
                                if isinstance(result, dict):
                                    event_id = result.get('id') or result.get('market_id') or result.get('event_id')
                                    if event_id:
                                        console.print(f"[dim]Event ID: {event_id}[/]")
                            else:
                                console.print("[yellow]⚠ Could not post to blockchain (service may be offline)[/]")
                        else:
                            console.print("[red]❌ Failed to parse URL[/]")
                else:
                    console.print(f"[yellow]Clipboard content is not a URL:[/] {clipboard_text[:50]}...")
            
            # Unknown
            else:
                # Auto-detect: Check if it's a URL (RSS feed or webpage)
                if cmd.startswith(("http://", "https://")):
                    url = cmd
                    
                    # Try RSS first
                    with console.status("[green]Detecting content type..."):
                        feed = parse_rss(url)
                    
                    if feed and feed.get("items"):
                        # It's an RSS feed - show top 3 items and store for selection
                        last_rss_items = feed["items"][:3]
                        
                        console.print(f"\n[bold orange3]📡 RSS Feed Detected:[/] {feed['title']}")
                        console.print("[dim]Showing 3 most recent posts:[/]\n")
                        
                        table = Table(title=feed["title"], border_style="orange3")
                        table.add_column("#", style="dim", width=3)
                        table.add_column("Title", style="orange3")
                        table.add_column("Link", style="dim")
                        
                        for i, item in enumerate(last_rss_items, 1):
                            link = item["link"][:45] + "..." if len(item["link"]) > 45 else item["link"]
                            table.add_row(str(i), item["title"][:55], link)
                        
                        console.print(table)
                        console.print(f"\n[dim]Type 1, 2, or 3 to scrape. Add 'json' or 'xml' for formatted output.[/]")
                        console.print(f"[dim]Example: '1 json' or '2 xml' or '3 json xml'[/]")
                    else:
                        # Not RSS, try scraping as webpage
                        with console.status("[green]Scraping URL..."):
                            data = scrape_url(url)
                        
                        if data:
                            last_event = analyze(data)
                            console.print(Panel(
                                f"[bold cyan]{last_event.title}[/]\n\n"
                                f"{last_event.description}\n\n"
                                f"[dim]Options:[/] {', '.join(last_event.options)}\n"
                                f"[dim]Confidence:[/] {last_event.confidence:.0%}",
                                title="🎯 Prediction Event",
                                border_style="green"
                            ))
                            
                            # Auto-post to blockchain
                            with console.status("[green]Posting to blockchain..."):
                                result = post_to_blockchain(last_event)
                            
                            if result:
                                console.print(f"[green]✓ Posted to blockchain![/]")
                                if isinstance(result, dict):
                                    event_id = result.get('id') or result.get('market_id') or result.get('event_id')
                                    if event_id:
                                        console.print(f"[dim]Event ID: {event_id}[/]")
                            else:
                                console.print("[yellow]⚠ Could not post to blockchain (service may be offline)[/]")
                        else:
                            console.print("[red]❌ Failed to parse URL (not RSS or scrapeable webpage)[/]")
                else:
                    console.print(f"[red]Unknown command:[/] {action}")
                    console.print("[dim]Type 'help' for available commands, or paste a URL/RSS feed[/]")
        
        except KeyboardInterrupt:
            continue
        except EOFError:
            console.print("\n[yellow]👋 Goodbye![/]")
            break


# ─────────────────────────────────────────────────────────────
# CLI Commands (Click)
# ─────────────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.version_option(version="0.1.0", prog_name="objectwire")
@click.option("--dev", is_flag=True, help="Run in dev mode with auto-reload on file changes")
@click.pass_context
def main(ctx, dev: bool):
    """🔌 ObjectWire - AI-Powered RSS/URL Scraper Agent for Prediction Markets"""
    if dev:
        run_dev_mode()
    elif ctx.invoked_subcommand is None:
        interactive_mode()


@main.command()
@click.argument("url")
@click.option("--post", is_flag=True, help="Post event to blockchain")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--xml", "as_xml", is_flag=True, help="Output as XML")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt when posting")
def scrape(url: str, post: bool, as_json: bool, as_xml: bool, yes: bool):
    """Scrape a URL and generate prediction event."""
    with console.status("[green]Scraping URL..."):
        data = scrape_url(url)
    
    if not data:
        console.print("[red]❌ Failed to scrape URL[/]")
        sys.exit(1)
    
    event = analyze(data)
    output = event.model_dump()
    
    # Build payload in correct format
    payload = {
        "source": {
            "domain": "objectwire-agent",
            "url": event.source_url
        },
        "event": {
            "title": event.title,
            "description": event.description,
            "category": event.category,
            "options": event.options,
            "confidence": event.confidence,
            "source_url": event.source_url
        }
    }
    
    # If posting explicitly with --post flag, show preview first
    if post:
        console.print("\n[bold cyan]═══ JSON Preview ═══[/]")
        console.print_json(data=payload)
        
        console.print("\n[bold cyan]═══ XML Preview ═══[/]")
        print_xml(payload, root_name="blockchain_payload")
        
        # Confirm before posting (unless --yes flag)
        if not yes:
            if not click.confirm("\nPost to blockchain?", default=False):
                console.print("[dim]Cancelled.[/]")
                sys.exit(0)
        
        with console.status("[green]Posting to blockchain..."):
            result = post_to_blockchain(event)
        if result:
            event_id = result.get('id') or result.get('market_id') or result.get('event_id') if isinstance(result, dict) else result
            console.print(f"\n[green]✓ Posted! Event ID:[/] [bold]{event_id}[/]")
        else:
            console.print("\n[red]❌ Failed to post to blockchain[/]")
    
    # Output format selection
    elif as_xml:
        print_xml(output, root_name="prediction_event")
    elif as_json:
        console.print_json(data=output)
    else:
        # Show event and auto-post
        console.print(Panel(
            f"[bold cyan]{event.title}[/]\n\n"
            f"{event.description}\n\n"
            f"[dim]Options:[/] {', '.join(event.options)}\n"
            f"[dim]Confidence:[/] {event.confidence:.0%}",
            title="🎯 Prediction Event",
            border_style="green"
        ))
        
        # Auto-post to blockchain
        with console.status("[green]Posting to blockchain..."):
            result = post_to_blockchain(event)
        
        if result:
            console.print(f"[green]✓ Posted to blockchain![/]")
            if isinstance(result, dict):
                event_id = result.get('id') or result.get('market_id') or result.get('event_id')
                if event_id:
                    console.print(f"[dim]Event ID: {event_id}[/]")
        else:
            console.print("[yellow]⚠ Could not post to blockchain (service may be offline)[/]")


@main.command()
@click.argument("feed_url")
@click.option("--limit", default=15, help="Max items to display")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--xml", "as_xml", is_flag=True, help="Output as XML")
def rss(feed_url: str, limit: int, as_json: bool, as_xml: bool):
    """Parse and display RSS feed items."""
    with console.status("[green]Fetching RSS feed..."):
        feed = parse_rss(feed_url)
    
    if not feed:
        console.print("[red]❌ Failed to parse RSS feed[/]")
        sys.exit(1)
    
    # Limit items
    limited_feed = {**feed, "items": feed["items"][:limit]}
    
    if as_xml:
        print_xml(limited_feed, root_name="rss_feed")
    elif as_json:
        console.print_json(data=limited_feed)
    else:
        table = Table(title=feed["title"])
        table.add_column("#", style="dim", width=3)
        table.add_column("Title", style="cyan")
        table.add_column("Link", style="dim")
        
        for i, item in enumerate(feed["items"][:limit], 1):
            table.add_row(str(i), item["title"][:55], item["link"][:40] + "..." if len(item["link"]) > 40 else item["link"])
        
        console.print(table)
        console.print(f"\n[dim]Showing {min(limit, len(feed['items']))} of {len(feed['items'])} items[/]")


@main.command()
def status():
    """Check system and API status."""
    show_status()


@main.command(name="post")
@click.argument("url")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--xml", "as_xml", is_flag=True, help="Output as XML")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def post_command(url: str, as_json: bool, as_xml: bool, yes: bool):
    """Scrape a URL and immediately post to blockchain."""
    with console.status("[green]Scraping URL..."):
        data = scrape_url(url)
    
    if not data:
        console.print("[red]❌ Failed to scrape URL[/]")
        sys.exit(1)
    
    event = analyze(data)
    payload = {
        "source": {
            "domain": "objectwire-agent",
            "url": event.source_url
        },
        "event": {
            "title": event.title,
            "description": event.description,
            "category": event.category,
            "options": event.options,
            "confidence": event.confidence,
            "source_url": event.source_url
        }
    }
    
    # Show preview
    console.print("\n[bold cyan]═══ JSON Preview ═══[/]")
    console.print_json(data=payload)
    
    console.print("\n[bold cyan]═══ XML Preview ═══[/]")
    print_xml(payload, root_name="blockchain_payload")
    
    # Confirm before posting (unless --yes flag)
    if not yes:
        if not click.confirm("\nPost to blockchain?", default=False):
            console.print("[dim]Cancelled.[/]")
            sys.exit(0)
    
    with console.status("[green]Posting to blockchain..."):
        result = post_to_blockchain(event)
    
    output = event.model_dump()
    if result:
        event_id = result.get('id') or result.get('market_id') or result.get('event_id') if isinstance(result, dict) else result
        output['event_id'] = event_id
        output['status'] = 'posted'
    else:
        output['status'] = 'failed'
    
    if as_xml:
        print_xml(output, root_name="blockchain_result")
    elif as_json:
        console.print_json(data=output)
    else:
        if result:
            event_id = result.get('id') or result.get('market_id') or result.get('event_id') if isinstance(result, dict) else result
            console.print(Panel(
                f"[bold cyan]{event.title}[/]\n\n"
                f"[green]✓ Successfully posted to blockchain![/]\n"
                f"[dim]Event ID:[/] [bold]{event_id}[/]\n\n"
                f"[dim]Options:[/] {', '.join(event.options)}",
                title="🎯 Posted Event",
                border_style="green"
            ))
        else:
            console.print(Panel(
                f"[bold cyan]{event.title}[/]\n\n"
                f"[red]❌ Failed to post to blockchain[/]\n\n"
                f"[dim]Check 'objectwire test' for connectivity issues[/]",
                title="⚠️ Post Failed",
                border_style="red"
            ))
            sys.exit(1)


@main.command()
def test():
    """Test blockchain connectivity and API health."""
    console.print(Panel.fit(
        "[bold cyan]ObjectWire Connectivity Test[/]",
        border_style="cyan"
    ))
    
    table = Table(show_header=True)
    table.add_column("Test", style="cyan")
    table.add_column("Result")
    table.add_column("Details", style="dim")
    
    # Test 1: Blockchain health endpoint
    try:
        with console.status("[green]Testing blockchain health..."):
            r = requests.get(f"{BLOCKCHAIN_URL}/health", timeout=5)
        if r.status_code == 200:
            table.add_row("Blockchain Health", "[green]✓ PASS[/]", f"Status: {r.status_code}")
        else:
            table.add_row("Blockchain Health", "[red]✗ FAIL[/]", f"Status: {r.status_code}")
    except requests.exceptions.ConnectionError:
        table.add_row("Blockchain Health", "[red]✗ FAIL[/]", "Connection refused")
    except requests.exceptions.Timeout:
        table.add_row("Blockchain Health", "[red]✗ FAIL[/]", "Timeout")
    except Exception as e:
        table.add_row("Blockchain Health", "[red]✗ FAIL[/]", str(e)[:40])
    
    # Test 2: AI events endpoint
    try:
        with console.status("[green]Testing AI events endpoint..."):
            r = requests.options(f"{BLOCKCHAIN_URL}/ai/events", timeout=5)
        if r.status_code in (200, 204, 405):  # 405 = method not allowed is OK (means endpoint exists)
            table.add_row("AI Events Endpoint", "[green]✓ PASS[/]", f"Endpoint reachable")
        else:
            table.add_row("AI Events Endpoint", "[yellow]? UNKNOWN[/]", f"Status: {r.status_code}")
    except requests.exceptions.ConnectionError:
        table.add_row("AI Events Endpoint", "[red]✗ FAIL[/]", "Connection refused")
    except Exception as e:
        table.add_row("AI Events Endpoint", "[red]✗ FAIL[/]", str(e)[:40])
    
    # Test 3: OpenAI API
    if OPENAI_API_KEY:
        table.add_row("OpenAI API Key", "[green]✓ CONFIGURED[/]", "Key present in environment")
    else:
        table.add_row("OpenAI API Key", "[yellow]⚠ NOT SET[/]", "Using fallback analysis")
    
    # Test 4: External URL scraping
    try:
        with console.status("[green]Testing URL scraping..."):
            r = requests.get("https://httpbin.org/get", timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
            })
        if r.status_code == 200:
            table.add_row("External HTTP", "[green]✓ PASS[/]", "Internet connectivity OK")
        else:
            table.add_row("External HTTP", "[red]✗ FAIL[/]", f"Status: {r.status_code}")
    except Exception as e:
        table.add_row("External HTTP", "[red]✗ FAIL[/]", str(e)[:40])
    
    console.print(table)
    console.print(f"\n[dim]Blockchain URL: {BLOCKCHAIN_URL}[/]")


if __name__ == "__main__":
    main()
