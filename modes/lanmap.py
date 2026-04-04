"""Network device scanner for tinyscreen bar display.

Discovers hosts on the local network via nmap ping scan.
Shows IPv4 address, hostname, and vendor/device type.
"""

import re
import subprocess
import threading
import time
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── Colors (vivid, matches other modes) ────────────────────────────
BG          = (5, 7, 12)
PANEL_BG    = (10, 14, 24)
ACCENT      = (0, 210, 255)
TEXT        = (220, 225, 240)
TEXT_DIM    = (65, 75, 100)
TEXT_BRIGHT = (252, 254, 255)
GREEN       = (0, 255, 140)
RED         = (255, 50, 50)
YELLOW      = (255, 225, 0)
ORANGE      = (255, 165, 30)
CYAN        = (0, 240, 255)
PURPLE      = (160, 110, 255)

# ── Font cache ─────────────────────────────────────────────────────
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

# ── Visual utilities (same pattern as other modes) ─────────────────
def _lerp_color(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

_bg_cache = {}

def _get_bg(w, h):
    if (w, h) not in _bg_cache:
        img = Image.new('RGBA', (w, h), BG + (255,))
        draw = ImageDraw.Draw(img)
        for y in range(h):
            t = y / max(h - 1, 1)
            c = _lerp_color((8, 12, 22), (4, 6, 12), t)
            draw.line([(0, y), (w, y)], fill=c)
        grid_c = (18, 24, 38, 50)
        for gx in range(0, w, 40):
            draw.line([(gx, 0), (gx, h)], fill=grid_c)
        for gy in range(0, h, 40):
            draw.line([(0, gy), (w, gy)], fill=grid_c)
        for gx in range(0, w, 80):
            for gy in range(0, h, 80):
                draw.ellipse([gx - 2, gy - 2, gx + 2, gy + 2], fill=ACCENT + (25,))
                draw.ellipse([gx - 1, gy - 1, gx + 1, gy + 1], fill=ACCENT + (45,))
        for i in range(50):
            a = int(30 * (1.0 - i / 50))
            draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=(0, 100, 180, a))
        vig = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        vd = ImageDraw.Draw(vig)
        for i in range(40):
            a = int(50 * (1.0 - i / 40))
            vd.line([(0, i), (w, i)], fill=(0, 0, 0, a))
            vd.line([(0, h - 1 - i), (w, h - 1 - i)], fill=(0, 0, 0, a))
        for i in range(60):
            a = int(40 * (1.0 - i / 60))
            vd.line([(i, 0), (i, h)], fill=(0, 0, 0, a))
            vd.line([(w - 1 - i, 0), (w - 1 - i, h)], fill=(0, 0, 0, a))
        img = Image.alpha_composite(img, vig)
        _bg_cache[(w, h)] = img
    return _bg_cache[(w, h)].copy()

_scanline_cache = {}

def _get_scanlines(w, h):
    key = (w, h)
    if key not in _scanline_cache:
        sl = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sl)
        for y in range(0, h, 3):
            sd.line([(0, y), (w, y)], fill=(0, 0, 0, 55))
        _scanline_cache[key] = sl
    return _scanline_cache[key]

def _draw_glow_dot(img, cx, cy, r, color):
    pad = 10
    size = (r + pad) * 2
    dot = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dot)
    dd.ellipse([pad - 3, pad - 3, pad + r * 2 + 3, pad + r * 2 + 3],
               fill=color + (60,))
    dot = dot.filter(ImageFilter.GaussianBlur(radius=5))
    img.paste(dot, (cx - r - pad, cy - r - pad), dot)
    sharp = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sharp)
    sd.ellipse([pad, pad, pad + r * 2, pad + r * 2], fill=color + (255,))
    bright = tuple(min(255, c + 80) for c in color)
    core = max(1, r - 2)
    sd.ellipse([pad + r - core, pad + r - core, pad + r + core, pad + r + core],
               fill=bright + (200,))
    img.paste(sharp, (cx - r - pad, cy - r - pad), sharp)

