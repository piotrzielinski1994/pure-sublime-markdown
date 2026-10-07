import os
import re
import sublime
import sublime_plugin
import traceback
import webbrowser

try:
    from urllib.parse import quote as _quote
except ImportError:
    from urllib import quote as _quote

try:
    import html as _html

    def _escape(s):
        return _html.escape(s, quote=False)
except ImportError:
    import cgi as _cgi

    def _escape(s):
        return _cgi.escape(s, quote=False)


SETTINGS_FILE = 'PureSublimeMarkdown.sublime-settings'
PHANTOM_KEY = 'pure_sublime_markdown_images'
PREVIEW_PHANTOM_KEY = 'pure_sublime_markdown_preview'
REGION_RED = 'pure_sublime_markdown_red'
REGION_GREEN = 'pure_sublime_markdown_green'

IMG_RE = re.compile(r'<img\s+[^>]*?src="([^"]+)"[^>]*?>', re.IGNORECASE)
TAG_RE = re.compile(r'(<img\s+[^>]*?>|<\s*span[^>]*>|<\s*/\s*span\s*>)', re.IGNORECASE)
SPAN_RE = re.compile(
    r'<span\s+[^>]*?color\s*:\s*(red|green)[^>]*?>(.*?)</\s*span\s*>',
    re.IGNORECASE | re.DOTALL)
LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)\s]+)\)')
BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
HEADING_RE = re.compile(r'^(#{1,6})\s+(.*\S)\s*$')
HR_RE = re.compile(r'^\s*(---|\*\*\*|___)\s*$')

_pending = set()
# Keep PhantomSet refs alive: a collected set removes its phantoms
_phantom_sets = {}
_preview_sets = {}


def _settings():
    return sublime.load_settings(SETTINGS_FILE)


def is_markdown_view(view):
    if view is None or not view.is_valid() or view.is_scratch():
        return False
    name = view.file_name() or ''
    return name.lower().endswith(('.md', '.markdown', '.mdown'))


def file_to_url(path):
    p = os.path.abspath(path).replace('\\', '/')
    url_path = _quote(p, safe='/:')
    if not url_path.startswith('/'):
        url_path = '/' + url_path
    return 'file://' + url_path


def resolve_src(src, base_dir):
    s = (src or '').strip()
    low = s.lower()
    if low.startswith(('http://', 'https://', 'data:', 'file://', 'res://', 'subl:')):
        return s
    if not base_dir:
        return s
    # strip query-ish leading ./ and normalize
    abs_path = os.path.normpath(os.path.join(base_dir, s))
    if os.path.isfile(abs_path):
        return file_to_url(abs_path)
    return s


def _resolve_img_tag(tag, base_dir):
    m = re.search(r'src="([^"]+)"', tag, re.IGNORECASE)
    if not m:
        return tag
    url = resolve_src(m.group(1), base_dir)
    return TAG_SRC_RE.sub('src="' + url + '"', tag, count=1)


TAG_SRC_RE = re.compile(r'src="[^"]*"', re.IGNORECASE)


def _inline_format(text):
    links = []

    def _link_sub(m):
        links.append('<a href="' + m.group(2) + '">' + _escape(m.group(1)) + '</a>')
        return '@@MDLINK' + str(len(links) - 1) + '@@MDLINK'

    s = LINK_RE.sub(_link_sub, text)
    s = _escape(s)
    s = BOLD_RE.sub(r'<strong>\1</strong>', s)
    for i, html in enumerate(links):
        s = s.replace('@@MDLINK' + str(i) + '@@MDLINK', html)
    return s


def format_mixed(line, base_dir):
    parts = TAG_RE.split(line)
    out = []
    for p in parts:
        if not p:
            continue
        low = p.lower()
        if low.startswith('<img'):
            out.append(_resolve_img_tag(p, base_dir))
        elif low.startswith('<span') or low.startswith('</span'):
            out.append(p)
        else:
            out.append(_inline_format(p))
    return ''.join(out)


