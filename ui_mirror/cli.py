"""Command line entry point: mirror a site and optionally serve the result."""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import socketserver
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from .check import analyze as analyze_site, format_report
from .fetcher import DEFAULT_USER_AGENT
from .mirror import DEFAULT_DENY_PATTERNS, MirrorOptions, MirrorReport, SiteMirror

DEFAULT_SERVE_PORT = 8765


def force_utf8_console() -> None:
    """Turkish text through a pipe must not turn into mojibake on Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ui-mirror",
        description=(
            "Download a website interface (HTML, CSS, JS, fonts, images) and rewrite "
            "its links so the copy opens offline - HTTrack style."
        ),
        epilog=(
            "Examples:\n"
            "  python -m ui_mirror https://example.com --gui          # masaüstü arayüz\n"
            "  python -m ui_mirror https://example.com --check        # kopyalanabilirlik teyidi\n"
            "  python -m ui_mirror https://example.com -o mirror_example\n"
            "  python -m ui_mirror https://example.com --depth 3 --serve\n"
            "  python -m ui_mirror https://spa.example.com --render --depth 1\n\n"
            "Only mirror sites you own or are allowed to copy."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", nargs="?", help="start page of the site to mirror")
    parser.add_argument("-o", "--output", help="output directory (default: mirror_<host>)")
    parser.add_argument(
        "--gui", action="store_true", help="open the animated desktop interface"
    )
    parser.add_argument(
        "--no-splash", action="store_true", help="skip the GUI intro animation"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="confirm the page is copyable (robots, anti-bot, SPA, assets) and exit",
    )
    parser.add_argument(
        "--check-json", action="store_true", help="print the confirmation report as JSON"
    )
    parser.add_argument(
        "-d", "--depth", type=int, default=0, help="extra link levels (default 0: only the given URL)"
    )
    parser.add_argument("--max-pages", type=int, default=40, help="maximum number of pages")
    parser.add_argument(
        "--max-assets", type=int, default=2000, help="maximum number of assets"
    )
    parser.add_argument(
        "--any-host", action="store_true", help="crawl pages on other hosts as well"
    )
    parser.add_argument(
        "--no-external-assets",
        action="store_true",
        help="do not download assets (CDN CSS/JS/fonts) from other hosts",
    )
    parser.add_argument(
        "--deny",
        action="append",
        default=None,
        metavar="REGEX",
        help="skip page URLs matching this regex (repeatable)",
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=None,
        metavar="REGEX",
        help="only mirror page URLs matching this regex (repeatable)",
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="render JavaScript pages with Playwright or installed Chrome/Edge",
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=4, help="parallel asset downloads"
    )
    parser.add_argument(
        "--delay", type=float, default=0.2, help="minimum seconds between requests"
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="request timeout (s)")
    parser.add_argument("--retries", type=int, default=2, help="retries per request")
    parser.add_argument(
        "--max-asset-mb",
        type=float,
        default=25.0,
        help="skip a single asset larger than this (MB)",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=600.0,
        help="overall time budget in seconds (0 = no limit)",
    )
    parser.add_argument(
        "--no-robots", action="store_true", help="ignore robots.txt (sites you own only)"
    )
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="User-Agent header")
    parser.add_argument(
        "--header",
        action="append",
        default=None,
        metavar="'Name: value'",
        help="extra header for the target origin only (repeatable)",
    )
    parser.add_argument(
        "--cookies", help="cookies for the target origin only, e.g. 'session=abc; theme=dark'"
    )
    parser.add_argument(
        "--serve",
        nargs="?",
        const=DEFAULT_SERVE_PORT,
        type=int,
        metavar="PORT",
        help=f"serve the copy over HTTP and open it (default port {DEFAULT_SERVE_PORT})",
    )
    parser.add_argument(
        "--open", action="store_true", help="open the entry page in the default browser"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="only print the summary")
    return parser


def normalize_url(raw: str) -> str:
    value = raw.strip()
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"only http(s) URLs are supported, got: {raw}")
    if not parts.hostname:
        raise ValueError(f"URL has no host: {raw}")
    return value


def default_output_dir(url: str) -> Path:
    host = (urlsplit(url).hostname or "site").lower()
    safe = "".join(char if char.isalnum() or char in ".-_" else "_" for char in host)
    return Path("mirror_" + safe.strip("_") or "mirror_site")


def parse_headers(values: list[str] | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw in values or []:
        if ":" not in raw:
            raise ValueError(f"header must look like 'Name: value', got: {raw}")
        name, value = raw.split(":", 1)
        if not name.strip():
            raise ValueError(f"header name is empty: {raw}")
        headers[name.strip()] = value.strip()
    return headers


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def print_summary(report: MirrorReport) -> None:
    assets_ok = sum(1 for asset in report.assets if asset.get("local"))
    print("")
    print(
        f"[done] {len(report.pages)} page(s), {assets_ok} asset(s), "
        f"{human_size(report.bytes_written)}, {report.duration_seconds:.1f}s"
    )
    if report.entry_file:
        entry_path = Path(report.output_dir) / report.entry_file
        print(f"[file] {entry_path}")
    if report.errors:
        print(
            f"[warn] {len(report.errors)} request(s) failed "
            f"- details in {report.report_path}"
        )
    print(f"[report] {report.report_path}")


def serve_directory(
    directory: Path, port: int, entry: str | None, open_browser: bool = True
) -> int:
    handler = functools.partial(_QuietHandler, directory=str(directory))
    try:
        httpd = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        print(f"[serve] cannot listen on 127.0.0.1:{port}: {exc}", file=sys.stderr)
        return 1

    httpd.daemon_threads = True
    target = f"http://127.0.0.1:{port}/" + (entry or "")
    print(f"[serve] {target}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(target)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] stopped")
    finally:
        httpd.server_close()
    return 0


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Static file handler without the per-request logging noise."""

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass


def main(argv: list[str] | None = None) -> int:
    force_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.gui:
        from .gui import main as gui_main

        gui_args: list[str] = []
        if args.no_splash:
            gui_args.append("--no-splash")
        if args.url:
            gui_args.append(args.url)
        if args.output:
            gui_args += ["-o", args.output]
        return gui_main(gui_args)

    if not args.url:
        parser.error("a URL is required (or use --gui)")

    try:
        url = normalize_url(args.url)
        headers = parse_headers(args.header)
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # unreachable, parser.error exits

    if args.check or args.check_json:
        report = analyze_site(
            url,
            user_agent=args.user_agent,
            timeout=max(1.0, args.timeout),
            cookies=args.cookies,
            extra_headers=headers,
        )
        if args.check_json:
            print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
        else:
            print(format_report(report))
        return 0 if report.ok else 1

    output_dir = Path(args.output).expanduser() if args.output else default_output_dir(url)
    options = MirrorOptions(
        start_url=url,
        output_dir=output_dir,
        max_depth=max(0, args.depth),
        max_pages=max(1, args.max_pages),
        max_assets=max(1, args.max_assets),
        stay_on_host=not args.any_host,
        external_assets=not args.no_external_assets,
        jobs=max(1, args.jobs),
        delay=max(0.0, args.delay),
        timeout=max(1.0, args.timeout),
        retries=max(0, args.retries),
        max_asset_bytes=max(1, int(args.max_asset_mb * 1024 * 1024)),
        time_budget=None if args.max_seconds <= 0 else args.max_seconds,
        user_agent=args.user_agent,
        headers=headers,
        cookies=args.cookies,
        respect_robots=not args.no_robots,
        render=args.render,
        verbose=not args.quiet,
        deny_patterns=tuple(args.deny) if args.deny else DEFAULT_DENY_PATTERNS,
        allow_patterns=tuple(args.allow) if args.allow else (),
    )

    if not args.quiet:
        print(f"[start] {url}")
        print(f"[out]   {output_dir.resolve()}")
        print(
            f"[opts]  depth={options.max_depth} pages<={options.max_pages} "
            f"assets<={options.max_assets} delay={options.delay}s jobs={options.jobs} "
            f"max_asset={args.max_asset_mb:g}MB budget={args.max_seconds:g}s "
            f"render={options.render}"
        )

    try:
        report = SiteMirror(options).run()
    except KeyboardInterrupt:
        print("\n[ui-mirror] interrupted", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"[ui-mirror] {exc}", file=sys.stderr)
        return 2

    print_summary(report)

    if args.serve is not None:
        return serve_directory(output_dir, args.serve, report.entry_file)
    if args.open and report.entry_file:
        webbrowser.open((output_dir / report.entry_file).resolve().as_uri())
    return 0 if report.entry_file else 1


if __name__ == "__main__":
    sys.exit(main())