# ── Device type guessing ───────────────────────────────────────────
# MAC vendor keywords → (type, color)
_VENDOR_TYPES = {
    'raspberry pi': ('Pi', PURPLE),
    'espressif': ('IoT', YELLOW),
    'sonos': ('Speaker', CYAN),
    'google': ('Google', ORANGE),
    'apple': ('Apple', TEXT_BRIGHT),
    'intel': ('PC', ACCENT),
    'actiontec': ('Router', GREEN),
    'samsung': ('Samsung', ACCENT),
    'amazon': ('Echo', ORANGE),
    'ring': ('Ring', CYAN),
    'wyze': ('Wyze', YELLOW),
    'tp-link': ('TP-Link', GREEN),
    'nest': ('Nest', ORANGE),
    'roku': ('Roku', PURPLE),
    'nvidia': ('GPU/PC', GREEN),
    'ai-link': ('IoT', YELLOW),
    'guangdong': ('Camera', RED),
    'china dragon': ('IoT', YELLOW),
    'shenzhen': ('IoT', YELLOW),
    'liteon': ('PC', ACCENT),
    'foxconn': ('PC', ACCENT),
    'murata': ('IoT', YELLOW),
    'wistron': ('PC', ACCENT),
    'tuya': ('IoT', YELLOW),
    'realtek': ('PC', ACCENT),
    'dell': ('PC', ACCENT),
    'hewlett': ('PC', ACCENT),
    'lenovo': ('PC', ACCENT),
    'asus': ('PC', ACCENT),
    'microsoft': ('PC', ACCENT),
    'netgear': ('Router', GREEN),
    'ubiquiti': ('Router', GREEN),
    'cisco': ('Router', GREEN),
    'linksys': ('Router', GREEN),
    'arris': ('Router', GREEN),
    'huawei': ('Router', GREEN),
    'synology': ('NAS', CYAN),
    'qnap': ('NAS', CYAN),
    'brother': ('Printer', PURPLE),
    'canon': ('Printer', PURPLE),
    'epson': ('Printer', PURPLE),
    'lg electr': ('TV', ORANGE),
    'vizio': ('TV', ORANGE),
    'tcl': ('TV', ORANGE),
    'hisense': ('TV', ORANGE),
    'ecobee': ('Thermo', YELLOW),
    'honeywell': ('Thermo', YELLOW),
    'philips': ('IoT', YELLOW),
    'lifx': ('IoT', YELLOW),
    'wemo': ('IoT', YELLOW),
}

# Open port → device type hints
_PORT_HINTS = {
    22: 'SSH',        # Linux/server
    53: 'DNS',        # Router/DNS server
    80: 'HTTP',       # Web server/device
    443: 'HTTPS',     # Web server/device
    445: 'SMB',       # Windows/NAS
    548: 'AFP',       # Mac file sharing
    554: 'RTSP',      # Camera
    631: 'Print',     # Printer (CUPS/IPP)
    3389: 'RDP',      # Windows
    5000: 'NAS',      # Synology DSM
    5353: 'mDNS',     # Apple/Bonjour
    8080: 'HTTP',     # Alt web server
    8443: 'HTTPS',    # Alt HTTPS
    8123: 'HA',       # Home Assistant
    9100: 'Print',    # Raw printing
    32400: 'Plex',    # Plex Media Server
    62078: 'Apple',   # iPhone sync
}