def convert_markdown(md_text, base_dir, img_height=150):
    html_lines = []
    for raw in md_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if HR_RE.match(line):
            # minihtml has no <hr>: bordered div instead (border-top is supported)
            html_lines.append('<div class="rule"></div>')
            continue
        mh = HEADING_RE.match(line.strip())
        if mh:
            level = len(mh.group(1))
            inner = format_mixed(mh.group(2), base_dir)
            html_lines.append('<h{0}>{1}</h{0}>'.format(level, inner))
            continue
        if IMG_RE.search(line) and not HEADING_RE.match(line):
            # image-only (or image-led) row: keep tags, no <p> wrapper
            html_lines.append('<div class="imgs">' + format_mixed(line, base_dir) + '</div>')
            continue
        html_lines.append('<p>' + format_mixed(line, base_dir) + '</p>')
    return '\n'.join(html_lines)


# minihtml fragment shown in a Sublime scratch view (VS Code preview equivalent).
# Only a CSS subset works here: plain colors, no flexbox. Images flow inline
# and wrap, which visually matches the VS Code grid for photo rows.
MINIHTML_TEMPLATE = """<style>
div.mdprev {{ font-size: 14px; color: {fg}; }}
h1 {{ font-size: 1.6em; }}
h2 {{ font-size: 1.4em; }}
h3 {{ color: #f0b429; font-size: 1.2em; }}
h3 a {{ color: #f0b429; }}
a {{ color: #4da3ff; }}
div.rule {{ border-top: 1px solid #555555; margin-top: 16px; margin-bottom: 16px; }}
</style>
<div class="mdprev">{body}</div>
"""


def build_minihtml(md_text, base_dir, fg='#d4d4d4'):
    body = convert_markdown(md_text, base_dir, _img_height())
    return MINIHTML_TEMPLATE.format(body=body, fg=fg)


def _on_preview_navigate(href):
    if href and href.lower().startswith(('http://', 'https://')):
        try:
            webbrowser.open(href)
        except Exception:
            pass


# source view id -> saved view state while in-place preview is on
_preview_state = {}


def _is_in_preview(view):
    try:
        return bool(view.settings().get('markdown_preview_mode', False))
    except Exception:
        return False


GENERATED_SCHEME = 'PureSublimeMarkdownHiddenCaret.generated.sublime-color-scheme'
_last_scheme_colors = (None, None)


def _write_preview_scheme(bg, fg):
    # buffer text fully invisible (no folds, so no fold markers);
    # phantom HTML carries its own explicit colors
    global _last_scheme_colors
    if (bg, fg) == _last_scheme_colors:
        return
    try:
        path = os.path.join(sublime.packages_path(), 'User', GENERATED_SCHEME)
        caret = bg + '00' if isinstance(bg, str) and bg.startswith('#') and len(bg) == 7 else '#00000000'
        g = []
        g.append('\t\t"background": "' + bg + '",')
        g.append('\t\t"foreground": "' + bg + '",')
        g.append('\t\t"caret": "' + caret + '",')
        for key in ('invisibles', 'line_highlight', 'selection',
                    'find_highlight', 'find_highlight_foreground',
                    'guide', 'active_guide', 'stack_guide',
                    'brackets_foreground'):
            g.append('\t\t"' + key + '": "' + bg + '",')
        content = ('{\n\t"name": "Markdown Preview (invisible buffer, generated)",\n'
                   '\t"globals": {\n' + '\n'.join(g) + '\n'
                   '\t},\n\t"rules": [\n\t]\n}\n')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        _last_scheme_colors = (bg, fg)
    except Exception as e:
        pass


def _preview_colors(src_view):
    bg, fg = '#343d46', '#d4d4d4'
    try:
        st = src_view.style()
        bg = st.get('background', bg) or bg
        fg = st.get('foreground', fg) or fg
    except Exception:
        pass
    return bg, fg


def _render_preview_html(view):
    base = _base_dir(view)
    md_text = view.substr(sublime.Region(0, view.size()))
    fg = _preview_colors(view)[1]
    return build_minihtml(md_text, base, fg)


