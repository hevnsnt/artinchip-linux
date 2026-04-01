"""RSS news ticker crawl for tinyscreen bar display."""

import time
import threading
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# --- Color palette (dark theme) ---
BG = (10, 12, 18)
PANEL_BG = (18, 22, 32)
BORDER = (35, 42, 58)
ACCENT = (0, 170, 255)
TEXT = (200, 210, 225)
TEXT_DIM = (100, 110, 130)
TEXT_BRIGHT = (240, 245, 255)
GREEN = (0, 220, 100)
RED = (255, 60, 60)
YELLOW = (255, 200, 0)
ORANGE = (255, 140, 0)
CYAN = (0, 220, 220)
PURPLE = (160, 100, 255)

# --- Font cache ---
_fonts = {}
def font(size):
    if size not in _fonts:
        for path in ['/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
                     '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
            try:
                _fonts[size] = ImageFont.truetype(path, size)
                return _fonts[size]
            except Exception:
                continue
        _fonts[size] = ImageFont.load_default()
    return _fonts[size]

# --- Default RSS feeds ---
DEFAULT_FEEDS = [
    ('Reuters', 'http://feeds.reuters.com/reuters/topNews'),
    ('BBC', 'http://feeds.bbci.co.uk/news/rss.xml'),
    ('Hacker News', 'https://hnrss.org/frontpage'),
]

DIVIDER = '   ///   '
FEED_REFRESH = 300  # seconds
SCROLL_SPEED = 3    # pixels per frame

# --- Data cache ---
_cache = {
    'headlines': [],           # List of (source, title) tuples
    'ticker_text': '',         # Prebuilt ticker string
    'last_fetch': 0,
    'fetching': False,
    'fetch_error': None,
    'scroll_x': 0,
    'ticker_width': 0,         # Pixel width of full ticker
    'frame_count': 0,
}


def _fetch_feeds_thread():
    """Fetch RSS feeds in a background thread."""
    try:
        import feedparser
    except ImportError:
        _cache['fetch_error'] = 'feedparser not installed'
        _cache['fetching'] = False
        return

    headlines = []
    for source_name, url in DEFAULT_FEEDS:
        try:
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                continue
            for entry in feed.entries[:15]:
                title = entry.get('title', '').strip()
                if title:
                    headlines.append((source_name, title))
        except Exception:
            continue

    if headlines:
        _cache['headlines'] = headlines
        _cache['fetch_error'] = None
    elif not _cache['headlines']:
        _cache['fetch_error'] = 'No feeds available'

    # Build ticker text
    _rebuild_ticker()
    _cache['fetching'] = False


def _rebuild_ticker():
    """Build the full ticker string and compute its pixel width."""
    headlines = _cache['headlines']
    if not headlines:
        _cache['ticker_text'] = ''
        _cache['ticker_width'] = 0
        return

    # Build composite string: "SOURCE: Headline /// SOURCE: Headline /// ..."
    parts = []
    for source, title in headlines:
        parts.append(f"{source}: {title}")
    _cache['ticker_text'] = DIVIDER.join(parts) + DIVIDER
    # Compute width using the ticker font
    ticker_font = font(30)
    _cache['ticker_width'] = int(ticker_font.getlength(_cache['ticker_text']))


def _maybe_fetch():
    """Trigger a feed fetch if enough time has passed."""
    now = time.time()
    if now - _cache['last_fetch'] >= FEED_REFRESH and not _cache['fetching']:
        _cache['fetching'] = True
        _cache['last_fetch'] = now
        t = threading.Thread(target=_fetch_feeds_thread, daemon=True)
        t.start()


def _draw_ticker(draw, img, y, h, w):
    """Draw the scrolling headline ticker in the bottom area."""
    ticker_font = font(30)
    divider_font = font(30)
    headlines = _cache['headlines']

    if not headlines:
        # Show status message
        if _cache['fetching']:
            msg = "Fetching news..."
        elif _cache['fetch_error']:
            msg = _cache['fetch_error']
        else:
            msg = "No feeds available"
        msg_font = font(24)
        tw = msg_font.getlength(msg)
        draw.text(((w - tw) / 2, y + (h - 24) / 2), msg, fill=TEXT_DIM, font=msg_font)
        return

    ticker_text = _cache['ticker_text']
    ticker_width = _cache['ticker_width']

    if ticker_width == 0:
        return

    # Current scroll position
    scroll_x = _cache['scroll_x']

    # We need to draw the text potentially multiple times to fill the screen
    # as it scrolls. Draw starting from -scroll_x, and repeat.
    text_y = y + (h - 32) // 2

    # Build segments with colors
    # Rather than drawing one monolithic string, draw segment by segment for coloring
    cursor_x = -scroll_x

    # We may need up to 2 full repetitions to fill the screen
    repetitions = max(2, (w // max(ticker_width, 1)) + 2)

    for _rep in range(repetitions):
        for i, (source, title) in enumerate(headlines):
            # Draw source name in accent color
            source_str = f"{source}: "
            if cursor_x + ticker_font.getlength(source_str) > 0 and cursor_x < w:
                draw.text((cursor_x, text_y), source_str, fill=ACCENT, font=ticker_font)
            cursor_x += int(ticker_font.getlength(source_str))

            # Draw headline in bright white
            title_str = title
            if cursor_x + ticker_font.getlength(title_str) > 0 and cursor_x < w:
                draw.text((cursor_x, text_y), title_str, fill=TEXT_BRIGHT, font=ticker_font)
            cursor_x += int(ticker_font.getlength(title_str))

            # Draw divider
            if cursor_x + divider_font.getlength(DIVIDER) > 0 and cursor_x < w:
                draw.text((cursor_x, text_y), DIVIDER, fill=TEXT_DIM, font=divider_font)
            cursor_x += int(divider_font.getlength(DIVIDER))

            # If we've gone past the right edge and have enough buffer, stop
            if cursor_x > w + 200:
                break

        if cursor_x > w + 200:
            break

    # Advance scroll position
    _cache['scroll_x'] = (scroll_x + SCROLL_SPEED) % max(ticker_width, 1)


def render_frame(w, h):
    """Render one frame of the news crawl. Returns a PIL Image."""
    img = Image.new('RGB', (w, h), BG)
    draw = ImageDraw.Draw(img)

    _maybe_fetch()

    _cache['frame_count'] += 1

    pad_x = 12

    # Layout: top 40%, bottom 60%
    top_h = int(h * 0.4)
    bottom_h = h - top_h

    # --- Top section: feed info and clock ---
    draw.rectangle([0, 0, w, top_h], fill=PANEL_BG)
    draw.line([0, top_h - 1, w, top_h - 1], fill=BORDER, width=2)

    title_font = font(28)
    info_font = font(20)
    clock_font = font(48)
    label_font = font(16)

    # Title
    draw.text((pad_x, 12), "NEWS CRAWL", fill=ACCENT, font=title_font)

    # Headline count and feed sources
    headlines = _cache['headlines']
    headline_count = len(headlines)

    if headline_count > 0:
        # Count by source
        source_counts = {}
        for source, _ in headlines:
            source_counts[source] = source_counts.get(source, 0) + 1

        count_text = f"{headline_count} headlines"
        draw.text((pad_x, 48), count_text, fill=TEXT, font=info_font)

        # Draw source breakdown
        sx = pad_x
        sy = 78
        for source_name, cnt in source_counts.items():
            tag = f"{source_name} ({cnt})"
            tag_w = int(label_font.getlength(tag)) + 16
            draw.rounded_rectangle([sx, sy, sx + tag_w, sy + 24], radius=4, fill=BORDER)
            draw.text((sx + 8, sy + 3), tag, fill=CYAN, font=label_font)
            sx += tag_w + 8
    else:
        status = "Fetching..." if _cache['fetching'] else (_cache['fetch_error'] or "Waiting for feeds")
        draw.text((pad_x, 50), status, fill=TEXT_DIM, font=info_font)

    # Draw feed status indicators
    if _cache['fetching']:
        # Pulsing dot to indicate fetching
        pulse = abs((_cache['frame_count'] % 30) - 15) / 15.0
        pulse_color = (
            int(ACCENT[0] * pulse),
            int(ACCENT[1] * pulse),
            int(ACCENT[2] * pulse),
        )
        draw.ellipse([pad_x + 200, 52, pad_x + 210, 62], fill=pulse_color)

    # Clock on the right side
    now = datetime.now()
    time_str = now.strftime('%H:%M:%S')
    date_str = now.strftime('%a %b %d, %Y')

    clock_w = clock_font.getlength(time_str)
    clock_x = w - pad_x - int(clock_w)
    draw.text((clock_x, 16), time_str, fill=TEXT_BRIGHT, font=clock_font)

    date_w = info_font.getlength(date_str)
    date_x = w - pad_x - int(date_w)
    draw.text((date_x, 72), date_str, fill=TEXT_DIM, font=info_font)

    # Last updated indicator
    if _cache['last_fetch'] > 0:
        ago = int(time.time() - _cache['last_fetch'])
        if ago < 60:
            updated_str = f"Updated {ago}s ago"
        else:
            updated_str = f"Updated {ago // 60}m ago"
        uw = label_font.getlength(updated_str)
        draw.text((w - pad_x - int(uw), 100), updated_str, fill=TEXT_DIM, font=label_font)

    # --- Bottom section: scrolling ticker ---
    # Draw a subtle gradient line at the division
    draw.line([0, top_h, w, top_h], fill=ACCENT, width=2)

    _draw_ticker(draw, img, top_h + 2, bottom_h - 2, w)

    return img