def _guess_device(hostname, vendor, open_ports=None):
    """Guess device type from hostname, MAC vendor, and open ports."""
    combined = f"{hostname} {vendor}".lower()

    # Check vendor keywords first
    for keyword, (label, color) in _VENDOR_TYPES.items():
        if keyword in combined:
            return label, color

    # Hostname patterns
    hn = (hostname or '').lower()
    if 'cam' in hn or 'ipc' in hn:
        return 'Camera', RED
    if 'phone' in hn or 'iphone' in hn or 'ipad' in hn:
        return 'Apple', TEXT_BRIGHT
    if 'macbook' in hn or 'imac' in hn:
        return 'Mac', TEXT_BRIGHT
    if 'esp' in hn or 'tasmota' in hn or 'shelly' in hn:
        return 'IoT', YELLOW
    if 'printer' in hn or 'brother' in hn:
        return 'Printer', PURPLE
    if any(x in hn for x in ['nas', 'synology', 'diskstation']):
        return 'NAS', CYAN
    if any(x in hn for x in ['tv', 'roku', 'firetv', 'chromecast']):
        return 'TV', ORANGE
    if any(x in hn for x in ['xbox', 'playstation', 'switch']):
        return 'Console', PURPLE

    # Port-based inference
    if open_ports:
        ports = set(open_ports)
        if 554 in ports:
            return 'Camera', RED
        if 62078 in ports:
            return 'Apple', TEXT_BRIGHT
        if 3389 in ports:
            return 'Windows', ACCENT
        if 548 in ports:
            return 'Mac', TEXT_BRIGHT
        if 445 in ports and 22 not in ports:
            return 'Windows', ACCENT
        if 5000 in ports or (445 in ports and 22 in ports):
            return 'NAS', CYAN
        if 631 in ports or 9100 in ports:
            return 'Printer', PURPLE
        if 8123 in ports:
            return 'HA', GREEN
        if 32400 in ports:
            return 'Plex', PURPLE
        if 22 in ports and 80 not in ports:
            return 'Linux', ACCENT
        if 22 in ports:
            return 'Server', ACCENT
        if 80 in ports or 443 in ports:
            return 'Device', TEXT_DIM

    return 'Device', TEXT_DIM

# ── Scan data ──────────────────────────────────────────────────────
_cache = {
    'hosts': [],
    'last_scan': 0,
    'scan_count': 0,
    'scanning': False,
    'error': None,
    'subnet': '192.168.1.0/24',
    'known_ips': set(),       # IPs seen in previous scans
    'new_ips': {},            # IP -> timestamp when first seen as "new"
}

SCAN_INTERVAL = 30   # seconds between scans
NEW_HIGHLIGHT_SEC = 10  # how long new devices stay highlighted
PAGE_ROTATE_SEC = 15  # seconds per page when paginating
GROUP_BY_TYPE = False  # set True to group hosts by device type

# Group ordering (lower = shown first)
_TYPE_ORDER = {
    'Router': 0, 'DNS': 0,
    'Apple': 1, 'Mac': 1, 'Phone': 1,
    'PC': 2, 'GPU/PC': 2, 'Windows': 2, 'Linux': 2,
    'Pi': 3,
    'Server': 4, 'NAS': 4, 'Plex': 4, 'HA': 4,
    'Device': 5, 'Samsung': 5,
    'Google': 6, 'Nest': 6,
    'Roku': 7, 'TV': 7, 'Console': 7,
    'IoT': 8, 'Wyze': 8, 'TP-Link': 8, 'Thermo': 8,
    'Speaker': 9, 'Echo': 9, 'Sonos': 9,
    'Camera': 10, 'Ring': 10,
    'Printer': 11,
}


def _detect_subnet():
    """Detect local subnet from default route."""
    try:
        out = subprocess.run(['ip', 'route'], capture_output=True, text=True, timeout=3)
        for line in out.stdout.splitlines():
            if line.startswith('default'):
                parts = line.split()
                src_idx = parts.index('src') if 'src' in parts else -1
                if src_idx > 0:
                    ip = parts[src_idx + 1]
                    octets = ip.split('.')
                    return f"{octets[0]}.{octets[1]}.{octets[2]}.0/24"
    except Exception:
        pass
    return '192.168.1.0/24'


def _apply_port_results(hosts, port_output):
    """Parse nmap port scan output and update host types based on open ports."""
    # Build IP → open ports mapping
    ip_ports = {}
    current_ip = None
    for line in port_output.splitlines():
        if line.startswith('Nmap scan report for'):
            match = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
            if match:
                current_ip = match.group(1)
                ip_ports[current_ip] = []
        elif current_ip and '/tcp' in line and 'open' in line:
            try:
                port = int(line.split('/')[0])
                ip_ports[current_ip].append(port)
            except ValueError:
                pass

    # Re-evaluate device types with port info
    for host in hosts:
        ports = ip_ports.get(host['ip'], [])
        if ports:
            host['ports'] = ports
            # Re-guess with port data (only upgrade, don't downgrade known types)
            if host['type'] == 'Device':
                new_type, new_color = _guess_device(
                    host['hostname'], host['vendor'], open_ports=ports)
                host['type'] = new_type
                host['color'] = new_color