def enter_preview(view):
    if _is_in_preview(view):
        return True
    try:
        html = _render_preview_html(view)
    except Exception as e:
        traceback.print_exc()
        sublime.error_message('Markdown preview: cannot render ({0})'.format(e))
        return False
    st = view.settings()
    _preview_state[view.id()] = {
        'sel': [(r.a, r.b) for r in view.sel()],
        'color_scheme': st.get('color_scheme'),
        'font_size': st.get('font_size'),
        'gutter': st.get('gutter'),
    }
    clear_view(view)
    try:
        # no folds (fold markers cannot be hidden): buffer text is invisible
        # via the generated scheme, phantom carries explicit colors
        bg, fg = _preview_colors(view)
        _write_preview_scheme(bg, fg)
        st.set('markdown_preview_mode', True)
        st.set('color_scheme', 'Packages/User/' + GENERATED_SCHEME)
        st.set('font_size', 2)
        st.set('gutter', False)
        view.set_read_only(True)
        pset = sublime.PhantomSet(view, PREVIEW_PHANTOM_KEY)
        pset.update([sublime.Phantom(
            sublime.Region(0), html, sublime.LAYOUT_BLOCK,
            _on_preview_navigate)])
        _preview_sets[view.id()] = pset
    except Exception as e:
        traceback.print_exc()
        exit_preview(view)
        return False
    sublime.status_message('Markdown preview on')
    return True


def exit_preview(view):
    st = view.settings()
    try:
        st.set('markdown_preview_mode', False)
    except Exception:
        pass
    try:
        sublime.PhantomSet(view, PREVIEW_PHANTOM_KEY).update([])
    except Exception:
        pass
    _preview_sets.pop(view.id(), None)
    try:
        for r in view.folded_regions():
            view.unfold(r)
    except Exception:
        pass
    state = _preview_state.pop(view.id(), {})
    try:
        for key in ('color_scheme', 'font_size', 'gutter'):
            if state.get(key) is not None:
                st.set(key, state[key])
            else:
                st.erase(key)
        if view.is_read_only():
            view.set_read_only(False)
    except Exception:
        pass
    try:
        view.sel().clear()
        for a, b in state.get('sel', [(0, 0)]):
            view.sel().add(sublime.Region(a, b))
    except Exception:
        pass
    if is_markdown_view(view):
        update_view_phantoms(view)
    sublime.status_message('Markdown preview off')


def refresh_inplace_preview(view):
    if not _is_in_preview(view):
        return False
    try:
        html = _render_preview_html(view)
        pset = sublime.PhantomSet(view, PREVIEW_PHANTOM_KEY)
        pset.update([sublime.Phantom(
            sublime.Region(0), html, sublime.LAYOUT_BLOCK,
            _on_preview_navigate)])
        _preview_sets[view.id()] = pset
    except Exception:
        return False
    return True


def _base_dir(view):
    fname = view.file_name() or ''
    if fname:
        return os.path.dirname(fname)
    folders = sublime.active_window().folders() if sublime.active_window() else []
    return folders[0] if folders else ''


def _img_height():
    try:
        return int(_settings().get('image_height', 150))
    except Exception:
        return 150


def update_view_phantoms(view, show_remote=True):
    if not is_markdown_view(view):
        return
    if _is_in_preview(view):
        return
    if not _settings().get('enabled', True):
        clear_view(view)
        return
    base = _base_dir(view)
    height = _img_height()
    try:
        max_per_line = int(_settings().get('max_images_per_line', 12))
    except Exception:
        max_per_line = 12

    phantoms = []
    red_regions = []
    green_regions = []

    # colorize <span style="color:red|green"> markers
    content = view.substr(sublime.Region(0, view.size()))
    for m in SPAN_RE.finditer(content):
        color = m.group(1).lower()
        a, b = m.span(2)
        if a < b:
            r = sublime.Region(a, b)
            if color == 'red':
                red_regions.append(r)
            else:
                green_regions.append(r)

    # one BLOCK phantom per image (stacked under the line),
    # same pattern as the Markdown Images package
    lines = view.lines(sublime.Region(0, view.size()))
    for line in lines:
        text = view.substr(line)
        imgs = IMG_RE.findall(text)
        if not imgs:
            continue
        for src in imgs[:max_per_line]:
            if not show_remote and src.lower().startswith(('http://', 'https://')):
                continue
            url = resolve_src(src, base)
            if url == src and not src.lower().startswith(
                    ('http://', 'https://', 'data:', 'file://', 'res://')):
                # local file missing: skip thumbnail, keep text as-is
                continue
            html = ('<img src="' + url + '" height="' + str(height) + '">')
            try:
                phantoms.append(sublime.Phantom(
                    sublime.Region(line.b), html, sublime.LAYOUT_BLOCK))
            except Exception:
                pass

    try:
        pset = sublime.PhantomSet(view, PHANTOM_KEY)
        pset.update(phantoms)
        if phantoms:
            _phantom_sets[view.id()] = pset
        else:
            _phantom_sets.pop(view.id(), None)
    except Exception:
        pass
    flags = getattr(sublime, 'DRAW_NO_OUTLINE', 0)
    try:
        if red_regions:
            view.add_regions(REGION_RED, red_regions, 'markup.deleted', '', flags)
        else:
            view.erase_regions(REGION_RED)
        if green_regions:
            view.add_regions(REGION_GREEN, green_regions, 'markup.inserted', '', flags)
        else:
            view.erase_regions(REGION_GREEN)
    except Exception:
        pass


