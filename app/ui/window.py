from dataclasses import asdict, replace
from pathlib import Path
from PySide6.QtCore import Qt, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QComboBox, QSpinBox, QPlainTextEdit, QPushButton, QListWidget, QListWidgetItem, QMessageBox, QFileDialog,
    QTabWidget, QLabel, QCheckBox, QDoubleSpinBox, QInputDialog, QDialog, QDialogButtonBox, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QScrollArea)
import json
from time import perf_counter
from datetime import datetime, timezone
from app.core.models import Project, StepStatus
from app.database.repository import ProjectRepository
from app.services.projects import ProjectService
from app.services.settings import Settings, SettingsStore, resolved_mapping
from app.services.paths import application_root, migrate_legacy_data
from app.services.diagnostics import optional_component_diagnostics
from app.services.product_info import export_sanitized_diagnostics, product_information
from app.services.pipeline import Pipeline
from app.services.video import render_audio_preview
from app.ui.workers import JobWorker
from app.ui.preview import Preview
from app.providers.llm import OllamaProvider
from app.providers.piper import PiperProvider
from app.providers.kokoro import KokoroProvider
from app.services.narration import normalize_text, speech_text
from app.services.script import generate_script
from app.services.ai import generate_pack, validate_pack
from app.ui.ai_panel import AIPanel
from app.services.music import load_catalog, copy_music, load_effect_catalog, copy_effect_catalog
from app.services.subtitles import alignment_metadata, apply_word_adjustments
from app.services.outcomes import planning_context, save_outcome
from app.services.timeline import validate_timeline, validate_director_timeline, invalidate_derived
from app.services.editing_presets import PRESETS, resolve_preset
from app.services.editorial_director import prompt as editorial_prompt, response_schema, timeline_from_response, stable_input_hash
from app.services.visual_director import VISION_DIRECTOR_VERSION, describe_imported_images, editorial_context
from app.services.hook_analysis import prompt as hook_prompt, response_schema as hook_response_schema, validate_response as validate_hook_response
from app.services.cover_copy import prompt as cover_copy_prompt, response_schema as cover_copy_schema, validate as validate_cover_copy, fallback as cover_copy_fallback
from app.services.cover_plan import PRESETS as COVER_PRESETS, prompt as cover_plan_prompt, response_schema as cover_plan_schema, validate as validate_cover_plan, fallback as cover_plan_fallback

