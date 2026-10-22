import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QRect, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QImageReader, QImageWriter, QPainter, QPainterPath, QPen, QLinearGradient


class CoverService:
    """Cria a capa vertical de um projeto sem tocar nos artefatos do vídeo."""

    size = (1080, 1920)

    def __init__(self, projects):
        self.projects = projects

    @classmethod
    def _foreground_rect(cls, image: QImage) -> QRect | None:
        """Return a safe foreground area for media that does not fill 9:16.

        Portrait artwork can remain full-frame. Landscape and square artwork
        gets a sharp, uncropped foreground over a softened version of itself,
        so a cover never silently removes the character at either edge.
        """
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            return None
        source_ratio = image.width() / image.height()
        target_ratio = cls.size[0] / cls.size[1]
        if source_ratio <= target_ratio * 1.15:
            return None
        max_width = round(cls.size[0] * .94)
        max_height = round(cls.size[1] * .64)
        scale = min(max_width / image.width(), max_height / image.height())
        width = max(1, round(image.width() * scale))
        height = max(1, round(image.height() * scale))
        return QRect((cls.size[0] - width) // 2, (cls.size[1] - height) // 2 + 85, width, height)

    @classmethod
    def _draw_source_composition(cls, painter: QPainter, canvas: QImage, image: QImage) -> None:
        """Render a full-frame portrait or uncropped landscape foreground."""
        background = image.scaled(*cls.size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                  Qt.TransformationMode.SmoothTransformation)
        # Downscale first to create a deterministic low-cost blur approximation
        # with Qt only; no cloud/GPU or additional image dependency is needed.
        softened = background.scaled(54, 96, Qt.AspectRatioMode.IgnoreAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        softened = softened.scaled(*cls.size, Qt.AspectRatioMode.IgnoreAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        painter.drawImage(0, 0, softened)
        rect = cls._foreground_rect(image)
        if rect is None:
            painter.drawImage((canvas.width() - background.width()) // 2,
                              (canvas.height() - background.height()) // 2, background)
            return
        foreground = image.scaled(rect.width(), rect.height(), Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
        x = rect.x() + (rect.width() - foreground.width()) // 2
        y = rect.y() + (rect.height() - foreground.height()) // 2
        painter.drawImage(x, y, foreground)

    @staticmethod
    def _clean_copy(value: str, maximum: int = 90) -> str:
        return ' '.join(str(value or '').replace('\n', ' ').split())[:maximum].upper()

    @staticmethod
    def _lines(text: str, font: QFont, width: int) -> list[str]:
        words, lines, current = text.split(), [], ''
        metrics = QFontMetrics(font)
        for word in words:
            candidate = f'{current} {word}'.strip()
            if current and metrics.horizontalAdvance(candidate) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    def _fit_copy(self, text: str, width: int, height: int, maximum_lines: int, *, maximum_points: int = 104,
                  minimum_points: int = 40) -> tuple[QFont, list[str]]:
        for points in range(maximum_points, minimum_points - 1, -2):
            # Arial is present in supported Windows installations; unlike
            # optional display fonts it yields stable outlines in QPainter.
            font = QFont('Arial')
            font.setBold(True)
            font.setPointSize(points)
            lines = self._lines(text, font, width)
            metrics = QFontMetrics(font)
            if len(lines) <= maximum_lines and len(lines) * metrics.lineSpacing() <= height:
                return font, lines
        return font, self._lines(text, font, width)[:maximum_lines]

    @staticmethod
    def _draw_outlined_lines(painter: QPainter, font: QFont, lines: list[str], top: float, color: QColor, width: int,
                             *, glow: bool = False, highlight_words: list[str] | None = None) -> None:
        """Draw compact cover text with individually highlighted words."""
        painter.setFont(font)
        metrics = QFontMetrics(font)
        y = top + metrics.ascent()
        stroke = max(5, round(font.pointSizeF() * .115))
        highlighted = {word.strip('.,!?;:').casefold() for word in (highlight_words or [])}
        for line in lines:
            x = (width - metrics.horizontalAdvance(line)) / 2
            words = line.split()
            for index, word in enumerate(words):
                path = QPainterPath()
                path.addText(x, y, font, word)
                if glow:
                    painter.setPen(QPen(QColor(185, 0, 0, 125), stroke + 8, Qt.PenStyle.SolidLine,
                                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPath(path)
                painter.setPen(QPen(QColor(0, 0, 0), stroke, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.setBrush(QColor('#ffe500') if word.strip('.,!?;:').casefold() in highlighted else color)
                painter.drawPath(path)
                if index < len(words) - 1:
                    x += metrics.horizontalAdvance(f'{word} ')
            y += metrics.lineSpacing()

    @staticmethod
    def _text_positions(position: str, headline_height: int, subheadline_height: int) -> tuple[int, int]:
        position = position if position in {'top', 'middle', 'bottom'} else 'top'
        if position == 'middle':
            headline_top = max(160, 720 - headline_height // 2)
        elif position == 'bottom':
            headline_top = max(160, 1250 - headline_height)
        else:
            headline_top = 120
        subheadline_top = min(1660, headline_top + headline_height + 54)
        if subheadline_top + subheadline_height > 1755:
            subheadline_top = max(headline_top + headline_height + 30, 1755 - subheadline_height)
        return headline_top, subheadline_top

    @staticmethod
    def _preset_overlay(painter: QPainter, canvas: QImage, preset: str, intensity: str) -> None:
        alpha = {'leve': 34, 'moderada': 62, 'intensa': 94}.get(intensity, 62)
        if preset == 'confronto':
            gradient = QLinearGradient(0, 0, canvas.width(), 0)
            gradient.setColorAt(0, QColor(20, 45, 120, alpha))
            gradient.setColorAt(.48, QColor(0, 0, 0, 0))
            gradient.setColorAt(1, QColor(139, 0, 0, alpha))
        elif preset == 'revelacao':
            gradient = QLinearGradient(0, 0, canvas.width(), canvas.height())
            gradient.setColorAt(0, QColor(65, 12, 85, alpha))
            gradient.setColorAt(.55, QColor(0, 0, 0, 0))
            gradient.setColorAt(1, QColor(15, 90, 74, alpha))
        else:
            gradient = QLinearGradient(0, 0, 0, canvas.height())
            gradient.setColorAt(0, QColor(65, 0, 0, alpha))
            gradient.setColorAt(.5, QColor(0, 0, 0, 0))
            gradient.setColorAt(1, QColor(0, 0, 0, alpha))
        painter.fillRect(canvas.rect(), gradient)

    @staticmethod
    def _draw_border(painter: QPainter, canvas: QImage, intensity: str) -> None:
        width = {'leve': 4, 'moderada': 7, 'intensa': 10}.get(intensity, 7)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor('#8b0000'), width))
        inset = width // 2 + 10
        painter.drawRect(inset, inset, canvas.width() - (inset * 2), canvas.height() - (inset * 2))
        painter.setPen(QPen(QColor('#ffd600'), max(2, width // 5)))
        painter.drawRect(inset + width, inset + width, canvas.width() - 2 * (inset + width), canvas.height() - 2 * (inset + width))

    def generate(self, project, source: str | Path, headline: str = '', subheadline: str = '', brand: str = 'Lore Oculta',
                 *, preset: str = 'cinematico', effect_intensity: str = 'moderada', border: bool = True,
                 text_position: str = 'top', highlight_words: list[str] | None = None) -> Path:
        source = Path(source)
        if not source.is_file():
            raise ValueError(f'Imagem de capa inexistente: {source.name or source}.')

        reader = QImageReader(str(source))
        reader.setAutoTransform(True)
        if not reader.canRead():
            raise ValueError(f'Imagem de capa inválida: {source.name}.')
        image = reader.read()
        if image.isNull():
            raise ValueError(f'Imagem de capa inválida: {source.name}.')

        target = self.projects.cover_path(project)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f'.{target.stem}-{uuid.uuid4().hex}.part.png')
        try:
            canvas = QImage(*self.size, QImage.Format.Format_RGB888)
            canvas.fill(QColor('black'))
            painter = QPainter(canvas)
            try:
                self._draw_source_composition(painter, canvas, image)
                # Darkened edges keep text readable while retaining the user's
                # original image as the visual focus.
                gradient = QLinearGradient(0, 0, 0, canvas.height())
                gradient.setColorAt(0, QColor(0, 0, 0, 205))
                gradient.setColorAt(.27, QColor(0, 0, 0, 96))
                gradient.setColorAt(.49, QColor(0, 0, 0, 8))
                gradient.setColorAt(1, QColor(0, 0, 0, 118))
                painter.fillRect(canvas.rect(), gradient)
                self._preset_overlay(painter, canvas, preset, effect_intensity)
                headline = self._clean_copy(headline or project.theme)
                subheadline = self._clean_copy(subheadline)
                if len(headline.split()) > 6:
                    raise ValueError('O texto principal da capa deve ter no máximo seis palavras.')
                if len(subheadline.split()) > 5:
                    raise ValueError('O texto secundário da capa deve ter no máximo cinco palavras.')
                sub_font, sub_lines, subheadline_top = QFont(), [], 1330
                if headline:
                    font, lines = self._fit_copy(headline, 960, 390, 2, maximum_points=116, minimum_points=56)
                    headline_height = len(lines) * QFontMetrics(font).lineSpacing()
                    sub_font, sub_lines = self._fit_copy(subheadline, 820, 82, 1, maximum_points=44, minimum_points=24) if subheadline else (font, [])
                    sub_height = len(sub_lines) * QFontMetrics(sub_font).lineSpacing()
                    headline_top, subheadline_top = self._text_positions(text_position, headline_height, sub_height)
                    self._draw_outlined_lines(painter, font, lines, headline_top, QColor('#ffffff'), canvas.width(),
                                              glow=effect_intensity == 'intensa', highlight_words=highlight_words)
                if subheadline:
                    self._draw_outlined_lines(painter, sub_font, sub_lines, subheadline_top, QColor('#ffffff'), canvas.width(), glow=False)
                brand = self._clean_copy(brand, 28)
                if brand:
                    brand_font = QFont('Georgia')
                    brand_font.setBold(True)
                    brand_font.setPointSize(36)
                    self._draw_outlined_lines(painter, brand_font, [brand], 1810, QColor('#ffe51b'), canvas.width())
                if border:
                    self._draw_border(painter, canvas, effect_intensity)
            finally:
                painter.end()
            writer = QImageWriter(str(temporary), b'png')
            saved = writer.write(canvas)
            error = writer.errorString()
            # No Windows, o arquivo continua bloqueado enquanto o writer existe.
            del writer
            if not saved:
                raise OSError(f'Não foi possível salvar a capa: {error}.')
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return target