def clear_view(view):
    try:
        sublime.PhantomSet(view, PHANTOM_KEY).update([])
    except Exception:
        pass
    _phantom_sets.pop(view.id(), None)
    try:
        view.erase_regions(REGION_RED)
        view.erase_regions(REGION_GREEN)
    except Exception:
        pass


def _schedule(view_id):
    if view_id in _pending:
        return
    _pending.add(view_id)

    def _run():
        _pending.discard(view_id)
        v = None
        for w in sublime.windows():
            for cand in w.views():
                if cand.id() == view_id:
                    v = cand
                    break
        if v is not None and v.is_valid():
            update_view_phantoms(v)

    sublime.set_timeout(_run, 400)


class PureSublimeMarkdownShow(sublime_plugin.EventListener):
    def on_load(self, view):
        if is_markdown_view(view):
            _schedule(view.id())

    def on_activated(self, view):
        if is_markdown_view(view):
            _schedule(view.id())

    def on_modified_async(self, view):
        if _is_in_preview(view):
            return
        if is_markdown_view(view):
            _schedule(view.id())

    def on_post_save(self, view):
        if _is_in_preview(view):
            refresh_inplace_preview(view)
        elif is_markdown_view(view):
            _schedule(view.id())

    def on_close(self, view):
        _pending.discard(view.id())
        _phantom_sets.pop(view.id(), None)
        _preview_sets.pop(view.id(), None)
        _preview_state.pop(view.id(), None)


class PureSublimeMarkdownImagesShowCommand(sublime_plugin.TextCommand):
    def run(self, edit, show_local=True, show_remote=True):
        update_view_phantoms(self.view, show_remote=show_remote)


class PureSublimeMarkdownImagesHideCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        clear_view(self.view)


class PureSublimeMarkdownImagesToggleCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        s = _settings()
        s.set('enabled', not s.get('enabled', True))
        sublime.save_settings(SETTINGS_FILE)
        if s.get('enabled', True):
            update_view_phantoms(self.view)
        else:
            clear_view(self.view)


class PureSublimeMarkdownWheelCommand(sublime_plugin.TextCommand):
    # plain wheel replacement: pixel scroll in preview, line scroll elsewhere
    def run(self, edit, direction=1, fast=False):
        v = self.view
        try:
            direction = float(direction)
        except Exception:
            direction = 1.0
        if _is_in_preview(v):
            step = 200.0 if fast else 350.0
        else:
            try:
                lh = float(v.line_height())
            except Exception:
                lh = 15.0
            if lh <= 0:
                lh = 15.0
            step = (10.0 if fast else 3.0) * lh
        try:
            x, y = v.viewport_position()
            v.set_viewport_position((x, y + direction * step), False)
        except Exception:
            pass


class PureSublimeMarkdownPreviewCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        if _is_in_preview(self.view):
            exit_preview(self.view)
            return
        if not is_markdown_view(self.view):
            sublime.error_message('Markdown preview: open a .md file first')
            return
        enter_preview(self.view)


def plugin_loaded():
    # pre-generate the preview scheme at startup so the file exists
    # (and is indexed) long before any preview references it
    try:
        v = None
        if sublime.active_window():
            v = sublime.active_window().active_view()
        if v is not None and v.is_valid():
            bg, fg = _preview_colors(v)
        else:
            bg, fg = '#0b111f', '#a9b7c6'
        _write_preview_scheme(bg, fg)
    except Exception:
        pass
    for w in sublime.windows():
        for v in w.views():
            if is_markdown_view(v):
                _schedule(v.id())
