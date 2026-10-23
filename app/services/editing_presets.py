"""Deterministic editorial presets; they never select assets or effects at random."""
from copy import deepcopy

PRESETS = {
    'neutral': {'name': 'Neutral', 'motion': 'static_focus', 'transition': 'fade', 'subtitle_style': 'standard', 'music_volume': 25},
    'cinematic': {'name': 'Cinematic', 'motion': 'slow_push', 'transition': 'dissolve', 'subtitle_style': 'standard', 'music_volume': 25},
    'dynamic': {'name': 'Dynamic', 'motion': 'pan_right', 'transition': 'impact_cut', 'subtitle_style': 'highlight', 'music_volume': 25},
    'suspense': {'name': 'Suspense', 'motion': 'slow_pull', 'transition': 'fade', 'subtitle_style': 'highlight', 'music_volume': 20},
    'emotional': {'name': 'Emotional', 'motion': 'slow_push', 'transition': 'dissolve', 'subtitle_style': 'standard', 'music_volume': 20},
    'misterio': {
        'name': 'Mistério', 'motion': 'slow_pull', 'transition': 'fade',
        'subtitle_style': 'highlight', 'music_volume': 20, 'mix_profile': 'cinematografica',
        'audio_intents': ['suspense', 'revelation'],
    },
    'epico': {
        'name': 'Épico', 'motion': 'slow_push', 'transition': 'impact_cut',
        'subtitle_style': 'highlight', 'music_volume': 25, 'mix_profile': 'cinematografica',
        'audio_intents': ['impact', 'climax'],
    },
    'revelacao': {
        'name': 'Revelação', 'motion': 'impact_zoom', 'transition': 'dissolve',
        'subtitle_style': 'highlight', 'music_volume': 22, 'mix_profile': 'cinematografica',
        'audio_intents': ['revelation', 'impact'],
    },
    'historia_sombria': {
        'name': 'História sombria', 'motion': 'slow_pull', 'transition': 'dissolve',
        'subtitle_style': 'highlight', 'music_volume': 22, 'mix_profile': 'cinematografica',
        'audio_intents': ['suspense', 'impact'],
    },
    'top_5': {
        'name': 'Top 5', 'motion': 'pan_right', 'transition': 'impact_cut',
        'subtitle_style': 'highlight', 'music_volume': 25, 'mix_profile': 'equilibrada',
        'audio_intents': ['transition', 'impact'],
    },
    'teoria_misterio': {
        'name': 'Teoria e mistério', 'motion': 'slow_push', 'transition': 'fade',
        'subtitle_style': 'highlight', 'music_volume': 22, 'mix_profile': 'cinematografica',
        'audio_intents': ['suspense', 'revelation'],
    },
    'retencao_maxima': {
        'name': 'Lore Oculta — Retenção Máxima', 'motion': 'impact_zoom',
        'transition': 'flash_transition', 'subtitle_style': 'retention',
        'music_volume': 25, 'mix_profile': 'cinematografica',
        # Six scenes are the editorial baseline, but the automatic timeline
        # must not throw away user media when a story needs a few more beats.
        # The upper bound prevents unusably fast cuts in the 25–30s target.
        'min_images': 6, 'max_images': 12, 'version': 'retencao-maxima/v2',
    },
}

RETENTION_PRESET = 'retencao_maxima'

def preset_key(value: str | None) -> str:
    """Normalize the UI label and legacy aliases to a stable persisted key."""
    raw = str(value or '').strip().casefold()
    aliases = {
        'lore oculta — retenção máxima': RETENTION_PRESET,
        'lore oculta - retenção máxima': RETENTION_PRESET,
        'lore oculta — retencao maxima': RETENTION_PRESET,
        'lore oculta - retencao maxima': RETENTION_PRESET,
        'história sombria': 'historia_sombria',
        'historia sombria': 'historia_sombria',
        'top 5': 'top_5',
        'teoria e mistério': 'teoria_misterio',
        'teoria e misterio': 'teoria_misterio',
        'mistério': 'misterio',
        'epico': 'epico',
        'épico': 'epico',
        'revelação': 'revelacao',
        'revelacao': 'revelacao',
    }
    return aliases.get(raw, raw)

def is_retention_preset(value: str | None) -> bool:
    return preset_key(value) == RETENTION_PRESET

def validate_preset_images(value: str | None, count: int) -> None:
    style = resolve_preset(value)
    minimum = style.get('min_images')
    maximum = style.get('max_images')
    if minimum is not None and count < minimum:
        raise ValueError(
            f'O preset Lore Oculta — Retenção Máxima precisa de pelo menos {minimum} imagens; '
            f'este projeto possui {count}. Importe mais imagens ou selecione outro preset.'
        )
    if maximum is not None and count > maximum:
        raise ValueError(
            f'O preset Lore Oculta — Retenção Máxima aceita no máximo {maximum} imagens; '
            f'este projeto possui {count}. Divida o roteiro em dois Shorts ou selecione outro preset.'
        )

def resolve_preset(value: str | None) -> dict:
    """Return a copy so persisted project configuration cannot mutate defaults."""
    return deepcopy(PRESETS.get(preset_key(value), PRESETS['neutral']))