def _parse_nmap_output(output):
    """Parse nmap -sn output into host list."""
    hosts = []
    lines = output.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('Nmap scan report for'):
            match = re.match(r'Nmap scan report for (?:(\S+) \()?(\d+\.\d+\.\d+\.\d+)\)?', line)
            if not match:
                match = re.match(r'Nmap scan report for (\d+\.\d+\.\d+\.\d+)', line)
            if match:
                groups = match.groups()
                if len(groups) == 2 and groups[0]:
                    hostname = groups[0].replace('.lan', '')
                    ip = groups[1]
                else:
                    hostname = ''
                    ip = groups[-1]

                vendor = ''
                for j in range(i + 1, min(i + 4, len(lines))):
                    if 'MAC Address:' in lines[j]:
                        mac_match = re.search(r'MAC Address: \S+ \((.+)\)', lines[j])
                        if mac_match:
                            vendor = mac_match.group(1)
                        break

                device_type, device_color = _guess_device(hostname, vendor)
                hosts.append({
                    'ip': ip,
                    'hostname': hostname,
                    'vendor': vendor,
                    'type': device_type,
                    'color': device_color,
                })
        i += 1
    return hosts


def _gather_local_intel():
    """Gather ARP table and mDNS data for enrichment before nmap scan."""
    intel = {}  # ip -> {'hostname': str, 'services': [], 'model': str}

    # ARP table — gives us IPs we already know about
    try:
        result = subprocess.run(['arp', '-an'], capture_output=True, text=True, timeout=3)
        for line in result.stdout.splitlines():
            m = re.search(r'\((\d+\.\d+\.\d+\.\d+)\)', line)
            if m:
                ip = m.group(1)
                if ip not in intel:
                    intel[ip] = {'hostname': '', 'services': [], 'model': ''}
    except Exception:
        pass

    # mDNS via avahi-browse — rich device info
    try:
        result = subprocess.run(
            ['avahi-browse', '-atrl', '--no-db-lookup', '-p'],
            capture_output=True, text=True, timeout=8
        )
        for line in result.stdout.splitlines():
            if not line.startswith('='):
                continue
            parts = line.split(';')
            if len(parts) < 9:
                continue
            # parts: =;iface;proto;name;service;domain;hostname;ip;port;txt...
            service = parts[4]
            mdns_host = parts[6].replace('.local', '')
            ip = parts[7]
            name = parts[3]
            # Decode avahi octal escapes (\032 = space, \058 = colon, etc)
            name = re.sub(r'\\(\d{3})', lambda m: chr(int(m.group(1), 8)), name)
            txt = ';'.join(parts[9:]) if len(parts) > 9 else ''

            if not re.match(r'\d+\.\d+\.\d+\.\d+', ip):
                continue

            if ip not in intel:
                intel[ip] = {'hostname': '', 'services': [], 'model': ''}

            # Better hostname from mDNS
            if mdns_host and len(mdns_host) > len(intel[ip]['hostname']):
                intel[ip]['hostname'] = mdns_host

            # Track services
            if service and service not in intel[ip]['services']:
                intel[ip]['services'].append(service)

            # Extract model from txt records
            model_match = re.search(r'model=([^"]+)', txt)
            if model_match and not intel[ip]['model']:
                intel[ip]['model'] = model_match.group(1)

            # Extract manufacturer
            mfr_match = re.search(r'manufacturer=([^"]+)', txt)
            if mfr_match:
                intel[ip]['model'] = mfr_match.group(1) + ' ' + intel[ip].get('model', '')

            # Friendly name from airplay/display service names
            if service in ('_airplay._tcp', '_display._tcp', '_raop._tcp'):
                if name and not intel[ip].get('friendly_name'):
                    intel[ip]['friendly_name'] = name

    except FileNotFoundError:
        pass  # avahi-browse not installed
    except Exception:
        pass

    return intel