class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.paths = migrate_legacy_data()
        self.store = SettingsStore(self.paths.settings)
        self.settings = self.store.load()
        configured_output = Path(self.settings.output_dir).expanduser() if self.settings.output_dir else None
        output_is_in_installation = bool(configured_output and configured_output.is_absolute() and configured_output.resolve().is_relative_to(application_root()))
        if not configured_output or not configured_output.is_absolute() or output_is_in_installation:
            self.settings.output_dir = str(self.paths.projects)
            self.store.save(self.settings)
        self.voice_explicit = False
        self.repo = ProjectRepository(self.paths.database, Path(self.settings.output_dir))
        self.service = ProjectService(self.repo)
        self.pipeline = Pipeline(self.service)
        self.current = None
        self.workers = []
        self.actions = []
        self.setWindowTitle('Lore Oculta Studio')
        self.resize(1100, 780)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.summary = QLabel()
        self.quick_theme = QLineEdit()
        self.quick_script = QPlainTextEdit()
        self.quick_preset = QComboBox()
        self.quick_preset.addItems([style['name'] for style in PRESETS.values()])
        self.automatic_progress = QLabel('Etapas: planejamento -> imagens -> voz -> legendas -> MP4')
        dashboard = QWidget()
        dashboard_scroll = QScrollArea()
        dashboard_scroll.setWidgetResizable(True)
        dashboard_scroll.setWidget(dashboard)
        self.dashboard_page = dashboard_scroll
        layout = QVBoxLayout(dashboard)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(18)
        for text, style in [('LORE OCULTA / STUDIO', 'eyebrow'), ('Sua próxima história começa aqui.', 'heading'), ('Do roteiro ao Short. Crie, revise e acompanhe cada etapa da produção local.', 'subtitle')]:
            label = QLabel(text)
            label.setObjectName(style)
            label.setWordWrap(True)
            layout.addWidget(label)
        guide = QLabel('01   Escreva e revise o roteiro\n\n02   Escolha as imagens da história\n\n03   Prepare a narração e as legendas\n\n04   Exporte seu Short vertical')
        guide.setText('1. Informe um tema ou cole o roteiro.\n\n2. Importe as imagens da historia.\n\n3. Clique em Gerar Short: voz, legendas e MP4 sao criados automaticamente.')
        guide.setObjectName('card')
        layout.addWidget(guide)
        self.quick_theme.setPlaceholderText('Tema do Short')
        self.quick_script.setMinimumHeight(130)
        self.quick_script.setPlaceholderText('Roteiro (opcional)')
        quick_options = QFormLayout()
        quick_options.addRow('Estilo de edição (opcional)', self.quick_preset)
        voice_hint = QLabel('A voz padrão será usada automaticamente. Altere-a apenas em Configurações, se desejar.')
        voice_hint.setWordWrap(True)
        self.automatic_progress.setWordWrap(True)
        layout.addWidget(self.quick_theme)
        layout.addWidget(self.quick_script)
        layout.addLayout(quick_options)
        layout.addWidget(voice_hint)
        layout.addWidget(self.automatic_progress)
        self.hook_suggestions = QLabel('Gancho: revise o início do roteiro com a IA local, se desejar.')
        self.hook_suggestions.setWordWrap(True)
        layout.addWidget(self.summary)
        self.button(layout, 'Importar imagens', self.import_dashboard_images)
        self.button(layout, 'Gerar Short', self.generate_short).setObjectName('primary')
        self.dashboard_optional_tools = QGroupBox('Ferramentas opcionais')
        self.dashboard_optional_tools.setCheckable(True)
        self.dashboard_optional_tools.setChecked(False)
        dashboard_optional_layout = QVBoxLayout(self.dashboard_optional_tools)
        self.dashboard_optional_body = QWidget()
        dashboard_optional_body_layout = QVBoxLayout(self.dashboard_optional_body)
        dashboard_optional_body_layout.setContentsMargins(0, 0, 0, 0)
        self.button(dashboard_optional_body_layout, 'Analisar gancho com IA local', self.analyze_hook)
        dashboard_optional_body_layout.addWidget(self.hook_suggestions)
        self.button(dashboard_optional_body_layout, 'Novo Short', self.new_project)
        dashboard_optional_layout.addWidget(self.dashboard_optional_body)
        self.dashboard_optional_tools.toggled.connect(self.dashboard_optional_body.setVisible)
        self.dashboard_optional_body.setVisible(False)
        layout.addWidget(self.dashboard_optional_tools)
        layout.addStretch()
        self.tabs.addTab(dashboard_scroll, 'Criar Short')
        self.editor_page = QWidget()
        self.editor(self.editor_page)
        self.ai = AIPanel(self.button, self.ai_plan, self.save_ai_plan)
        self.saved_ai_pack = self.ai.pack()
        self.script.textChanged.connect(lambda: self.ai.update_notice(self.script.toPlainText()))
        library = QWidget()
        layout = QVBoxLayout(library)
        self.items = QListWidget()
        self.items.itemClicked.connect(self.load)
        layout.addWidget(QLabel('Selecione um projeto para retomá-lo.'))
        layout.addWidget(self.items)
        self.tabs.addTab(library, 'Projetos')
        media = QWidget()
        media_scroll = QScrollArea()
        media_scroll.setWidgetResizable(True)
        media_scroll.setWidget(media)
        self.media_page = media_scroll
        layout = QVBoxLayout(media)
        layout.addWidget(QLabel('Envie todas as imagens de uma vez. A montagem automática analisa as mídias locais, relaciona cada uma à narração e cria as cenas; você só revisa se desejar.'))
        self.media = QListWidget()
        self.media.setIconSize(QSize(112, 80))
        self.media.setMaximumHeight(130)
        layout.addWidget(self.media)
        media_actions = QHBoxLayout()
        self.button(media_actions, 'Adicionar imagens em lote', self.import_images)
        self.button(media_actions, 'Remover imagem selecionada', self.remove_image)
        layout.addLayout(media_actions)
        self.editing_preset = QComboBox()
        self.editing_preset.addItems([style['name'] for style in PRESETS.values()])
        self.quick_preset.currentTextChanged.connect(self.editing_preset.setCurrentText)
        self.editing_preset.currentTextChanged.connect(self.quick_preset.setCurrentText)
        self.editing_preset.currentTextChanged.connect(self.apply_preset_defaults)
        style_form = QFormLayout()
        style_form.addRow('Estilo de edição', self.editing_preset)
        layout.addLayout(style_form)
        self.automatic_assembly = QGroupBox('Revisar montagem com IA (opcional)')
        self.automatic_assembly.setCheckable(True)
        self.automatic_assembly.setChecked(False)
        automatic_layout = QVBoxLayout(self.automatic_assembly)
        self.automatic_assembly_body = QWidget()
        automatic_body_layout = QVBoxLayout(self.automatic_assembly_body)
        automatic_layout.addWidget(self.automatic_assembly_body)
        self.automatic_assembly.toggled.connect(self.automatic_assembly_body.setVisible)
        automatic_hint = QLabel(
            'Depois de gerar a narração, use esta ação para a IA analisar as imagens importadas, '
            'associá-las aos trechos do roteiro e montar as cenas. A tabela de tempos só é necessária '
            'se você quiser corrigir algo específico.'
        )
        automatic_hint.setWordWrap(True)
        automatic_body_layout.addWidget(automatic_hint)
        self.button(automatic_body_layout, 'Analisar imagens e montar cenas com IA', self.assemble_scenes_automatically).setObjectName('primary')
        self.automatic_assembly_body.setVisible(False)
        layout.addWidget(self.automatic_assembly)
        self.timeline_notice = QLabel()
        self.timeline_notice.setWordWrap(True)
        self.timeline_notice.setObjectName('warning')
        self.timeline_notice.hide()
        layout.addWidget(self.timeline_notice)
        self.timeline_table = QTableWidget(0, 5)
        self.timeline_table.setHorizontalHeaderLabels(['Cena / imagem', 'Início (s)', 'Fim (s)', 'Narração da cena', 'Imagem / edição'])
        self.timeline_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.timeline_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.timeline_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.timeline_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.timeline_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.timeline_table.setMinimumHeight(180)
        self.timeline_table.setMaximumHeight(240)
        self.manual_timeline_controls = QGroupBox('Ajustar cenas manualmente (opcional)')
        self.manual_timeline_controls.setCheckable(True)
        self.manual_timeline_controls.setChecked(False)
        manual_timeline_layout = QVBoxLayout(self.manual_timeline_controls)
        self.manual_timeline_body = QWidget()
        timeline_layout = QVBoxLayout(self.manual_timeline_body)
        manual_timeline_layout.addWidget(self.manual_timeline_body)
        self.manual_timeline_controls.toggled.connect(self.manual_timeline_body.setVisible)
        timeline_hint = QLabel('Use apenas para corrigir uma cena após a montagem automática. Os tempos e notas não alteram o roteiro principal.')
        timeline_hint.setWordWrap(True)
        timeline_layout.addWidget(timeline_hint)
        timeline_layout.addWidget(self.timeline_table)
        self.manual_timeline_body.setVisible(False)
        layout.addWidget(self.manual_timeline_controls)
        self.music_state = QLabel('Trilha: somente narração. Use apenas música para a qual você tenha direitos.')
        self.music_state.setWordWrap(True)
        layout.addWidget(self.music_state)
        self.caption_state = QLabel('Legendas: serão verificadas depois que a narração for gerada.')
        self.caption_state.setWordWrap(True)
        layout.addWidget(self.caption_state)
        self.visual_state = QLabel('Planejamento visual: roteiro e ordem das imagens.')
        self.visual_state.setWordWrap(True)
        layout.addWidget(self.visual_state)
        self.advanced_media_controls = QGroupBox('Opções avançadas de edição')
        self.cover_simple_controls = QGroupBox('Capa do Short (opcional)')
        cover_simple_layout = QVBoxLayout(self.cover_simple_controls)
        cover_simple_hint = QLabel('Crie uma capa PNG 1080×1920 a partir das imagens importadas. A IA local sugere a composição; você pode personalizar depois.')
        cover_simple_hint.setWordWrap(True)
        cover_simple_layout.addWidget(cover_simple_hint)
        self.button(cover_simple_layout, 'Criar capa automaticamente', self.generate_cover_with_ai).setObjectName('primary')
        self.advanced_media_controls.setCheckable(True)
        self.advanced_media_controls.setChecked(False)
        advanced_media_container = QVBoxLayout(self.advanced_media_controls)
        self.advanced_media_body = QWidget()
        advanced_media_layout = QVBoxLayout(self.advanced_media_body)
        advanced_media_container.addWidget(self.advanced_media_body)
        self.advanced_media_controls.toggled.connect(self.advanced_media_body.setVisible)
        advanced_media_layout.addWidget(self.cover_simple_controls)
        finishing = QFormLayout()
        self.motion = QCheckBox('Zoom suave nas imagens')
        self.transition = QCheckBox('Dissolver entre cenas')
        self.music = QLineEdit()
        self.selected_music_license = ''
        self.music_plan = None
        self.licensed_effects = {}
        self.music.setReadOnly(True)
        self.music.setPlaceholderText('Sem trilha — somente narração')
        self.music_volume = QSpinBox()
        self.music_volume.setRange(0, 100)
        self.music_volume.setValue(25)
        self.music_volume.setSuffix('%')
        self.music_mix_profile = QComboBox()
        self.music_mix_profile.addItem('Discreta', 'discreta')
        self.music_mix_profile.addItem('Equilibrada', 'equilibrada')
        self.music_mix_profile.addItem('Cinematográfica', 'cinematografica')
        self.effects_intensity = QComboBox()
        self.effects_intensity.addItem('Desativados', 'off')
        self.effects_intensity.addItem('Moderados', 'moderada')
        self.effects_intensity.addItem('Intensos', 'intensa')
        self.effects_intensity.setCurrentIndex(self.effects_intensity.findData('moderada'))
        self.effects_state = QLabel('Nenhum efeito licenciado configurado.')
        self.effects_state.setWordWrap(True)
        self.cover_source = QLineEdit()
        self.cover_source.setReadOnly(True)
        self.cover_source.setPlaceholderText('Nenhuma imagem de capa selecionada')
        self.cover_headline = QLineEdit()
        self.cover_headline.setPlaceholderText('Texto principal grande (a IA pode sugerir)')
        self.cover_subheadline = QLineEdit()
        self.cover_subheadline.setPlaceholderText('Texto secundário opcional')
        self.cover_preset = QComboBox()
        for key, name in COVER_PRESETS.items():
            self.cover_preset.addItem(name, key)
        self.cover_text_position = QComboBox()
        self.cover_text_position.addItem('Texto no topo', 'top')
        self.cover_text_position.addItem('Texto no meio', 'middle')
        self.cover_text_position.addItem('Texto na parte inferior', 'bottom')
        self.cover_effect_intensity = QComboBox()
        self.cover_effect_intensity.addItem('Efeitos leves', 'leve')
        self.cover_effect_intensity.addItem('Efeitos moderados', 'moderada')
        self.cover_effect_intensity.addItem('Efeitos intensos', 'intensa')
        self.cover_border = QCheckBox('Moldura vermelha e dourada')
        self.cover_border.setChecked(True)
        self.cover_reason = QLabel('A IA local pode sugerir a composição; você sempre pode revisar antes de gerar.')
        self.cover_reason.setWordWrap(True)
        self.cover_preview = QLabel('A prévia da capa aparecerá aqui.')
        self.cover_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_preview.setMinimumSize(162, 288)
        self.cover_preview.setMaximumSize(216, 384)
        self.cover_path = QLabel('Nenhuma capa gerada.')
        self.cover_path.setWordWrap(True)
        finishing.addRow('Movimento', self.motion)
        finishing.addRow('Transição', self.transition)
        finishing.addRow('Trilha local', self.music)
        finishing.addRow('Volume da trilha', self.music_volume)
        finishing.addRow('Intensidade da trilha', self.music_mix_profile)
        finishing.addRow('Efeitos sonoros', self.effects_intensity)
        finishing.addRow('Biblioteca de efeitos', self.effects_state)
        advanced_media_layout.addLayout(finishing)
        music_actions = QHBoxLayout()
        self.button(music_actions, 'Escolher trilha', self.choose_music)
        self.button(music_actions, 'Selecionar do catálogo licenciado', self.choose_catalog_music)
        self.button(music_actions, 'Sem trilha', self.clear_music)
        advanced_media_layout.addLayout(music_actions)
        media_tools = QHBoxLayout()
        self.button(media_tools, 'Ajustar sincronização por palavra', self.edit_caption_timings)
        self.button(media_tools, 'Ouvir prévia do áudio final', self.preview_final_audio)
        self.button(media_tools, 'Abrir pasta do projeto', self.open_folder)
        self.button(media_tools, 'Exportar somente MP4', self.video)
        advanced_media_layout.addLayout(media_tools)
        effects_actions = QHBoxLayout()
        self.button(effects_actions, 'Selecionar catálogo de efeitos', self.choose_effect_catalog)
        self.button(effects_actions, 'Remover efeitos', self.clear_effects)
        advanced_media_layout.addLayout(effects_actions)
        self.cover_custom_controls = QGroupBox('Personalizar capa (opcional)')
        self.cover_custom_controls.setCheckable(True)
        self.cover_custom_controls.setChecked(False)
        cover_custom_layout = QVBoxLayout(self.cover_custom_controls)
        self.cover_custom_body = QWidget()
        cover_custom_form = QFormLayout(self.cover_custom_body)
        cover_custom_form.addRow('Imagem de capa', self.cover_source)
        cover_custom_form.addRow('Texto principal', self.cover_headline)
        cover_custom_form.addRow('Texto secundário', self.cover_subheadline)
        cover_custom_form.addRow('Estilo', self.cover_preset)
        cover_custom_form.addRow('Posição do texto', self.cover_text_position)
        cover_custom_form.addRow('Intensidade visual', self.cover_effect_intensity)
        cover_custom_form.addRow('Moldura', self.cover_border)
        cover_custom_form.addRow('Decisão da IA', self.cover_reason)
        cover_custom_form.addRow('Prévia', self.cover_preview)
        cover_custom_form.addRow('Arquivo gerado', self.cover_path)
        cover_custom_layout.addWidget(self.cover_custom_body)
        self.cover_custom_controls.toggled.connect(self.cover_custom_body.setVisible)
        self.cover_custom_body.setVisible(False)
        cover_actions = QHBoxLayout()
        self.button(cover_actions, 'Sugerir textos', self.suggest_cover_copy)
        self.button(cover_actions, 'Escolher imagem e gerar', self.choose_cover)
        self.button(cover_actions, 'Atualizar capa', self.regenerate_cover)
        self.button(cover_actions, 'Abrir capa', self.open_cover)
        cover_custom_layout.addLayout(cover_actions)
        advanced_media_layout.addWidget(self.cover_custom_controls)
        self.advanced_media_body.setVisible(False)
        self.button(layout, 'Gerar Short automaticamente', self.generate_short).setObjectName('primary')
        layout.addWidget(self.advanced_media_controls)
        layout.addStretch()
        self.tabs.addTab(media_scroll, 'Mídias')
        feedback = QWidget()
        self.feedback_page = feedback
        feedback_layout = QFormLayout(feedback)
        self.outcome_rating = QComboBox()
        self.outcome_rating.addItem('Positivo', 'positive'); self.outcome_rating.addItem('Neutro', 'neutral'); self.outcome_rating.addItem('A melhorar', 'negative')
        self.outcome_feedback = QPlainTextEdit()
        self.outcome_feedback.setPlaceholderText('O que funcionou ou deve mudar no próximo planejamento local?')
        feedback_layout.addRow('Resultado', self.outcome_rating)
        feedback_layout.addRow('Feedback local', self.outcome_feedback)
        self.button(feedback_layout, 'Salvar aprendizado para próximos planejamentos', self.save_feedback)
        feedback_layout.addRow(QLabel('Este texto fica somente no banco local e é enviado ao Ollama local como contexto editorial, nunca como fonte factual.'))
        self.preview = Preview()
        self.preview_page = self.preview
        preview_actions = QHBoxLayout()
        self.button(preview_actions, 'Ouvir narração', lambda: self.preview_artifact('voz', 'narracao.wav'))
        self.button(preview_actions, 'Assistir ao Short', lambda: self.preview_artifact('video', 'video_final.mp4'))
        self.preview.layout().insertLayout(0, preview_actions)
        self.tabs.addTab(self.preview, 'Prévia')
        config = QWidget()
        config_scroll = QScrollArea()
        config_scroll.setWidgetResizable(True)
        config_scroll.setWidget(config)
        self.config_page = config_scroll
        self.config(config)
        self.tabs.addTab(config_scroll, 'Configurações')
        self.integration_notice = QLabel('\n'.join(optional_component_diagnostics(self.settings)))
        self.integration_notice.setWordWrap(True)
        self.statusBar().addPermanentWidget(self.integration_notice)
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        self.status_label = QLabel('Pronto.')
        self.statusBar().addWidget(self.status_label, 1)
        self.cancel_button = QPushButton('Cancelar operação')
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        self.statusBar().addPermanentWidget(self.cancel_button)
        self.refresh()
        self.setup_help_menu()
        if not self.settings.onboarding_complete:
            self.show_onboarding()

    def apply_preset_defaults(self, value: str):
        """Apply only the optional finishing defaults of a selected preset."""
        style = resolve_preset(value)
        profile = style.get('mix_profile')
        if profile and hasattr(self, 'music_mix_profile'):
            index = self.music_mix_profile.findData(profile)
            if index >= 0:
                self.music_mix_profile.setCurrentIndex(index)
        if hasattr(self, 'music_volume') and 'music_volume' in style:
            self.music_volume.setValue(int(style['music_volume']))
        minimum = style.get('min_images')
        maximum = style.get('max_images')
        if (minimum is not None or maximum is not None) and hasattr(self, 'timeline_notice'):
            count = len(self.current.images) if self.current else 0
            if minimum is not None and count < minimum:
                self.timeline_notice.setText(
                    f'Retenção Máxima usa de {minimum} a {maximum} imagens; há {count}. '
                    'Importe mais imagens ou escolha outro preset antes de gerar.'
                )
                self.timeline_notice.show()
            elif maximum is not None and count > maximum:
                self.timeline_notice.setText(
                    f'Retenção Máxima usa de {minimum} a {maximum} imagens; há {count}. '
                    'Divida o roteiro em dois Shorts ou escolha outro preset antes de gerar.'
                )
                self.timeline_notice.show()
            else:
                self.timeline_notice.hide()

    def setup_help_menu(self):
        menu = self.menuBar().addMenu('Ajuda')
        menu.addAction('Central de ajuda e suporte', self.show_help)
        menu.addAction('Exportar diagnóstico…', self.export_diagnostics)
        menu.addAction('Sobre', self.show_about)

    def show_onboarding(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Boas-vindas ao Lore Oculta Studio')
        layout = QVBoxLayout(dialog)
        label = QLabel('Crie um Short informando um tema ou roteiro, importe imagens próprias ou licenciadas e clique em Gerar Short.\n\nProjetos, configurações e logs ficam somente em:\n' + str(self.paths.root) + '\n\nOllama, ComfyUI e Piper são integrações opcionais locais. Você pode configurar ou ignorá-las sem bloquear a criação de projetos.')
        label.setWordWrap(True)
        layout.addWidget(label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject); buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()
        self.settings.onboarding_complete = True
        self.store.save(self.settings)

    def show_help(self):
        info = product_information()
        documents = '\n'.join(f'{name}: {path or "não distribuído"}' for name, path in info['documents'].items())
        QMessageBox.information(self, 'Ajuda e suporte', f"Versão: {info['version']}\nDados locais: {info['data_path']}\nSuporte: {info['support']}\n\nDocumentos:\n{documents}\n\nIntegrações externas são opcionais e locais.")

    def show_about(self):
        info = product_information()
        QMessageBox.information(self, 'Sobre', f"{info['name']}\nVersão {info['version']}\n\nAplicativo local para criar Shorts. Licença, privacidade e avisos de terceiros estão nos documentos distribuídos.")

    def export_diagnostics(self):
        suggested = self.paths.root / 'diagnostico-lore-oculta.json'
        filename, _ = QFileDialog.getSaveFileName(self, 'Salvar diagnóstico', str(suggested), 'JSON (*.json)')
        if not filename:
            return
        try:
            export_sanitized_diagnostics(Path(filename), self.settings)
            self.status_label.setText('Diagnóstico sanitizado salvo localmente.')
        except OSError as error:
            self.error('Diagnóstico', f'Não foi possível salvar o diagnóstico: {error}')

    def button(self, layout, text, slot):
        button = QPushButton(text)
        button.clicked.connect(slot)
        layout.addWidget(button)
        self.actions.append(button)
        return button

    def editor(self, page):
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        heading = QLabel('Conte a sua história')
        heading.setObjectName('heading')
        layout.addWidget(heading)
        form = QFormLayout()
        self.theme = QLineEdit()
        self.theme.setPlaceholderText('Ex.: A origem de Carrie — mistério em 45 segundos')
        self.category = QComboBox()
        self.category.addItems(['Filmes', 'Séries', 'Games', 'Outros'])
        self.fmt = QComboBox()
        self.fmt.addItems(['Short'])
        self.duration = QSpinBox()
        self.duration.setRange(15, 900)
        self.duration.setValue(45)
        for title, widget in [('Tema', self.theme), ('Categoria', self.category), ('Formato (exportação: Short)', self.fmt), ('Duração alvo em segundos', self.duration)]:
            form.addRow(title, widget)
        layout.addLayout(form)
        self.script = QPlainTextEdit()
        self.script.setMinimumHeight(180)
        self.script.setPlaceholderText('Escreva e revise seu roteiro. A duração final será a duração real da voz.')
        layout.addWidget(self.script)
        self.sources = QPlainTextEdit()
        self.sources.setMaximumHeight(90)
        self.sources.setPlaceholderText('Cole fatos e trechos verificados com suas fontes. URLs sozinhas não são consultadas automaticamente.')
        layout.addWidget(self.sources)
        self.fiction = QCheckBox('Ficção original — permite gerar apenas com o tema, sem tratar o texto como factual')
        layout.addWidget(self.fiction)
        self.button(layout, 'Gerar rascunho com Ollama — revisar antes de narrar', self.ai_script)
        layout.addWidget(QLabel('Ao gerar, o Studio cria narração, planeja cenas, sincroniza legendas e valida o MP4 automaticamente.'))
        self.button(layout, 'Gerar Short automaticamente', self.generate_short).setObjectName('primary')
        self.advanced_creation_controls = QGroupBox('Ações técnicas (opcional)')
        self.advanced_creation_controls.setCheckable(True)
        self.advanced_creation_controls.setChecked(False)
        advanced_creation_container = QVBoxLayout(self.advanced_creation_controls)
        self.advanced_creation_body = QWidget()
        advanced_layout = QHBoxLayout(self.advanced_creation_body)
        advanced_creation_container.addWidget(self.advanced_creation_body)
        self.advanced_creation_controls.toggled.connect(self.advanced_creation_body.setVisible)
        for title, action in [('Salvar rascunho', self.save), ('Gerar voz', self.voice), ('Gerar legendas', self.subtitles), ('Exportar MP4', self.video)]:
            self.button(advanced_layout, title, action)
        self.advanced_creation_body.setVisible(False)
        layout.addWidget(self.advanced_creation_controls)

    def config(self, page):
        layout = QFormLayout(page)
        self.tts_provider = QComboBox(); self.tts_provider.addItem('Kokoro TTS', 'kokoro'); self.tts_provider.addItem('Piper TTS', 'piper')
        self.tts_provider.setCurrentIndex(max(0, self.tts_provider.findData(getattr(self.settings, 'tts_provider', 'kokoro'))))
        self.kokoro_voice = QComboBox()
        for voice in ('pm_alex', 'pm_santa', 'pf_dora'): self.kokoro_voice.addItem(voice, voice)
        self.kokoro_voice.setCurrentIndex(max(0, self.kokoro_voice.findData(getattr(self.settings, 'kokoro_voice', 'pm_alex'))))
        self.piper = QLineEdit(self.settings.piper_executable)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.set_model(self.settings.piper_model)
        self.model.currentIndexChanged.connect(lambda _: setattr(self, 'voice_explicit', self.model_path() != self.settings.piper_model))
        self.model.lineEdit().textEdited.connect(lambda _: setattr(self, 'voice_explicit', True))
        self.ffmpeg = QLineEdit(self.settings.ffmpeg)
        self.ollama_url = QLineEdit(self.settings.ollama_url)
        self.ollama = QComboBox()
        self.ollama.setEditable(True)
        self.ollama.setCurrentText(self.settings.ollama_model)
        self.visual_analysis_enabled = QCheckBox('Usar análise visual local para relacionar imagens à narração')
        self.visual_analysis_enabled.setChecked(bool(getattr(self.settings, 'visual_analysis_enabled', False)))
        self.visual_ollama_model = QLineEdit(getattr(self.settings, 'visual_ollama_model', ''))
        self.visual_ollama_model.setPlaceholderText('Opcional: nome de um modelo visual instalado no Ollama')
        self.comfyui_url = QLineEdit(self.settings.comfyui_url)
        layout.addRow('Servidor Ollama local', self.ollama_url)
        layout.addRow('Modelo Ollama local', self.ollama)
        layout.addRow(self.visual_analysis_enabled)
        layout.addRow('Modelo visual local (opcional)', self.visual_ollama_model)
        layout.addRow('Servidor ComfyUI local', self.comfyui_url)
        comfyui_note = QLabel('Imagens: perfil local SD 1.5 vertical conservador (512×896, 20 passos). Instale o checkpoint licenciado v1-5-pruned-emaonly.safetensors no ComfyUI antes do primeiro uso.')
        comfyui_note.setWordWrap(True)
        layout.addRow(comfyui_note)
        layout.addRow('Motor de voz', self.tts_provider)
        layout.addRow('Voz Kokoro PT-BR', self.kokoro_voice)
        self.button(layout, 'Testar voz', self.test_voice)
        self.ollama_status = QLabel('Conexão ainda não verificada. Inicie o Ollama e consulte os modelos instalados.')
        self.ollama_status.setWordWrap(True)
        self.ollama_url.textChanged.connect(self.invalidate_ollama_connection)
        layout.addRow(self.ollama_status)
        self.button(layout, 'Verificar conexão e listar modelos', self.check_ollama)
        for title, field in [('Piper executável', self.piper), ('Voz Piper local (PT-BR)', self.model), ('FFmpeg executável ou comando no PATH', self.ffmpeg)]:
            layout.addRow(title, field)
        self.narration_speed = QDoubleSpinBox(); self.narration_speed.setRange(.6, 1.5); self.narration_speed.setSingleStep(.05); self.narration_speed.setValue(self.settings.narration_speed)
        self.sentence_pause = QDoubleSpinBox(); self.sentence_pause.setRange(0, 2); self.sentence_pause.setSingleStep(.1); self.sentence_pause.setValue(self.settings.sentence_pause); self.sentence_pause.setSuffix(' s')
        self.asr_model = QLineEdit(self.settings.asr_model)
        self.pronunciations = QPlainTextEdit(json.dumps(self.settings.pronunciation_dictionary, ensure_ascii=False, indent=2)); self.pronunciations.setMaximumHeight(110); self.pronunciations.setPlaceholderText('{"Hal Jordan": "Rél Djórdan"}')
        layout.addRow('Velocidade da narração', self.narration_speed)
        layout.addRow('Pausa após frase', self.sentence_pause)
        layout.addRow('Modelo ASR local (faster-whisper)', self.asr_model)
        layout.addRow('Dicionário fonético (JSON)', self.pronunciations)
        narration_note = QLabel('O roteiro salvo nunca é alterado pela narração. Este dicionário afeta somente o áudio e as legendas gerados.')
        narration_note.setWordWrap(True)
        layout.addRow(narration_note)
        layout.addRow(QLabel('Piper, voz e FFmpeg são detectados automaticamente. Altere estes campos apenas para recursos personalizados.'))
        self.button(layout, 'Salvar configurações avançadas', self.save_settings)

    def tool_settings(self):
        try:
            pronunciations = json.loads(self.pronunciations.toPlainText() or '{}')
        except json.JSONDecodeError as error:
            raise ValueError('Dicionário fonético deve ser um JSON válido, por exemplo {"Nome": "Pronúncia"}.') from error
        if not isinstance(pronunciations, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in pronunciations.items()):
            raise ValueError('Dicionário fonético deve conter pares texto/pronúncia.')
        return dict(tts_provider=self.tts_provider.currentData(), kokoro_python=self.settings.kokoro_python, kokoro_voice=self.kokoro_voice.currentData(), piper_executable=self.piper.text().strip(), piper_model=self.model_path(), voice_selected_explicitly=self.voice_explicit or self.model_path() != self.settings.piper_model, ffmpeg=self.ffmpeg.text().strip(), ollama_model=self.ollama.currentText().strip(), ollama_url=self.ollama_url.text().strip(), visual_analysis_enabled=self.visual_analysis_enabled.isChecked(), visual_ollama_model=self.visual_ollama_model.text().strip(), comfyui_url=self.comfyui_url.text().strip(), pronunciation_dictionary=pronunciations, narration_speed=self.narration_speed.value(), sentence_pause=self.sentence_pause.value(), asr_model=self.asr_model.text().strip() or 'small')

    def model_path(self):
        return str(self.model.currentData() or self.model.currentText()).strip()

    def set_model(self, value):
        index = self.model.findData(value)
        if index >= 0:
            self.model.setCurrentIndex(index)
        else:
            self.model.setCurrentText(value)

    def load_timeline_table(self):
        """Show project-owned scene timing without changing the saved script."""
        self.timeline_table.blockSignals(True)
        self.timeline_table.setRowCount(0)
        if not self.current or not self.current.images:
            self.timeline_table.blockSignals(False)
            return
        current = self.current.settings.get('timeline') or {}
        existing = current.get('clips', []) if isinstance(current, dict) else []
        by_source = {clip.get('source'): clip for clip in existing if isinstance(clip, dict)}
        director = current.get('director') if isinstance(current, dict) else None
        # Show the actual render order when a local vision model chose the
        # semantic sequence. Without that explicit provenance, import order is
        # still authoritative.
        sources = ([clip.get('source') for clip in existing]
                   if isinstance(director, dict) and director.get('visual_ordering') is True
                   else list(self.current.images))
        total = float(self.current.audio_duration or self.current.duration or len(self.current.images))
        part = total / len(self.current.images)
        for row, source in enumerate(sources):
            clip = by_source.get(source, {})
            self.timeline_table.insertRow(row)
            name = QTableWidgetItem(f'{row + 1:02d} · {Path(source).name}')
            name.setData(Qt.ItemDataRole.UserRole, source)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.timeline_table.setItem(row, 0, name)
            self.timeline_table.setItem(row, 1, QTableWidgetItem(f"{float(clip.get('start', row * part)):.2f}"))
            self.timeline_table.setItem(row, 2, QTableWidgetItem(f"{float(clip.get('end', (row + 1) * part)):.2f}"))
            self.timeline_table.setItem(row, 3, QTableWidgetItem(str(clip.get('narration_text', ''))))
            self.timeline_table.setItem(row, 4, QTableWidgetItem(str(clip.get('editing_direction', ''))))
        self.timeline_table.blockSignals(False)

    def timeline_from_table(self, settings):
        """Validate manual scene ranges before persisting them in timeline/v1."""
        if not self.current or not self.current.images or self.timeline_table.rowCount() != len(self.current.images):
            return None
        previous = 0.0
        old = self.current.settings.get('timeline') or {}
        old_by_source = {clip.get('source'): clip for clip in old.get('clips', []) if isinstance(clip, dict)}
        style = resolve_preset(settings.get('editing_preset'))
        clips = []
        for row in range(self.timeline_table.rowCount()):
            source = self.timeline_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            try:
                start = float(self.timeline_table.item(row, 1).text().replace(',', '.'))
                end = float(self.timeline_table.item(row, 2).text().replace(',', '.'))
            except (AttributeError, ValueError) as error:
                raise ValueError(f'Cena {row + 1}: informe inicio e fim em segundos.') from error
            if start < previous or end <= start:
                raise ValueError(f'Cena {row + 1}: os tempos devem ser continuos e o fim maior que o inicio.')
            prior = old_by_source.get(source, {})
            narration = (self.timeline_table.item(row, 3).text() if self.timeline_table.item(row, 3) else '').strip()
            direction = (self.timeline_table.item(row, 4).text() if self.timeline_table.item(row, 4) else '').strip()
            clip = {'source': source, 'start': start, 'end': end,
                    'motion': prior.get('motion', style['motion']),
                    'transition': prior.get('transition', style['transition'] if row else 'cut'),
                    'allow_crop': bool(prior.get('allow_crop', False))}
            if narration:
                clip['narration_text'] = narration
            if direction:
                clip['editing_direction'] = direction
            clips.append(clip)
            previous = end
        timeline = {'schema': 'timeline/v1', 'timing_mode': 'manual', 'fps': int(settings.get('video_fps', 30)),
                                  'preset': str(settings.get('editing_preset', 'neutral')).lower(), 'clips': clips,
                                  'subtitles': {'srt': 'legendas.srt', 'ass': 'legendas.ass'},
                                  'audio_events': list(old.get('audio_events', [])), 'dependencies': {}}
        # The table displays times to centiseconds.  A save without an actual
        # table edit must retain the precise generated plan and its hash; a
        # user edit intentionally turns it into an ordinary manual timeline.
        if isinstance(old.get('director'), dict):
            old_clips = old.get('clips', [])
            unchanged = len(old_clips) == len(clips) and all(
                old_clip.get('source') == clip.get('source')
                and round(float(old_clip.get('start', -1)), 2) == round(float(clip['start']), 2)
                and round(float(old_clip.get('end', -1)), 2) == round(float(clip['end']), 2)
                and old_clip.get('narration_text', '') == clip.get('narration_text', '')
                and old_clip.get('editing_direction', '') == clip.get('editing_direction', '')
                for old_clip, clip in zip(old_clips, clips))
            if unchanged:
                return old
        return validate_timeline(timeline)

    def assemble_scenes_automatically(self):
        """Prepare narration when needed, then create an AI scene plan.

        This is the review-first counterpart to ``Gerar Short``: it lets the
        user upload all media at once and inspect the proposed scene order
        before asking FFmpeg to export a complete video.
        """
        try:
            if self.workers:
                return
            project = self.project()
            if not project.script.strip() or not project.images:
                raise ValueError('Escreva um roteiro e importe todas as imagens antes de montar as cenas.')
            if project.audio_duration and project.audio_duration > 0 and project.statuses.get('voz') == StepStatus.DONE:
                self.generate_editorial_plan()
                return
            settings = dict(project.settings)

            def build_plan(cancel):
                self.pipeline.execute(project, 'voz', settings, cancel)
                return self.pipeline._plan_editorial_automatically(project, settings, cancel)

            worker = JobWorker(build_plan)
            self.workers.append(worker)

            def assembled(created):
                self.current = self.repo.get(project.id) or project
                self.refresh()
                if created:
                    self.status_label.setText('Cenas montadas automaticamente. Revise apenas se quiser ajustar algo.')
                else:
                    self.status_label.setText('Narração pronta. O plano de IA não ficou disponível; Gerar Short usará o preset seguro.')

            worker.succeeded.connect(assembled)
            worker.failed.connect(self.failed)
            worker.finished.connect(lambda: self.finished(worker))
            self.set_busy(True)
            self.status_label.setText('Gerando narração e analisando as imagens para montar as cenas…')
            worker.start()
        except Exception as error:
            self.error('Montar cenas automaticamente', error)

    def generate_editorial_plan(self):
        try:
            if self.workers: return
            project = self.project()
            if not project.script.strip() or not project.images:
                raise ValueError('Escreva um roteiro e importe imagens antes de gerar o plano editorial.')
            duration = float(project.audio_duration or 0)
            if duration <= 0: raise ValueError('Gere a narração primeiro para o plano respeitar sua duração real.')
            if project.settings.get('timeline') and QMessageBox.question(self, 'Substituir plano editorial?', 'Uma resposta válida substituirá a timeline atual. Continuar?') != QMessageBox.StandardButton.Yes:
                return
            provider = self.ollama_provider()
            preset = project.settings.get('editing_preset')
            request_hash = stable_input_hash(project.script, project.images, duration, preset)
            def create_plan():
                visual = {}
                context = ''
                if project.settings.get('visual_analysis_enabled'):
                    model = str(project.settings.get('visual_ollama_model', '')).strip()
                    if not model:
                        visual = {'status': 'unavailable', 'reason': 'Nenhum modelo visual local foi configurado.'}
                    else:
                        try:
                            vision = OllamaProvider(model, project.settings.get('ollama_url', self.ollama_url.text()))
                            vision.cancel = provider.cancel
                            notes = describe_imported_images(vision, self.service.artifact_dir(project), project.images)
                            context = editorial_context(notes)
                            visual = {'status': 'completed', 'version': VISION_DIRECTOR_VERSION, 'model': model, 'notes': notes}
                        except Exception as error:
                            if provider.cancel is not None and provider.cancel.is_set():
                                raise
                            visual = {'status': 'unavailable', 'model': model, 'reason': str(error)}
                visual_ordering = visual.get('status') == 'completed' and bool(context)
                request = editorial_prompt(project.script, project.images, duration, preset, context, visual_ordering)
                return provider.complete_structured(request, response_schema()), visual
            self.start_ai_job(create_plan,
                              lambda result: self.accept_editorial_plan(project.id, result[0], duration, provider.model, request_hash, result[1]),
                              'Gerando plano editorial local', provider)
        except Exception as error:
            self.error('Plano editorial', error)

    def accept_editorial_plan(self, project_id, response, duration, model, request_hash, visual_analysis=None):
        project = self.repo.get(project_id)
        current_hash = stable_input_hash(project.script, project.images, float(project.audio_duration or 0),
                                         project.settings.get('editing_preset'))
        if current_hash != request_hash:
            raise ValueError('O projeto mudou durante o planejamento editorial; o resultado não foi salvo.')
        timeline = timeline_from_response(
            response, script=project.script, sources=project.images, duration=duration,
            model=model, preset=project.settings.get('editing_preset'),
            fps=int(project.settings.get('video_fps', 30)),
            visual_ordering=bool(isinstance(visual_analysis, dict)
                                 and visual_analysis.get('status') == 'completed'
                                 and visual_analysis.get('notes')),
        )
        validate_director_timeline(timeline, project.images, duration)
        project.settings['timeline'] = timeline
        if isinstance(visual_analysis, dict) and visual_analysis:
            project.settings['visual_analysis'] = visual_analysis
        invalidate_derived(project, {'timeline'})
        self.current = self.repo.save(project)
        self.refresh()
        self.status_label.setText('Plano editorial local salvo. Revise os tempos e notas antes de exportar.')

    def refresh(self):
        self.items.clear()
        projects = self.repo.list()
        for project in projects:
            statuses = ' · '.join(f'{step}: {str(status).capitalize()}' for step, status in project.statuses.items()) or 'Pronto para começar'
            item = QListWidgetItem(f'{project.theme}\n{project.format} · {project.duration}s planejados\n{statuses}')
            item.setData(Qt.ItemDataRole.UserRole, project.id)
            self.items.addItem(item)
        self.summary.setText(f'{len(projects)} projeto(s) salvo(s) localmente.')
        self.media.clear()
        if self.current:
            for index, name in enumerate(self.current.images, 1):
                item = QListWidgetItem(f'Cena {index:02d}  ·  {Path(name).name}')
                item.setToolTip(name)
                item.setData(Qt.ItemDataRole.UserRole, name)
                try:
                    item.setIcon(QIcon(str(self.service.artifact(self.current, name))))
                except ValueError:
                    item.setText(item.text() + ' — arquivo indisponível')
                self.media.addItem(item)
            self.history.setPlainText('\n'.join(f"{row['time']} | {row['step']} | {row['status']} | {row['message']}" for row in self.current.history))
            self.cover_source.setText(self.current.settings.get('cover_source', ''))
            self.cover_headline.setText(self.current.settings.get('cover_headline', self.current.theme))
            self.cover_subheadline.setText(self.current.settings.get('cover_subheadline', ''))
            self.cover_preset.setCurrentIndex(max(0, self.cover_preset.findData(self.current.settings.get('cover_preset', 'cinematico'))))
            self.cover_text_position.setCurrentIndex(max(0, self.cover_text_position.findData(self.current.settings.get('cover_text_position', 'top'))))
            self.cover_effect_intensity.setCurrentIndex(max(0, self.cover_effect_intensity.findData(self.current.settings.get('cover_effect_intensity', 'moderada'))))
            self.cover_border.setChecked(bool(self.current.settings.get('cover_border', True)))
            plan = self.current.settings.get('cover_plan') or {}
            self.cover_reason.setText(str(plan.get('reason') or 'A imagem e os textos podem ser ajustados antes de gerar.'))
            warning = self.current.settings.get('timeline_duration_warning')
            if isinstance(warning, dict) and warning.get('message'):
                self.timeline_notice.setText(str(warning['message']))
                self.timeline_notice.show()
            else:
                style = resolve_preset(self.current.settings.get('editing_preset'))
                minimum = style.get('min_images')
                maximum = style.get('max_images')
                image_count = len(self.current.images)
                if minimum is not None and image_count < minimum:
                    self.timeline_notice.setText(
                        f'Retenção Máxima usa de {minimum} a {maximum} imagens; há {image_count}. '
                        'Importe mais imagens ou escolha outro estilo de edição.'
                    )
                    self.timeline_notice.show()
                elif maximum is not None and image_count > maximum:
                    self.timeline_notice.setText(
                        f'Retenção Máxima usa de {minimum} a {maximum} imagens; há {image_count}. '
                        'Divida o roteiro em dois Shorts ou escolha outro estilo de edição.'
                    )
                    self.timeline_notice.show()
                else:
                    self.timeline_notice.hide()
            cover = self.service.existing_cover_path(self.current)
            self.cover_path.setText(str(cover) if cover.is_file() else 'Nenhuma capa gerada.')
            if cover.is_file():
                pixmap = QPixmap(str(cover))
                self.cover_preview.setPixmap(pixmap.scaled(216, 384, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            else:
                self.cover_preview.setPixmap(QPixmap())
                self.cover_preview.setText('A prévia da capa aparecerá aqui.')
            plan = self.current.settings.get('video_music_plan') or {}
            if self.current.settings.get('video_music'):
                mode = 'batidas detectadas' if plan.get('beat_detection') == 'detected' else 'cortes uniformes seguros'
                self.music_state.setText(f'Trilha automática pronta: {mode}. Use apenas música para a qual você tenha direitos.')
            else:
                self.music_state.setText('Trilha: somente narração. Use apenas música para a qual você tenha direitos.')
            effects = self.current.settings.get('licensed_effects') or {}
            if isinstance(effects, dict) and effects:
                names = ', '.join(sorted(effects))
                self.effects_state.setText(f'Efeitos licenciados prontos: {names}. Eles entram apenas quando a timeline usar o evento correspondente.')
            else:
                self.effects_state.setText('Nenhum efeito licenciado configurado.')
            caption_timing = self.current.settings.get('caption_timing')
            if caption_timing == 'word':
                self.caption_state.setText('Legendas: sincronização por palavra confirmada; o destaque amarelo será aplicado na exportação.')
            else:
                self.caption_state.setText('Legendas: gere a narração para criar a sincronização obrigatória por palavra.')
            visual = self.current.settings.get('visual_analysis') or {}
            if visual.get('status') == 'completed':
                timeline = self.current.settings.get('timeline') or {}
                director = timeline.get('director') if isinstance(timeline, dict) else {}
                if isinstance(director, dict) and director.get('visual_ordering') is True:
                    outcome = 'A IA associou e organizou as imagens pelos trechos da narração.'
                else:
                    outcome = 'As imagens foram analisadas; a ordem importada foi mantida neste plano.'
                self.visual_state.setText(
                    f"Planejamento visual local: {len(visual.get('notes', []))} imagem(ns) analisada(s) por "
                    f"{visual.get('model', 'modelo local')}. {outcome}"
                )
            elif visual.get('status') == 'unavailable':
                self.visual_state.setText('Planejamento visual: indisponível; a edição usa roteiro, ordem das imagens e narração real.')
            else:
                self.visual_state.setText('Planejamento visual: sem modelo visual, a ordem importada é preservada. Com modelo local, a IA pode relacionar cada imagem ao trecho mais adequado.')
        else:
            self.history.clear()
            self.cover_source.clear()
            self.cover_headline.clear()
            self.cover_subheadline.clear()
            self.cover_preset.setCurrentIndex(0)
            self.cover_text_position.setCurrentIndex(0)
            self.cover_effect_intensity.setCurrentIndex(self.cover_effect_intensity.findData('moderada'))
            self.cover_border.setChecked(True)
            self.cover_reason.setText('A IA local pode sugerir a composição; você sempre pode revisar antes de gerar.')
            self.cover_preview.setPixmap(QPixmap())
            self.cover_preview.setText('A prévia da capa aparecerá aqui.')
            self.cover_path.setText('Nenhuma capa gerada.')
            self.music_state.setText('Trilha: somente narração. Use apenas música para a qual você tenha direitos.')
            self.effects_state.setText('Nenhum efeito licenciado configurado.')
            self.caption_state.setText('Legendas: gere a narração para criar a sincronização obrigatória por palavra.')
            self.visual_state.setText('Planejamento visual: roteiro e ordem das imagens.')

        self.load_timeline_table()

    def new_project(self):
        if not self.confirm_ai_edits():
            return
        self.preview.clear()
        self.current = None
        self.theme.clear()
        self.script.clear()
        self.quick_theme.clear()
        self.quick_script.clear()
        self.sources.clear()
        self.fiction.setChecked(False)
        self.ai.set_pack({})
        self.ai.count.setValue(5)
        self.ai.style.setText('cinematográfico sombrio, paleta consistente')
        self.ai.update_notice('')
        self.saved_ai_pack = self.ai.pack()
        self.motion.setChecked(False)
        self.transition.setChecked(False)
        self.music.clear()
        self.selected_music_license = ''
        self.music_plan = None
        self.licensed_effects = {}
        self.music_volume.setValue(25)
        self.outcome_feedback.clear()
        self.outcome_rating.setCurrentIndex(0)
        self.duration.setValue(45)
        self.fmt.setCurrentText('Short')
        self.refresh()
        self.tabs.setCurrentWidget(self.dashboard_page)

    def load(self, item):
        try:
            project_id = item.data(Qt.ItemDataRole.UserRole)
            if not self.confirm_ai_edits():
                return
            self.preview.clear()
            self.current = self.service.reopen(project_id)
            project = self.current
            self.theme.setText(project.theme)
            self.category.setCurrentText(project.category)
            self.fmt.setCurrentText(project.format)
            self.duration.setValue(project.duration)
            self.script.setPlainText(project.script)
            self.quick_theme.setText(project.theme)
            self.quick_script.setPlainText(project.script)
            project_settings = dict(project.settings)
            if not project_settings.get('voice_selected_explicitly', False):
                project_settings.pop('piper_model', None)
                project_settings.pop('piper_executable', None)
            settings = resolved_mapping(asdict(self.settings) | project_settings)
            self.piper.setText(settings['piper_executable'])
            self.tts_provider.setCurrentIndex(max(0, self.tts_provider.findData(settings.get('tts_provider', 'piper'))))
            self.kokoro_voice.setCurrentIndex(max(0, self.kokoro_voice.findData(settings.get('kokoro_voice', 'pm_alex'))))
            self.set_model(settings['piper_model'])
            self.voice_explicit = bool(project.settings.get('voice_selected_explicitly', False))
            self.ffmpeg.setText(settings['ffmpeg'])
            self.ollama.setCurrentText(settings['ollama_model'])
            self.ollama_url.setText(settings['ollama_url'])
            self.visual_analysis_enabled.setChecked(bool(settings.get('visual_analysis_enabled', False)))
            self.visual_ollama_model.setText(settings.get('visual_ollama_model', ''))
            self.comfyui_url.setText(settings.get('comfyui_url', ''))
            self.narration_speed.setValue(settings.get('narration_speed', 1.0))
            self.sentence_pause.setValue(settings.get('sentence_pause', .3))
            self.asr_model.setText(settings.get('asr_model', 'small'))
            self.pronunciations.setPlainText(json.dumps(settings.get('pronunciation_dictionary', {}), ensure_ascii=False, indent=2))
            self.sources.setPlainText(project.settings.get('research_notes', ''))
            self.fiction.setChecked(project.settings.get('ai_fiction', False))
            self.ai.set_pack(project.settings.get('ai_pack', {}), project.settings.get('ai_source_script', ''))
            self.ai.count.setValue(project.settings.get('ai_scene_count', 5))
            self.ai.style.setText(project.settings.get('ai_style', 'cinematográfico sombrio, paleta consistente'))
            self.ai.update_notice(project.script)
            self.saved_ai_pack = self.ai.pack()
            self.motion.setChecked(project.settings.get('video_motion', False))
            self.transition.setChecked(project.settings.get('video_transition', False))
            self.music.setText(project.settings.get('video_music', ''))
            self.editing_preset.setCurrentIndex(max(0, self.editing_preset.findText(project.settings.get('editing_preset', 'neutral').capitalize())))
            self.selected_music_license = project.settings.get('video_music_license', '')
            self.music_plan = project.settings.get('video_music_plan')
            saved_effects = project.settings.get('licensed_effects', {})
            self.licensed_effects = dict(saved_effects) if isinstance(saved_effects, dict) else {}
            self.music_volume.setValue(project.settings.get('video_music_volume', 25))
            profile = project.settings.get('music_mix_profile', '')
            self.music_mix_profile.setCurrentIndex(max(0, self.music_mix_profile.findData(profile or 'equilibrada')))
            self.effects_intensity.setCurrentIndex(max(0, self.effects_intensity.findData(project.settings.get('effects_intensity', 'moderada'))))
            outcome = self.repo.outcome(project.id)
            self.outcome_rating.setCurrentIndex(max(0, self.outcome_rating.findData(outcome['rating'] if outcome else 'positive')))
            self.outcome_feedback.setPlainText(outcome['feedback'] if outcome else '')
            self.refresh()
            self.quick_theme.setText(project.theme)
            self.quick_script.setPlainText(project.script)
            self.tabs.setCurrentWidget(self.dashboard_page)
            self.status_label.setText(f'Projeto retomado: {project.theme}. Consulte os estados e o histórico.')
        except Exception as error:
            self.error('Retomar projeto', error)

    def project(self):
        values = dict(theme=self.theme.text(), category=self.category.currentText(), format=self.fmt.currentText(), duration=self.duration.value(), script=self.script.toPlainText())
        candidate = replace(self.current, **values, statuses=dict(self.current.statuses), history=list(self.current.history)) if self.current else Project(id=None, slug='', **values)
        candidate.settings = dict(self.current.settings) if self.current else {}
        candidate.settings.update(self.tool_settings())
        candidate.settings['research_notes'] = self.sources.toPlainText()
        candidate.settings.update(ai_fiction=self.fiction.isChecked(), ai_pack=self.ai.pack(),
                                  ai_source_script=self.ai.source_script, ai_scene_count=self.ai.count.value(), ai_style=self.ai.style.text())
        finishing = dict(video_motion=self.motion.isChecked(), video_transition=self.transition.isChecked(),
                         video_music=self.music.text(), video_music_volume=self.music_volume.value(), video_music_license=self.selected_music_license,
                         video_music_plan=self.music_plan, editing_preset=self.editing_preset.currentText().lower(),
                         music_mix_profile=self.music_mix_profile.currentData(), effects_intensity=self.effects_intensity.currentData(),
                         licensed_effects=dict(self.licensed_effects))
        candidate.settings.update(finishing)
        timeline = self.timeline_from_table(candidate.settings)
        if timeline:
            candidate.settings['timeline'] = timeline
        defaults = dict(video_motion=False, video_transition=False, video_music='', video_music_volume=25, video_music_license='', video_music_plan=None, editing_preset='neutral', music_mix_profile='equilibrada', effects_intensity='moderada', licensed_effects={})
        if self.current and any(value != self.current.settings.get(key, defaults[key]) for key, value in finishing.items()):
            candidate.statuses['video'] = StepStatus.PENDING
        if self.current and timeline and timeline != self.current.settings.get('timeline'):
            candidate.statuses['video'] = StepStatus.PENDING
        if self.current and candidate.script != self.current.script:
            for step in ('voz', 'legendas', 'video'):
                if step in candidate.statuses:
                    candidate.statuses[step] = StepStatus.PENDING
            candidate.audio_duration = None
        voice_inputs = ('tts_provider', 'kokoro_voice', 'piper_model', 'piper_executable', 'pronunciation_dictionary', 'narration_speed', 'sentence_pause')
        old_voice = self.current.settings.get('piper_model') if self.current and self.current.settings.get('voice_selected_explicitly') else self.settings.piper_model
        if self.current and (candidate.settings['piper_model'] != old_voice or any(candidate.settings[key] != self.current.settings.get(key, asdict(self.settings).get(key)) for key in voice_inputs)):
            for step in ('voz', 'legendas', 'video'):
                if step in candidate.statuses:
                    candidate.statuses[step] = StepStatus.PENDING
            candidate.audio_duration = None
        if self.current and candidate.settings['asr_model'] != self.current.settings.get('asr_model', asdict(self.settings)['asr_model']):
            for step in ('legendas', 'video'):
                if step in candidate.statuses:
                    candidate.statuses[step] = StepStatus.PENDING
        if self.current and candidate.format != self.current.format and 'video' in candidate.statuses:
            candidate.statuses['video'] = StepStatus.PENDING
        self.current = self.repo.save(candidate)
        self.saved_ai_pack = self.ai.pack()
        self.refresh()
        return self.current

    def save(self):
        try:
            self.project()
            self.status_label.setText('Projeto salvo.')
        except Exception as error:
            self.error('Salvar projeto', error)

    def save_settings(self):
        try:
            global_values = self.tool_settings()
            # Esta marca controla herança por projeto; não pertence ao arquivo global Settings.
            global_values.pop('voice_selected_explicitly', None)
            self.settings = replace(self.settings, **global_values)
            self.store.save(self.settings)
            if self.current:
                self.project()
            self.status_label.setText('Configurações salvas localmente.')
        except Exception as error:
            self.error('Configurações', error)

    def import_images(self):
        try:
            files, _ = QFileDialog.getOpenFileNames(self, 'Imagens próprias/licenciadas', '', 'Imagens (*.png *.jpg *.jpeg *.jfif *.webp)')
            if files:
                self.service.add_images(self.project(), files)
                self.refresh()
        except Exception as error:
            self.refresh()
            self.error('Importação', error)

    def import_dashboard_images(self):
        if not self.quick_theme.text().strip() and not self.quick_script.toPlainText().strip():
            self.status_label.setText('Informe um tema ou cole o roteiro antes de importar as imagens.')
            return
        self.theme.setText(self.quick_theme.text())
        self.script.setPlainText(self.quick_script.toPlainText())
        if not self.theme.text().strip():
            first_line = next((line.strip() for line in self.script.toPlainText().splitlines() if line.strip()), '')
            self.theme.setText(first_line[:80] or 'Short personalizado')
        try:
            self.project()
            self.tabs.setCurrentWidget(self.media_page)
            self.import_images()
        except Exception as error:
            self.error('Importar imagens', error)

    def remove_image(self):
        try:
            selected = self.media.currentItem()
            source = selected.data(Qt.ItemDataRole.UserRole) if selected else None
            if not source and self.timeline_table.currentRow() >= 0:
                cell = self.timeline_table.item(self.timeline_table.currentRow(), 0)
                source = cell.data(Qt.ItemDataRole.UserRole) if cell else None
            if not self.current:
                raise ValueError('Abra ou crie um projeto antes de remover uma imagem.')
            if not source or source not in self.current.images:
                raise ValueError('Selecione uma imagem na lista ou uma cena na timeline para remove-la.')
            index = self.current.images.index(source)
            timeline = self.current.settings.get('timeline')
            if isinstance(timeline, dict):
                timeline = dict(timeline)
                timeline['clips'] = [clip for clip in timeline.get('clips', []) if clip.get('source') != source]
                self.current.settings['timeline'] = timeline
            if self.current and index >= 0:
                self.current.images.pop(index)
                self.current.statuses['video'] = StepStatus.PENDING
                self.service.set_status(self.current, 'imagens', StepStatus.DONE if self.current.images else StepStatus.PENDING, 'Imagem removida da sequência; arquivo preservado.')
                self.refresh()
        except Exception as error:
            self.error('Mídias', error)

    def open_folder(self):
        if self.current:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.service.artifact_dir(self.current))))

    def choose_music(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Trilha própria ou licenciada', '', 'Áudio (*.wav *.mp3 *.flac *.ogg *.m4a)')
        if filename:
            try:
                if not self.current:
                    raise ValueError('Salve o projeto e gere a narração antes de adicionar uma trilha.')
                previous = self.current.settings.get('video_music', '')
                internal, plan = copy_music(self.service, self.current, filename, self.ffmpeg.text())
                self.music.setText(internal)
                self.music_plan = plan
                self.selected_music_license = ''
                self.project()
                self.remove_owned_music(previous)
                self.status_label.setText('Trilha adicionada. Mixagem automática e cortes foram preparados localmente.')
            except Exception as error:
                self.error('Trilha sonora', error)

    def clear_music(self):
        previous = self.current.settings.get('video_music', '') if self.current else ''
        self.music.clear()
        self.selected_music_license = ''
        self.music_plan = None
        if self.current:
            try:
                self.project()
                self.remove_owned_music(previous)
                self.status_label.setText('Trilha removida; apenas o vídeo será reexportado.')
            except Exception as error:
                self.error('Trilha sonora', error)

    def remove_owned_music(self, relative):
        """Delete only a prior internally-copied track after settings persisted."""
        if not relative or not self.current:
            return
        try:
            track = self.service.artifact(self.current, relative)
            owned_dir = self.service.artifact_dir(self.current) / 'trilha'
            if track.parent == owned_dir:
                track.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    def choose_effect_catalog(self):
        try:
            if not self.current:
                raise ValueError('Salve ou abra um projeto antes de adicionar efeitos.')
            manifest, _ = QFileDialog.getOpenFileName(self, 'Catálogo local de efeitos licenciados', '', 'JSON (*.json)')
            if not manifest:
                return
            catalog = load_effect_catalog(manifest)
            previous = dict(self.current.settings.get('licensed_effects') or {})
            copied = copy_effect_catalog(self.service, self.current, catalog, self.ffmpeg.text())
            self.licensed_effects = copied
            try:
                self.project()
            except Exception:
                self.licensed_effects = previous
                self.remove_owned_effects(copied)
                raise
            self.remove_owned_effects(previous, keep={record['path'] for record in copied.values()})
            self.status_label.setText('Efeitos licenciados adicionados. A timeline os aplicará somente aos eventos compatíveis.')
        except Exception as error:
            self.error('Efeitos sonoros', error)

    def clear_effects(self):
        if not self.current:
            return
        previous = dict(self.current.settings.get('licensed_effects') or {})
        try:
            self.licensed_effects = {}
            self.project()
            self.remove_owned_effects(previous)
            self.status_label.setText('Efeitos licenciados removidos; o próximo MP4 será renderizado sem eles.')
        except Exception as error:
            self.licensed_effects = previous
            self.error('Efeitos sonoros', error)

    def remove_owned_effects(self, effects, keep=None):
        """Remove only previous project-owned effect files after settings persist."""
        if not self.current or not isinstance(effects, dict):
            return
        keep = set(keep or ())
        try:
            owned_dir = self.service.artifact_dir(self.current) / 'efeitos'
            for record in effects.values():
                relative = record.get('path') if isinstance(record, dict) else None
                if not relative or relative in keep:
                    continue
                effect = self.service.artifact(self.current, relative)
                if effect.parent == owned_dir:
                    effect.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    def choose_catalog_music(self):
        try:
            manifest, _ = QFileDialog.getOpenFileName(self, 'Manifesto do catálogo licenciado', '', 'JSON (*.json)')
            if not manifest:
                return
            catalog = load_catalog(manifest)
            if not catalog:
                raise ValueError('O catálogo não possui trilhas licenciadas.')
            labels = [f"{item['title']} — licença: {item['license']}" for item in catalog]
            selected, accepted = QInputDialog.getItem(self, 'Catálogo licenciado', 'Trilha', labels, 0, False)
            if not accepted:
                return
            record = catalog[labels.index(selected)]
            if not self.current:
                raise ValueError('Salve o projeto e gere a narração antes de adicionar uma trilha.')
            previous = self.current.settings.get('video_music', '')
            internal, plan = copy_music(self.service, self.current, record['path'], self.ffmpeg.text())
            self.music.setText(internal)
            self.music_plan = plan
            self.selected_music_license = record['license']
            self.project()
            self.remove_owned_music(previous)
            self.status_label.setText(f"Trilha licenciada adicionada: {record['title']} ({record['license']}).")
        except Exception as error:
            self.error('Catálogo de trilhas', error)

    def save_feedback(self):
        try:
            project = self.project()
            save_outcome(self.repo, project.id, self.outcome_rating.currentData(), self.outcome_feedback.toPlainText())
            self.status_label.setText('Aprendizado salvo localmente para os próximos planejamentos com Ollama.')
        except Exception as error:
            self.error('Aprendizados', error)

    def choose_cover(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Imagem de capa', '', 'Imagens (*.png *.jpg *.jpeg *.jfif *.webp)')
        if filename:
            self.generate_cover(filename)

    def regenerate_cover(self):
        if not self.current:
            self.error('Capa', ValueError('Salve ou abra um projeto antes de gerar a capa.'))
            return
        source = self.current.settings.get('cover_source', '')
        if not source:
            self.error('Capa', ValueError('Escolha uma imagem de capa antes de gerar.'))
            return
        self.generate_cover(source)

    def suggest_cover_copy(self):
        try:
            if self.workers:
                return
            project = self.project()
            if not project.script.strip():
                raise ValueError('Escreva ou cole o roteiro antes de pedir o texto da capa.')
            provider = self.ollama_provider()
            self.start_ai_job(
                lambda: validate_cover_copy(provider.complete_structured(cover_copy_prompt(project.theme, project.script), cover_copy_schema())),
                self.accept_cover_copy,
                'Criando sugestão local para a capa',
                provider,
            )
        except Exception as error:
            self.error('Texto de capa', error)

    def accept_cover_copy(self, copy):
        self.cover_headline.setText(copy['headline'])
        self.cover_subheadline.setText(copy['subheadline'])
        self.status_label.setText('Sugestão de capa pronta. Revise o texto e escolha a imagem para gerar a capa.')

    def generate_cover_with_ai(self):
        """Create a reviewable local cover plan, then render only project-owned media."""
        try:
            if self.workers:
                return
            project = self.project()
            if not project.script.strip() or not project.images:
                raise ValueError('Escreva o roteiro e importe ao menos uma imagem antes de gerar a capa com IA.')
            provider = self.ollama_provider()
            def create_plan():
                visual = project.settings.get('visual_analysis') or {}
                notes = visual.get('notes', []) if isinstance(visual, dict) and visual.get('status') == 'completed' else []
                # Reuse prior local vision. If it has not run yet, perform it
                # here only when the user configured a local visual model.
                if not notes and project.settings.get('visual_analysis_enabled'):
                    model = str(project.settings.get('visual_ollama_model', '')).strip()
                    if model:
                        try:
                            vision = OllamaProvider(model, project.settings.get('ollama_url', self.ollama_url.text()))
                            vision.cancel = provider.cancel
                            notes = describe_imported_images(vision, self.service.artifact_dir(project), project.images)
                            visual = {'status': 'completed', 'version': VISION_DIRECTOR_VERSION, 'model': model, 'notes': notes}
                        except Exception as error:
                            if provider.cancel is not None and provider.cancel.is_set():
                                raise
                            visual = {'status': 'unavailable', 'model': model, 'reason': str(error)}
                try:
                    request = cover_plan_prompt(project.theme, project.script, list(project.images), notes)
                    plan = validate_cover_plan(provider.complete_structured(request, cover_plan_schema()), list(project.images))
                except Exception as error:
                    if provider.cancel is not None and provider.cancel.is_set():
                        raise
                    plan = cover_plan_fallback(project.theme, list(project.images))
                    plan['reason'] = f'Proposta segura sem IA local: {error}'[:240]
                return plan, visual
            self.start_ai_job(
                create_plan,
                lambda result: self.accept_cover_plan(project.id, result[0], result[1]),
                'Analisando roteiro e montando proposta de capa local', provider,
            )
        except Exception as error:
            self.error('Capa com IA', error)

    def accept_cover_plan(self, project_id, plan, visual_analysis=None):
        project = self.repo.get(project_id)
        if project is None or plan['source'] not in project.images:
            raise ValueError('O projeto ou as imagens mudaram durante a criação da capa; a proposta não foi aplicada.')
        self.current = project
        self.cover_headline.setText(plan['headline'])
        self.cover_subheadline.setText(plan['subheadline'])
        self.cover_preset.setCurrentIndex(max(0, self.cover_preset.findData(plan['preset'])))
        self.cover_text_position.setCurrentIndex(max(0, self.cover_text_position.findData(plan['text_position'])))
        self.cover_effect_intensity.setCurrentIndex(max(0, self.cover_effect_intensity.findData(plan['effect_intensity'])))
        self.cover_reason.setText(plan['reason'])
        project.settings['cover_plan'] = dict(plan)
        if isinstance(visual_analysis, dict) and visual_analysis:
            project.settings['visual_analysis'] = visual_analysis
        self.current = self.repo.save(project)
        self.generate_cover(str(self.service.artifact(project, plan['source'])), plan['highlight_words'])

    def generate_cover(self, source, highlight_words=None):
        try:
            if not self.current:
                raise ValueError('Salve ou abra um projeto antes de gerar a capa.')
            headline = self.cover_headline.text().strip() or cover_copy_fallback(self.current.theme)['headline']
            cover = self.service.create_cover(
                self.current, source,
                headline=headline,
                subheadline=self.cover_subheadline.text().strip(),
                preset=self.cover_preset.currentData(),
                effect_intensity=self.cover_effect_intensity.currentData(),
                border=self.cover_border.isChecked(),
                text_position=self.cover_text_position.currentData(),
                highlight_words=highlight_words if highlight_words is not None else self.current.settings.get('cover_highlight_words', []),
            )
            self.refresh()
            self.status_label.setText(f'Capa criada: {cover}')
        except Exception as error:
            self.refresh()
            self.error('Capa', error)

    def open_cover(self):
        try:
            if not self.current:
                raise ValueError('Abra um projeto antes de abrir a capa.')
            cover = self.service.existing_cover_path(self.current)
            if not cover.is_file():
                raise ValueError('Ainda não há uma capa gerada para este projeto.')
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(cover)))
        except Exception as error:
            self.error('Capa', error)

    def voice(self):
        self.run_step('voz')

    def test_voice(self):
        if self.workers:
            return
        try:
            project = self.project()
            settings = project.settings
            text = speech_text(normalize_text(project.script, settings.get('pronunciation_dictionary', {})))
            provider_name = settings.get('tts_provider', 'piper')
            provider = KokoroProvider(settings.get('kokoro_python', ''), settings.get('kokoro_voice', 'pm_alex')) if provider_name == 'kokoro' else PiperProvider(settings['piper_executable'], settings['piper_model'])
            target = self.service.artifact_dir(project) / 'narracao_previa.wav'
            started = perf_counter()
            worker = JobWorker(lambda cancel: provider.synthesize(text, target, cancel, speed=settings.get('narration_speed', 1.0), sentence_pause=settings.get('sentence_pause', .3)))
            self.workers.append(worker)
            def done(path):
                self.preview.open(path)
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
                self.status_label.setText(f'Prévia: {provider_name} / {getattr(provider, "voice", Path(settings["piper_model"]).name)} em {perf_counter()-started:.1f}s.')
            worker.succeeded.connect(done); worker.failed.connect(self.failed); worker.finished.connect(lambda: self.finished(worker))
            self.set_busy(True); self.status_label.setText('Gerando prévia de voz…'); worker.start()
        except Exception as error:
            self.error('Testar voz', error)

    def ai_script(self):
        if self.workers:
            return
        try:
            project = self.project()
            theme, seconds = project.theme, project.duration
            notes = self.sources.toPlainText()
            fiction = self.fiction.isChecked()
            provider = self.ollama_provider()
            if not notes.strip() and not fiction:
                raise ValueError('Preencha as notas para roteiro factual ou selecione Ficção original.')
            if project.script.strip() and QMessageBox.question(self, 'Substituir roteiro?', 'Gerar um novo rascunho substituirá o roteiro atual após uma resposta válida. Continuar?') != QMessageBox.StandardButton.Yes:
                return
            self.start_ai_job(lambda: generate_script(provider, theme, seconds, notes, fiction=fiction, planning_context=planning_context(self.repo)),
                              self.accept_draft, 'Gerando roteiro local', provider)
        except Exception as error:
            self.error('Roteiro', error)

    def analyze_hook(self):
        if self.workers:
            return
        try:
            script = self.quick_script.toPlainText() if self.tabs.currentWidget() is self.dashboard_page else self.script.toPlainText()
            provider = self.ollama_provider()
            self.start_ai_job(
                lambda: validate_hook_response(provider.complete_structured(hook_prompt(script), hook_response_schema()), script),
                self.accept_hook_suggestions, 'Analisando gancho localmente', provider,
            )
        except Exception as error:
            self.error('Analisar gancho', error)

    def accept_hook_suggestions(self, suggestions):
        rows = []
        labels = {'conflito': 'Conflito', 'consequência': 'Consequência', 'curiosidade': 'Curiosidade'}
        for suggestion in suggestions:
            rows.append(f"{labels[suggestion['angle']]}: {suggestion['text']}\n{suggestion['reason']}")
        self.hook_suggestions.setText('Sugestões de gancho — revise antes de usar:\n\n' + '\n\n'.join(rows))
        self.status_label.setText('Sugestões de gancho geradas localmente; o roteiro não foi alterado.')

    def accept_draft(self, draft):
        previous = self.script.toPlainText()
        try:
            self.script.setPlainText(draft)
            self.project()
        except Exception:
            self.script.setPlainText(previous)
            raise
        self.status_label.setText('Rascunho salvo. Revise fatos e redação antes de gerar a voz.')

    def ollama_provider(self):
        return OllamaProvider(self.ollama.currentText(), self.ollama_url.text())

    def invalidate_ollama_connection(self):
        selected = self.ollama.currentText()
        self.ollama.clear()
        self.ollama.setCurrentText(selected)
        self.ollama_status.setText('Endereço alterado. Verifique novamente a conexão e os modelos instalados.')

    def confirm_ai_edits(self):
        if self.ai.pack() == self.saved_ai_pack:
            return True
        answer = QMessageBox.question(self, 'Edições de IA não salvas',
            'Salvar as edições de cenas e metadados antes de continuar?',
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            try:
                self.project()
            except Exception as error:
                self.error('Salvar edições de IA', error)
                return False
        return True

    def start_ai_job(self, task, accept, message, provider=None):
        if self.workers:
            return
        def execute(cancel):
            if provider is not None:
                provider.cancel = cancel
            if cancel.is_set():
                raise RuntimeError('Operação cancelada; conteúdo anterior preservado.')
            result = task()
            if cancel.is_set():
                raise RuntimeError('Operação cancelada; conteúdo anterior preservado.')
            return result
        worker = JobWorker(execute)
        def receive(result):
            if worker.cancel_event.is_set():
                self.status_label.setText('Operação cancelada; conteúdo anterior preservado.')
                return
            try:
                accept(result)
            except Exception as error:
                self.error('Salvar resultado de IA', error)
                self.status_label.setText('Não foi possível salvar o resultado de IA.')
        def failure(message):
            self.ollama_status.setText('Operação de IA não concluída. Confira conexão, modelo e dados de entrada.')
            self.status_label.setText(message)
            if not worker.cancel_event.is_set():
                self.error('Assistente IA', message)
        self.workers.append(worker)
        worker.succeeded.connect(receive)
        worker.failed.connect(failure)
        worker.finished.connect(lambda: self.finished(worker))
        self.set_busy(True)
        self.status_label.setText(message + '… Você pode cancelar. Limite de 90 s por chamada; cenas podem ter uma tentativa adicional de correção.')
        worker.start()

    def check_ollama(self):
        try:
            provider = self.ollama_provider()
            self.start_ai_job(provider.list_models, self.accept_models, 'Consultando modelos locais', provider)
        except Exception as error:
            self.error('Ollama', error)

    def accept_models(self, models):
        previous = self.ollama.currentText()
        self.ollama.clear()
        self.ollama.addItems(models)
        if previous in models:
            self.ollama.setCurrentText(previous)
        self.ollama_status.setText(f'Conectado: {len(models)} modelo(s) instalado(s).' if models else 'Conectado, mas sem modelos. Instale um modelo no Ollama antes de gerar.')
        self.status_label.setText(self.ollama_status.text())

    def ai_plan(self):
        if self.workers:
            return
        try:
            project = self.project()
            provider = self.ollama_provider()
            script, count, style = project.script, self.ai.count.value(), self.ai.style.text()
            if not script.strip():
                raise ValueError('Escreva ou gere e revise o roteiro primeiro.')
            if self.ai.scenes.rowCount() and QMessageBox.question(self, 'Substituir planejamento?', 'Uma resposta válida substituirá as cenas e metadados atuais. Continuar?') != QMessageBox.StandardButton.Yes:
                return
            self.start_ai_job(lambda: generate_pack(provider, script, count, style, planning_context(self.repo)),
                              lambda pack: self.accept_plan(pack, script), 'Gerando cenas, prompts e metadados', provider)
        except Exception as error:
            self.error('Planejamento IA', error)

    def accept_plan(self, pack, script):
        previous, previous_script = self.ai.pack(), self.ai.source_script
        try:
            self.ai.set_pack(pack, script)
            self.project()
        except Exception:
            self.ai.set_pack(previous, previous_script)
            raise
        self.ai.update_notice(script)
        self.tabs.setCurrentWidget(self.dashboard_page)
        self.status_label.setText('Planejamento salvo como rascunho. Revise os textos; nenhuma imagem foi gerada.')

    def save_ai_plan(self):
        try:
            script = self.script.toPlainText()
            pack = validate_pack(self.ai.pack(), script)
            self.accept_plan(pack, script)
            self.status_label.setText('Revisão salva no projeto. Nenhum conteúdo publicado.')
        except Exception as error:
            self.error('Revisão de IA', error)

    def preview_artifact(self, step, filename):
        try:
            if not self.current or self.current.statuses.get(step) != StepStatus.DONE:
                raise ValueError('Conclua esta etapa antes de abrir a prévia.')
            self.preview.open(self.service.artifact(self.current, filename))
            self.tabs.setCurrentWidget(self.preview)
        except Exception as error:
            self.error('Prévia', error)

    def subtitles(self):
        self.run_step('legendas')

    def preview_final_audio(self):
        if self.workers:
            return
        try:
            project = self.project()
            if project.statuses.get('voz') != StepStatus.DONE:
                raise ValueError('Gere a narração antes de ouvir a prévia final.')
            root = self.service.artifact_dir(project)
            music = self.service.artifact(project, project.settings['video_music']) if project.settings.get('video_music') else None
            target = root / 'mixagem_previa.wav'
            # Older projects can have an empty or moved FFmpeg path.  Use the
            # same resolver used by the export pipeline before starting a job.
            preview_settings = resolved_mapping({**project.settings, 'ffmpeg': project.settings.get('ffmpeg') or self.settings.ffmpeg})
            worker = JobWorker(lambda cancel: render_audio_preview(
                preview_settings['ffmpeg'], root / 'narracao.wav', target,
                music=music, music_volume=preview_settings.get('video_music_volume', 25) / 100,
                mix_profile=preview_settings.get('music_mix_profile', 'equilibrada'), cancel=cancel,
            ))
            self.workers.append(worker)
            def done(path):
                self.preview.open(path)
                self.tabs.setCurrentWidget(self.preview_page)
                self.status_label.setText('Prévia de voz e trilha pronta. A exportação usará a mesma mixagem base.')
            worker.succeeded.connect(done)
            worker.failed.connect(self.failed)
            worker.finished.connect(lambda: self.finished(worker))
            self.set_busy(True)
            self.status_label.setText('Gerando prévia de áudio final…')
            worker.start()
        except Exception as error:
            self.error('Prévia de áudio', error)

    def edit_caption_timings(self):
        """Let the user correct validated ASR word intervals before export."""
        try:
            if not self.current:
                raise ValueError('Abra um projeto antes de ajustar as legendas.')
            target = self.service.artifact(self.current, 'legendas.srt')
            alignment = alignment_metadata(target)
            words = alignment.get('words') if isinstance(alignment, dict) else None
            if not alignment.get('synchronized') or not isinstance(words, list) or not words:
                raise ValueError('Gere legendas sincronizadas por palavra antes de ajustá-las.')
            dialog = QDialog(self)
            dialog.setWindowTitle('Ajustar sincronização por palavra')
            dialog.resize(700, 560)
            layout = QVBoxLayout(dialog)
            note = QLabel('Ajuste somente quando necessário. Os tempos vêm do ASR local; salvar atualizará SRT, ASS e deixará apenas o vídeo pendente.')
            note.setWordWrap(True)
            layout.addWidget(note)
            table = QTableWidget(len(words), 4, dialog)
            table.setHorizontalHeaderLabels(['Palavra', 'Início (s)', 'Fim (s)', 'Confiança ASR'])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for row, word in enumerate(words):
                text = QTableWidgetItem(str(word.get('text', '')))
                text.setFlags(text.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, 0, text)
                table.setItem(row, 1, QTableWidgetItem(f"{float(word.get('start', 0)):.3f}"))
                table.setItem(row, 2, QTableWidgetItem(f"{float(word.get('end', 0)):.3f}"))
                confidence = word.get('confidence')
                value = '—' if confidence is None else f'{float(confidence):.2f}'
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, 3, item)
            layout.addWidget(table)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            buttons.rejected.connect(dialog.reject)
            buttons.accepted.connect(dialog.accept)
            layout.addWidget(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            changes = []
            for index, word in enumerate(words):
                try:
                    start = float(table.item(index, 1).text().replace(',', '.'))
                    end = float(table.item(index, 2).text().replace(',', '.'))
                except (AttributeError, ValueError):
                    raise ValueError(f'Tempo inválido na palavra {index + 1}.') from None
                if start != float(word.get('start')) or end != float(word.get('end')):
                    changes.append({'index': index, 'start': start, 'end': end})
            if not changes:
                return
            style = resolve_preset(self.current.settings.get('editing_preset'))
            self.current.settings['caption_alignment'] = apply_word_adjustments(target, changes, style['subtitle_style'])
            self.current.settings['caption_timing'] = 'word'
            self.current.statuses['video'] = StepStatus.PENDING
            self.current.history.append({'time': datetime.now(timezone.utc).isoformat(),
                                         'step': 'legendas', 'status': StepStatus.DONE.value,
                                         'message': 'Sincronização por palavra ajustada manualmente.'})
            self.current = self.repo.save(self.current)
            self.refresh()
            self.status_label.setText('Sincronização ajustada. Exporte novamente o MP4.')
        except Exception as error:
            self.error('Ajustar legendas', error)

    def video(self):
        self.run_step('video')

    def generate_short(self):
        if self.workers:
            return
        if self.tabs.currentWidget() is self.dashboard_page:
            self.theme.setText(self.quick_theme.text())
            self.script.setPlainText(self.quick_script.toPlainText())
        if not self.theme.text().strip() and not self.script.toPlainText().strip():
            self.status_label.setText('Informe um tema ou cole o roteiro para gerar o Short.')
            return
        if not self.theme.text().strip():
            first_line = next((line.strip() for line in self.script.toPlainText().splitlines() if line.strip()), '')
            self.theme.setText(first_line[:80] or 'Short personalizado')
        try:
            project = self.project()
            self.preview.clear()
            worker = JobWorker(lambda cancel: self.pipeline.execute_short(project, project.settings, cancel))
            self.workers.append(worker)
            worker.succeeded.connect(self.completed)
            worker.failed.connect(self.failed)
            worker.finished.connect(lambda: self.finished(worker))
            self.set_busy(True)
            visual_ready = bool(project.settings.get('visual_analysis_enabled') and project.settings.get('visual_ollama_model'))
            if project.images and visual_ready:
                self.automatic_progress.setText('Em andamento: analisando imagens, montando cenas, gerando voz, legendas e MP4.')
            elif project.images:
                self.automatic_progress.setText('Em andamento: usando as imagens enviadas na ordem importada; gerando voz, legendas e MP4.')
            else:
                self.automatic_progress.setText('Em andamento: planejamento, imagens, voz, legendas e MP4.')
            self.status_label.setText('Gerando Short: narração, legendas e vídeo…')
            worker.start()
        except Exception as error:
            self.error('Gerar Short', error)

    def run_step(self, step):
        if self.workers:
            return
        try:
            project = self.project()
            self.preview.clear()
            settings = dict(project.settings)
            worker = JobWorker(lambda cancel: self.pipeline.execute(project, step, settings, cancel))
            self.workers.append(worker)
            worker.succeeded.connect(self.completed)
            worker.failed.connect(self.failed)
            worker.finished.connect(lambda: self.finished(worker))
            self.set_busy(True)
            self.status_label.setText(f'Processando {step}…')
            worker.start()
        except Exception as error:
            self.error(step, error)

    def set_busy(self, busy):
        for widget in [*self.actions, self.items, self.quick_theme, self.quick_script, self.quick_preset, self.theme, self.category, self.fmt, self.duration, self.script, self.sources, self.ollama, self.ollama_url, self.visual_analysis_enabled, self.visual_ollama_model, self.comfyui_url, self.ai, self.fiction, self.piper, self.model, self.tts_provider, self.kokoro_voice, self.ffmpeg, self.narration_speed, self.sentence_pause, self.asr_model, self.pronunciations, self.media, self.motion, self.transition, self.editing_preset, self.music, self.music_volume, self.music_mix_profile, self.effects_intensity, self.cover_headline, self.cover_subheadline, self.cover_preset, self.cover_text_position, self.cover_effect_intensity, self.cover_border]:
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def completed(self, result):
        self.automatic_progress.setText('Concluido: planejamento, imagens, voz, legendas e MP4.')
        self.status_label.setText(f'Concluído: {result.name}')
        self.refresh()

    def failed(self, message):
        self.automatic_progress.setText(f'Falha: {message}')
        self.status_label.setText('Etapa falhou. Corrija a causa e execute novamente.')
        self.refresh()
        self.error('Falha na etapa', message)

    def finished(self, worker):
        self.workers.remove(worker)
        worker.deleteLater()
        self.set_busy(False)

    def cancel(self):
        for worker in self.workers:
            worker.cancel()
        self.status_label.setText('Cancelamento solicitado…')

    def closeEvent(self, event):
        if self.workers:
            self.cancel()
            self.status_label.setText('Aguarde o cancelamento terminar e feche novamente.')
            event.ignore()
        else:
            if not self.confirm_ai_edits():
                event.ignore()
                return
            self.preview.clear()
            event.accept()

    def error(self, title, error):
        QMessageBox.critical(self, title, str(error))
