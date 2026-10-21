"""Small libass-compatible writer for word-timed readable captions."""
import re
from pathlib import Path

STYLES = {
    # BorderStyle 1 means outline/shadow only; it never draws a box behind text.
    'standard': {'font_size': 64, 'margin_v': 250, 'outline': 4, 'shadow': 2},
    'highlight': {'font_size': 68, 'margin_v': 270, 'outline': 5, 'shadow': 2},
    'retention': {'font_size': 68, 'margin_v': 290, 'outline': 4, 'shadow': 2},
}

def _header(style: str) -> str:
    values = STYLES.get(style, STYLES['standard'])
    return (
        '[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n'
        'Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n'
        f"Style: Default,Arial,{values['font_size']},&H00FFFFFF,&H0000FFFF,&H00101010,&H00000000,1,0,0,0,100,100,0,0,1,{values['outline']},{values['shadow']},2,60,60,{values['margin_v']},1\n\n"
        '[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n'
    )
_TIME = re.compile(r'^\d+:\d{2}:\d{2}\.\d{2}$')


def _ass_time(value: float) -> str:
    """Format a libass timestamp as ``H:MM:SS.cc``.

    SRT uses ``HH:MM:SS,mmm``. Trimming it creates ``00:00.000``, which
    libass rejects while FFmpeg still exits successfully.
    """
    centiseconds = round(float(value) * 100)
    if centiseconds < 0:
        raise ValueError('Timestamp ASS inv\u00e1lido.')
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, fraction = divmod(remainder, 100)
    return f'{hours}:{minutes:02}:{seconds:02}.{fraction:02}'


def _seconds(value: str) -> float:
    hours, minutes, tail = value.split(':')
    seconds, fraction = tail.split('.')
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction) / 100


def validate_ass_file(target: Path) -> int:
    """Reject an ASS file which libass would silently render without text."""
    try:
        lines = Path(target).read_text(encoding='utf8').splitlines()
    except OSError as error:
        raise ValueError('N\u00e3o foi poss\u00edvel ler as legendas ASS.') from error
    events = 0
    for line in lines:
        if not line.startswith('Dialogue:'):
            continue
        parts = line.split(',', 9)
        if len(parts) != 10 or not _TIME.fullmatch(parts[1]) or not _TIME.fullmatch(parts[2]):
            raise ValueError('Timestamps ASS inv\u00e1lidos. As legendas n\u00e3o foram renderizadas.')
        if _seconds(parts[2]) <= _seconds(parts[1]):
            raise ValueError('Timestamps ASS inv\u00e1lidos. As legendas n\u00e3o foram renderizadas.')
        events += 1
    if not events:
        raise ValueError('O arquivo ASS n\u00e3o cont\u00e9m palavras sincronizadas para renderizar.')
    return events
def _escape(text: str) -> str:
    return text.replace('\\', r'\\').replace('{', r'\{').replace('}', r'\}').replace('\n', r'\N')
def write_ass(words, target: Path, group_size: int = 3, style: str = 'standard') -> Path:
    values = []
    for word in words:
        start, end, text = (word['start'], word['end'], word['text']) if isinstance(word, dict) else (word.start, word.end, word.word)
        if start is None or end is None or float(end) <= float(start) or not str(text).strip(): raise ValueError('Timestamps por palavra inválidos para ASS.')
        values.append((float(start), float(end), _escape(str(text).strip())))
    if not values: raise ValueError('ASS requer palavras com timestamps.')
    if style not in STYLES: raise ValueError('Estilo ASS invÃ¡lido.')
    group_size = min(5, max(2, int(group_size))); rows = [_header(style)]
    for offset in range(0, len(values), group_size):
        group = values[offset:offset + group_size]
        for active, (start, end, _) in enumerate(group):
            text = ' '.join('{\\c&H0000FFFF&}' + token if index == active else token for index, (_, _, token) in enumerate(group))
            rows.append(f'Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}\n')
    temporary = Path(target).with_suffix('.tmp'); temporary.write_text(''.join(rows), encoding='utf8'); temporary.replace(target)
    return Path(target)