def _guess_from_services(services):
    """Infer device type from mDNS service list."""
    svc_set = set(services)
    if '_airplay._tcp' in svc_set or '_raop._tcp' in svc_set:
        if '_display._tcp' in svc_set:
            return 'TV'
        return 'AirPlay'
    if '_hap._tcp' in svc_set:
        return 'HomeKit'
    if '_spotify-connect._tcp' in svc_set:
        return 'Speaker'
    if '_printer._tcp' in svc_set or '_ipp._tcp' in svc_set:
        return 'Printer'
    if '_smb._tcp' in svc_set or '_afpovertcp._tcp' in svc_set:
        return 'NAS'
    if '_ssh._tcp' in svc_set:
        return 'SSH'
    if '_remotepairing._tcp' in svc_set or '_apple-mobdev2._tcp' in svc_set:
        return 'Apple'
    return None


def _do_scan():
    """Background scan: ARP+mDNS enrichment, nmap discovery, port probe."""
    subnet = _cache['subnet']
    try:
        # Phase 0: gather local intel (ARP + mDNS) — instant, no network scan
        local_intel = _gather_local_intel()

        # Phase 1: fast ping scan to discover hosts
        result = subprocess.run(
            ['nmap', '-sn', subnet, '--host-timeout', '3s'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            _cache['error'] = 'nmap scan failed'
            _cache['scanning'] = False
            return

        hosts = _parse_nmap_output(result.stdout)

        # Enrich hosts with local intel
        for host in hosts:
            ip = host['ip']
            if ip in local_intel:
                info = local_intel[ip]
                # Better hostname from mDNS
                if info['hostname'] and (not host['hostname'] or
                        len(info['hostname']) > len(host['hostname'])):
                    host['hostname'] = info['hostname']
                # Use friendly name if available (e.g., "65in TCL Roku TV")
                if info.get('friendly_name'):
                    # Clean avahi escapes
                    fname = re.sub(r'\\(\d{3})', lambda m: chr(int(m.group(1), 8)),
                                   info['friendly_name'])
                    # Strip MAC prefix patterns like "F22522335B1D@"
                    fname = re.sub(r'^[0-9A-Fa-f]{12}@', '', fname)
                    if fname:
                        host['hostname'] = fname
                # Re-guess type with enriched hostname
                if host['type'] == 'Device':
                    new_type, new_color = _guess_device(host['hostname'], host['vendor'])
                    if new_type != 'Device':
                        host['type'] = new_type
                        host['color'] = new_color

                # Service-based type inference (overrides vendor guess for unknowns)
                if info['services'] and host['type'] == 'Device':
                    svc_type = _guess_from_services(info['services'])
                    if svc_type:
                        color_map = {
                            'TV': ORANGE, 'HomeKit': YELLOW, 'AirPlay': TEXT_BRIGHT,
                            'Speaker': CYAN, 'Printer': PURPLE, 'NAS': CYAN,
                            'Apple': TEXT_BRIGHT, 'SSH': ACCENT,
                        }
                        host['type'] = svc_type
                        host['color'] = color_map.get(svc_type, TEXT_DIM)

        # Phase 1.5: resolve hostnames for any hosts still showing "—"
        # Try reverse DNS, avahi-resolve, and nmblookup
        unnamed = [h for h in hosts if not h['hostname']]
        for host in unnamed:
            ip = host['ip']
            name = ''
            # Try reverse DNS first (fastest)
            try:
                result = subprocess.run(
                    ['dig', '-x', ip, '+short', '+timeout=1', '+tries=1'],
                    capture_output=True, text=True, timeout=3)
                ans = result.stdout.strip().rstrip('.')
                if ans and not re.match(r'^[\d-]+\.lan$', ans):
                    name = ans.replace('.lan', '').replace('.local', '')
            except Exception:
                pass
            # Try avahi-resolve
            if not name:
                try:
                    result = subprocess.run(
                        ['avahi-resolve', '-a', ip],
                        capture_output=True, text=True, timeout=3)
                    parts = result.stdout.strip().split('\t')
                    if len(parts) >= 2 and parts[1]:
                        name = parts[1].replace('.local', '')
                except Exception:
                    pass
            # Try NetBIOS
            if not name:
                try:
                    result = subprocess.run(
                        ['nmblookup', '-A', ip],
                        capture_output=True, text=True, timeout=3)
                    for line in result.stdout.splitlines():
                        line = line.strip()
                        if '<00>' in line and 'GROUP' not in line:
                            name = line.split()[0]
                            break
                except Exception:
                    pass
            if name:
                host['hostname'] = name
                # Re-evaluate type with new hostname
                if host['type'] == 'Device':
                    new_type, new_color = _guess_device(name, host['vendor'])
                    if new_type != 'Device':
                        host['type'] = new_type
                        host['color'] = new_color

        # Phase 2: quick port probe on discovered hosts for better identification
        # Only probe key ports, with tight timeouts
        probe_ports = '22,53,80,443,445,548,554,631,3389,5000,5353,8080,8123,9100,32400,62078'
        ips = ' '.join(h['ip'] for h in hosts)
        if ips:
            try:
                port_result = subprocess.run(
                    ['nmap', '-sT', '-p', probe_ports, '--host-timeout', '2s',
                     '--max-retries', '1', '-T4', '--open'] + [h['ip'] for h in hosts],
                    capture_output=True, text=True, timeout=45
                )
                if port_result.returncode == 0:
                    _apply_port_results(hosts, port_result.stdout)
            except Exception:
                pass  # port scan is best-effort, don't fail the whole scan
        hosts.sort(key=lambda h: tuple(int(o) for o in h['ip'].split('.')))

        # Detect new devices
        now = time.time()
        current_ips = {h['ip'] for h in hosts}
        if _cache['scan_count'] > 0:
            # Only flag new IPs after the first scan
            for ip in current_ips - _cache['known_ips']:
                _cache['new_ips'][ip] = now

        # Expire old "new" highlights
        _cache['new_ips'] = {ip: t for ip, t in _cache['new_ips'].items()
                             if now - t < NEW_HIGHLIGHT_SEC}

        _cache['known_ips'] = current_ips
        _cache['hosts'] = hosts
        _cache['scan_count'] += 1
        _cache['error'] = None

    except subprocess.TimeoutExpired:
        _cache['error'] = 'Scan timed out'
    except FileNotFoundError:
        _cache['error'] = 'nmap not installed'
    except Exception as e:
        _cache['error'] = str(e)[:60]
    finally:
        _cache['scanning'] = False
        _cache['last_scan'] = time.time()


def _scan_network():
    """Kick off a background scan if it's time."""
    now = time.time()
    if now - _cache['last_scan'] < SCAN_INTERVAL:
        return
    if _cache['scanning']:
        return

    _cache['scanning'] = True
    t = threading.Thread(target=_do_scan, daemon=True)
    t.start()


def init():
    _cache['subnet'] = _detect_subnet()
    _cache['last_scan'] = 0
    _cache['scan_count'] = 0
    _cache['known_ips'] = set()
    _cache['new_ips'] = {}
    _scan_network()


# ── Render ─────────────────────────────────────────────────────────
def render_frame(w=1920, h=440):
    _scan_network()

    if not _cache['hosts'] and _cache['scan_count'] == 0:
        if not _cache.get('_init_done'):
            _cache['subnet'] = _detect_subnet()
            _cache['_init_done'] = True

    # Expire stale new highlights
    now = time.time()
    _cache['new_ips'] = {ip: t for ip, t in _cache['new_ips'].items()
                         if now - t < NEW_HIGHLIGHT_SEC}

    img = _get_bg(w, h)
    draw = ImageDraw.Draw(img)

    pad_x = 20
    new_ips = _cache['new_ips']

    # Sort: new devices first, then grouped by type or by IP
    hosts_new = []
    hosts_rest = []
    for entry in _cache['hosts']:
        if entry['ip'] in new_ips:
            hosts_new.append(entry)
        else:
            hosts_rest.append(entry)
    hosts_new.sort(key=lambda e: -new_ips.get(e['ip'], 0))

    if GROUP_BY_TYPE:
        hosts_rest.sort(key=lambda e: (
            _TYPE_ORDER.get(e['type'], 99),
            e['type'],  # alphabetical within same priority group
            tuple(int(o) for o in e['ip'].split('.'))
        ))

    hosts = hosts_new + hosts_rest

    # ── Header ──
    header_h = 44
    for row in range(header_h):
        t = row / max(header_h - 1, 1)
        c = _lerp_color((14, 18, 32), (8, 11, 22), t)
        draw.line([(0, row), (w, row)], fill=c)

    # Accent line
    glow = Image.new('RGBA', (w, 16), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rectangle([0, 0, w, 2], fill=ACCENT + (200,))
    gd.rectangle([0, 2, w, 6], fill=ACCENT + (50,))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=4))
    img.paste(glow, (0, header_h - 2), glow)

    draw = ImageDraw.Draw(img)
    draw.text((pad_x, 10), "NETWORK DEVICES", fill=ACCENT, font=font(24))

    # Status — show scanning indicator prominently
    if _cache['scanning']:
        status_text = f"{len(hosts)} hosts  SCANNING..."
        status_color = YELLOW
    elif _cache['scan_count'] > 0:
        ago = int(time.time() - _cache['last_scan'])
        status_text = f"{len(hosts)} hosts  scanned {ago}s ago"
        status_color = TEXT
    else:
        status_text = "initializing..."
        status_color = YELLOW
    # New device count
    if new_ips:
        status_text += f"  ({len(new_ips)} new)"
    draw.text((w - pad_x - font(20).getlength(status_text), 14),
              status_text, fill=status_color, font=font(20))

    # ── Scanning / empty state — show a message but DON'T return blank ──
    if not hosts and _cache['error']:
        draw.text((w // 2 - 100, h // 2 - 12), _cache['error'],
                  fill=RED, font=font(24))

    if not hosts:
        # Show scanning animation dots
        dots = '.' * (int(time.time() * 2) % 4)
        draw.text((w // 2 - 120, h // 2 - 12),
                  f"Scanning {_cache['subnet']}{dots}",
                  fill=ACCENT, font=font(28))
        img = Image.alpha_composite(img, _get_scanlines(w, h))
        out = Image.new('RGB', (w, h), BG)
        out.paste(img, (0, 0), img)
        return out

    # ── Column header ──
    col_y = header_h + 4
    col_h = 22
    for row in range(col_h):
        t = row / max(col_h - 1, 1)
        c = _lerp_color((12, 16, 28), (8, 11, 20), t)
        draw.line([(0, col_y + row), (w, col_y + row)], fill=c)

    # Column headers (only shown for first column, positions are relative)
    draw.text((pad_x + 26, col_y + 2), "IP ADDRESS", fill=TEXT_DIM, font=font(14))
    draw.text((200, col_y + 2), "HOSTNAME", fill=TEXT_DIM, font=font(14))
    draw.text((420, col_y + 2), "TYPE", fill=TEXT_DIM, font=font(14))

    sep_y = col_y + col_h
    for i in range(2):
        a = int(100 * (1.0 - i / 2))
        draw.line([(0, sep_y + i), (w, sep_y + i)], fill=ACCENT + (a,))

    # ── Dynamic layout — scale font/rows to fill the screen ──
    content_top = sep_y + 3
    avail_h = h - content_top - 12
    n_hosts = len(hosts)

    # Try column counts from fewest to most, pick the one that fits all hosts
    # with the largest possible row height (and thus font)
    max_cols = 4
    min_row_h = 26   # hard floor — anything smaller is unreadable
    max_row_h = 36   # largest row height (big font)

    # Strategy: use max columns, maximize row height within that
    # More columns = fewer rows per column = bigger font
    rows_at_max_cols = -(-n_hosts // max_cols)
    best_row_h = min(max_row_h, avail_h // max(rows_at_max_cols, 1))
    best_row_h = max(min_row_h, best_row_h)
    best_cols = max_cols

    num_cols = best_cols
    row_h = best_row_h
    rows_per_col = max(1, avail_h // row_h)

    # Font scales with row height — minimum 16px, never unreadable
    data_font_size = max(16, min(18, row_h - 8))

    # Paginate only if still can't fit
    hosts_per_page = rows_per_col * num_cols
    total_pages = max(1, -(-n_hosts // hosts_per_page))
    current_page = int(time.time() / PAGE_ROTATE_SEC) % total_pages
    page_start = current_page * hosts_per_page
    page_hosts = hosts[page_start:page_start + hosts_per_page]

    col_w = w // num_cols

    def _draw_host_rows(host_list, col_idx, start_y):
        nonlocal draw
        x_off = col_idx * col_w
        for i, host in enumerate(host_list):
            ry = start_y + i * row_h
            if ry + row_h > h - 8:
                break

            is_new = host['ip'] in new_ips

            # Row background
            if is_new:
                # Bright green highlight for new devices
                for row in range(row_h):
                    draw.line([(x_off, ry + row), (x_off + col_w - 2, ry + row)],
                              fill=(0, 40, 20))
            elif i % 2 == 0:
                for row in range(row_h):
                    t = row / max(row_h - 1, 1)
                    rc = _lerp_color((12, 16, 28), (10, 13, 22), t)
                    draw.line([(x_off, ry + row), (x_off + col_w - 2, ry + row)],
                              fill=rc)

            mid_y = ry + row_h // 2

            # Dot — green glow for new, normal color otherwise
            dot_color = GREEN if is_new else host['color']
            _draw_glow_dot(img, x_off + 14, mid_y, 3, dot_color)
            draw = ImageDraw.Draw(img)

            # Column positions — IP, hostname, type (no vendor)
            ip_x = x_off + 26
            name_x = x_off + int(col_w * 0.35)
            type_x = x_off + int(col_w * 0.75)
            df = font(data_font_size)
            half_font = data_font_size // 2

            # IP
            ip_color = GREEN if is_new else TEXT_BRIGHT
            draw.text((ip_x, mid_y - half_font), host['ip'],
                      fill=ip_color, font=df)

            # Hostname — truncate to never overlap with IP
            ip_end = ip_x + int(df.getlength(host['ip'])) + 8
            name_start = max(name_x, ip_end)
            avail_name_w = type_x - name_start - 8
            hostname = host['hostname'] if host['hostname'] else '—'
            while len(hostname) > 1 and df.getlength(hostname) > avail_name_w:
                hostname = hostname[:-1]
            draw.text((name_start, mid_y - half_font), hostname,
                      fill=GREEN if is_new else TEXT, font=df)

            # Type
            draw.text((type_x, mid_y - half_font), host['type'],
                      fill=GREEN if is_new else host['color'], font=df)

    # Split page hosts across columns
    for col_idx in range(num_cols):
        start = col_idx * rows_per_col
        end = start + rows_per_col
        col_hosts = page_hosts[start:end]
        if not col_hosts:
            break
        _draw_host_rows(col_hosts, col_idx, content_top)

        # Column separator line
        if col_idx < num_cols - 1 and col_hosts:
            sx = (col_idx + 1) * col_w - 1
            for y in range(content_top, h - 10):
                draw.point((sx, y), fill=ACCENT + (35,))

    # Page indicator (if paginating)
    total_shown = len(page_hosts)
    if total_pages > 1:
        page_text = f"Page {current_page + 1}/{total_pages}"
        draw.text((w // 2 - 40, h - 26), page_text, fill=ACCENT, font=font(16))
    if len(hosts) > total_shown:
        extra = len(hosts) - total_shown
        draw.text((pad_x, h - 30), f"+ {extra} more devices",
                  fill=TEXT_DIM, font=font(16))

    # ── Bottom accent ──
    for i in range(8):
        a = int(180 * (1.0 - i / 8))
        draw.line([(0, h - 1 - i), (w, h - 1 - i)], fill=ACCENT + (a,))

    # Scanlines + convert
    img = Image.alpha_composite(img, _get_scanlines(w, h))
    out = Image.new('RGB', (w, h), BG)
    out.paste(img, (0, 0), img)
    return out


if __name__ == '__main__':
    import sys
    if '--once' in sys.argv:
        init()
        img = render_frame()
        img.save('/tmp/lanmap.png')
        print("Saved to /tmp/lanmap.png")
    else:
        print("Usage: python3 lanmap.py --once")
